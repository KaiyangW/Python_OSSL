import argparse
import json
import sys
from pathlib import Path
import ctypes

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

from onset_calculator import calculate_pl_onset_from_file
from plotter import (
    plot_pl_dual_onset_comparison,
    plot_pl_dual_onset_comparison_energy,
    plot_pl_onset_validation,
    plot_pl_onset_validation_energy,
    wavelength_nm_to_energy_ev,
)

# Analysis defaults (adjust here or extend with argparse later if needed)
BASELINE_REGION = 0.10  # first 10% of sorted spectrum as non-emitting background
WINDOW_LENGTH = 11
POLYORDER = 3
SAVE_DPI = 600

SETTINGS_FILE = Path(__file__).resolve().parent / "pl_onset_last_path.json"


def get_last_directory(fallback=None):
    """Return the last folder used in the file dialog, or a sensible default."""
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
    """Remember the parent folder of the selected PL file for the next run."""
    directory = str(Path(path).resolve().parent)
    try:
        with SETTINGS_FILE.open("w", encoding="utf-8") as handle:
            json.dump({"last_dir": directory}, handle, indent=2)
    except OSError:
        pass


def choose_pl_csv(initial_dir=None, title="Select PL spectrum (CSV)"):
    """Open a file dialog and return the selected PL CSV path, or None."""
    if filedialog is None:
        raise RuntimeError("Tkinter is not available; pass the CSV path on the command line.")

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    kwargs = {
        "title": title,
        "filetypes": [("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*.*")],
    }
    kwargs["initialdir"] = initial_dir or get_last_directory()

    filepath = filedialog.askopenfilename(**kwargs)
    root.destroy()

    if filepath:
        save_last_directory(filepath)

    return filepath or None


def choose_dual_pl_csv(initial_dir=None):
    """Select fluorescence and phosphorescence CSV files sequentially."""
    flu_path = choose_pl_csv(
        initial_dir=initial_dir,
        title="Select fluorescence PL spectrum (CSV)",
    )
    if not flu_path:
        return None, None

    phos_path = choose_pl_csv(
        initial_dir=get_last_directory(),
        title="Select phosphorescence PL spectrum (CSV)",
    )
    if not phos_path:
        return flu_path, None

    return flu_path, phos_path


def ask_analysis_mode():
    """Ask whether to run single-file or dual fluorescence/phosphorescence analysis."""
    if messagebox is None:
        return "single"

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    use_dual = messagebox.askyesno(
        "PL onset analysis",
        "Compare fluorescence and phosphorescence?\n\n"
        "Yes — select two files and plot both onsets with their difference.\n"
        "No — analyse a single PL spectrum (original mode).",
    )
    root.destroy()
    return "dual" if use_dual else "single"


def output_wavelength_svg_path(input_path):
    """``spectrum.csv`` -> ``spectrum_onset.svg`` in the same folder."""
    path = Path(input_path)
    return path.with_name(f"{path.stem}_onset.svg")


def output_energy_svg_path(input_path):
    """``spectrum.csv`` -> ``spectrum_onset_energy.svg`` in the same folder."""
    path = Path(input_path)
    return path.with_name(f"{path.stem}_onset_energy.svg")


def dual_output_wavelength_svg_path(flu_path, phos_path):
    """``flu.csv`` + ``phos.csv`` -> ``flu_vs_phos_onset_diff.svg`` next to flu file."""
    flu = Path(flu_path)
    phos = Path(phos_path)
    return flu.with_name(f"{flu.stem}_vs_{phos.stem}_onset_diff.svg")


def dual_output_energy_svg_path(flu_path, phos_path):
    """``flu.csv`` + ``phos.csv`` -> ``flu_vs_phos_onset_diff_energy.svg`` next to flu file."""
    flu = Path(flu_path)
    phos = Path(phos_path)
    return flu.with_name(f"{flu.stem}_vs_{phos.stem}_onset_diff_energy.svg")


def _calculate_onset(csv_path):
    return calculate_pl_onset_from_file(
        csv_path,
        baseline_region=BASELINE_REGION,
        window_length=WINDOW_LENGTH,
        polyorder=POLYORDER,
    )


def print_onset_summary(csv_path, result):
    """Print onset details for one spectrum."""
    print(f"File:        {Path(csv_path).name}")
    print(f"Method:      {result['method']}")
    onset_nm = result["onset_x"]
    onset_ev = result.get("onset_energy_ev", wavelength_nm_to_energy_ev(onset_nm))
    print(f"Onset:       {onset_nm:.4f} nm  ({onset_ev:.4f} eV)")
    if result.get("calculation_domain") == "energy":
        print("Calculated:  energy domain with Jacobian I(E) = I(lambda) * lambda^2 / hc")
        print(f"Baseline:    {result['baseline_y_energy']:.6g} in I(E)")
        print(
            "Tangent @:   "
            f"E = {result['edge_energy_ev']:.6g} eV "
            f"({result['edge_x']:.6g} nm), "
            f"slope = {result['tangent_slope_energy']:.6g} dI(E)/dE"
        )
    else:
        print(f"Baseline:    {result['baseline_y']:.6g}")
        print(f"Tangent @:   x = {result['edge_x']:.6g},  slope = {result['tangent_slope']:.6g}")
    if "source_file" in result:
        meta = result["source_file"]
        print(f"Data points: {meta['n_points']} (numeric rows from line {meta['first_numeric_line']})")


def run_onset_analysis(csv_path, show_plot=True):
    """
    Calculate PL onset and save validation plots.

    Saves wavelength-domain and energy-domain validation plots as SVG.

    Returns
    -------
    dict
        Onset result dictionary from calculate_pl_onset_from_file.
    """
    csv_path = Path(csv_path).resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(f"File not found: {csv_path}")

    result = _calculate_onset(csv_path)
    wavelength_svg_path = output_wavelength_svg_path(csv_path)
    energy_svg_path = output_energy_svg_path(csv_path)

    plot_pl_onset_validation(
        onset_result=result,
        save_path=str(wavelength_svg_path),
        dpi=SAVE_DPI,
        show=show_plot,
    )
    plot_pl_onset_validation_energy(
        onset_result=result,
        save_path=str(energy_svg_path),
        dpi=SAVE_DPI,
        show=False,
    )

    print("\n" + "=" * 60)
    print_onset_summary(csv_path, result)
    print(f"Saved SVG (wavelength): {wavelength_svg_path}")
    print(f"Saved SVG (energy):     {energy_svg_path}")
    print("=" * 60 + "\n")

    return result


def run_dual_onset_analysis(flu_path, phos_path, show_plot=True):
    """
    Calculate onsets for fluorescence and phosphorescence, then plot and report their difference.

    Returns
    -------
    tuple
        (flu_result, phos_result, comparison_summary)
    """
    flu_path = Path(flu_path).resolve()
    phos_path = Path(phos_path).resolve()

    if not flu_path.is_file():
        raise FileNotFoundError(f"Fluorescence file not found: {flu_path}")
    if not phos_path.is_file():
        raise FileNotFoundError(f"Phosphorescence file not found: {phos_path}")

    flu_result = _calculate_onset(flu_path)
    phos_result = _calculate_onset(phos_path)

    wavelength_svg_path = dual_output_wavelength_svg_path(flu_path, phos_path)
    energy_svg_path = dual_output_energy_svg_path(flu_path, phos_path)
    _, _, comparison = plot_pl_dual_onset_comparison(
        flu_result,
        phos_result,
        save_path=str(wavelength_svg_path),
        dpi=SAVE_DPI,
        show=show_plot,
    )
    plot_pl_dual_onset_comparison_energy(
        flu_result,
        phos_result,
        save_path=str(energy_svg_path),
        dpi=SAVE_DPI,
        show=False,
    )

    print("\n" + "=" * 60)
    print("Fluorescence / phosphorescence onset comparison")
    print("-" * 60)
    print_onset_summary(flu_path, flu_result)
    print("-" * 60)
    print_onset_summary(phos_path, phos_result)
    print("-" * 60)
    print(f"Δλ (flu − phos):  {comparison['delta_nm']:.4f} nm")
    print(f"ΔE (flu − phos):  {comparison['delta_ev']:.4f} eV")
    print(f"Saved SVG (wavelength): {wavelength_svg_path}")
    print(f"Saved SVG (energy):     {energy_svg_path}")
    print("=" * 60 + "\n")

    return flu_result, phos_result, comparison


def _show_error(title, message):
    if messagebox is None:
        return
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(title, message)
    root.destroy()


def parse_args(argv):
    parser = argparse.ArgumentParser(description="PL onset detection from spectrometer CSV files.")
    parser.add_argument(
        "--dual",
        action="store_true",
        help="Compare fluorescence and phosphorescence (two files, one combined plot).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional CSV path(s): one file for single mode, two files with --dual.",
    )
    return parser.parse_args(argv)


def main():
    script_dir = Path(__file__).resolve().parent
    args = parse_args(sys.argv[1:])

    try:
        if args.dual:
            if len(args.paths) >= 2:
                flu_path, phos_path = args.paths[0], args.paths[1]
                save_last_directory(flu_path)
                save_last_directory(phos_path)
            elif len(args.paths) == 1:
                raise ValueError("--dual requires two CSV paths, or omit paths to use file dialogs.")
            else:
                flu_path, phos_path = choose_dual_pl_csv(initial_dir=get_last_directory(script_dir))
                if not flu_path:
                    print("No fluorescence file selected. Exiting.")
                    return
                if not phos_path:
                    print("No phosphorescence file selected. Exiting.")
                    return

            run_dual_onset_analysis(flu_path, phos_path, show_plot=True)
            return

        if len(args.paths) == 1:
            run_onset_analysis(args.paths[0], show_plot=True)
            return

        if len(args.paths) > 1:
            raise ValueError("Pass one CSV path for single mode, or use --dual with two paths.")

        mode = ask_analysis_mode()
        if mode == "dual":
            flu_path, phos_path = choose_dual_pl_csv(initial_dir=get_last_directory(script_dir))
            if not flu_path:
                print("No fluorescence file selected. Exiting.")
                return
            if not phos_path:
                print("No phosphorescence file selected. Exiting.")
                return
            run_dual_onset_analysis(flu_path, phos_path, show_plot=True)
            return

        csv_path = choose_pl_csv(initial_dir=get_last_directory(script_dir))
        if not csv_path:
            print("No file selected. Exiting.")
            return
        run_onset_analysis(csv_path, show_plot=True)

    except Exception as exc:
        _show_error("PL onset analysis failed", str(exc))
        raise


if __name__ == "__main__":
    main()
