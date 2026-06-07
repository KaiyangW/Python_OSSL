import importlib
import tkinter as tk
from tkinter import filedialog

import numpy as np
import pandas as pd
from scipy.io import loadmat
from pathlib import Path


def pick_mat_file() -> Path | None:
    root = tk.Tk()
    root.withdraw()
    selected = filedialog.askopenfilename(
        title="Select a TA .mat file to convert",
        filetypes=[("MATLAB files", "*.mat"), ("All files", "*.*")],
    )
    root.destroy()
    return Path(selected) if selected else None


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


def main() -> None:
    mat_path = pick_mat_file()
    if mat_path is None:
        print("No file selected.")
        return

    out = convert(mat_path)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
