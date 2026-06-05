"""
一次性使用：给136PS gated PL 80K光谱文件去峰，得到clean phosphorescence
"""

from pathlib import Path
import argparse
import csv
import json
import sys
import ctypes

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except ImportError:
    tk = None
    filedialog = None
    messagebox = None


HC_EV_NM = 1240.0
SAVE_DPI = 600

BASELINE_WINDOW_NM = (420.0, 450.0)
BASELINE_MODEL = "linear"  # "linear" or "constant"

FIT_WINDOW_EV = (2.620, 2.780)  # PF blue edge only (~446-473 nm), avoids phosphorescence
SHIFT_BOUNDS_EV = (-0.20, 0.20)
GAMMA_BOUNDS = (0.4, 2.0)  # gamma < 1 narrows, gamma > 1 broadens RT template around its peak
EDGE_SEARCH_EV = (2.40, 2.85)  # wide enough to capture PF peak of both 80 K (~2.56 eV) and RT (~2.43 eV)
EDGE_LEVELS = (0.5, np.exp(-1))  # half-max and 1/e crossings on the blue edge
TAPER_TRANSITION_WIDTH_EV = 0.13
TAPER_OFFSET_BELOW_PF_PEAK_EV = 0.13  # taper transition starts this far below 80 K PF peak


def choose_csv_file(title, initial_dir=None):
    """Return a CSV path chosen in a file dialog."""
    if filedialog is None:
        raise RuntimeError("Tkinter is not available; pass file paths on the command line.")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    filepath = filedialog.askopenfilename(
        title=title,
        initialdir=str(initial_dir or Path(__file__).resolve().parent),
        filetypes=[("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*.*")],
    )
    root.destroy()
    return Path(filepath) if filepath else None


def _try_read_rows(path, encoding):
    with Path(path).open("r", encoding=encoding, newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel
        return list(csv.reader(handle, dialect))


def load_spectrum(path, min_numeric_rows=5):
    """Load spectrum and auto-detect first two numeric columns robustly."""
    path = Path(path)
    encodings = ["utf-8-sig", "windows-1252", "latin-1"]
    last_error = None
    rows = None

    for encoding in encodings:
        try:
            rows = _try_read_rows(path, encoding=encoding)
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if rows is None:
        if last_error is not None:
            raise UnicodeDecodeError(
                last_error.encoding,
                last_error.object,
                last_error.start,
                last_error.end,
                f"Could not decode {path} with attempted encodings.",
            )
        raise ValueError(f"Failed to read {path}")

    if not rows:
        raise ValueError(f"{path} is empty.")

    max_cols = max(len(row) for row in rows)
    numeric = np.full((len(rows), max_cols), np.nan, dtype=float)
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            numeric[i, j] = pd.to_numeric(str(cell).strip(), errors="coerce")

    numeric_counts = np.sum(np.isfinite(numeric), axis=0)
    candidate_cols = [idx for idx, count in enumerate(numeric_counts) if count >= min_numeric_rows]
    if len(candidate_cols) < 2:
        raise ValueError(f"Could not find two numeric columns in {path}.")

    wavelength_col, intensity_col = candidate_cols[0], candidate_cols[1]
    spectrum = pd.DataFrame(
        {
            "wavelength_nm": numeric[:, wavelength_col],
            "intensity": numeric[:, intensity_col],
        }
    )
    spectrum = spectrum.dropna()
    spectrum = spectrum[spectrum["wavelength_nm"] > 0]
    spectrum = spectrum.sort_values("wavelength_nm").drop_duplicates("wavelength_nm")
    spectrum = spectrum.reset_index(drop=True)
    if spectrum.empty:
        raise ValueError(f"No valid wavelength/intensity data found in {path}.")
    return spectrum


def evaluate_baseline(wavelength_nm, intensity, window_nm, model="linear"):
    """Build baseline model from configured window."""
    wl = np.asarray(wavelength_nm, dtype=float)
    y = np.asarray(intensity, dtype=float)
    wmin, wmax = window_nm
    mask = (wl >= wmin) & (wl <= wmax)

    if np.count_nonzero(mask) < 2:
        baseline_level = float(np.nanmedian(y))
        return np.full_like(y, baseline_level), {"type": "constant_fallback", "c0": baseline_level}

    wl_win = wl[mask]
    y_win = y[mask]
    if model == "constant":
        c0 = float(np.nanmedian(y_win))
        return np.full_like(y, c0), {"type": "constant", "c0": c0}

    coeff = np.polyfit(wl_win, y_win, deg=1)
    c1 = float(coeff[0])
    c0 = float(coeff[1])
    baseline = c1 * wl + c0
    return baseline, {"type": "linear", "c0": c0, "c1": c1}


def baseline_correct_spectrum(spectrum, dark_spectrum=None, baseline_model=BASELINE_MODEL):
    """Subtract optional dark and estimated baseline."""
    wl = spectrum["wavelength_nm"].to_numpy(dtype=float)
    raw = spectrum["intensity"].to_numpy(dtype=float)
    warnings = []

    if dark_spectrum is not None:
        dark_interp = np.interp(
            wl,
            dark_spectrum["wavelength_nm"].to_numpy(dtype=float),
            dark_spectrum["intensity"].to_numpy(dtype=float),
            left=0.0,
            right=0.0,
        )
    else:
        dark_interp = np.zeros_like(raw)

    after_dark = raw - dark_interp
    baseline, baseline_params = evaluate_baseline(
        wl,
        after_dark,
        BASELINE_WINDOW_NM,
        model=baseline_model,
    )
    corrected = after_dark - baseline

    if baseline_params["type"] == "constant_fallback":
        warnings.append("Baseline window has too few points; used median fallback baseline.")

    return {
        "wavelength_nm": wl,
        "raw": raw,
        "dark_interp": dark_interp,
        "baseline": baseline,
        "corrected": corrected,
        "baseline_params": baseline_params,
        "warnings": warnings,
    }


def wavelength_to_energy(wavelength_nm, intensity_lambda):
    """Convert I(lambda) to I(E) with strict Jacobian, returning arrays sorted by increasing energy."""
    wl = np.asarray(wavelength_nm, dtype=float)
    i_lambda = np.asarray(intensity_lambda, dtype=float)
    energy_ev = HC_EV_NM / wl
    intensity_energy = i_lambda * (wl**2) / HC_EV_NM
    order = np.argsort(energy_ev)
    return energy_ev[order], intensity_energy[order]


def energy_to_wavelength(energy_ev, intensity_energy):
    """Convert I(E) back to I(lambda) with strict inverse Jacobian, returning arrays sorted by increasing wavelength."""
    e = np.asarray(energy_ev, dtype=float)
    i_e = np.asarray(intensity_energy, dtype=float)
    wavelength_nm = HC_EV_NM / e
    intensity_lambda = i_e * (e**2) / HC_EV_NM
    order = np.argsort(wavelength_nm)
    return wavelength_nm[order], intensity_lambda[order]


def shifted_template_ev(energy_grid, template_energy, template_y, shift_ev):
    """
    Shift RT template in energy domain.
    Positive shift moves template to higher energy (shorter wavelength).
    """
    return np.interp(
        energy_grid - shift_ev,
        template_energy,
        template_y,
        left=0.0,
        right=0.0,
    )


def _pf_peak_energy(energy_ev, intensity, search_ev=EDGE_SEARCH_EV):
    """Return PF peak energy within the configured search window."""
    e = np.asarray(energy_ev, dtype=float)
    y = np.asarray(intensity, dtype=float)
    mask = (e >= search_ev[0]) & (e <= search_ev[1])
    if np.count_nonzero(mask) < 3:
        raise ValueError(f"Too few points in PF edge search window {search_ev[0]}-{search_ev[1]} eV.")

    em = e[mask]
    ym = y[mask]
    peak_idx = int(np.argmax(ym))
    i_peak = float(ym[peak_idx])
    if i_peak <= np.finfo(float).eps:
        raise ValueError("PF search window has non-positive peak intensity.")
    return float(em[peak_idx]), i_peak


def _crossing_energy_blue_edge(energy_ev, intensity, e_peak, i_peak, fraction):
    """Find the blue-edge (E >= e_peak) crossing at fraction * i_peak."""
    e = np.asarray(energy_ev, dtype=float)
    y = np.asarray(intensity, dtype=float)
    target = float(fraction) * float(i_peak)

    blue_mask = e >= e_peak
    e_blue = e[blue_mask]
    y_blue = y[blue_mask]
    if e_blue.size < 2:
        return np.nan

    for idx in range(e_blue.size - 1):
        y0, y1 = y_blue[idx], y_blue[idx + 1]
        e0, e1 = e_blue[idx], e_blue[idx + 1]
        if (y0 - target) * (y1 - target) <= 0.0 and y1 != y0:
            return float(e0 + (target - y0) * (e1 - e0) / (y1 - y0))
    return np.nan


def pf_blue_edge_crossings(energy_ev, intensity, search_ev=EDGE_SEARCH_EV, levels=EDGE_LEVELS):
    """Return PF peak energy and blue-edge crossing energies at requested fractions."""
    e_peak, i_peak = _pf_peak_energy(energy_ev, intensity, search_ev=search_ev)
    crossings = {}
    for level in levels:
        crossings[float(level)] = _crossing_energy_blue_edge(
            energy_ev,
            intensity,
            e_peak,
            i_peak,
            level,
        )
    return e_peak, crossings


def warped_shifted_template_ev(
    energy_grid,
    template_energy,
    template_y,
    shift_ev,
    gamma=1.0,
    e0=None,
):
    """
    Shift and geometrically warp RT template around its PF peak.
    Positive shift moves template to higher energy (shorter wavelength).
    gamma < 1 narrows the template around e0 (use for 80 K sharpening);
    gamma > 1 broadens it. gamma == 1 leaves the line width unchanged.
    """
    e_grid = np.asarray(energy_grid, dtype=float)
    rt_e = np.asarray(template_energy, dtype=float)
    rt_y = np.asarray(template_y, dtype=float)
    if e0 is None:
        e0, _ = _pf_peak_energy(rt_e, rt_y)

    e_aligned = e_grid - float(shift_ev)
    source_e = float(e0) + (e_aligned - float(e0)) / float(gamma)
    return np.interp(source_e, rt_e, rt_y, left=0.0, right=0.0)


def _alpha_window_mask_ev(energy_ev, window_ev=FIT_WINDOW_EV):
    emin, emax = window_ev
    return (energy_ev >= emin) & (energy_ev <= emax)


def compute_alpha_analytical_ev(
    energy_ev,
    intensity_80k,
    rt_energy,
    rt_intensity,
    shift_ev,
    gamma=1.0,
    rt_e0=None,
):
    """Compute alpha by least squares in FIT_WINDOW_EV only."""
    alpha_mask = _alpha_window_mask_ev(energy_ev)
    if np.count_nonzero(alpha_mask) < 2:
        raise ValueError("Too few points in alpha fit window.")

    e_win = energy_ev[alpha_mask]
    y80_win = intensity_80k[alpha_mask]
    rt_model_win = warped_shifted_template_ev(
        e_win,
        rt_energy,
        rt_intensity,
        shift_ev,
        gamma=gamma,
        e0=rt_e0,
    )

    denom = float(np.sum(rt_model_win**2))
    if denom <= np.finfo(float).eps:
        raise ValueError("RT template has near-zero power in alpha fit window.")

    alpha = float(np.sum(y80_win * rt_model_win) / denom)
    return alpha


def alpha_window_sse_ev(
    energy_ev,
    intensity_80k,
    rt_energy,
    rt_intensity,
    shift_ev,
    alpha=None,
    gamma=1.0,
    rt_e0=None,
):
    """Return SSE and alpha within FIT_WINDOW_EV."""
    alpha_mask = _alpha_window_mask_ev(energy_ev)
    e_win = energy_ev[alpha_mask]
    y80_win = intensity_80k[alpha_mask]
    rt_model_win = warped_shifted_template_ev(
        e_win,
        rt_energy,
        rt_intensity,
        shift_ev,
        gamma=gamma,
        e0=rt_e0,
    )

    if alpha is None:
        alpha = compute_alpha_analytical_ev(
            energy_ev,
            intensity_80k,
            rt_energy,
            rt_intensity,
            shift_ev,
            gamma=gamma,
            rt_e0=rt_e0,
        )

    residual = y80_win - alpha * rt_model_win
    return float(np.sum(residual**2)), float(alpha)


def _edge_match_sse(crossings_ref, crossings_model):
    """Sum squared differences between blue-edge anchor energies."""
    sse = 0.0
    for level, e_ref in crossings_ref.items():
        e_model = crossings_model.get(level, np.nan)
        if not np.isfinite(e_model):
            return 1.0e300
        delta = float(e_ref - e_model)
        sse += delta * delta
    return sse


def fit_shift_gamma_by_edge_matching(
    energy_common,
    intensity_80k_corrected,
    intensity_rt_corrected,
    optimize_shift=True,
    optimize_gamma=True,
    manual_shift_ev=None,
    manual_gamma=None,
):
    """Align RT template to 80 K PF blue edge using half-max and 1/e crossings."""
    e = np.asarray(energy_common, dtype=float)
    y80 = np.asarray(intensity_80k_corrected, dtype=float)
    rt_y = np.asarray(intensity_rt_corrected, dtype=float)

    e_peak_80k, crossings_80k = pf_blue_edge_crossings(e, y80)
    rt_e0, _ = _pf_peak_energy(e, rt_y)

    if manual_shift_ev is not None or manual_gamma is not None:
        if (manual_shift_ev is None) ^ (manual_gamma is None):
            raise ValueError("Manual geometric override requires both --manual-shift and --manual-gamma.")
        shift_ev = float(manual_shift_ev)
        gamma = float(manual_gamma)
        if not (SHIFT_BOUNDS_EV[0] <= shift_ev <= SHIFT_BOUNDS_EV[1]):
            raise ValueError("Manual shift is out of allowed bounds.")
        if not (GAMMA_BOUNDS[0] <= gamma <= GAMMA_BOUNDS[1]):
            raise ValueError("Manual gamma is out of allowed bounds.")
        rt_model = warped_shifted_template_ev(e, e, rt_y, shift_ev, gamma=gamma, e0=rt_e0)
        _, crossings_rt = pf_blue_edge_crossings(e, rt_model)
        edge_sse = _edge_match_sse(crossings_80k, crossings_rt)
        fit_success = True
        fit_message = "Manual shift/gamma override used."
    else:
        shift_bounds = SHIFT_BOUNDS_EV if optimize_shift else (0.0, 0.0)
        gamma_bounds = GAMMA_BOUNDS if optimize_gamma else (1.0, 1.0)
        shift0 = 0.0 if optimize_shift else 0.0
        gamma0 = 1.1 if optimize_gamma else 1.0

        def objective(params):
            shift_ev, gamma = params
            try:
                rt_model = warped_shifted_template_ev(e, e, rt_y, shift_ev, gamma=gamma, e0=rt_e0)
                _, crossings_rt = pf_blue_edge_crossings(e, rt_model)
                return _edge_match_sse(crossings_80k, crossings_rt)
            except ValueError:
                return 1.0e300

        opt = minimize(
            objective,
            x0=np.array([shift0, gamma0], dtype=float),
            bounds=[shift_bounds, gamma_bounds],
            method="L-BFGS-B",
        )
        shift_ev = float(opt.x[0])
        gamma = float(opt.x[1])
        rt_model = warped_shifted_template_ev(e, e, rt_y, shift_ev, gamma=gamma, e0=rt_e0)
        _, crossings_rt = pf_blue_edge_crossings(e, rt_model)
        edge_sse = _edge_match_sse(crossings_80k, crossings_rt)
        fit_success = bool(opt.success)
        fit_message = str(opt.message)

    return {
        "shift_ev": shift_ev,
        "gamma": gamma,
        "edge_sse": float(edge_sse),
        "fit_success": fit_success,
        "fit_message": fit_message,
        "pf_peak_ev_80k": e_peak_80k,
        "pf_peak_ev_rt": rt_e0,
        "edge_crossings_80k": crossings_80k,
        "edge_crossings_rt": crossings_rt,
    }


def fit_alpha_and_shift_ev(
    energy_common,
    intensity_80k_corrected,
    intensity_rt_corrected,
    optimize_shift=True,
    optimize_gamma=True,
    manual_alpha=None,
    manual_shift_ev=None,
    manual_gamma=None,
    use_geometric=True,
):
    """Fit RT template parameters, then alpha in FIT_WINDOW_EV."""
    e = np.asarray(energy_common, dtype=float)
    y80 = np.asarray(intensity_80k_corrected, dtype=float)
    rt_y = np.asarray(intensity_rt_corrected, dtype=float)
    rt_e0, _ = _pf_peak_energy(e, rt_y)

    if np.count_nonzero(_alpha_window_mask_ev(e)) < 2:
        raise ValueError(
            f"Too few overlap points in alpha fit window {FIT_WINDOW_EV[0]}-{FIT_WINDOW_EV[1]} eV."
        )

    if manual_alpha is not None:
        if manual_shift_ev is None or manual_gamma is None:
            raise ValueError("Manual alpha override requires --manual-shift and --manual-gamma.")
        shift_ev = float(manual_shift_ev)
        gamma = float(manual_gamma)
        alpha = float(manual_alpha)
        if not (SHIFT_BOUNDS_EV[0] <= shift_ev <= SHIFT_BOUNDS_EV[1]):
            raise ValueError("Manual shift is out of allowed bounds.")
        if not (GAMMA_BOUNDS[0] <= gamma <= GAMMA_BOUNDS[1]):
            raise ValueError("Manual gamma is out of allowed bounds.")
        if alpha < 0:
            raise ValueError("Manual alpha must be non-negative.")
        fit_sse, _ = alpha_window_sse_ev(
            e,
            y80,
            e,
            rt_y,
            shift_ev,
            alpha=alpha,
            gamma=gamma,
            rt_e0=rt_e0,
        )
        edge_info = {
            "edge_sse": np.nan,
            "fit_success": True,
            "fit_message": "Manual alpha/shift/gamma override used.",
            "pf_peak_ev_80k": np.nan,
            "pf_peak_ev_rt": rt_e0,
            "edge_crossings_80k": {},
            "edge_crossings_rt": {},
        }
        fit_method = "manual"
    elif use_geometric:
        edge_info = fit_shift_gamma_by_edge_matching(
            e,
            y80,
            rt_y,
            optimize_shift=optimize_shift,
            optimize_gamma=optimize_gamma,
            manual_shift_ev=manual_shift_ev,
            manual_gamma=manual_gamma,
        )
        shift_ev = edge_info["shift_ev"]
        gamma = edge_info["gamma"]
        fit_sse, alpha = alpha_window_sse_ev(
            e,
            y80,
            e,
            rt_y,
            shift_ev,
            gamma=gamma,
            rt_e0=rt_e0,
        )
        fit_method = "geometric_edge_match"
    elif optimize_shift:
        def objective(shift_ev):
            try:
                sse, _ = alpha_window_sse_ev(e, y80, e, rt_y, float(shift_ev), gamma=1.0, rt_e0=rt_e0)
            except ValueError:
                return 1.0e300
            return sse

        opt = minimize_scalar(
            objective,
            bounds=SHIFT_BOUNDS_EV,
            method="bounded",
        )
        shift_ev = float(opt.x)
        gamma = 1.0
        fit_sse, alpha = alpha_window_sse_ev(e, y80, e, rt_y, shift_ev, gamma=gamma, rt_e0=rt_e0)
        edge_info = {
            "edge_sse": np.nan,
            "fit_success": bool(opt.success),
            "fit_message": str(opt.message),
            "pf_peak_ev_80k": np.nan,
            "pf_peak_ev_rt": rt_e0,
            "edge_crossings_80k": {},
            "edge_crossings_rt": {},
        }
        fit_method = "legacy_sse_shift"
    else:
        shift_ev = 0.0
        gamma = 1.0
        fit_sse, alpha = alpha_window_sse_ev(e, y80, e, rt_y, shift_ev, gamma=gamma, rt_e0=rt_e0)
        edge_info = {
            "edge_sse": np.nan,
            "fit_success": True,
            "fit_message": "Alpha fitted with shift fixed at 0 eV.",
            "pf_peak_ev_80k": np.nan,
            "pf_peak_ev_rt": rt_e0,
            "edge_crossings_80k": {},
            "edge_crossings_rt": {},
        }
        fit_method = "legacy_sse_fixed_shift"

    shifted_rt = warped_shifted_template_ev(e, e, rt_y, shift_ev, gamma=gamma, e0=rt_e0)
    scaled_rt = alpha * shifted_rt

    alpha_mask = _alpha_window_mask_ev(e)
    fit_residual_alpha_window = y80[alpha_mask] - scaled_rt[alpha_mask]

    return {
        "alpha": float(alpha),
        "shift_ev": float(shift_ev),
        "gamma": float(gamma),
        "fit_sse": float(fit_sse),
        "edge_sse": float(edge_info["edge_sse"]) if np.isfinite(edge_info["edge_sse"]) else edge_info["edge_sse"],
        "fit_success": bool(edge_info["fit_success"]),
        "fit_message": edge_info["fit_message"],
        "fit_method": fit_method,
        "pf_peak_ev_80k": edge_info["pf_peak_ev_80k"],
        "pf_peak_ev_rt": edge_info["pf_peak_ev_rt"],
        "edge_crossings_80k": edge_info["edge_crossings_80k"],
        "edge_crossings_rt": edge_info["edge_crossings_rt"],
        "shifted_rt_template": shifted_rt,
        "scaled_rt_component": scaled_rt,
        "fit_residual_alpha_window": fit_residual_alpha_window,
        "used_manual_alpha_shift": manual_alpha is not None,
    }


def compute_taper_weights(energy_ev, intensity_80k, pf_peak_ev=None):
    """
    Compute cosine taper anchored on the 80 K PF peak.

    The taper is full (weight=1) above ``pf_peak_ev - TAPER_OFFSET_BELOW_PF_PEAK_EV``
    so the RT model is subtracted fully through the PF peak itself, and fades to zero
    over ``TAPER_TRANSITION_WIDTH_EV`` further below to stop subtracting in the
    pure phosphorescence region.
    """
    e = np.asarray(energy_ev, dtype=float)

    if pf_peak_ev is None or not np.isfinite(pf_peak_ev):
        try:
            e_pf_peak, _ = _pf_peak_energy(e, intensity_80k)
        except ValueError:
            e_pf_peak = 2.55
    else:
        e_pf_peak = float(pf_peak_ev)

    e_high = e_pf_peak - TAPER_OFFSET_BELOW_PF_PEAK_EV
    e_low = e_high - TAPER_TRANSITION_WIDTH_EV

    weights = np.ones_like(e)
    weights[e <= e_low] = 0.0

    transition_mask = (e > e_low) & (e < e_high)
    if np.any(transition_mask):
        phase = np.pi * (e[transition_mask] - e_low) / (e_high - e_low)
        weights[transition_mask] = 0.5 * (1.0 - np.cos(phase))

    return weights, e_pf_peak


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _fit_window_nm():
    """Return alpha fit window as (wl_min, wl_max) in nm (increasing wavelength)."""
    emin, emax = FIT_WINDOW_EV
    return HC_EV_NM / emax, HC_EV_NM / emin


def _plot_wavelength_domain(table, fit_info, metadata, png_path):
    """Plot baseline-corrected spectra and clean phosphorescence in wavelength domain."""
    tapered_rt_e = table["scaled_shifted_RT_E"] * table["subtraction_weight"]

    wl_80k, i80_lambda = energy_to_wavelength(table["energy_eV"], table["intensity_80K_E"])
    wl_tap, rt_tapered_lambda = energy_to_wavelength(table["energy_eV"], tapered_rt_e)
    wl_clean, clean_lambda = energy_to_wavelength(table["energy_eV"], table["clean_phosphorescence_E"])

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax, ax_res = axes

    ax.plot(wl_80k, i80_lambda, color="black", label="Baseline-corrected 80 K", linewidth=1.5)
    ax.plot(
        wl_tap,
        rt_tapered_lambda,
        color="tab:blue",
        label="Scaled + shifted RT template (tapered)",
        linewidth=1.4,
    )

    untapered_mask = table["subtraction_weight"].to_numpy() < 0.99
    if np.any(untapered_mask):
        wl_untap, rt_untap_lambda = energy_to_wavelength(
            table.loc[untapered_mask, "energy_eV"],
            table.loc[untapered_mask, "scaled_shifted_RT_E"],
        )
        ax.plot(
            wl_untap,
            rt_untap_lambda,
            color="tab:blue",
            linestyle="--",
            alpha=0.4,
            linewidth=1.0,
            label="RT template (untapered)",
        )

    ax.plot(
        wl_clean,
        clean_lambda,
        color="tab:red",
        label="Clean phosphorescence (negatives clipped to 0)",
        linewidth=1.8,
    )

    wl_fit_min, wl_fit_max = _fit_window_nm()
    ax.axvspan(
        wl_fit_min,
        wl_fit_max,
        color="gray",
        alpha=0.18,
        label=f"Alpha fit window {wl_fit_min:.1f}-{wl_fit_max:.1f} nm",
    )
    ax.set_ylabel("Intensity (Wavelength Domain)")
    ax.legend(loc="best")
    ax.grid(alpha=0.2)
    ax.set_title(
        "RT template subtraction (Wavelength Domain)\n"
        f"alpha={fit_info['alpha']:.5g}, shift={fit_info['shift_ev']:.4g} eV, "
        f"gamma={fit_info['gamma']:.4g}, negative fraction (pre-clip)={metadata['negative_fraction_before_clip']:.3f}"
    )

    ax_res.plot(wl_clean, clean_lambda, color="tab:red", linewidth=1.2, label="Clean phosphorescence")
    ax_res.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
    ax_res.set_xlabel("Wavelength (nm)")
    ax_res.set_ylabel("Residual")
    ax_res.grid(alpha=0.2)
    ax_res.legend(loc="best")

    secax = ax_res.secondary_xaxis("top", functions=(lambda wl: HC_EV_NM / wl, lambda e: HC_EV_NM / e))
    secax.set_xlabel("Energy (eV)")

    plt.tight_layout()
    plt.savefig(png_path, dpi=SAVE_DPI)
    plt.show()
    plt.close(fig)


def save_outputs(output_base, table, fit_info, metadata, warnings, save_wavelength_clean=False):
    csv_path = output_base.with_name(f"{output_base.stem}_phosphorescence_fit_table.csv")
    png_path = output_base.with_name(f"{output_base.stem}_phosphorescence_fit_plot.png")
    png_lambda_path = output_base.with_name(f"{output_base.stem}_phosphorescence_fit_plot_lambda.png")
    param_path = output_base.with_name(f"{output_base.stem}_phosphorescence_fit_params.json")

    table.to_csv(csv_path, index=False)

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
    ax, ax_res = axes

    ax.plot(
        table["energy_eV"],
        table["intensity_80K_E"],
        color="black",
        label="Baseline-corrected 80 K",
        linewidth=1.5,
    )
    
    tapered_rt = table["scaled_shifted_RT_E"] * table["subtraction_weight"]
    ax.plot(
        table["energy_eV"],
        tapered_rt,
        color="tab:blue",
        label="Scaled + shifted RT template (tapered)",
        linewidth=1.4,
    )
    
    untapered_mask = table["subtraction_weight"] < 0.99
    if np.any(untapered_mask):
        ax.plot(
            table.loc[untapered_mask, "energy_eV"],
            table.loc[untapered_mask, "scaled_shifted_RT_E"],
            color="tab:blue",
            linestyle="--",
            alpha=0.4,
            linewidth=1.0,
            label="RT template (untapered)",
        )

    ax.plot(
        table["energy_eV"],
        table["clean_phosphorescence_E"],
        color="tab:red",
        label="Clean phosphorescence (negatives clipped to 0)",
        linewidth=1.8,
    )
    
    ax.axvspan(
        FIT_WINDOW_EV[0],
        FIT_WINDOW_EV[1],
        color="gray",
        alpha=0.18,
        label=f"Alpha fit window {FIT_WINDOW_EV[0]:.3f}-{FIT_WINDOW_EV[1]:.3f} eV",
    )
    ax.set_ylabel("Intensity (Energy Domain)")
    ax.legend(loc="best")
    ax.grid(alpha=0.2)
    ax.set_title(
        "RT template subtraction (Energy Domain)\n"
        f"alpha={fit_info['alpha']:.5g}, shift={fit_info['shift_ev']:.4g} eV, "
        f"gamma={fit_info['gamma']:.4g}, negative fraction (pre-clip)={metadata['negative_fraction_before_clip']:.3f}"
    )

    ax_res.plot(
        table["energy_eV"],
        table["clean_phosphorescence_E"],
        color="tab:red",
        linewidth=1.2,
        label="Clean phosphorescence residual",
    )
    ax_res.axhline(0.0, color="black", linewidth=0.8, alpha=0.7)
    ax_res.set_xlabel("Energy (eV)")
    ax_res.set_ylabel("Residual")
    ax_res.grid(alpha=0.2)
    ax_res.legend(loc="best")
    
    secax = ax_res.secondary_xaxis('top', functions=(lambda e: HC_EV_NM / e, lambda wl: HC_EV_NM / wl))
    secax.set_xlabel("Wavelength (nm)")

    plt.tight_layout()
    plt.savefig(png_path, dpi=SAVE_DPI)
    plt.show()
    plt.close(fig)

    _plot_wavelength_domain(table, fit_info, metadata, png_lambda_path)

    fit_parameter_summary = {
        "alpha": fit_info["alpha"],
        "shift_ev": fit_info["shift_ev"],
        "gamma": fit_info["gamma"],
        "fit_method": fit_info["fit_method"],
        "fit_sse": fit_info["fit_sse"],
        "edge_sse": fit_info["edge_sse"],
        "fit_success": fit_info["fit_success"],
        "fit_message": fit_info["fit_message"],
        "used_manual_alpha_shift": fit_info["used_manual_alpha_shift"],
        "fit_window_ev": FIT_WINDOW_EV,
        "edge_search_ev": EDGE_SEARCH_EV,
        "edge_levels": EDGE_LEVELS,
        "pf_peak_ev_80k": fit_info["pf_peak_ev_80k"],
        "pf_peak_ev_rt": fit_info["pf_peak_ev_rt"],
        "edge_crossings_80k": fit_info["edge_crossings_80k"],
        "edge_crossings_rt": fit_info["edge_crossings_rt"],
    }

    with param_path.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "fit_parameters": _json_safe(fit_parameter_summary),
                "diagnostics": _json_safe(metadata),
                "windows": {
                    "baseline_window_nm": BASELINE_WINDOW_NM,
                    "fit_window_ev": FIT_WINDOW_EV,
                    "edge_search_ev": EDGE_SEARCH_EV,
                    "edge_levels": EDGE_LEVELS,
                },
                "warnings": _json_safe(warnings),
            },
            handle,
            indent=2,
            ensure_ascii=False,
        )

    wavelength_csv_path = None
    if save_wavelength_clean:
        wavelength_csv_path = output_base.with_name(f"{output_base.stem}_clean_phosphorescence_lambda.csv")
        wl_nm, clean_lambda = energy_to_wavelength(table["energy_eV"], table["clean_phosphorescence_E"])
        pd.DataFrame(
            {
                "wavelength_nm": wl_nm,
                "clean_phosphorescence_I_lambda": clean_lambda,
            }
        ).to_csv(wavelength_csv_path, index=False)

    return csv_path, png_path, png_lambda_path, param_path, wavelength_csv_path


def process_spectra(
    path_80k,
    path_rt,
    dark_80k_path=None,
    dark_rt_path=None,
    manual_alpha=None,
    manual_shift_ev=None,
    manual_gamma=None,
    optimize_shift=True,
    optimize_gamma=True,
    use_geometric=True,
    save_wavelength_clean=True,
):
    """Main geometric edge-matching and subtraction workflow."""
    warnings = []
    spec_80k = load_spectrum(path_80k)
    spec_rt = load_spectrum(path_rt)
    dark_80k = load_spectrum(dark_80k_path) if dark_80k_path else None
    dark_rt = load_spectrum(dark_rt_path) if dark_rt_path else None

    corrected_80k = baseline_correct_spectrum(spec_80k, dark_spectrum=dark_80k, baseline_model=BASELINE_MODEL)
    corrected_rt = baseline_correct_spectrum(spec_rt, dark_spectrum=dark_rt, baseline_model=BASELINE_MODEL)
    warnings.extend(corrected_80k["warnings"])
    warnings.extend(corrected_rt["warnings"])

    e80, i80_e = wavelength_to_energy(corrected_80k["wavelength_nm"], corrected_80k["corrected"])
    ert, irt_e = wavelength_to_energy(corrected_rt["wavelength_nm"], corrected_rt["corrected"])

    overlap_min = max(float(e80.min()), float(ert.min()))
    overlap_max = min(float(e80.max()), float(ert.max()))
    
    common_e = np.linspace(overlap_min, overlap_max, num=int((overlap_max - overlap_min) / 0.001) + 1)
    
    if common_e.size < 50:
        raise ValueError("Too few overlap points between 80 K and RT spectra in energy domain.")

    i80_common = np.interp(common_e, e80, i80_e, left=0.0, right=0.0)
    irt_common = np.interp(common_e, ert, irt_e, left=0.0, right=0.0)

    fit_info = fit_alpha_and_shift_ev(
        common_e,
        i80_common,
        irt_common,
        optimize_shift=optimize_shift,
        optimize_gamma=optimize_gamma,
        manual_alpha=manual_alpha,
        manual_shift_ev=manual_shift_ev,
        manual_gamma=manual_gamma,
        use_geometric=use_geometric,
    )

    pf_peak_for_taper = fit_info.get("pf_peak_ev_80k", None)
    if pf_peak_for_taper is None or not np.isfinite(pf_peak_for_taper):
        pf_peak_for_taper = None
    taper_weights, taper_anchor_ev = compute_taper_weights(
        common_e, i80_common, pf_peak_ev=pf_peak_for_taper
    )

    clean_unclipped = i80_common - fit_info["scaled_rt_component"] * taper_weights
    clean = np.maximum(clean_unclipped, 0.0)

    alpha_mask = _alpha_window_mask_ev(common_e)
    neg_fraction = float(np.mean(clean_unclipped < 0.0))
    min_clean = float(np.min(clean_unclipped))

    neg_band_mask = (common_e >= 2.21) & (common_e <= 2.58)  # approx 480-560 nm
    if np.mean(clean_unclipped[neg_band_mask] < 0.0) > 0.25:
        warnings.append("Large negative fraction in 2.21-2.58 eV region.")
    if abs(fit_info["shift_ev"] - SHIFT_BOUNDS_EV[0]) < 0.002 or abs(fit_info["shift_ev"] - SHIFT_BOUNDS_EV[1]) < 0.002:
        warnings.append("Fitted shift is near boundary; check fit trustworthiness.")
    if abs(fit_info["gamma"] - GAMMA_BOUNDS[0]) < 0.01 or abs(fit_info["gamma"] - GAMMA_BOUNDS[1]) < 0.01:
        warnings.append("Fitted gamma is near boundary; check fit trustworthiness.")
    if fit_info["alpha"] > 10.0:
        warnings.append("Alpha is unusually large.")
    if not fit_info["fit_success"]:
        warnings.append(f"Shift optimization did not fully converge: {fit_info['fit_message']}")
    if np.count_nonzero(alpha_mask) < 5:
        warnings.append("Very few points in alpha fit window.")

    table = pd.DataFrame(
        {
            "energy_eV": common_e,
            "intensity_80K_E": i80_common,
            "scaled_shifted_RT_E": fit_info["scaled_rt_component"],
            "subtraction_weight": taper_weights,
            "clean_phosphorescence_E": clean,
            "clean_phosphorescence_E_unclipped": clean_unclipped,
        }
    )

    metadata = {
        "negative_fraction_before_clip": neg_fraction,
        "minimum_clean_intensity_before_clip": min_clean,
        "negatives_clipped_to_zero": True,
        "alpha_fit_sse": fit_info["fit_sse"],
        "edge_match_sse": fit_info["edge_sse"],
        "fit_method": fit_info["fit_method"],
        "taper_anchor_ev": taper_anchor_ev,
        "baseline_80k": corrected_80k["baseline_params"],
        "baseline_rt": corrected_rt["baseline_params"],
    }

    csv_path, png_path, png_lambda_path, param_path, wavelength_csv_path = save_outputs(
        Path(path_80k),
        table,
        fit_info,
        metadata,
        warnings,
        save_wavelength_clean=save_wavelength_clean,
    )

    return {
        "fit_info": fit_info,
        "metadata": metadata,
        "warnings": warnings,
        "csv_path": csv_path,
        "png_path": png_path,
        "png_lambda_path": png_lambda_path,
        "param_path": param_path,
        "wavelength_csv_path": wavelength_csv_path,
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="RT template subtraction for clean phosphorescence using geometric PF blue-edge matching."
    )
    parser.add_argument("path_80k", nargs="?", help="80 K gated PL CSV path")
    parser.add_argument("path_rt", nargs="?", help="RT PL CSV path")
    parser.add_argument("--dark-80k", dest="dark_80k", default=None, help="Optional dark/blank for 80 K")
    parser.add_argument("--dark-rt", dest="dark_rt", default=None, help="Optional dark/blank for RT")
    parser.add_argument("--manual-alpha", type=float, default=None, help="Manual alpha override")
    parser.add_argument(
        "--manual-shift",
        type=float,
        default=None,
        help="RT template rigid energy shift, unit eV. Positive value moves template to higher energy (shorter wavelength).",
    )
    parser.add_argument(
        "--manual-gamma",
        type=float,
        default=None,
        help="Manual RT template narrowing factor around PF peak (gamma > 1 narrows).",
    )
    parser.add_argument(
        "--legacy-sse-fit",
        action="store_true",
        help="Use legacy window SSE shift optimization instead of geometric edge matching",
    )
    parser.add_argument(
        "--no-fit-shift",
        action="store_true",
        help="Fix shift at 0 eV during geometric or legacy fitting",
    )
    parser.add_argument(
        "--no-fit-gamma",
        action="store_true",
        help="Fix gamma at 1.0 during geometric fitting",
    )
    parser.add_argument(
        "--no-save-wavelength",
        action="store_true",
        help="Do not save the optional wavelength-domain clean spectrum CSV",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.path_80k and args.path_rt:
        path_80k = Path(args.path_80k)
        path_rt = Path(args.path_rt)
    else:
        path_80k = choose_csv_file("Select 80 K gated PL spectrum")
        if path_80k is None:
            return 1
        path_rt = choose_csv_file("Select RT PL spectrum", initial_dir=path_80k.parent)
        if path_rt is None:
            return 1

    try:
        result = process_spectra(
            path_80k=path_80k,
            path_rt=path_rt,
            dark_80k_path=args.dark_80k,
            dark_rt_path=args.dark_rt,
            manual_alpha=args.manual_alpha,
            manual_shift_ev=args.manual_shift,
            manual_gamma=args.manual_gamma,
            optimize_shift=not args.no_fit_shift,
            optimize_gamma=not args.no_fit_gamma,
            use_geometric=not args.legacy_sse_fit,
            save_wavelength_clean=not args.no_save_wavelength,
        )
    except Exception as exc:
        if messagebox is not None:
            messagebox.showerror("Phosphorescence subtraction failed", str(exc))
        raise

    warning_text = "\n".join(f"- {msg}" for msg in result["warnings"]) if result["warnings"] else "- none"
    wl_line = (
        f"Wavelength CSV: {result['wavelength_csv_path']}\n" if result['wavelength_csv_path'] is not None else ""
    )
    edge_sse_text = (
        f"{result['fit_info']['edge_sse']:.6g}"
        if np.isfinite(result["fit_info"]["edge_sse"])
        else "n/a"
    )
    message = (
        "Phosphorescence subtraction complete.\n"
        f"fit_method = {result['fit_info']['fit_method']}\n"
        f"alpha = {result['fit_info']['alpha']:.6g}\n"
        f"shift_ev = {result['fit_info']['shift_ev']:.6g}\n"
        f"gamma = {result['fit_info']['gamma']:.6g}\n"
        f"alpha_fit_sse = {result['fit_info']['fit_sse']:.6g}\n"
        f"edge_match_sse = {edge_sse_text}\n"
        f"negative_fraction_before_clip = {result['metadata']['negative_fraction_before_clip']:.6g}\n"
        f"CSV: {result['csv_path']}\n"
        f"PNG (energy): {result['png_path']}\n"
        f"PNG (wavelength): {result['png_lambda_path']}\n"
        f"Params: {result['param_path']}\n"
        f"{wl_line}"
        f"Warnings:\n{warning_text}"
    )
    print(message)
    if messagebox is not None:
        messagebox.showinfo("Phosphorescence subtraction complete", message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
