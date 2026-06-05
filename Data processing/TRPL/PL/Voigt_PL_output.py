import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


OUTPUT_DIRNAME = "Voigt Output"
PLOT_FIGSIZE = (20 / 2.54, 15 / 2.54)
PLOT_FONT_SIZE = 16
PLOT_AXIS_LINEWIDTH = 1


def apply_plot_style(ax):
    """统一出图样式：20 cm × 15 cm、无网格无标题、16 pt 字体、轴线宽 1、刻度向内（仅左/下）。"""
    ax.set_title("")
    ax.grid(False)
    ax.tick_params(
        axis="both",
        which="both",
        direction="in",
        top=False,
        right=False,
        labelsize=PLOT_FONT_SIZE,
        width=PLOT_AXIS_LINEWIDTH,
    )
    for spine in ax.spines.values():
        spine.set_linewidth(PLOT_AXIS_LINEWIDTH)
    ax.xaxis.label.set_size(PLOT_FONT_SIZE)
    ax.yaxis.label.set_size(PLOT_FONT_SIZE)


def ensure_output_dir(data_dir):
    """在数据目录下创建 Output 输出文件夹。"""
    output_dir = os.path.join(data_dir, OUTPUT_DIRNAME)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def plot_representative_fit_77k_dual(
    record,
    fit_result,
    output_path,
    gamma_ev,
    *,
    eval_77k_dual_voigt_func,
    compute_r_squared_func,
    compute_linewidth_metrics_func,
):
    """77K 双 Voigt 代表性拟合图：总拟合 + 0-0 / 0-1 分量。"""
    energy = record["energy"]
    intensity = record["intensity"]
    fit_mask = record["fit_mask"]
    fit_lower = record["fit_lower"]
    fit_upper = record["fit_upper"]
    energy_fit = energy[fit_mask]
    intensity_window = intensity[fit_mask]

    voigt_00, voigt_01, total = eval_77k_dual_voigt_func(
        fit_result.params, energy_fit, sigma_name="sigma", gamma_name="gamma"
    )
    r2 = compute_r_squared_func(intensity_window, total)
    gamma_mev = gamma_ev * 1000.0
    sigma_ev = float(fit_result.params["sigma"].value)
    gamma_ev_fit = float(fit_result.params["gamma"].value)
    linewidth_metrics = compute_linewidth_metrics_func(sigma_ev, gamma_ev_fit)

    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    ax.axvspan(fit_lower, fit_upper, color="gold", alpha=0.18, label="Fit window")
    ax.plot(energy, intensity, "o", markersize=3, color="0.35", label="Full spectrum")
    ax.plot(energy_fit, voigt_00, "--", color="royalblue", linewidth=1.4, label="0-0 transition")
    ax.plot(energy_fit, voigt_01, "--", color="darkorange", linewidth=1.4, label="0-1 transition")
    ax.plot(
        energy_fit,
        total,
        "-",
        color="crimson",
        linewidth=1.8,
        label=(
            f"Total fit ($\\gamma$={gamma_mev:.0f} meV, $R^2$={r2:.3f}; "
            f"$W_G$={linewidth_metrics['gaussian_fwhm_mev']:.0f} meV, "
            f"$W_L$={linewidth_metrics['lorentzian_fwhm_mev']:.0f} meV, "
            f"$W_V$={linewidth_metrics['voigt_fwhm_mev']:.0f} meV)"
        ),
    )
    ax.set_xlabel("Energy (eV)")
    ax.set_ylabel("Intensity (arb. units)")
    apply_plot_style(ax)
    ax.legend(frameon=False, fontsize=PLOT_FONT_SIZE)
    fig.tight_layout()
    fig.savefig(output_path, dpi=400, bbox_inches="tight")
    plt.close(fig)


def plot_representative_fit(record, fit_row, output_path, *, build_sensitivity_fit_mask_func):
    """绘制 gamma = 20 meV 的双 Voigt 代表性拟合图。"""
    energy = record["energy"]
    intensity = record["intensity"]
    fit_mask = build_sensitivity_fit_mask_func(record)
    fit_lower = float(np.min(energy[fit_mask]))
    fit_upper = float(np.max(energy[fit_mask]))

    energy_fit = energy[fit_mask]
    fit_curve = np.asarray(fit_row["best_fit"], dtype=float)
    voigt_00 = np.asarray(fit_row["voigt_00"], dtype=float)
    voigt_01 = np.asarray(fit_row["voigt_01"], dtype=float)
    fit_label = (
        "Total dual Voigt fit "
        f"($W_G$={fit_row['gaussian_fwhm_mev']:.0f} meV, "
        f"$W_L$={fit_row['lorentzian_fwhm_mev']:.0f} meV, "
        f"$W_V$={fit_row['voigt_fwhm_mev']:.0f} meV)"
    )

    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    ax.axvspan(fit_lower, fit_upper, color="gold", alpha=0.18, label="Fit window")
    ax.plot(energy, intensity, "o", markersize=3, color="0.35", label="Full spectrum")
    ax.plot(energy_fit, voigt_00, "--", color="royalblue", linewidth=1.4, label="0-0 transition")
    ax.plot(energy_fit, voigt_01, "--", color="darkorange", linewidth=1.4, label="0-1 transition")
    ax.plot(energy_fit, fit_curve, "-", color="crimson", linewidth=1.8, label=fit_label)

    ax.set_xlabel("Energy (eV)")
    ax.set_ylabel("Intensity (arb. units)")
    apply_plot_style(ax)
    ax.legend(frameon=False, fontsize=PLOT_FONT_SIZE)
    fig.tight_layout()
    fig.savefig(output_path, dpi=400, bbox_inches="tight")
    plt.close(fig)


def plot_sigma_sensitivity(summary_df, output_path, svg_path=None):
    """绘制 sigma 对固定 gamma 的敏感性折线图。"""
    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    markers = ["o", "s", "^", "D", "v", "<", ">", "p", "*", "h", "X", "P"]

    successful = summary_df[summary_df["fit_success"]].copy()
    if successful.empty:
        raise ValueError("没有成功的拟合结果，无法绘制敏感性分析图")

    filenames = sorted(successful["filename"].unique())
    for index, filename in enumerate(filenames):
        subset = successful[successful["filename"] == filename].sort_values("gamma_mev")
        label = os.path.splitext(filename)[0]
        ax.plot(
            subset["gamma_mev"],
            subset["sigma_mev"],
            marker=markers[index % len(markers)],
            linewidth=1.6,
            markersize=6,
            label=label,
        )

    ax.set_xlabel("Fixed gamma (meV)")
    ax.set_ylabel("Fitted main-peak sigma (meV)")
    apply_plot_style(ax)
    ax.legend(
        frameon=False,
        fontsize=PLOT_FONT_SIZE,
        bbox_to_anchor=(1.05, 1),
        loc="upper left",
    )
    fig.tight_layout()
    fig.savefig(output_path, dpi=400, bbox_inches="tight")
    if svg_path:
        fig.savefig(svg_path, bbox_inches="tight")
    plt.close(fig)


def plot_global_fit_comparison(
    rt_record,
    k77_record,
    fit_result,
    output_path,
    *,
    evaluate_rt_fit_curve_func,
    evaluate_77k_vibronic_fit_func,
    compute_r_squared_func,
    compute_linewidth_metrics_func,
):
    """在同一窗口中对比 RT 与 77K 的原始数据与全局拟合曲线。"""
    fig, axes = plt.subplots(2, 1, figsize=PLOT_FIGSIZE, sharex=True)

    ax_rt = axes[0]
    energy_rt = rt_record["energy"]
    intensity_rt = rt_record["intensity"]
    energy_fit_rt, rt_voigt_00, rt_voigt_01, total_rt = evaluate_rt_fit_curve_func(rt_record, fit_result)
    intensity_window_rt = rt_record["intensity"][rt_record["fit_mask"]]
    r2_rt = compute_r_squared_func(intensity_window_rt, total_rt)
    sigma_ev = float(fit_result.params["sigma_global"].value)
    gamma_rt_ev = float(fit_result.params["gamma_RT"].value)
    gamma_77k_ev = float(fit_result.params["gamma_77K"].value)
    metrics_rt = compute_linewidth_metrics_func(sigma_ev, gamma_rt_ev)

    ax_rt.axvspan(rt_record["fit_lower"], rt_record["fit_upper"], color="gold", alpha=0.18, label="Fit window")
    ax_rt.plot(energy_rt, intensity_rt, "o", markersize=3, color="0.35", label="Data")
    ax_rt.plot(
        energy_fit_rt,
        rt_voigt_00,
        "--",
        color="royalblue",
        linewidth=1.4,
        label="0-0 transition",
    )
    ax_rt.plot(
        energy_fit_rt,
        rt_voigt_01,
        "--",
        color="darkorange",
        linewidth=1.4,
        label="0-1 transition",
    )
    ax_rt.plot(
        energy_fit_rt,
        total_rt,
        "-",
        color="crimson",
        linewidth=1.8,
        label=(
            f"Total fit ($R^2$={r2_rt:.3f}; "
            f"$W_G$={metrics_rt['gaussian_fwhm_mev']:.0f} meV, "
            f"$W_L$={metrics_rt['lorentzian_fwhm_mev']:.0f} meV, "
            f"$W_V$={metrics_rt['voigt_fwhm_mev']:.0f} meV)"
        ),
    )
    ax_rt.set_ylabel("Intensity (arb. units)")
    apply_plot_style(ax_rt)
    ax_rt.text(
        0.02,
        0.95,
        "RT (300 K, dual Voigt)",
        transform=ax_rt.transAxes,
        fontsize=PLOT_FONT_SIZE,
        va="top",
        ha="left",
    )
    ax_rt.legend(frameon=False, fontsize=PLOT_FONT_SIZE - 2, loc="upper right")

    ax_77k = axes[1]
    energy_77k = k77_record["energy"]
    intensity_77k = k77_record["intensity"]
    energy_fit_77k, voigt_00, voigt_01, total_77k = evaluate_77k_vibronic_fit_func(k77_record, fit_result)
    intensity_window_77k = k77_record["intensity"][k77_record["fit_mask"]]
    r2_77k = compute_r_squared_func(intensity_window_77k, total_77k)
    metrics_77k = compute_linewidth_metrics_func(sigma_ev, gamma_77k_ev)

    ax_77k.axvspan(k77_record["fit_lower"], k77_record["fit_upper"], color="gold", alpha=0.18, label="Fit window")
    ax_77k.plot(energy_77k, intensity_77k, "o", markersize=3, color="0.35", label="Data")
    ax_77k.plot(
        energy_fit_77k,
        voigt_00,
        "--",
        color="royalblue",
        linewidth=1.4,
        label="0-0 transition",
    )
    ax_77k.plot(
        energy_fit_77k,
        voigt_01,
        "--",
        color="darkorange",
        linewidth=1.4,
        label="0-1 transition",
    )
    ax_77k.plot(
        energy_fit_77k,
        total_77k,
        "-",
        color="crimson",
        linewidth=1.8,
        label=(
            f"Total fit ($R^2$={r2_77k:.3f}; "
            f"$W_G$={metrics_77k['gaussian_fwhm_mev']:.0f} meV, "
            f"$W_L$={metrics_77k['lorentzian_fwhm_mev']:.0f} meV, "
            f"$W_V$={metrics_77k['voigt_fwhm_mev']:.0f} meV)"
        ),
    )
    ax_77k.set_ylabel("Intensity (arb. units)")
    apply_plot_style(ax_77k)
    ax_77k.text(
        0.02,
        0.95,
        "77 K (dual Voigt)",
        transform=ax_77k.transAxes,
        fontsize=PLOT_FONT_SIZE,
        va="top",
        ha="left",
    )
    ax_77k.legend(frameon=False, fontsize=PLOT_FONT_SIZE - 2, loc="upper right")
    axes[1].set_xlabel("Energy (eV)")
    fig.tight_layout()
    fig.savefig(output_path, dpi=400, bbox_inches="tight")
    plt.close(fig)

    return {"r2_rt": r2_rt, "r2_77k": r2_77k}


def save_global_fit_summary(
    fit_result,
    rt_record,
    k77_record,
    r2_values,
    output_path,
    *,
    compute_linewidth_metrics_func,
):
    """将全局拟合参数导出为 CSV。"""
    sigma_ev = float(fit_result.params["sigma_global"].value)
    gamma_rt_ev = float(fit_result.params["gamma_RT"].value)
    gamma_77k_ev = float(fit_result.params["gamma_77K"].value)
    metrics_rt = compute_linewidth_metrics_func(sigma_ev, gamma_rt_ev)
    metrics_77k = compute_linewidth_metrics_func(sigma_ev, gamma_77k_ev)
    rows = [
        {"parameter": "model", "value": "global_dual_voigt_rt_77k"},
        {
            "parameter": "physical_interpretation_sigma",
            "value": "sigma_global is an effective Gaussian-like broadening parameter. It is used here as a phenomenological descriptor of static/disorder-like spectral broadening.",
        },
        {
            "parameter": "physical_interpretation_gamma",
            "value": "gamma_RT and gamma_77K are effective Lorentzian-like broadening parameters. Their absolute physical interpretation as homogeneous phonon broadening requires additional evidence, such as temperature-dependent linewidth analysis and instrumental-resolution correction.",
        },
        {
            "parameter": "dominant_broadening_rule",
            "value": "Dominant Gaussian-like or Lorentzian-like contribution should be assessed using FWHM values W_G and W_L, not raw sigma and gamma.",
        },
        {
            "parameter": "sigma_global",
            "value_ev": sigma_ev,
            "value_mev": sigma_ev * 1000.0,
            "stderr_ev": float(fit_result.params["sigma_global"].stderr or np.nan),
            "note": "shared by RT and 77K 0-0/0-1 peaks; inhomogeneous broadening from conformational disorder, temperature-independent",
        },
        {
            "parameter": "gamma_RT",
            "value_ev": gamma_rt_ev,
            "value_mev": gamma_rt_ev * 1000.0,
            "stderr_ev": float(fit_result.params["gamma_RT"].stderr or np.nan),
            "filename": rt_record["filename"],
            "note": "shared by RT 0-0 and 0-1 peaks; homogeneous broadening from phonon scattering",
        },
        {
            "parameter": "gamma_77K",
            "value_ev": gamma_77k_ev,
            "value_mev": gamma_77k_ev * 1000.0,
            "stderr_ev": float(fit_result.params["gamma_77K"].stderr or np.nan),
            "filename": k77_record["filename"],
            "note": "shared by 77K 0-0 and 0-1 peaks; homogeneous broadening reduced at lower temperature",
        },
        {
            "parameter": "gaussian_fwhm_global",
            "value_ev": metrics_rt["gaussian_fwhm_ev"],
            "value_mev": metrics_rt["gaussian_fwhm_mev"],
            "note": "W_G = 2 * sqrt(2 * ln(2)) * sigma_global",
        },
        {
            "parameter": "lorentzian_fwhm_RT",
            "value_ev": metrics_rt["lorentzian_fwhm_ev"],
            "value_mev": metrics_rt["lorentzian_fwhm_mev"],
            "filename": rt_record["filename"],
            "note": "W_L = 2 * gamma_RT",
        },
        {
            "parameter": "lorentzian_fwhm_77K",
            "value_ev": metrics_77k["lorentzian_fwhm_ev"],
            "value_mev": metrics_77k["lorentzian_fwhm_mev"],
            "filename": k77_record["filename"],
            "note": "W_L = 2 * gamma_77K",
        },
        {
            "parameter": "voigt_fwhm_RT",
            "value_ev": metrics_rt["voigt_fwhm_ev"],
            "value_mev": metrics_rt["voigt_fwhm_mev"],
            "filename": rt_record["filename"],
            "note": "Approximate Voigt FWHM W_V",
        },
        {
            "parameter": "voigt_fwhm_77K",
            "value_ev": metrics_77k["voigt_fwhm_ev"],
            "value_mev": metrics_77k["voigt_fwhm_mev"],
            "filename": k77_record["filename"],
            "note": "Approximate Voigt FWHM W_V",
        },
        {
            "parameter": "gaussian_to_lorentzian_fwhm_ratio_RT",
            "value": metrics_rt["gaussian_to_lorentzian_fwhm_ratio"],
            "filename": rt_record["filename"],
            "note": "W_G / W_L_RT",
        },
        {
            "parameter": "gaussian_to_lorentzian_fwhm_ratio_77K",
            "value": metrics_77k["gaussian_to_lorentzian_fwhm_ratio"],
            "filename": k77_record["filename"],
            "note": "W_G / W_L_77K",
        },
        {
            "parameter": "E_vib",
            "value_ev": float(fit_result.params["E_vib"].value),
            "value_mev": float(fit_result.params["E_vib"].value) * 1000.0,
            "stderr_ev": float(fit_result.params["E_vib"].stderr or np.nan),
            "filename": k77_record["filename"],
            "note": "RT center_RT_01 is fixed to center_RT_00 - E_vib",
        },
        {
            "parameter": "center_RT_00",
            "value_ev": float(fit_result.params["center_RT_00"].value),
            "stderr_ev": float(fit_result.params["center_RT_00"].stderr or np.nan),
            "filename": rt_record["filename"],
        },
        {
            "parameter": "center_RT_01",
            "value_ev": float(fit_result.params["center_RT_01"].value),
            "stderr_ev": float(fit_result.params["center_RT_01"].stderr or np.nan),
            "filename": rt_record["filename"],
            "note": "center_RT_00 - E_vib",
        },
        {
            "parameter": "amplitude_RT_00",
            "value": float(fit_result.params["amplitude_RT_00"].value),
            "stderr": float(fit_result.params["amplitude_RT_00"].stderr or np.nan),
            "filename": rt_record["filename"],
        },
        {
            "parameter": "amplitude_RT_01",
            "value": float(fit_result.params["amplitude_RT_01"].value),
            "stderr": float(fit_result.params["amplitude_RT_01"].stderr or np.nan),
            "filename": rt_record["filename"],
        },
        {
            "parameter": "center_00",
            "value_ev": float(fit_result.params["center_00"].value),
            "stderr_ev": float(fit_result.params["center_00"].stderr or np.nan),
            "filename": k77_record["filename"],
        },
        {
            "parameter": "center_01",
            "value_ev": float(fit_result.params["center_01"].value),
            "stderr_ev": float(fit_result.params["center_01"].stderr or np.nan),
            "filename": k77_record["filename"],
            "note": "center_00 - E_vib",
        },
        {
            "parameter": "amplitude_00",
            "value": float(fit_result.params["amplitude_00"].value),
            "stderr": float(fit_result.params["amplitude_00"].stderr or np.nan),
            "filename": k77_record["filename"],
        },
        {
            "parameter": "amplitude_01",
            "value": float(fit_result.params["amplitude_01"].value),
            "stderr": float(fit_result.params["amplitude_01"].stderr or np.nan),
            "filename": k77_record["filename"],
        },
        {
            "parameter": "R2_RT",
            "value": r2_values["r2_rt"],
            "filename": rt_record["filename"],
        },
        {
            "parameter": "R2_77K_total",
            "value": r2_values["r2_77k"],
            "filename": k77_record["filename"],
        },
    ]
    pd.DataFrame(rows).to_csv(output_path, index=False, encoding="utf-8-sig")


def save_77k_dual_fit_summary(
    record,
    fit_result,
    gamma_ev,
    r2_total,
    output_path,
    *,
    e_vib_min_ev,
    e_vib_max_ev,
    compute_linewidth_metrics_func,
):
    """导出 77K 双 Voigt 专用参数表（parameter/value 格式，非 gamma 扫描表）。"""
    sigma_ev = float(fit_result.params["sigma"].value)
    gamma_ev_fit = float(fit_result.params["gamma"].value)
    linewidth_metrics = compute_linewidth_metrics_func(sigma_ev, gamma_ev_fit)
    rows = [
        {"parameter": "model", "value": "dual_voigt_00_01", "unit": "", "note": ""},
        {"parameter": "filename", "value": record["filename"], "unit": "", "note": ""},
        {
            "parameter": "gamma_fixed",
            "value": gamma_ev_fit,
            "unit": "eV",
            "value_mev": gamma_ev_fit * 1000.0,
            "note": f"fixed at {gamma_ev * 1000:.0f} meV",
        },
        {
            "parameter": "sigma",
            "value": sigma_ev,
            "unit": "eV",
            "value_mev": sigma_ev * 1000.0,
            "stderr": float(fit_result.params["sigma"].stderr or np.nan),
            "note": "shared by 0-0 and 0-1",
        },
        {
            "parameter": "gaussian_fwhm",
            "value": linewidth_metrics["gaussian_fwhm_ev"],
            "unit": "eV",
            "value_mev": linewidth_metrics["gaussian_fwhm_mev"],
            "note": "W_G = 2 * sqrt(2 * ln(2)) * sigma",
        },
        {
            "parameter": "lorentzian_fwhm",
            "value": linewidth_metrics["lorentzian_fwhm_ev"],
            "unit": "eV",
            "value_mev": linewidth_metrics["lorentzian_fwhm_mev"],
            "note": "W_L = 2 * gamma",
        },
        {
            "parameter": "voigt_fwhm",
            "value": linewidth_metrics["voigt_fwhm_ev"],
            "unit": "eV",
            "value_mev": linewidth_metrics["voigt_fwhm_mev"],
            "note": "Approximate Voigt FWHM W_V",
        },
        {
            "parameter": "gaussian_to_lorentzian_fwhm_ratio",
            "value": linewidth_metrics["gaussian_to_lorentzian_fwhm_ratio"],
            "unit": "",
            "note": "W_G / W_L",
        },
        {
            "parameter": "E_vib",
            "value": float(fit_result.params["E_vib"].value),
            "unit": "eV",
            "value_mev": float(fit_result.params["E_vib"].value) * 1000.0,
            "stderr": float(fit_result.params["E_vib"].stderr or np.nan),
            "note": f"constraint [{e_vib_min_ev}, {e_vib_max_ev}] eV",
        },
        {
            "parameter": "center_00",
            "value": float(fit_result.params["center_00"].value),
            "unit": "eV",
            "stderr": float(fit_result.params["center_00"].stderr or np.nan),
            "note": "0-0 transition",
        },
        {
            "parameter": "center_01",
            "value": float(fit_result.params["center_01"].value),
            "unit": "eV",
            "stderr": float(fit_result.params["center_01"].stderr or np.nan),
            "note": "0-1 transition, center_00 - E_vib",
        },
        {
            "parameter": "amplitude_00",
            "value": float(fit_result.params["amplitude_00"].value),
            "unit": "arb.",
            "stderr": float(fit_result.params["amplitude_00"].stderr or np.nan),
            "note": "0-0 transition",
        },
        {
            "parameter": "amplitude_01",
            "value": float(fit_result.params["amplitude_01"].value),
            "unit": "arb.",
            "stderr": float(fit_result.params["amplitude_01"].stderr or np.nan),
            "note": "0-1 transition",
        },
        {
            "parameter": "R2_total",
            "value": r2_total,
            "unit": "",
            "note": "fit window, 0-0 + 0-1 sum",
        },
        {
            "parameter": "redchi",
            "value": float(fit_result.redchi),
            "unit": "",
            "note": "reduced chi-square",
        },
    ]
    pd.DataFrame(rows).to_csv(output_path, index=False, encoding="utf-8-sig")


def save_sensitivity_summary_xlsx(summary_df, output_path):
    """保存模式 1 结果：Sheet1 为画图宽表，Sheet2 为完整明细。"""
    details_df = summary_df.sort_values(["filename", "gamma_mev"]).reset_index(drop=True)
    plot_df = details_df.copy()
    plot_df["sample"] = plot_df["filename"].map(lambda name: os.path.splitext(name)[0])
    plot_df = (
        plot_df.pivot_table(
            index="gamma_mev",
            columns="sample",
            values="sigma_mev",
            aggfunc="first",
        )
        .sort_index()
        .reset_index()
    )
    plot_df.columns.name = None

    with pd.ExcelWriter(output_path) as writer:
        plot_df.to_excel(writer, sheet_name="plot_data", index=False)
        details_df.to_excel(writer, sheet_name="details", index=False)
