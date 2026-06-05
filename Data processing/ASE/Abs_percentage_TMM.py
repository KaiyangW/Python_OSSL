"""
Compute thin-film absorption A = 1 - R - T from CompleteEASE nk data using TMM （Transfer Matrix Method）.

Usage
-----
Double-click or run from a terminal::

    python Abs_percentage_TMM.py

Select the nk text file in the dialog, then enter film thickness (nm).
Optional command line::

    python Abs_percentage_TMM.py "path/to/nk data.txt" 120

Outputs (same folder as the nk file):
  - ``<stem>_absorption_TMM.csv``
  - ``<stem>_absorption_TMM.pdf``  (600 dpi, 20 cm x 15 cm)
"""

from __future__ import annotations

import argparse
import ctypes
import json
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import tmm

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog
except ImportError:
    tk = None
    filedialog = None
    messagebox = None
    simpledialog = None

SUBSTRATE_N = 1.52
AMBIENT_N = 1.0
SAVE_DPI = 600
PLOT_FIGSIZE = (20 / 2.54, 15 / 2.54)
PLOT_FONT_SIZE = 16
PLOT_AXIS_LINEWIDTH = 1.0

SETTINGS_FILE = Path(__file__).resolve().parent / "abs_tmm_last_path.json"


def get_last_directory(fallback=None):
    fallback = Path(fallback or Path(__file__).resolve().parent)
    if not SETTINGS_FILE.is_file():
        return str(fallback)

    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            last_dir = json.load(handle).get("last_dir")
        if last_dir and Path(last_dir).is_dir():
            return last_dir
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    return str(fallback)


def save_last_directory(path):
    directory = str(Path(path).resolve().parent)
    try:
        with SETTINGS_FILE.open("w", encoding="utf-8") as handle:
            json.dump({"last_dir": directory}, handle, indent=2)
    except OSError:
        pass


def choose_nk_file(initial_dir=None):
    if filedialog is None:
        raise RuntimeError("Tkinter is not available; pass the nk file path on the command line.")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    filepath = filedialog.askopenfilename(
        title="Select CompleteEASE nk data file",
        initialdir=initial_dir or get_last_directory(),
        filetypes=[("Text files", "*.txt"), ("CSV files", "*.csv"), ("All files", "*.*")],
    )
    root.destroy()

    if filepath:
        save_last_directory(filepath)

    return filepath or None


def ask_thickness_nm(default_value="100"):
    if simpledialog is None:
        raise RuntimeError("Tkinter is not available; pass thickness (nm) on the command line.")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    value = simpledialog.askstring(
        "Film thickness",
        "Enter film thickness d (nm):",
        initialvalue=default_value,
        parent=root,
    )
    root.destroy()

    if value is None:
        return None

    value = value.strip()
    if not value:
        return None

    try:
        thickness = float(value)
    except ValueError as exc:
        raise ValueError(f"Invalid thickness: {value!r}") from exc

    if thickness <= 0:
        raise ValueError(f"Thickness must be positive, got {thickness} nm.")

    return thickness


def read_complete_ease_nk(filepath):
    """Read wavelength (nm), n, k from a CompleteEASE export."""
    path = Path(filepath)
    rows = []
    header_found = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue

            if not header_found:
                if re.search(r"wavelength", stripped, flags=re.IGNORECASE) and "n" in stripped.lower():
                    header_found = True
                continue

            parts = re.split(r"\s+", stripped)
            if len(parts) < 3:
                continue

            try:
                wavelength_nm = float(parts[0])
                n_value = float(parts[1])
                k_value = float(parts[2])
            except ValueError:
                continue

            rows.append((wavelength_nm, n_value, k_value))

    if not rows:
        raise ValueError(f"No numeric nk data found in {path}")

    data = pd.DataFrame(rows, columns=["wavelength_nm", "n", "k"])
    data = data.sort_values("wavelength_nm").drop_duplicates("wavelength_nm", keep="first")
    data = data.reset_index(drop=True)
    return data


def compute_absorption_vs_wavelength(nk_data, thickness_nm):
    """Return arrays of wavelength, R, T, A for normal incidence."""
    wavelengths = nk_data["wavelength_nm"].to_numpy(dtype=float)
    n_film = nk_data["n"].to_numpy(dtype=float) + 1j * nk_data["k"].to_numpy(dtype=float)

    reflectance = np.empty_like(wavelengths, dtype=float)
    transmittance = np.empty_like(wavelengths, dtype=float)
    absorption = np.empty_like(wavelengths, dtype=float)

    d_list = [np.inf, float(thickness_nm), np.inf]

    for index, (lam_nm, n_complex) in enumerate(zip(wavelengths, n_film)):
        n_list = [AMBIENT_N + 0j, n_complex, SUBSTRATE_N + 0j]
        result = tmm.unpolarized_RT(n_list, d_list, 0.0, lam_nm)
        r_val = float(np.real(result["R"]))
        t_val = float(np.real(result["T"]))
        reflectance[index] = r_val
        transmittance[index] = t_val
        absorption[index] = 1.0 - r_val - t_val

    return wavelengths, reflectance, transmittance, absorption


def apply_plot_style(ax):
    ax.set_title("")
    ax.grid(False)
    ax.tick_params(
        axis="both",
        which="both",
        direction="in",
        top=True,
        right=True,
        bottom=True,
        left=True,
        labelsize=PLOT_FONT_SIZE,
        width=PLOT_AXIS_LINEWIDTH,
    )
    for spine in ax.spines.values():
        spine.set_linewidth(PLOT_AXIS_LINEWIDTH)
    ax.xaxis.label.set_size(PLOT_FONT_SIZE)
    ax.yaxis.label.set_size(PLOT_FONT_SIZE)


def absorption_at_wavelength(wavelengths, absorption_percent, target_nm):
    """Linear interpolation of absorption (%) at a target wavelength."""
    return float(np.interp(target_nm, wavelengths, absorption_percent))


def peak_absorption_after_wavelength(wavelengths, absorption_percent, min_nm):
    """Return (wavelength_nm, A_percent) of the maximum after min_nm."""
    mask = wavelengths > min_nm
    if not np.any(mask):
        return None, None

    wl_region = wavelengths[mask]
    a_region = absorption_percent[mask]
    peak_idx = int(np.argmax(a_region))
    return float(wl_region[peak_idx]), float(a_region[peak_idx])


def annotate_absorption_markers(ax, wavelengths, absorption_percent):
    """Mark A at 330 nm and the peak A for wavelength > 400 nm."""
    marker_wl = 330.0
    a_330 = absorption_at_wavelength(wavelengths, absorption_percent, marker_wl)
    peak_wl, peak_a = peak_absorption_after_wavelength(wavelengths, absorption_percent, 400.0)

    ax.axvline(marker_wl, color="0.45", linestyle="--", linewidth=1.0, zorder=1)
    ax.plot(marker_wl, a_330, "o", color="black", markersize=7, zorder=3)
    ax.annotate(
        f"330 nm: {a_330:.1f}%",
        xy=(marker_wl, a_330),
        xytext=(12, 14),
        textcoords="offset points",
        fontsize=PLOT_FONT_SIZE,
        ha="left",
        va="bottom",
        arrowprops={"arrowstyle": "-", "color": "0.3", "lw": 0.8},
    )

    if peak_wl is not None:
        ax.axvline(peak_wl, color="0.45", linestyle="--", linewidth=1.0, zorder=1)
        ax.plot(peak_wl, peak_a, "o", color="black", markersize=7, zorder=3)
        ax.annotate(
            f"{peak_wl:.0f} nm: {peak_a:.1f}%",
            xy=(peak_wl, peak_a),
            xytext=(-12, 14),
            textcoords="offset points",
            fontsize=PLOT_FONT_SIZE,
            ha="right",
            va="bottom",
            arrowprops={"arrowstyle": "-", "color": "0.3", "lw": 0.8},
        )

    return a_330, peak_wl, peak_a


def save_absorption_outputs(
    nk_path,
    thickness_nm,
    wavelengths,
    reflectance,
    transmittance,
    absorption,
):
    nk_path = Path(nk_path)
    stem = nk_path.stem
    output_dir = nk_path.parent
    csv_path = output_dir / f"{stem}_absorption_TMM.csv"
    pdf_path = output_dir / f"{stem}_absorption_TMM.pdf"

    result_df = pd.DataFrame(
        {
            "wavelength_nm": wavelengths,
            "R": reflectance,
            "T": transmittance,
            "A_percent": absorption * 100.0,
        }
    )
    result_df.to_csv(csv_path, index=False)

    absorption_percent = absorption * 100.0

    fig, ax = plt.subplots(figsize=PLOT_FIGSIZE)
    ax.plot(wavelengths, absorption_percent, color="crimson", linewidth=1.5)
    a_330, peak_wl, peak_a = annotate_absorption_markers(ax, wavelengths, absorption_percent)
    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Absorption (%)")
    ax.set_xlim(float(np.min(wavelengths)), float(np.max(wavelengths)))
    apply_plot_style(ax)
    fig.tight_layout()
    fig.savefig(pdf_path, dpi=SAVE_DPI, bbox_inches="tight")
    plt.close(fig)

    marker_info = {
        "a_330_percent": a_330,
        "peak_after_400_wl_nm": peak_wl,
        "peak_after_400_a_percent": peak_a,
    }
    return csv_path, pdf_path, result_df, marker_info


def run_analysis(nk_path, thickness_nm):
    nk_data = read_complete_ease_nk(nk_path)
    wavelengths, reflectance, transmittance, absorption = compute_absorption_vs_wavelength(
        nk_data, thickness_nm
    )
    csv_path, pdf_path, result_df, marker_info = save_absorption_outputs(
        nk_path,
        thickness_nm,
        wavelengths,
        reflectance,
        transmittance,
        absorption,
    )

    summary_lines = [
        f"Thickness: {thickness_nm:g} nm",
        f"Substrate n: {SUBSTRATE_N}",
        f"Wavelength range: {wavelengths.min():.1f} - {wavelengths.max():.1f} nm",
        f"A at 330 nm: {marker_info['a_330_percent']:.2f} %",
    ]
    if marker_info["peak_after_400_wl_nm"] is not None:
        summary_lines.append(
            "Peak absorption (>400 nm): "
            f"{marker_info['peak_after_400_a_percent']:.2f} % "
            f"at {marker_info['peak_after_400_wl_nm']:.1f} nm"
        )
    summary_lines.extend([f"CSV: {csv_path}", f"PDF: {pdf_path}"])
    summary = "\n".join(summary_lines)
    return summary, result_df


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Compute thin-film absorption via TMM.")
    parser.add_argument(
        "nk_file",
        nargs="?",
        help="CompleteEASE nk text file (optional; opens file dialog if omitted).",
    )
    parser.add_argument(
        "thickness_nm",
        nargs="?",
        type=float,
        help="Film thickness in nm (optional; prompts if omitted).",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    nk_path = args.nk_file
    thickness_nm = args.thickness_nm

    if nk_path is None:
        nk_path = choose_nk_file()
        if not nk_path:
            print("No file selected.")
            return 1

    if thickness_nm is None:
        try:
            thickness_nm = ask_thickness_nm()
        except ValueError as exc:
            if messagebox is not None:
                root = tk.Tk()
                root.withdraw()
                messagebox.showerror("Invalid thickness", str(exc))
                root.destroy()
            else:
                print(exc, file=sys.stderr)
            return 1

        if thickness_nm is None:
            print("No thickness entered.")
            return 1

    if thickness_nm <= 0:
        print("Thickness must be positive.", file=sys.stderr)
        return 1

    try:
        summary, _ = run_analysis(nk_path, thickness_nm)
    except Exception as exc:
        if messagebox is not None:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("TMM calculation failed", str(exc))
            root.destroy()
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(summary)

    if messagebox is not None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("TMM absorption complete", summary)
        root.destroy()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
