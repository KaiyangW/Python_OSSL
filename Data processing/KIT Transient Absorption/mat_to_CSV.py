import concurrent.futures
import importlib
import os
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import pandas as pd
from scipy.io import loadmat
from pathlib import Path

def read_mat_header(mat_path: Path) -> bytes:
    with open(mat_path, "rb") as fh:
        return fh.read(128)


def is_v73(header: bytes) -> bool:
    # v7.3 files are HDF5; their text header still starts with "MATLAB 7.3"
    return b"MATLAB 7.3" in header


def looks_like_cloud_placeholder(mat_path: Path, header: bytes) -> bool:
    # Google Drive / OneDrive online-only files are tiny and lack the MAT signature.
    size = mat_path.stat().st_size
    return size < 1024 or not header.startswith(b"MATLAB")


def _get(obj, name):
    # Works for scipy structs (attributes) and mat73/mat4py dicts (keys).
    if isinstance(obj, dict):
        return obj[name]
    return getattr(obj, name)


def load_with_mat4py(mat_path: Path):
    """Lenient pure-Python MAT v5 reader.

    LabVIEW (and some other non-MATLAB writers) tag name/char fields as
    miUINT8/miUTF16 instead of the miINT8/miUTF8 the spec demands, which makes
    scipy raise 'Expecting miINT8 as data type'. mat4py is pure Python, so we
    disable its strict type enforcement and let it unpack by the actual tag.
    """
    ml = importlib.import_module("mat4py.loadmat")
    original_read_elements = ml.read_elements

    def lenient_read_elements(fd, endian, mtps, is_name=False):
        return original_read_elements(fd, endian, None, is_name=is_name)

    ml.read_elements = lenient_read_elements
    try:
        return ml.loadmat(str(mat_path))
    finally:
        ml.read_elements = original_read_elements


def load_ta_data(mat_path: Path):
    header = read_mat_header(mat_path)

    if looks_like_cloud_placeholder(mat_path, header):
        raise RuntimeError(
            f"'{mat_path}' does not look like a real MAT file (size "
            f"{mat_path.stat().st_size} bytes). If it lives in Google Drive/OneDrive, "
            "right-click it and choose 'Make available offline' / 'Always keep on this "
            "device', wait for it to download, then retry."
        )

    if is_v73(header):
        import mat73
        mat = mat73.loadmat(str(mat_path))
    else:
        try:
            mat = loadmat(mat_path, struct_as_record=False, squeeze_me=True)
        except (TypeError, ValueError, NotImplementedError):
            # Non-standard MAT v5 (e.g. written by LabVIEW) -> lenient reader.
            mat = load_with_mat4py(mat_path)

    return mat["TA_data"]


def convert(mat_path: Path) -> Path:
    TA_data = load_ta_data(mat_path)

    times = np.asarray(_get(TA_data, "Stage_positions"), dtype=float).ravel()        # length T
    wavelengths = np.asarray(_get(TA_data, "wavelength_calibration"), dtype=float)[1, :]  # MATLAB row 2
    dTT = np.asarray(_get(TA_data, "dT_T"), dtype=float)

    # ensure dTT is [W x T]: wavelengths down rows, times across columns
    if dTT.shape[0] != wavelengths.size:
        dTT = dTT.T

    # first column = wavelengths (nm), first row = times (s), corner labels both axes
    df = pd.DataFrame(
        dTT,
        index=pd.Index(wavelengths, name="Wavelength (nm) \\ Time (s)"),
        columns=times,
    )

    out = mat_path.with_suffix(".csv")
    df.to_csv(out)
    return out


def process_folder(folder_data: tuple[str, list[str]]) -> tuple[str, int]:
    folder_path, file_list = folder_data
    converted_count = 0

    for filepath in file_list:
        mat_path = Path(filepath)
        try:
            out = convert(mat_path)
            converted_count += 1
            print(f"Wrote {out}")
        except Exception as e:
            print(f"Failed to convert {mat_path.name}: {e}")

    return folder_path, converted_count


def main() -> None:
    root = tk.Tk()
    root.withdraw()

    target_folder = filedialog.askdirectory(title="Select Folder to Scan for MAT Files")

    if not target_folder:
        print("No folder selected. Exiting.")
        root.destroy()
        return

    print(f"Scanning directory and subdirectories:\n{target_folder}\n")

    # Group files by their parent directory, matching the batch loop style used
    # by Draw_mutiple_graphs.py.
    folder_dict: dict[str, list[str]] = {}
    for current_root, dirs, files in os.walk(target_folder):
        for file in files:
            if file.lower().endswith(".mat"):
                if current_root not in folder_dict:
                    folder_dict[current_root] = []
                folder_dict[current_root].append(os.path.join(current_root, file))

    total_folders = len(folder_dict)
    if total_folders == 0:
        print("No MAT files found.")
        root.destroy()
        return

    print(f"Found MAT files in {total_folders} folders. Starting parallel processing...")

    folder_tasks = list(folder_dict.items())
    converted_count = 0

    with concurrent.futures.ProcessPoolExecutor() as executor:
        results = executor.map(process_folder, folder_tasks)

        for folder_path, folder_count in results:
            converted_count += folder_count

    print(f"\nDone! Created {converted_count} CSV files.")

    root.update()
    messagebox.showinfo(
        "Batch Conversion Complete",
        f"Successfully generated {converted_count} CSV files.",
        parent=root,
    )
    root.destroy()


if __name__ == "__main__":
    main()
