import argparse
import ctypes
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd
from lmfit import Minimizer, Parameters
from lmfit.models import VoigtModel
from scipy.special import voigt_profile
from tqdm import tqdm

from Voigt_PL_output import (
    ensure_output_dir,
    plot_global_fit_comparison,
    plot_representative_fit,
    plot_representative_fit_77k_dual,
    plot_sigma_sensitivity,
    save_77k_dual_fit_summary,
    save_global_fit_summary,
    save_sensitivity_summary_xlsx,
)

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:
    tk = None
    filedialog = None
    messagebox = None

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 物理常数与 gamma 扫描网格（eV）
HC_EV_NM = 1239.8
GAMMA_MIN_EV = 0.010
GAMMA_MAX_EV = 0.030
GAMMA_STEP_EV = 0.002
GAMMA_REF_EV = 0.020
GAMMA_RT_INIT_EV = 0.025
GAMMA_77K_INIT_EV = 0.0075
SENSITIVITY_E_VIB_FIXED_EV = 0.15
E_VIB_MIN_EV = 0.12
E_VIB_MAX_EV = 0.2
E_VIB_INIT_EV = 0.165
ANALYSIS_MODE_SENSITIVITY = "sensitivity"
ANALYSIS_MODE_GLOBAL = "global"


def gaussian_fwhm_from_sigma(sigma_ev):
    """Convert Gaussian sigma to Gaussian FWHM."""
    return 2.0 * np.sqrt(2.0 * np.log(2.0)) * sigma_ev


def lorentzian_fwhm_from_gamma(gamma_ev):
    """Convert Lorentzian HWHM gamma to Lorentzian FWHM."""
    return 2.0 * gamma_ev


def voigt_fwhm_approx(sigma_ev, gamma_ev):
    """Approximate Voigt FWHM from Gaussian sigma and Lorentzian gamma."""
    w_g = gaussian_fwhm_from_sigma(sigma_ev)
    w_l = lorentzian_fwhm_from_gamma(gamma_ev)
    return 0.5346 * w_l + np.sqrt(0.2166 * w_l**2 + w_g**2)


def compute_linewidth_metrics(sigma_ev, gamma_ev):
    """Return comparable Gaussian, Lorentzian, and Voigt linewidth metrics."""
    sigma_ev = float(sigma_ev)
    gamma_ev = float(gamma_ev)

    w_g_ev = gaussian_fwhm_from_sigma(sigma_ev)
    w_l_ev = lorentzian_fwhm_from_gamma(gamma_ev)
    w_v_ev = voigt_fwhm_approx(sigma_ev, gamma_ev)

    if np.isfinite(w_l_ev) and w_l_ev > 0:
        ratio = w_g_ev / w_l_ev
    else:
        ratio = np.nan

    return {
        "gaussian_fwhm_ev": w_g_ev,
        "gaussian_fwhm_mev": w_g_ev * 1000.0,
        "lorentzian_fwhm_ev": w_l_ev,
        "lorentzian_fwhm_mev": w_l_ev * 1000.0,
        "voigt_fwhm_ev": w_v_ev,
        "voigt_fwhm_mev": w_v_ev * 1000.0,
        "gaussian_to_lorentzian_fwhm_ratio": ratio,
    }


def load_pl_csv(filepath):
    """读取 PL 光谱 CSV/TXT（第一列波长 nm，第二列强度）。"""
    scan_type = ""
    data_start_row = -1
    is_uvvis = False

    with open(filepath, "r", encoding="windows-1252") as f:
        for i, line in enumerate(f):
            parts = [p.strip() for p in line.replace("\t", ",").split(",")]

            if i == 1 and len(parts) >= 2:
                scan_type = parts[1].lower()

            if i == 2 and len(parts) >= 2 and parts[1].lower() == "jasco":
                is_uvvis = True
                scan_type = "uv-vis"

            if is_uvvis:
                if "xydata" in line.lower():
                    data_start_row = i + 1
                    break
            elif len(parts) >= 2:
                try:
                    float(parts[0])
                    float(parts[1])
                    data_start_row = i
                    break
                except ValueError:
                    pass

    if data_start_row == -1:
        raise ValueError(f"无法在 {os.path.basename(filepath)} 中定位数值数据起始行")

    df = pd.read_csv(
        filepath,
        skiprows=data_start_row,
        header=None,
        usecols=[0, 1],
        engine="python",
        encoding="windows-1252",
    )
    df.columns = ["Wavelength_nm", "Intensity"]
    df["Wavelength_nm"] = pd.to_numeric(df["Wavelength_nm"], errors="coerce")
    df["Intensity"] = pd.to_numeric(df["Intensity"], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    if df.empty:
        raise ValueError(f"{os.path.basename(filepath)} 中没有有效数值数据")

    return df, scan_type


def wavelength_to_energy_spectrum(wavelength_nm, intensity_nm):
    """波长域转能量域，并做雅可比修正 Intensity_eV = Intensity_nm * lambda^2。"""
    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    intensity_nm = np.asarray(intensity_nm, dtype=float)

    valid = wavelength_nm > 0
    wavelength_nm = wavelength_nm[valid]
    intensity_nm = intensity_nm[valid]

    energy_ev = HC_EV_NM / wavelength_nm
    intensity_ev = intensity_nm * wavelength_nm**2

    order = np.argsort(energy_ev)
    return energy_ev[order], intensity_ev[order]


def estimate_fwhm_nm(wavelength_nm, intensity_nm):
    """在原始波长域估计半高全宽 FWHM。"""
    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    intensity_nm = np.asarray(intensity_nm, dtype=float)

    order = np.argsort(wavelength_nm)
    wavelength_nm = wavelength_nm[order]
    intensity_nm = intensity_nm[order]

    if len(intensity_nm) == 0:
        return np.nan

    baseline = np.percentile(intensity_nm, 5)
    intensity_corr = np.maximum(intensity_nm - baseline, 0.0)
    peak_value = intensity_corr.max()
    if peak_value <= 0:
        return np.nan

    half_max = 0.5 * peak_value
    peak_idx = int(np.argmax(intensity_corr))

    left_idx = peak_idx
    while left_idx > 0 and intensity_corr[left_idx] >= half_max:
        left_idx -= 1

    right_idx = peak_idx
    while right_idx < len(intensity_corr) - 1 and intensity_corr[right_idx] >= half_max:
        right_idx += 1

    if left_idx >= peak_idx or right_idx <= peak_idx:
        return np.nan

    def interpolate_crossing(idx_left, idx_right):
        x1, x2 = wavelength_nm[idx_left], wavelength_nm[idx_right]
        y1 = intensity_corr[idx_left] - half_max
        y2 = intensity_corr[idx_right] - half_max
        if y2 == y1:
            return x1
        return x1 - y1 * (x2 - x1) / (y2 - y1)

    left_crossing = interpolate_crossing(left_idx, left_idx + 1)
    right_crossing = interpolate_crossing(right_idx - 1, right_idx)
    return abs(right_crossing - left_crossing)


def fwhm_nm_to_ev(fwhm_nm, peak_wavelength_nm):
    """将波长域 FWHM 换算为峰位处的能量宽度。"""
    if not np.isfinite(fwhm_nm) or peak_wavelength_nm <= 0:
        return np.nan
    return fwhm_nm * HC_EV_NM / peak_wavelength_nm**2


def select_fit_window(energy, peak_energy, fwhm_ev, lower_factor=0.7, upper_factor=1.2):
    """按峰位与 FWHM 截取局部拟合窗口，避开低能端振动旁带。"""
    if not np.isfinite(fwhm_ev) or fwhm_ev <= 0:
        raise ValueError("无法估计有效的 FWHM，无法构建拟合窗口")

    energy_min = float(np.min(energy))
    energy_max = float(np.max(energy))
    lower = max(energy_min, peak_energy - lower_factor * fwhm_ev)
    upper = min(energy_max, peak_energy + upper_factor * fwhm_ev)

    if lower >= upper:
        raise ValueError("拟合窗口在可用能量范围内为空")

    fit_mask = (energy >= lower) & (energy <= upper)
    if np.count_nonzero(fit_mask) < 5:
        raise ValueError("拟合窗口内数据点过少")

    return fit_mask, lower, upper


def estimate_peak_energy(energy, intensity):
    """估计谱峰能量位置。"""
    peak_idx = int(np.argmax(intensity))
    return float(energy[peak_idx])


def gaussian_component(energy, center, sigma):
    """归一化高斯线型，用于分量分解可视化。"""
    return np.exp(-0.5 * ((energy - center) / sigma) ** 2) / (sigma * np.sqrt(2 * np.pi))


def lorentzian_component(energy, center, gamma):
    """归一化洛伦兹线型，用于分量分解可视化。"""
    return (gamma / np.pi) / (gamma**2 + (energy - center) ** 2)


def build_gamma_grid():
    """生成固定 gamma 扫描网格（eV）。"""
    gamma_values = np.arange(GAMMA_MIN_EV, GAMMA_MAX_EV + GAMMA_STEP_EV / 2, GAMMA_STEP_EV)
    return [float(gamma) for gamma in gamma_values]


def prepare_spectrum_record(filepath):
    """读取单条光谱并完成能量换算与动态拟合窗口截取。"""
    df, scan_type = load_pl_csv(filepath)
    wavelength_nm = df["Wavelength_nm"].values
    intensity_nm = df["Intensity"].values
    energy, intensity = wavelength_to_energy_spectrum(wavelength_nm, intensity_nm)

    peak_idx = int(np.argmax(intensity))
    peak_energy = float(energy[peak_idx])
    peak_wavelength_nm = float(wavelength_nm[np.argmax(intensity_nm)])

    fwhm_nm = estimate_fwhm_nm(wavelength_nm, intensity_nm)
    fwhm_ev_window = fwhm_nm_to_ev(fwhm_nm, peak_wavelength_nm)
    fit_mask, fit_lower, fit_upper = select_fit_window(energy, peak_energy, fwhm_ev_window)

    return {
        "filename": os.path.basename(filepath),
        "filepath": filepath,
        "scan_type": scan_type,
        "energy": energy,
        "intensity": intensity,
        "fit_mask": fit_mask,
        "fit_lower": fit_lower,
        "fit_upper": fit_upper,
        "peak_energy": peak_energy,
        "peak_wavelength_nm": peak_wavelength_nm,
        "fwhm_nm": fwhm_nm,
        "fwhm_ev_window": fwhm_ev_window,
    }


def eval_sensitivity_dual_voigt(params, energy):
    """模式 1 双 Voigt：0-1 中心固定为 0-0 - 0.15 eV，sigma/gamma 共享。"""
    sigma = params["sigma"].value
    gamma = params["gamma"].value
    voigt_00 = eval_voigt_component(
        params["amplitude_00"].value,
        params["center_00"].value,
        sigma,
        gamma,
        energy,
    )
    voigt_01 = eval_voigt_component(
        params["amplitude_01"].value,
        params["center_01"].value,
        sigma,
        gamma,
        energy,
    )
    return voigt_00, voigt_01, voigt_00 + voigt_01


def sensitivity_dual_voigt_residual(params, energy, intensity):
    """模式 1 双 Voigt 残差。"""
    _, _, predicted = eval_sensitivity_dual_voigt(params, energy)
    return intensity - predicted


def fit_voigt_fixed_gamma(energy, intensity, gamma_ev):
    """固定 gamma 下执行双 Voigt 拟合，主/次峰共享 sigma 和 gamma。"""
    peak_amp = float(np.max(intensity) - np.min(intensity))
    params = Parameters()
    params.add("amplitude_00", value=peak_amp * 0.65, min=0)
    params.add("center_00", value=estimate_peak_energy(energy, intensity))
    params.add("sigma", value=0.05, min=1e-4, max=0.5)
    params.add("gamma", value=gamma_ev, vary=False)
    params.add("amplitude_01", value=peak_amp * 0.35, min=0)
    params.add("center_01", expr=f"center_00 - {SENSITIVITY_E_VIB_FIXED_EV}")

    minimizer = Minimizer(sensitivity_dual_voigt_residual, params, fcn_args=(energy, intensity))
    return minimizer.minimize(method="leastsq")


def fit_gamma_task(task):
    """进程池子任务：对 (光谱, gamma) 执行一次 Voigt 拟合。"""
    filename = task["filename"]
    gamma_ev = task["gamma_ev"]
    energy_window = task["energy_window"]
    intensity_window = task["intensity_window"]

    try:
        result = fit_voigt_fixed_gamma(energy_window, intensity_window, gamma_ev)
        voigt_00, voigt_01, total = eval_sensitivity_dual_voigt(result.params, energy_window)
        r_squared = compute_r_squared(intensity_window, total)
        sigma_ev = float(result.params["sigma"].value)
        gamma_ev_fit = float(result.params["gamma"].value)
        linewidth_metrics = compute_linewidth_metrics(sigma_ev, gamma_ev_fit)
        return {
            "filename": filename,
            "gamma_ev": gamma_ev,
            "gamma_mev": gamma_ev * 1000.0,
            "sigma_ev": sigma_ev,
            "sigma_mev": sigma_ev * 1000.0,
            "center_00_ev": float(result.params["center_00"].value),
            "center_01_ev": float(result.params["center_01"].value),
            "amplitude_00": float(result.params["amplitude_00"].value),
            "amplitude_01": float(result.params["amplitude_01"].value),
            **linewidth_metrics,
            "r_squared": r_squared,
            "fit_success": True,
            "error_message": "",
            "best_fit": total.astype(float),
            "voigt_00": voigt_00.astype(float),
            "voigt_01": voigt_01.astype(float),
        }
    except Exception as exc:
        return {
            "filename": filename,
            "gamma_ev": gamma_ev,
            "gamma_mev": gamma_ev * 1000.0,
            "sigma_ev": np.nan,
            "sigma_mev": np.nan,
            "center_00_ev": np.nan,
            "center_01_ev": np.nan,
            "amplitude_00": np.nan,
            "amplitude_01": np.nan,
            "gaussian_fwhm_ev": np.nan,
            "gaussian_fwhm_mev": np.nan,
            "lorentzian_fwhm_ev": np.nan,
            "lorentzian_fwhm_mev": np.nan,
            "voigt_fwhm_ev": np.nan,
            "voigt_fwhm_mev": np.nan,
            "gaussian_to_lorentzian_fwhm_ratio": np.nan,
            "r_squared": np.nan,
            "fit_success": False,
            "error_message": str(exc),
            "best_fit": None,
            "voigt_00": None,
            "voigt_01": None,
        }


def build_sensitivity_fit_mask(record):
    """模式 1 双峰拟合窗口：在原主峰窗口基础上向低能侧扩展到 0-1 峰。"""
    energy = record["energy"]
    fwhm_ev = record["fwhm_ev_window"]
    margin_ev = fwhm_ev if np.isfinite(fwhm_ev) and fwhm_ev > 0 else 0.03
    lower = max(float(np.min(energy)), record["fit_lower"] - SENSITIVITY_E_VIB_FIXED_EV - margin_ev)
    upper = record["fit_upper"]
    fit_mask = (energy >= lower) & (energy <= upper)
    if np.count_nonzero(fit_mask) < 5:
        return record["fit_mask"]
    return fit_mask


def build_fit_tasks(spectrum_records, gamma_values):
    """将每条光谱与每个 gamma 组合成独立拟合任务。"""
    tasks = []
    for record in spectrum_records:
        energy = record["energy"]
        intensity = record["intensity"]
        fit_mask = build_sensitivity_fit_mask(record)
        energy_window = energy[fit_mask]
        intensity_window = intensity[fit_mask]
        for gamma_ev in gamma_values:
            tasks.append(
                {
                    "filename": record["filename"],
                    "gamma_ev": gamma_ev,
                    "energy_window": energy_window,
                    "intensity_window": intensity_window,
                }
            )
    return tasks


def run_parallel_fits(tasks, max_workers=None):
    """使用 ProcessPoolExecutor 并行执行 gamma 网格扫描。"""
    results = []
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fit_gamma_task, task) for task in tasks]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Voigt 拟合进度"):
            results.append(future.result())
    return results


def results_to_dataframe(fit_results):
    """将拟合结果整理为汇总表。"""
    columns = [
        "filename",
        "gamma_mev",
        "gamma_ev",
        "sigma_mev",
        "sigma_ev",
        "center_00_ev",
        "center_01_ev",
        "amplitude_00",
        "amplitude_01",
        "gaussian_fwhm_ev",
        "gaussian_fwhm_mev",
        "lorentzian_fwhm_ev",
        "lorentzian_fwhm_mev",
        "voigt_fwhm_ev",
        "voigt_fwhm_mev",
        "gaussian_to_lorentzian_fwhm_ratio",
        "r_squared",
        "fit_success",
        "error_message",
    ]
    return pd.DataFrame(fit_results, columns=columns)


def estimate_sigma_initial(record):
    """由 FWHM 粗估 sigma 初值（高斯近似）。"""
    fwhm_ev = record["fwhm_ev_window"]
    if np.isfinite(fwhm_ev) and fwhm_ev > 0:
        return fwhm_ev / (2.0 * np.sqrt(2.0 * np.log(2.0)))
    return 0.02


def compute_r_squared(observed, predicted):
    """计算决定系数 R²。"""
    observed = np.asarray(observed, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    ss_res = np.sum((observed - predicted) ** 2)
    ss_tot = np.sum((observed - np.mean(observed)) ** 2)
    if ss_tot <= 0:
        return np.nan
    return float(1.0 - ss_res / ss_tot)


def eval_voigt_component(amplitude, center, sigma, gamma, energy):
    """计算单 Voigt 峰在能量轴上的包络。"""
    model = VoigtModel()
    return model.eval(
        amplitude=amplitude,
        center=center,
        sigma=sigma,
        gamma=gamma,
        x=energy,
    )


def add_rt_vibronic_peak_params(params, record):
    """向 Parameters 添加 RT 0-0 / 0-1 振动峰参数，E_vib 由 77K 模型共享固定。"""
    energy_rt = record["energy"][record["fit_mask"]]
    intensity_rt = record["intensity"][record["fit_mask"]]
    peak_amp_rt = float(np.max(intensity_rt) - np.min(intensity_rt))
    center_00_init = estimate_peak_energy(energy_rt, intensity_rt)
    params.add("amplitude_RT_00", value=peak_amp_rt * 0.65, min=0)
    params.add("center_RT_00", value=center_00_init)
    params.add("amplitude_RT_01", value=peak_amp_rt * 0.35, min=0)
    params.add("center_RT_01", expr="center_RT_00 - E_vib")


def add_77k_vibronic_peak_params(params, record):
    """向 Parameters 添加 77K 0-0 / 0-1 振动峰参数（含 E_vib 约束）。"""
    energy_77k = record["energy"][record["fit_mask"]]
    intensity_77k = record["intensity"][record["fit_mask"]]
    peak_amp_77k = float(np.max(intensity_77k) - np.min(intensity_77k))
    center_00_init = estimate_peak_energy(energy_77k, intensity_77k)
    params.add("amplitude_00", value=peak_amp_77k * 0.65, min=0)
    params.add("center_00", value=center_00_init)
    params.add("amplitude_01", value=peak_amp_77k * 0.35, min=0)
    params.add("E_vib", value=E_VIB_INIT_EV, min=E_VIB_MIN_EV, max=E_VIB_MAX_EV)
    params.add("center_01", expr="center_00 - E_vib")


def eval_77k_dual_voigt(params, energy, sigma_name="sigma_global", gamma_name="gamma_77K"):
    """77K 双 Voigt 叠加：0-0 与 0-1 峰共享同一 sigma 与 gamma。"""
    sigma = params[sigma_name].value
    gamma = params[gamma_name].value
    voigt_00 = eval_voigt_component(
        params["amplitude_00"].value,
        params["center_00"].value,
        sigma,
        gamma,
        energy,
    )
    voigt_01 = eval_voigt_component(
        params["amplitude_01"].value,
        params["center_01"].value,
        sigma,
        gamma,
        energy,
    )
    return voigt_00, voigt_01, voigt_00 + voigt_01


def eval_rt_dual_voigt(params, energy):
    """RT 双 Voigt 叠加：0-0 与 0-1 峰共享 sigma_global 与 gamma_RT。"""
    sigma = params["sigma_global"].value
    gamma = params["gamma_RT"].value
    voigt_00 = eval_voigt_component(
        params["amplitude_RT_00"].value,
        params["center_RT_00"].value,
        sigma,
        gamma,
        energy,
    )
    voigt_01 = eval_voigt_component(
        params["amplitude_RT_01"].value,
        params["center_RT_01"].value,
        sigma,
        gamma,
        energy,
    )
    return voigt_00, voigt_01, voigt_00 + voigt_01


def build_global_voigt_params(rt_record, k77_record):
    """构建全局 Voigt 参数：RT 与 77K 均为双振动峰，共享 E_vib 与 sigma。"""
    params = Parameters()
    sigma_init = float(
        np.mean([estimate_sigma_initial(rt_record), estimate_sigma_initial(k77_record)])
    )

    params.add("sigma_global", value=sigma_init, min=1e-4, max=0.5)
    params.add("gamma_RT", value=GAMMA_RT_INIT_EV, min=1e-6, max=0.5)
    params.add(
        "gamma_frac",
        value=GAMMA_77K_INIT_EV / GAMMA_RT_INIT_EV,
        min=1e-6,
        max=0.999,
    )
    params.add("gamma_77K", expr="gamma_RT * gamma_frac")

    add_77k_vibronic_peak_params(params, k77_record)
    add_rt_vibronic_peak_params(params, rt_record)

    return params


def build_standalone_77k_dual_params(record, gamma_ev):
    """单条 77K 光谱的双 Voigt 参数（固定 gamma）。"""
    params = Parameters()
    params.add("sigma", value=estimate_sigma_initial(record), min=1e-4, max=0.5)
    params.add("gamma", value=gamma_ev, vary=False)
    add_77k_vibronic_peak_params(params, record)
    return params


def standalone_77k_dual_residual(params, energy, intensity):
    """单条 77K 光谱双 Voigt 残差。"""
    _, _, predicted = eval_77k_dual_voigt(params, energy, sigma_name="sigma", gamma_name="gamma")
    return intensity - predicted


def fit_77k_dual_voigt(record, gamma_ev):
    """固定 gamma 下对单条 77K 光谱做双 Voigt 振动峰拟合。"""
    energy = record["energy"][record["fit_mask"]]
    intensity = record["intensity"][record["fit_mask"]]
    params = build_standalone_77k_dual_params(record, gamma_ev)
    minimizer = Minimizer(standalone_77k_dual_residual, params, fcn_args=(energy, intensity))
    return minimizer.minimize(method="leastsq")


def global_voigt_residual(params, rt_record, k77_record):
    """联合残差：RT 与 77K 均使用双 Voigt（0-0 + 0-1）叠加。"""
    residual_chunks = []

    energy_rt = rt_record["energy"][rt_record["fit_mask"]]
    intensity_rt = rt_record["intensity"][rt_record["fit_mask"]]
    _, _, predicted_rt = eval_rt_dual_voigt(params, energy_rt)
    residual_chunks.append(intensity_rt - predicted_rt)

    energy_77k = k77_record["energy"][k77_record["fit_mask"]]
    intensity_77k = k77_record["intensity"][k77_record["fit_mask"]]
    _, _, predicted_77k = eval_77k_dual_voigt(params, energy_77k)
    residual_chunks.append(intensity_77k - predicted_77k)

    return np.concatenate(residual_chunks)


def fit_global_voigt(rt_record, k77_record):
    """对 RT 与 77K 执行共享 sigma/E_vib 的双峰全局 Voigt 最小二乘拟合。"""
    params = build_global_voigt_params(rt_record, k77_record)
    minimizer = Minimizer(
        global_voigt_residual,
        params,
        fcn_args=(rt_record, k77_record),
    )
    result = minimizer.minimize(method="leastsq")
    return result


def evaluate_rt_fit_curve(rt_record, fit_result):
    """RT 双 Voigt 拟合：返回总曲线及 0-0 / 0-1 分量（拟合窗口内）。"""
    energy = rt_record["energy"][rt_record["fit_mask"]]
    voigt_00, voigt_01, total = eval_rt_dual_voigt(fit_result.params, energy)
    return energy, voigt_00, voigt_01, total


def evaluate_77k_vibronic_fit(k77_record, fit_result):
    """77K 双 Voigt 拟合：返回总曲线及 0-0 / 0-1 分量（拟合窗口内）。"""
    energy = k77_record["energy"][k77_record["fit_mask"]]
    voigt_00, voigt_01, total = eval_77k_dual_voigt(fit_result.params, energy)
    return energy, voigt_00, voigt_01, total


def print_global_fit_report(fit_result, rt_record, k77_record, r2_values):
    """打印全局 sigma、gamma 对比及 RT/77K 振动峰参数。"""
    sigma_ev = float(fit_result.params["sigma_global"].value)
    sigma_mev = sigma_ev * 1000.0
    gamma_rt_ev = float(fit_result.params["gamma_RT"].value)
    gamma_77k_ev = float(fit_result.params["gamma_77K"].value)
    gamma_rt_mev = gamma_rt_ev * 1000.0
    gamma_77k_mev = gamma_77k_ev * 1000.0
    delta_gamma_mev = gamma_rt_mev - gamma_77k_mev
    ratio = gamma_rt_mev / gamma_77k_mev if gamma_77k_mev > 0 else np.nan
    metrics_rt = compute_linewidth_metrics(sigma_ev, gamma_rt_ev)
    metrics_77k = compute_linewidth_metrics(sigma_ev, gamma_77k_ev)
    e_vib_ev = float(fit_result.params["E_vib"].value)
    rt_center_00_ev = float(fit_result.params["center_RT_00"].value)
    rt_center_01_ev = float(fit_result.params["center_RT_01"].value)
    k77_center_00_ev = float(fit_result.params["center_00"].value)
    k77_center_01_ev = float(fit_result.params["center_01"].value)

    print("\n========== 全局 Voigt 联立拟合报告 ==========")
    print(f"RT 文件：{rt_record['filename']}")
    print(f"77K 文件：{k77_record['filename']}")
    print(f"共享 sigma_global = {sigma_mev:.3f} meV ({sigma_ev:.6f} eV) [RT/77K 的 0-0 与 0-1 共用]")
    print(f"gamma_RT  = {gamma_rt_mev:.3f} meV ({gamma_rt_ev:.6f} eV) [RT 0-0 与 0-1 共用]")
    print(f"gamma_77K = {gamma_77k_mev:.3f} meV ({gamma_77k_ev:.6f} eV) [0-0 与 0-1 共用]")
    print(f"gamma_RT - gamma_77K = {delta_gamma_mev:.3f} meV")
    print(f"gamma_RT / gamma_77K = {ratio:.3f}")
    print(f"约束 gamma_77K < gamma_RT：{'满足' if gamma_77k_ev < gamma_rt_ev else '未满足'}")
    print("--- FWHM-based linewidth comparison ---")
    print(f"Gaussian FWHM W_G = {metrics_rt['gaussian_fwhm_mev']:.3f} meV")
    print(f"RT Lorentzian FWHM W_L = {metrics_rt['lorentzian_fwhm_mev']:.3f} meV")
    print(f"77K Lorentzian FWHM W_L = {metrics_77k['lorentzian_fwhm_mev']:.3f} meV")
    print(f"RT approximate Voigt FWHM W_V = {metrics_rt['voigt_fwhm_mev']:.3f} meV")
    print(f"77K approximate Voigt FWHM W_V = {metrics_77k['voigt_fwhm_mev']:.3f} meV")
    print(f"W_G / W_L_RT = {metrics_rt['gaussian_to_lorentzian_fwhm_ratio']:.3f}")
    if np.isfinite(metrics_77k["gaussian_to_lorentzian_fwhm_ratio"]):
        print(f"W_G / W_L_77K = {metrics_77k['gaussian_to_lorentzian_fwhm_ratio']:.3f}")
    print("--- 共享振动能量差 ---")
    print(f"E_vib = {e_vib_ev * 1000:.1f} meV ({e_vib_ev:.4f} eV)，约束 [{E_VIB_MIN_EV}, {E_VIB_MAX_EV}] eV")
    print("RT 的 center_RT_01 被固定为 center_RT_00 - E_vib")
    print("--- RT (300 K) 振动旁带模型 ---")
    print(f"center_RT_00 = {rt_center_00_ev:.4f} eV，center_RT_01 = {rt_center_01_ev:.4f} eV")
    print(f"amplitude_RT_00 = {fit_result.params['amplitude_RT_00'].value:.4g}")
    print(f"amplitude_RT_01 = {fit_result.params['amplitude_RT_01'].value:.4g}")
    print("--- 77K 振动旁带模型 ---")
    print(f"center_00 = {k77_center_00_ev:.4f} eV，center_01 = {k77_center_01_ev:.4f} eV")
    print(f"amplitude_00 = {fit_result.params['amplitude_00'].value:.4g}")
    print(f"amplitude_01 = {fit_result.params['amplitude_01'].value:.4g}")
    print(f"R² (RT 总拟合)    = {r2_values['r2_rt']:.4f}")
    print(f"R² (77K 总拟合)   = {r2_values['r2_77k']:.4f}")
    print(f"约化卡方 chi-square_red = {fit_result.redchi:.4g}")
    print("============================================\n")


def print_77k_dual_fit_report(record, fit_result, gamma_ev, r2_total):
    """打印 77K 双 Voigt 振动峰拟合报告（非敏感性扫描格式）。"""
    sigma_ev = float(fit_result.params["sigma"].value)
    gamma_ev_fit = float(fit_result.params["gamma"].value)
    metrics = compute_linewidth_metrics(sigma_ev, gamma_ev_fit)
    e_vib_ev = float(fit_result.params["E_vib"].value)
    center_00_ev = float(fit_result.params["center_00"].value)
    center_01_ev = float(fit_result.params["center_01"].value)

    print("\n========== 77K 双 Voigt 振动峰拟合报告 ==========")
    print(f"文件：{record['filename']}")
    print(f"模型：0-0 + 0-1 双 Voigt（共享 sigma、gamma）")
    print(f"固定 gamma = {gamma_ev_fit * 1000:.1f} meV")
    print(f"sigma = {sigma_ev * 1000:.3f} meV ({sigma_ev:.6f} eV)")
    print("--- FWHM-based linewidth comparison ---")
    print(f"Gaussian FWHM W_G = {metrics['gaussian_fwhm_mev']:.3f} meV")
    print(f"Lorentzian FWHM W_L = {metrics['lorentzian_fwhm_mev']:.3f} meV")
    print(f"approximate Voigt FWHM W_V = {metrics['voigt_fwhm_mev']:.3f} meV")
    print(f"W_G / W_L = {metrics['gaussian_to_lorentzian_fwhm_ratio']:.3f}")
    print(f"E_vib = {e_vib_ev * 1000:.1f} meV ({e_vib_ev:.4f} eV)，约束 [{E_VIB_MIN_EV}, {E_VIB_MAX_EV}] eV")
    print(f"center_00 = {center_00_ev:.4f} eV")
    print(f"center_01 = {center_01_ev:.4f} eV  (center_00 - E_vib)")
    print(f"amplitude_00 = {fit_result.params['amplitude_00'].value:.4g}")
    print(f"amplitude_01 = {fit_result.params['amplitude_01'].value:.4g}")
    print(f"R² (总拟合, 拟合窗口) = {r2_total:.4f}")
    print(f"约化卡方 chi-square_red = {fit_result.redchi:.4g}")
    print("================================================\n")


def run_77k_dual_analysis(data_dir, csv_path=None, gamma_ev=None):
    """77K 双 Voigt 振动峰拟合：专用报告与出图，不参与 gamma 扫描。"""
    if gamma_ev is None:
        gamma_ev = GAMMA_REF_EV

    if csv_path:
        if not os.path.isabs(csv_path):
            csv_path = os.path.join(data_dir, csv_path)
    else:
        csv_path = choose_csv_file(data_dir, "选择 77K PL 光谱 CSV")

    if not os.path.isfile(csv_path):
        raise FileNotFoundError(f"光谱文件不存在：{csv_path}")

    record = prepare_spectrum_record(csv_path)
    fit_result = fit_77k_dual_voigt(record, gamma_ev)

    energy_fit = record["energy"][record["fit_mask"]]
    intensity_fit = record["intensity"][record["fit_mask"]]
    _, _, total = eval_77k_dual_voigt(
        fit_result.params, energy_fit, sigma_name="sigma", gamma_name="gamma"
    )
    r2_total = compute_r_squared(intensity_fit, total)

    output_dir = ensure_output_dir(data_dir)
    stem = os.path.splitext(record["filename"])[0]
    plot_path = os.path.join(output_dir, f"{stem}_77k_dual_voigt_fit.png")
    summary_path = os.path.join(output_dir, f"{stem}_77k_dual_voigt_summary.csv")

    plot_representative_fit_77k_dual(
        record,
        fit_result,
        plot_path,
        gamma_ev,
        eval_77k_dual_voigt_func=eval_77k_dual_voigt,
        compute_r_squared_func=compute_r_squared,
        compute_linewidth_metrics_func=compute_linewidth_metrics,
    )
    save_77k_dual_fit_summary(
        record,
        fit_result,
        gamma_ev,
        r2_total,
        summary_path,
        e_vib_min_ev=E_VIB_MIN_EV,
        e_vib_max_ev=E_VIB_MAX_EV,
        compute_linewidth_metrics_func=compute_linewidth_metrics,
    )
    print_77k_dual_fit_report(record, fit_result, gamma_ev, r2_total)

    print(f"已保存 77K 双 Voigt 参数表：{summary_path}")
    print(f"已保存拟合图：{plot_path}")
    print(f"输出目录：{output_dir}")
    return fit_result, output_dir


def run_global_fit(data_dir, rt_path=None, k77_path=None):
    """主流程：RT/77K 全局 Voigt 联立拟合、出图与报告（手动指定光谱）。"""
    if rt_path and not os.path.isabs(rt_path):
        rt_path = os.path.join(data_dir, rt_path)
    if k77_path and not os.path.isabs(k77_path):
        k77_path = os.path.join(data_dir, k77_path)

    if not rt_path:
        rt_path = choose_csv_file(data_dir, "选择 RT (300 K) PL 光谱")
    if not k77_path:
        k77_path = choose_csv_file(data_dir, "选择 77K PL 光谱")

    for label, path in (("RT", rt_path), ("77K", k77_path)):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{label} 光谱文件不存在：{path}")

    rt_record = prepare_spectrum_record(rt_path)
    k77_record = prepare_spectrum_record(k77_path)

    fit_result = fit_global_voigt(rt_record, k77_record)

    output_dir = ensure_output_dir(data_dir)
    summary_path = os.path.join(output_dir, "global_voigt_fit_summary.csv")
    plot_path = os.path.join(output_dir, "global_RT_77K_voigt_fit.png")

    r2_values = plot_global_fit_comparison(
        rt_record,
        k77_record,
        fit_result,
        plot_path,
        evaluate_rt_fit_curve_func=evaluate_rt_fit_curve,
        evaluate_77k_vibronic_fit_func=evaluate_77k_vibronic_fit,
        compute_r_squared_func=compute_r_squared,
        compute_linewidth_metrics_func=compute_linewidth_metrics,
    )
    save_global_fit_summary(
        fit_result,
        rt_record,
        k77_record,
        r2_values,
        summary_path,
        compute_linewidth_metrics_func=compute_linewidth_metrics,
    )
    print_global_fit_report(fit_result, rt_record, k77_record, r2_values)

    print(f"已保存全局拟合参数：{summary_path}")
    print(f"已保存对比图：{plot_path}")
    print(f"输出目录：{output_dir}")
    return fit_result, output_dir


def discover_csv_files(data_dir):
    """枚举数据文件夹内全部 CSV 光谱文件，跳过文件名含 Gated 的文件。"""
    csv_files = []
    for entry in sorted(os.listdir(data_dir)):
        if "gated" in entry.lower():
            continue
        if entry.lower().endswith(".csv"):
            csv_files.append(os.path.join(data_dir, entry))
    return csv_files


def run_sensitivity_analysis(data_dir, max_workers=None):
    """主流程：预处理、并行拟合、导出 XLSX 与 PNG 图。"""
    csv_files = discover_csv_files(data_dir)
    if not csv_files:
        raise FileNotFoundError(f"在 {data_dir} 中未找到任何 .csv 文件")

    spectrum_records = []
    for filepath in csv_files:
        spectrum_records.append(prepare_spectrum_record(filepath))

    gamma_values = build_gamma_grid()
    tasks = build_fit_tasks(spectrum_records, gamma_values)
    fit_results = run_parallel_fits(tasks, max_workers=max_workers)
    summary_df = results_to_dataframe(fit_results)

    output_dir = ensure_output_dir(data_dir)
    summary_path = os.path.join(output_dir, "voigt_gamma_sensitivity_summary.xlsx")
    save_sensitivity_summary_xlsx(summary_df, summary_path)

    record_by_name = {record["filename"]: record for record in spectrum_records}
    successful_results = [row for row in fit_results if row["fit_success"]]

    for filename, record in record_by_name.items():
        ref_rows = [
            row
            for row in successful_results
            if row["filename"] == filename and np.isclose(row["gamma_ev"], GAMMA_REF_EV, atol=1e-12)
        ]
        if not ref_rows:
            print(f"警告：{filename} 在 gamma = 20 meV 处无成功拟合，跳过代表性拟合图")
            continue

        fit_row = ref_rows[0]
        stem = os.path.splitext(filename)[0]
        plot_path = os.path.join(output_dir, f"{stem}_gamma20meV_fit.png")
        plot_representative_fit(
            record,
            fit_row,
            plot_path,
            build_sensitivity_fit_mask_func=build_sensitivity_fit_mask,
        )

    sensitivity_path = os.path.join(output_dir, "sigma_vs_gamma_sensitivity.png")
    sensitivity_svg_path = os.path.join(output_dir, "sigma_vs_gamma_sensitivity.svg")
    plot_sigma_sensitivity(summary_df, sensitivity_path, svg_path=sensitivity_svg_path)

    print(f"已保存综合报告：{summary_path}")
    print(f"已保存敏感性分析图：{sensitivity_path}")
    print(f"已保存敏感性分析图（SVG）：{sensitivity_svg_path}")
    print(f"输出目录：{output_dir}")
    return summary_df, output_dir


def choose_input_folder():
    """通过对话框选择数据文件夹。"""
    if filedialog is None:
        raise RuntimeError("当前环境无法使用 Tkinter 文件夹选择对话框")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title="选择包含 PL CSV 光谱的文件夹")
    root.destroy()
    return folder


def choose_csv_file(data_dir, title):
    """从数据文件夹中手动选择一条 CSV 光谱。"""
    if tk is None:
        raise RuntimeError("当前环境无法使用 Tkinter 文件选择对话框")

    csv_files = discover_csv_files(data_dir)
    if not csv_files:
        raise FileNotFoundError(f"在 {data_dir} 中未找到任何 .csv 文件")

    if len(csv_files) == 1:
        return csv_files[0]

    selected = {"path": None}
    root = tk.Tk()
    root.title(title)
    root.attributes("-topmost", True)

    tk.Label(root, text=title, font=("", 11)).pack(padx=12, pady=(10, 6))
    listbox = tk.Listbox(root, width=90, height=min(14, len(csv_files)), exportselection=False)
    for filepath in csv_files:
        listbox.insert(tk.END, os.path.basename(filepath))
    listbox.pack(padx=12, pady=6)
    listbox.selection_set(0)
    listbox.focus_set()

    def on_ok():
        selection = listbox.curselection()
        if selection:
            selected["path"] = csv_files[selection[0]]
        root.destroy()

    def on_cancel():
        root.destroy()

    button_frame = tk.Frame(root)
    button_frame.pack(pady=(4, 12))
    tk.Button(button_frame, text="确定", width=10, command=on_ok).pack(side=tk.LEFT, padx=8)
    tk.Button(button_frame, text="取消", width=10, command=on_cancel).pack(side=tk.LEFT, padx=8)

    root.mainloop()

    if not selected["path"]:
        raise ValueError(f"未选择 CSV 文件：{title}")
    return selected["path"]


def choose_analysis_mode():
    """通过对话框手动选择分析模式。"""
    if tk is None:
        raise RuntimeError("当前环境无法使用 Tkinter 模式选择对话框")

    mode_options = [
        (ANALYSIS_MODE_SENSITIVITY, "1. Gamma 敏感性扫描（双峰 Voigt，全文件夹）"),
        (ANALYSIS_MODE_GLOBAL, "3. RT + 77K 全局联立拟合（手动各选一条光谱）"),
    ]
    selected = {"mode": None}

    root = tk.Tk()
    root.title("选择 Voigt 分析模式")
    root.attributes("-topmost", True)

    tk.Label(root, text="请选择分析模式：", font=("", 11)).pack(padx=16, pady=(12, 8))
    mode_var = tk.StringVar(value=ANALYSIS_MODE_SENSITIVITY)
    for mode_id, label in mode_options:
        tk.Radiobutton(
            root,
            text=label,
            variable=mode_var,
            value=mode_id,
            anchor="w",
            justify=tk.LEFT,
        ).pack(anchor="w", padx=20, pady=2)

    def on_ok():
        selected["mode"] = mode_var.get()
        root.destroy()

    def on_cancel():
        root.destroy()

    button_frame = tk.Frame(root)
    button_frame.pack(pady=14)
    tk.Button(button_frame, text="确定", width=10, command=on_ok).pack(side=tk.LEFT, padx=8)
    tk.Button(button_frame, text="取消", width=10, command=on_cancel).pack(side=tk.LEFT, padx=8)

    root.mainloop()

    if not selected["mode"]:
        raise ValueError("未选择分析模式")
    return selected["mode"]


def resolve_analysis_mode(args):
    """解析分析模式：命令行优先，否则弹出对话框。"""
    if args.mode:
        return args.mode
    if args.global_fit:
        return ANALYSIS_MODE_GLOBAL
    return choose_analysis_mode()


def parse_args(argv=None):
    """命令行参数解析。"""
    parser = argparse.ArgumentParser(
        description=(
            "对 PL 光谱执行 Voigt 分析。"
            "未指定 --mode 时将弹出对话框手动选择模式。"
        )
    )
    parser.add_argument("data_dir", nargs="?", help="包含 .csv 光谱文件的数据文件夹")
    parser.add_argument(
        "--mode",
        choices=[ANALYSIS_MODE_SENSITIVITY, ANALYSIS_MODE_GLOBAL],
        help="分析模式：sensitivity=gamma 扫描 | global=RT+77K 联立",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="并行进程数（默认使用 CPU 核心数，仅敏感性分析）",
    )
    parser.add_argument("--rt-csv", dest="rt_csv", help="全局联立模式：RT 光谱 CSV")
    parser.add_argument("--77k-csv", dest="k77_csv", help="全局联立模式：77K 光谱 CSV")
    parser.add_argument(
        "--global-fit",
        action="store_true",
        help="（已弃用，等同 --mode global）RT+77K 全局联立拟合",
    )
    return parser.parse_args(argv)


def main(argv=None):
    """脚本入口。"""
    args = parse_args(argv)
    data_dir = args.data_dir

    if not data_dir:
        data_dir = choose_input_folder()

    if not data_dir:
        print("未选择数据文件夹")
        return 1

    if not os.path.isdir(data_dir):
        print(f"数据文件夹不存在：{data_dir}", file=sys.stderr)
        return 1

    try:
        mode = resolve_analysis_mode(args)
        print(f"当前分析模式：{mode}")

        if mode == ANALYSIS_MODE_SENSITIVITY:
            run_sensitivity_analysis(data_dir, max_workers=args.workers)
        elif mode == ANALYSIS_MODE_GLOBAL:
            run_global_fit(data_dir, rt_path=args.rt_csv, k77_path=args.k77_csv)
        else:
            raise ValueError(f"未知分析模式：{mode}")
    except Exception as exc:
        if messagebox is not None:
            messagebox.showerror("Voigt 分析错误", str(exc))
        else:
            print(f"错误：{exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
