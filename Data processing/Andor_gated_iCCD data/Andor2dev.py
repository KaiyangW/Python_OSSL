#!/usr/bin/env python3
"""
TRPES Processor -- Andor iStar ICCD .asc Data Analysis
=======================================================

Reads .asc files exported from an Andor iStar ICCD camera (kinetic mode),
processes time-resolved photoluminescence emission spectra (TRPES),
and generates analysis plots: spectra, heatmaps, decay kinetics,
and multi-exponential lifetime fittings.

Usage:
    python Andor2dev.py FILE1.asc [FILE2.asc ...] [options]

Options:
    -b, --background  BG.asc [BG2.asc ...]  Background file(s)
    -g, --gain-file   gainFunctionNew.dat   Gain calibration table
    -w, --wavelength  520                   Wavelength (nm) for kinetics
    -o, --output-dir  ./plots               Output directory for figures
    --no-bg                                 Skip background subtraction
    --tmax            1e-3                  Max time (s) for exp fitting
"""

import argparse
import os
import sys

import numpy as np
from scipy import signal
from scipy.optimize import curve_fit
import matplotlib
matplotlib.use("Agg")                          # headless backend
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors


# ---------------------------------------------------------------------------
#  Metadata parsing
# ---------------------------------------------------------------------------

def parse_metadata(filepath):
    """Extract experiment parameters from an Andor ICCD .asc header."""
    params = {}
    with open(filepath, "r") as f:
        for j, line in enumerate(f):
            if "Kinetic Cycle Time" in line:
                *_, params["EXP"] = line.split()
            elif "Gain level" in line:
                *_, params["G"] = line.split()
            elif "Integrate on chip" in line:
                *_, params["Int"] = line.split()
            elif "Gate Mode" in line:
                *_, gate1, gate2 = line.split()
                params["Gate"] = gate1 + gate2
            elif "Accumulate Cycle Time" in line:
                *_, params["EXP"] = line.split()
            elif "Number of Accumulations" in line:
                *_, params["ACC"] = line.split()
            elif "Gate Width (" in line:
                *_, params["OW"] = line.split()
            elif "Gate Delay (" in line:
                *_, params["DL"] = line.split()
            elif "Gate Delay Step" in line:
                *_, params["fDL"] = line.split()
            elif "Gate Width Step" in line:
                *_, params["fOW"] = line.split()
            elif line == "\n":
                params["endOfMetadata"] = j + 2
                break
    return params


# ---------------------------------------------------------------------------
#  Time-axis construction
# ---------------------------------------------------------------------------

def build_time_axis(init, func, time_step):
    """Construct a time axis from an initial value and a step function string.

    Supported step-function formats exported by Andor Solis:
        ``<coeff>lin(<coeff>x)``   linear step
        ``<coeff>exp(<coeff>x)``   exponential step
        ``<coeff>log(<coeff>x)``   exponential step (Solis log mode)
        ``const(<coeff>x)``        constant offset step
        ``<number>``               fixed numeric step
        ``0``                      zero step (no change)

    Parameters
    ----------
    init : str
        Starting value (ps).
    func : str
        Step-function descriptor string.
    time_step : int
        Number of time steps.

    Returns
    -------
    time : ndarray        time points in units of 1000 * ps = ns
    """
    t = [1000.0 * float(init)]

    if func.rfind("const") != -1:
        coeff = func.split("const")
        coeff[1] = coeff[1][1:-2]          # strip "(...x)"
        for i in range(1, time_step):
            t.append(t[i - 1] + float(coeff[0]) + float(coeff[1]))
    elif func.rfind("lin") != -1:
        coeff = func.split("lin")
        coeff[1] = coeff[1][1:-2]
        for i in range(1, time_step):
            t.append(t[i - 1] + float(coeff[0]) + float(coeff[1]) * i)
    elif func.rfind("exp") != -1:
        coeff = func.split("exp")
        coeff[1] = coeff[1][1:-2]
        for i in range(1, time_step):
            t.append(t[i - 1] + float(coeff[0]) * np.exp(float(coeff[1]) * i))
    elif func.rfind("log") != -1:
        # Note: Andor Solis "log" mode uses exp internally
        coeff = func.split("log")
        coeff[1] = coeff[1][1:-2]
        for i in range(1, time_step):
            t.append(t[i - 1] + float(coeff[0]) * np.exp(float(coeff[1]) * i))
    else:
        step = float(func)
        for i in range(1, time_step):
            t.append(t[i - 1] + step)

    return np.array(t)


# ---------------------------------------------------------------------------
#  Data I/O
# ---------------------------------------------------------------------------

def read_data(filepath, skip_rows):
    """Read spectral matrix from an Andor .asc file.

    Returns
    -------
    wavelengths : ndarray (n_wl,)
    intensities : ndarray (n_wl, n_steps)
    """
    data = np.loadtxt(filepath, skiprows=skip_rows)
    return data[:, 0], data[:, 1:]


def load_gain_calibration(filepath):
    """Load two-column gain calibration table (gain_level, gain_factor)."""
    data = np.loadtxt(filepath, skiprows=1, delimiter="\t")
    return data[:, 0], data[:, 1]


def find_gain_for_level(gain_levels, gain_values, target):
    """Return the gain factor whose level is closest to *target*."""
    idx = np.argmin(np.abs(gain_levels - float(target)))
    return gain_values[idx]


def find_file(filename, search_dirs):
    """Locate a file in one of *search_dirs*."""
    for d in search_dirs:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            return candidate
    return filename


# ---------------------------------------------------------------------------
#  Exponential models
# ---------------------------------------------------------------------------

def double_exp(x, A1, tau1, A2, tau2, C):
    return A1 * np.exp(-x / tau1) + A2 * np.exp(-x / tau2) + C


def triple_exp(x, A1, tau1, A2, tau2, A3, tau3, C):
    return A1 * np.exp(-x / tau1) + A2 * np.exp(-x / tau2) + A3 * np.exp(-x / tau3) + C


# ---------------------------------------------------------------------------
#  Plotting helpers
# ---------------------------------------------------------------------------

def _save_close(fig, path):
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Process Andor ICCD .asc TRPES data."
    )
    parser.add_argument(
        "files", nargs="+", metavar="FILE.asc",
        help="One or more Andor ICCD .asc data files",
    )
    parser.add_argument(
        "-b", "--background", nargs="*", default=None,
        metavar="BG.asc", help="Background .asc file(s)",
    )
    parser.add_argument(
        "-g", "--gain-file", default="gainFunctionNew.dat",
        help="Gain calibration table (default: gainFunctionNew.dat)",
    )
    parser.add_argument(
        "-w", "--wavelength", type=float, default=520,
        help="Wavelength (nm) for kinetics extraction (default: 520)",
    )
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="Output directory for figures (default: beside first input)",
    )
    parser.add_argument(
        "--no-bg", action="store_true",
        help="Skip background subtraction",
    )
    parser.add_argument(
        "--tmax", type=float, default=1e-3,
        help="Maximum time (s) for exponential fitting (default: 1e-3)",
    )

    args = parser.parse_args()
    files = args.files
    bg_files = args.background
    gain_file_arg = args.gain_file
    wl_target = args.wavelength
    tmax = args.tmax
    no_bg = args.no_bg

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.dirname(os.path.abspath(files[0]))
    os.makedirs(output_dir, exist_ok=True)

    # ---- locate gain calibration file ----
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.abspath(files[0])),
        os.getcwd(),
    ]
    gain_path = None
    if os.path.isfile(gain_file_arg):
        gain_path = gain_file_arg
    else:
        gain_path = find_file(gain_file_arg, search_dirs)
    if gain_path and os.path.isfile(gain_path):
        print(f"Gain calibration: {gain_path}")
        gain_levels, gain_values = load_gain_calibration(gain_path)
        have_gain = True
    else:
        print("WARNING: gain calibration file not found – skipping gain correction")
        have_gain = False

    # ===================================================================
    #  Phase 1 — Parse metadata & read data
    # ===================================================================
    params_list = []
    wavelengths_list = []
    trpes_list = []
    names = []

    print("\n" + "=" * 60)
    print("Parsing files ...")
    print("=" * 60)

    for fp in files:
        print(f"\n  {fp}")
        params = parse_metadata(fp)
        params_list.append(params)

        n_expected = (
            "EXP", "G", "Int", "Gate", "DL", "OW", "fDL", "fOW"
        )
        for k in n_expected:
            print(f"    {k:6s} = {params.get(k, '?')}")

        wl, intensities = read_data(fp, params["endOfMetadata"])
        wavelengths_list.append(wl)
        trpes_list.append(intensities)

        base, _ = os.path.splitext(os.path.basename(fp))
        names.append(base)
        print(f"    matrix shape = {intensities.shape}")

    n_files = len(files)

    # ===================================================================
    #  Phase 2 — Read background files (optional)
    # ===================================================================
    bg_list = []
    if bg_files and not no_bg:
        print("\n" + "=" * 60)
        print("Parsing background files ...")
        print("=" * 60)
        for fp in bg_files:
            print(f"\n  {fp}")
            bgp = parse_metadata(fp)
            print(f"    EXP = {bgp.get('EXP', '?')}")
            _wl, bg_data = read_data(fp, bgp["endOfMetadata"])
            # normalise by exposure time & accumulations
            bg = bg_data.astype(float)
            bg /= float(bgp.get("EXP", 1))
            if "ACC" in bgp:
                bg /= float(bgp["ACC"])
            bg_list.append(bg)
            print(f"    shape = {bg.shape}")

    # ===================================================================
    #  Phase 3 — Build time axes & gate-width normalisation
    # ===================================================================
    print("\n" + "=" * 60)
    print("Building time axes ...")
    print("=" * 60)

    time_axes = []
    for i in range(n_files):
        p = params_list[i]
        n_steps = trpes_list[i].shape[1]

        DL_raw = build_time_axis(p["DL"], p["fDL"], n_steps)
        OW_raw = build_time_axis(p["OW"], p["fOW"], n_steps)

        DL = DL_raw / 1e12             # ps → s
        OW = OW_raw / 1e12             # ps → s

        t_axis = DL + OW / 2.0        # centre of each gate

        # Set first data point as t = 0
        t_axis -= DL[0]
        t_axis = np.insert(t_axis, 0, 0.0)
        time_axes.append(t_axis)

        # Gate-width normalisation (original: unconditional)
        trpes_list[i] = trpes_list[i] / OW

        print(f"  {names[i]}:  steps={n_steps},  "
              f"range = {t_axis[1]:.4e} – {t_axis[-1]:.4e} s")

    # ===================================================================
    #  Phase 4 — Normalisation
    # ===================================================================
    print("\n" + "=" * 60)
    print("Normalising ...")
    print("=" * 60)

    for i in range(n_files):
        p = params_list[i]

        # Gain correction
        if have_gain:
            g = find_gain_for_level(gain_levels, gain_values, p["G"])
            trpes_list[i] /= g

        # Exposure-time normalisation
        trpes_list[i] /= float(p["EXP"])

        trpes_list[i] = np.round(trpes_list[i])
        print(f"  {names[i]}: done")

    # ===================================================================
    #  Phase 5 — Background subtraction
    # ===================================================================
    if bg_list and not no_bg:
        print("\n" + "=" * 60)
        print("Subtracting background ...")
        print("=" * 60)
        for i in range(n_files):
            if i < len(bg_list):
                n_cols = trpes_list[i].shape[1]
                bg = bg_list[i]
                if bg.ndim == 1:
                    tr_bg = np.tile(bg[:, np.newaxis], (1, n_cols))
                else:
                    tr_bg = np.tile(bg, (1, n_cols))
                trpes_list[i] = trpes_list[i] - tr_bg
                print(f"  {names[i]}: subtracted")

    # ===================================================================
    #  Phase 6 — Generate plots
    # ===================================================================
    print("\n" + "=" * 60)
    print("Generating plots ...")
    print("=" * 60)

    for i in range(n_files):
        name = names[i]
        wl = wavelengths_list[i]
        data = trpes_list[i]
        t_axis = time_axes[i]
        n_steps = data.shape[1]
        base = os.path.join(output_dir, name)

        # ---- 6a.  TRPES overlay (unsmoothed, matching original cell 45) ----
        fig, ax = plt.subplots(figsize=(8, 5))
        cmap = plt.get_cmap("viridis")
        colors = cmap(np.linspace(0, 1, n_steps))
        for j in range(n_steps):
            ax.plot(wl, data[:, j], color=colors[j], lw=0.6,
                    label=f"{t_axis[j + 1] * 1e6:.1f} µs" if j < 16 else "")
        ax.set_xlabel("Wavelength (nm)")
        ax.set_ylabel("Intensity (counts)")
        ax.set_yscale("log")
        ax.set_title(f"TRPES — {name}")
        if n_steps <= 16:
            ax.legend(fontsize=7, loc="upper right")
        _save_close(fig, f"{base}_trpes.png")
        print(f"  {base}_trpes.png")

        # ---- 6b.  Heatmap (unsmoothed, matching original cell 46) ----
        fig, ax = plt.subplots(figsize=(8, 5))
        im = ax.imshow(
            data, aspect="auto", origin="lower",
            extent=[t_axis[0] * 1e6, t_axis[-1] * 1e6, wl[0], wl[-1]],
            interpolation="nearest", cmap="viridis",
        )
        plt.colorbar(im, ax=ax, label="Counts")
        ax.set_xlabel("Time (µs)")
        ax.set_ylabel("Wavelength (nm)")
        ax.set_title(f"TRPL Spectral Evolution — {name}")
        _save_close(fig, f"{base}_heatmap.png")
        print(f"  {base}_heatmap.png")

        # ---- Smooth data for kinetics (matching original cell 48) ----
        data = signal.savgol_filter(data, 10, 0, axis=1)

        # ---- 6c.  Kinetics at fixed wavelength (matching original cell 48) ----
        idx = np.argmin(np.abs(wl - wl_target))
        margin = 5
        lo = max(0, idx - margin)
        hi = min(len(wl), idx + margin + 1)
        kin = np.sum(data[lo:hi, :], axis=0)

        mask = kin > 1
        kf = kin[mask]
        tf = t_axis[1:][mask]                       # skip the prepended 0

        if len(kf) > 3:
            kf_s = signal.savgol_filter(kf, min(3, len(kf) - 2), 0)
            kf_n = kf_s / kf_s.max()

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(tf * 1e6, kf_n, "-o", ms=3, label=name)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_ylim(1e-7, 10)
            ax.set_xlabel("Time (µs)")
            ax.set_ylabel("Intensity (arb.u.)")
            ax.set_title(
                "Kinetics at " + np.format_float_positional(wl_target, 2, trim='-') + " nm"
            )
            ax.legend()
            _save_close(fig, f"{base}_kinetics_{wl_target:.0f}nm.png")
            print(f"  {base}_kinetics_{wl_target:.0f}nm.png")

        # ---- 6d.  Full-spectrum integrated kinetics (matching original cell 49) ----
        # Note: original applies savgol again here, but since data is already
        # smoothed above, we just use the smoothed data directly.
        kin_full = np.sum(data, axis=0)
        mask_f = kin_full > 1
        kf_f = kin_full[mask_f]
        tf_f = t_axis[1:][mask_f]

        if len(kf_f) > 3:
            kf_sf = signal.savgol_filter(kf_f, min(3, len(kf_f) - 2), 0)
            kf_nf = kf_sf / kf_sf.max()

            fig, ax = plt.subplots(figsize=(8, 5))
            ax.plot(tf_f * 1e6, kf_nf, "-o", ms=3, label=name)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_ylim(1e-7, 10)
            ax.set_xlabel("Time (µs)")
            ax.set_ylabel("Intensity (arb.u.)")
            ax.set_title("Kinetics integrated over whole spectrum")
            ax.legend()
            _save_close(fig, f"{base}_kinetics_integrated.png")
            print(f"  {base}_kinetics_integrated.png")

        # ---- 6e.  Exponential fitting (matching original cells 50/51) ----
        # Reuse the fixed-wavelength kinetics variables (normKinetics / tempTimeAxis)
        if len(kf) > 3:
            max_i = np.argmax(kf_n)
            sel = (np.arange(len(tf)) >= max_i) & (tf >= 0) & (tf <= tmax)
            xf = tf[sel]
            yf = kf_n[sel]

            if len(xf) >= 7:
                eps = 1e-12
                sigma = np.maximum(yf, eps)

                # --- Double exponential ---
                p0_d = [0.5, 1e-9, 0.5, 1e-6, yf.min()]
                try:
                    popt_d, _ = curve_fit(
                        double_exp, xf, yf, p0=p0_d,
                        sigma=sigma, absolute_sigma=True, maxfev=50000,
                    )
                    y_dfit = double_exp(xf, *popt_d)

                    fig, ax = plt.subplots(figsize=(8, 5))
                    ax.plot(xf * 1e6, yf, "o", ms=3, label="Data")
                    ax.plot(xf * 1e6, y_dfit, "-", label="Double-exp fit")
                    ax.set_xscale("log")
                    ax.set_yscale("log")
                    ax.set_xlabel("Time (µs)")
                    ax.set_ylabel("Intensity (arb.u.)")
                    ax.legend()
                    ax.set_title(
                        f"Double exp: A1={popt_d[0]:.2g}, "
                        f"tau1={popt_d[1]:.2g}s, A2={popt_d[2]:.2g}, "
                        f"tau2={popt_d[3]:.2g}s, C={popt_d[4]:.2g}"
                    )
                    _save_close(fig, f"{base}_fit_double.png")
                    print(f"  {base}_fit_double.png")
                    print(f"    τ1 = {popt_d[1]:.3e} s,  τ2 = {popt_d[3]:.3e} s")
                except Exception as exc:
                    print(f"  Double-exp fit failed: {exc}")

                # --- Triple exponential ---
                p0_t = [0.33, 1e-8, 0.33, 1e-6, 0.33, 1e-4, yf.min()]
                try:
                    popt_t, _ = curve_fit(
                        triple_exp, xf, yf, p0=p0_t,
                        sigma=sigma, absolute_sigma=True, maxfev=50000,
                    )
                    y_tfit = triple_exp(xf, *popt_t)

                    fig, ax = plt.subplots(figsize=(8, 5))
                    ax.plot(xf * 1e6, yf, "o", ms=3, label="Data")
                    ax.plot(xf * 1e6, y_tfit, "-", label="Triple-exp fit")
                    ax.set_xscale("log")
                    ax.set_yscale("log")
                    ax.set_xlabel("Time (µs)")
                    ax.set_ylabel("Intensity (arb.u.)")
                    ax.legend()
                    ax.set_title(
                        f"Triple exp: A1={popt_t[0]:.2g}, tau1={popt_t[1]:.2g}s, "
                        f"A2={popt_t[2]:.2g}, tau2={popt_t[3]:.2g}s, "
                        f"A3={popt_t[4]:.2g}, tau3={popt_t[5]:.2g}s, C={popt_t[6]:.2g}"
                    )
                    _save_close(fig, f"{base}_fit_triple.png")
                    print(f"  {base}_fit_triple.png")
                    print(f"    τ1 = {popt_t[1]:.3e} s,  "
                          f"τ2 = {popt_t[3]:.3e} s,  "
                          f"τ3 = {popt_t[5]:.3e} s")
                except Exception as exc:
                    print(f"  Triple-exp fit failed: {exc}")
            else:
                print(f"  Skipping fits: only {len(xf)} usable points")
        else:
            print(f"  Skipping fits: no valid kinetics points")

    print("\n" + "=" * 60)
    print("All done.")
    print("=" * 60)


if __name__ == "__main__":
    main()
