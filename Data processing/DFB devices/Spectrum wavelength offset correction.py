import os
import re
import pandas as pd
import tkinter as tk
from tkinter import filedialog

'''This py is for shifting the spectrum wavelength to the correct value for 
multiple data files'''

# ==========================================
# Configuration — edit these before running
# ==========================================
WAVELENGTH_OFFSET = -1  # nm; positive = shift to longer wavelength, negative = shorter

# "both" = all spec CSVs | "standard" = non-transpose only | "transpose" = transpose only
TARGET_FORMAT = "both"

BASE_FOLDER = ""  # leave empty to pick folder via dialog on run


def find_files_fuzzy(folder, keywords):
    matches = []
    try:
        files = os.listdir(folder)
        for f in files:
            if f.startswith("."):
                continue
            if all(re.search(k, f, re.IGNORECASE) for k in keywords) and f.lower().endswith(".csv"):
                matches.append(os.path.join(folder, f))
    except Exception:
        return []
    return sorted(matches)


def correct_spectrum_file(f_path, offset):
    df_s = pd.read_csv(f_path, header=None)
    is_transpose = "transpose" in os.path.basename(f_path).lower()

    if is_transpose:
        wave = df_s.iloc[3:, -1].astype(float)
        df_s.iloc[3:, -1] = wave + offset
    else:
        wave = df_s.iloc[-1, 3:].astype(float)
        df_s.iloc[-1, 3:] = wave + offset

    df_s.to_csv(f_path, index=False, header=False)
    return is_transpose, len(wave)


def process_folder(folder, offset, target_format="both"):
    spec_files = find_files_fuzzy(folder, ["spec"])
    spec_files = [f for f in spec_files if "extract" not in f and "process" not in f]
    if not spec_files:
        return []

    if target_format == "transpose":
        spec_files = [f for f in spec_files if "transpose" in os.path.basename(f).lower()]
    elif target_format == "standard":
        spec_files = [f for f in spec_files if "transpose" not in os.path.basename(f).lower()]
    if not spec_files:
        return []

    results = []
    for f_path in spec_files:
        is_transpose, n_points = correct_spectrum_file(f_path, offset)
        fmt = "transpose" if is_transpose else "standard"
        results.append((f_path, fmt, n_points))
    return results


def main():
    base_folder = BASE_FOLDER.strip()
    if not base_folder:
        root = tk.Tk()
        root.withdraw()
        base_folder = filedialog.askdirectory(title="Select root folder (recursive)")
        root.destroy()
        if not base_folder:
            print("No folder selected. Exiting.")
            return

    if not os.path.isdir(base_folder):
        print(f"Invalid folder: {base_folder}")
        return

    print(f"Wavelength offset: {WAVELENGTH_OFFSET:+.3f} nm")
    print(f"Target format: {TARGET_FORMAT}")
    print(f"Scanning: {base_folder}\n")

    total_files = 0
    for current_root, _, files in os.walk(base_folder):
        if not any(f.lower().endswith(".csv") for f in files):
            continue

        results = process_folder(current_root, WAVELENGTH_OFFSET, TARGET_FORMAT)
        for f_path, fmt, n_points in results:
            total_files += 1
            rel = os.path.relpath(f_path, base_folder)
            print(f"  [{fmt}] {rel}  ({n_points} wavelength points, overwritten)")

    if total_files == 0:
        print("No spectrum CSV files found.")
    else:
        print(f"\nDone. Corrected {total_files} file(s).")


if __name__ == "__main__":
    main()
