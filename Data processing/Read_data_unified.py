"""Read_data_unified.py
================================================================================
A single, shared data-reading layer for every script in this "Data processing"
folder.  The goal: stop re-writing "find where the numbers start, guess the
encoding/delimiter, pull two columns" in every script.  Instead, all scripts
import this module and call one of a few high-level functions that always return
the SAME unified objects (``Spectrum`` / ``Grid`` / ``FolderBundle``).

--------------------------------------------------------------------------------
DESIGN (read this before extending)
--------------------------------------------------------------------------------
We normalise data into a few *shapes*, not per-software formats:

    * Spectrum  -> a single XY curve            (TRPL decay, PL, UV-Vis, n,k ...)
    * Grid      -> a 2D matrix with two axes    (TA time x wavelength, ASE spec,
                                                 beam profiler image ...)
    * FolderBundle -> several files combined     (ASE "energy"+"spec"+"integ")

Each lab program is just a *handler* for one of those shapes.  Handlers are kept
in registries (``_XY_HANDLERS`` / ``_GRID_HANDLERS``).  A handler has:
    name        - short id, also usable as the manual ``format=`` override
    fingerprint - cheap test on a peek of the file -> match score (0 = no match)
    reader      - function that returns a Spectrum / Grid

Auto-detection = run all fingerprints, pick the best score, fall back to the
"generic" handler.  You can always bypass detection with ``format="..."``.

To support a NEW program in the lab you normally:
    1. save one example file,
    2. add one handler function + register it,
    3. (optionally) give it a fingerprint.
No existing script needs to change.

--------------------------------------------------------------------------------
PUBLIC API
--------------------------------------------------------------------------------
    read_xy(path, format=None, encoding=None, usecols=(0, 1)) -> Spectrum
    read_grid(path, layout="auto") -> Grid
    read_workbook(path, sheet=None) -> pandas object        (needs pandas)
    read_mat(path) -> dict                                  (needs scipy/mat73)
    read_folder(folder, roles, ...) -> FolderBundle
    read_auto(path) -> Spectrum | Grid                      (guesses the shape)

    list_formats() -> dict      # introspection: what handlers exist
    register_xy_handler(...) / register_grid_handler(...)   # extend at runtime

Only ``numpy`` is required.  ``pandas`` / ``scipy`` / ``mat73`` / ``mat4py`` /
``openpyxl`` are imported lazily, so importing this module never fails because of
an optional dependency.

Run ``python Read_data_unified.py <file>`` to inspect what a file is detected as.
"""

from __future__ import annotations

import csv
import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Union

import numpy as np

PathLike = Union[str, os.PathLike]

# Encoding fallback order used everywhere.  Mirrors what the individual scripts
# already do (utf-8-sig for BOM'd instrument CSVs, windows-1252/cp1252 for the
# "µ"/"°" characters that fluorimeters and JASCO emit, latin-1 as last resort).
DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8-sig", "utf-8", "cp1252", "latin-1")

# How many lines/cells we peek at when fingerprinting a file.
_PEEK_LINES = 40


# =============================================================================
#  Unified return types
# =============================================================================
@dataclass
class Spectrum:
    """A single XY curve plus metadata.

    Attributes
    ----------
    x, y : np.ndarray
        1D float arrays of equal length (NaNs already dropped).
    meta : dict
        Free-form provenance, e.g. ``source_format``, ``encoding``,
        ``data_start_row``, ``scan_type``, ``x_name``, ``y_name``, ``path``.
    """

    x: np.ndarray
    y: np.ndarray
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return int(self.x.shape[0])

    def as_dataframe(self):
        """Return a 2-column pandas DataFrame (lazy import)."""
        pd = _import_pandas()
        xn = self.meta.get("x_name", "x")
        yn = self.meta.get("y_name", "y")
        return pd.DataFrame({xn: self.x, yn: self.y})

    def __repr__(self) -> str:
        fmt = self.meta.get("source_format", "?")
        return f"Spectrum(n={len(self)}, format={fmt!r})"


@dataclass
class Grid:
    """A 2D dataset with explicit row and column axes.

    ``data`` has shape ``(len(row_values), len(col_values))``.

    For TA: rows = wavelengths (nm), cols = times (s), data = dT/T.
    For ASE spec: rows = frames/shots, cols = wavelengths (nm), data = intensity.
    For beam profiler: rows/cols = pixel indices, data = intensity.
    """

    row_values: np.ndarray
    col_values: np.ndarray
    data: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.data.shape

    def as_dataframe(self):
        """Return a DataFrame indexed by row_values, columns = col_values."""
        pd = _import_pandas()
        return pd.DataFrame(self.data, index=self.row_values, columns=self.col_values)

    def __repr__(self) -> str:
        fmt = self.meta.get("source_format", "?")
        return f"Grid(shape={self.data.shape}, format={fmt!r})"


@dataclass
class FolderBundle:
    """Several related files from one measurement folder.

    ``items`` maps a role name (e.g. ``"energy"``, ``"spec"``, ``"integ"``) to a
    ``Spectrum`` or ``Grid``.  ``meta["files"]`` records which file filled which
    role.
    """

    items: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def __getitem__(self, key: str):
        return self.items[key]

    def get(self, key: str, default=None):
        return self.items.get(key, default)

    def __repr__(self) -> str:
        return f"FolderBundle(roles={list(self.items)})"


class DataReadError(Exception):
    """Raised when a file cannot be read by any handler."""


# =============================================================================
#  Lazy optional-dependency importers
# =============================================================================
def _import_pandas():
    try:
        return importlib.import_module("pandas")
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DataReadError(
            "pandas is required for this operation. Install it with 'pip install pandas'."
        ) from exc


# =============================================================================
#  Shared low-level text helpers (the part every text handler reuses)
# =============================================================================
def read_text_lines(
    path: PathLike,
    encodings: Sequence[str] = DEFAULT_ENCODINGS,
    max_lines: Optional[int] = None,
) -> tuple[list[str], str]:
    """Read a text file, trying several encodings in order.

    Returns ``(lines, encoding_used)``.  The final encoding always succeeds
    because the last attempt uses ``errors="replace"``.
    """
    p = Path(path)
    last_err: Optional[Exception] = None
    for enc in encodings:
        try:
            with p.open("r", encoding=enc, newline="") as fh:
                if max_lines is None:
                    return fh.read().splitlines(), enc
                lines = []
                for i, line in enumerate(fh):
                    if i >= max_lines:
                        break
                    lines.append(line.rstrip("\r\n"))
                return lines, enc
        except UnicodeDecodeError as exc:
            last_err = exc
            continue
    # Last resort: never fail on encoding.
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        if max_lines is None:
            return fh.read().splitlines(), "utf-8(replace)"
        lines = []
        for i, line in enumerate(fh):
            if i >= max_lines:
                break
            lines.append(line.rstrip("\r\n"))
        return lines, "utf-8(replace)"


def sniff_delimiter(sample_lines: Sequence[str], default: str = ",") -> str:
    """Guess the column delimiter from a few sample lines.

    Order of preference matches what the instruments emit: comma, tab, then
    semicolon, then any run of whitespace (returned as the sentinel ``"\\s+"``).
    """
    text = "\n".join(line for line in sample_lines if line.strip())
    if not text:
        return default
    try:
        dialect = csv.Sniffer().sniff(text, delimiters=",\t;")
        return dialect.delimiter
    except csv.Error:
        pass
    # Manual tally on the first non-empty line.
    for line in sample_lines:
        if not line.strip():
            continue
        if "," in line:
            return ","
        if "\t" in line:
            return "\t"
        if ";" in line:
            return ";"
        if len(line.split()) >= 2:
            return "\\s+"  # sentinel meaning "split on whitespace"
        break
    return default


def _split(line: str, delimiter: str) -> list[str]:
    if delimiter == "\\s+":
        return line.split()
    if delimiter == ",":
        # Normalise tabs to commas first so mixed exports still split (matches
        # the line.replace('\t', ',') trick used across the scripts).
        return [c.strip() for c in line.replace("\t", ",").split(",")]
    return [c.strip() for c in line.split(delimiter)]


def _is_float(token: str) -> bool:
    if token is None:
        return False
    t = token.strip()
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def find_first_numeric_row(
    lines: Sequence[str],
    delimiter: str,
    n_required: int = 2,
    comment: Optional[str] = None,
) -> int:
    """Return the index of the first line whose first ``n_required`` fields are
    all numeric.  Returns -1 if none found.  This is the single piece of logic
    duplicated in nearly every original loader.
    """
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if comment and line.lstrip().startswith(comment):
            continue
        parts = _split(line, delimiter)
        if len(parts) < n_required:
            continue
        if all(_is_float(parts[k]) for k in range(n_required)):
            return i
    return -1


def _peek(path: PathLike) -> tuple[list[str], str]:
    """Read just the first few lines for fingerprinting."""
    return read_text_lines(path, max_lines=_PEEK_LINES)


# =============================================================================
#  XY handlers
# =============================================================================
# A handler is described by (name, fingerprint, reader).
#   fingerprint(path, lines, encoding) -> float score in [0, 1]; 0 = no match.
#   reader(path, **kw) -> Spectrum
@dataclass
class _XYHandler:
    name: str
    fingerprint: Callable[[PathLike, list[str], str], float]
    reader: Callable[..., Spectrum]
    description: str = ""


_XY_HANDLERS: "dict[str, _XYHandler]" = {}


def register_xy_handler(handler: _XYHandler) -> None:
    _XY_HANDLERS[handler.name] = handler


def _numeric_two_columns(path, lines, delimiter, encoding, usecols, extra_meta):
    """Core text -> Spectrum: locate data start, parse two numeric columns.

    Implemented without pandas so it works in any environment; falls back
    gracefully on ragged rows.
    """
    comment = extra_meta.get("comment")
    start = find_first_numeric_row(lines, delimiter, n_required=max(usecols) + 1
                                   if usecols else 2, comment=comment)
    if start < 0:
        # Relax: only require the two requested columns to exist as floats.
        start = find_first_numeric_row(lines, delimiter, n_required=2, comment=comment)
    if start < 0:
        raise DataReadError(
            f"Could not locate a numeric data row in {Path(path).name}."
        )

    ci, cj = (usecols[0], usecols[1]) if usecols else (0, 1)
    xs: list[float] = []
    ys: list[float] = []
    for line in lines[start:]:
        if not line.strip():
            continue
        if comment and line.lstrip().startswith(comment):
            continue
        parts = _split(line, delimiter)
        if len(parts) <= max(ci, cj):
            continue
        if _is_float(parts[ci]) and _is_float(parts[cj]):
            xs.append(float(parts[ci]))
            ys.append(float(parts[cj]))

    if len(xs) < 2:
        raise DataReadError(
            f"Found fewer than 2 valid data rows in {Path(path).name}."
        )

    meta = {
        "path": str(path),
        "encoding": encoding,
        "delimiter": delimiter,
        "data_start_row": start,
        "usecols": (ci, cj),
    }
    meta.update(extra_meta)
    return Spectrum(np.asarray(xs, float), np.asarray(ys, float), meta)


def _read_generic_xy(path, encoding=None, usecols=(0, 1), **_):
    encs = (encoding,) if encoding else DEFAULT_ENCODINGS
    lines, enc = read_text_lines(path, encs)
    delim = sniff_delimiter(lines[:_PEEK_LINES])
    return _numeric_two_columns(
        path, lines, delim, enc, usecols,
        {"source_format": "generic_xy", "x_name": "x", "y_name": "y"},
    )


def _read_fluoracle(path, encoding=None, usecols=(0, 1), **_):
    """Edinburgh Instruments Fluoracle TRPL/decay export (time, counts)."""
    encs = (encoding,) if encoding else ("utf-8-sig",) + DEFAULT_ENCODINGS
    lines, enc = read_text_lines(path, encs)
    delim = sniff_delimiter(lines[:_PEEK_LINES])
    return _numeric_two_columns(
        path, lines, delim, enc, usecols,
        {"source_format": "fluoracle_decay", "x_name": "Time", "y_name": "Counts"},
    )


def _read_oceanoptics(path, encoding=None, usecols=(0, 1), **_):
    """Ocean Optics spectrometer CSV: '#'-commented header, wavelength,intensity."""
    encs = (encoding,) if encoding else DEFAULT_ENCODINGS
    lines, enc = read_text_lines(path, encs)
    delim = sniff_delimiter([l for l in lines[:_PEEK_LINES] if not l.lstrip().startswith("#")])
    return _numeric_two_columns(
        path, lines, delim, enc, usecols,
        {"source_format": "oceanoptics_csv", "comment": "#",
         "x_name": "Wavelength_nm", "y_name": "Intensity"},
    )


def _read_jasco_uvvis(path, encoding=None, usecols=(0, 1), **_):
    """JASCO UV-Vis (.csv/.txt): metadata header then an 'XYDATA' marker line,
    after which two columns of wavelength,absorbance follow."""
    encs = (encoding,) if encoding else ("windows-1252", "cp1252") + DEFAULT_ENCODINGS
    lines, enc = read_text_lines(path, encs)
    start = -1
    for i, line in enumerate(lines):
        if "xydata" in line.lower():
            start = i + 1
            break
    if start < 0:
        # Fall back to generic numeric detection.
        delim = sniff_delimiter(lines[:_PEEK_LINES])
        return _numeric_two_columns(
            path, lines, delim, enc, usecols,
            {"source_format": "jasco_uvvis", "scan_type": "uv-vis",
             "x_name": "Wavelength_nm", "y_name": "Absorbance"},
        )
    body = lines[start:]
    delim = sniff_delimiter(body[:_PEEK_LINES])
    spec = _numeric_two_columns(
        path, body, delim, enc, usecols,
        {"source_format": "jasco_uvvis", "scan_type": "uv-vis",
         "x_name": "Wavelength_nm", "y_name": "Absorbance"},
    )
    spec.meta["data_start_row"] = start + spec.meta.get("data_start_row", 0)
    return spec


def _read_whitespace_xy(path, encoding=None, usecols=(0, 1), **_):
    """Whitespace- or comma-separated XY (e.g. ellipsometer n,k files where the
    first lines are titles like 'Opt. Const. of B-Spline vs. nm')."""
    encs = (encoding,) if encoding else ("utf-8-sig",) + DEFAULT_ENCODINGS
    lines, enc = read_text_lines(path, encs)
    # Force whitespace splitting (commas are normalised to spaces).
    norm = [l.replace(",", " ") for l in lines]
    return _numeric_two_columns(
        path, norm, "\\s+", enc, usecols,
        {"source_format": "whitespace_xy", "x_name": "x", "y_name": "y"},
    )


# ---- XY fingerprints --------------------------------------------------------
def _fp_jasco(path, lines, encoding) -> float:
    low = "\n".join(lines).lower()
    score = 0.0
    if "xydata" in low:
        score += 0.6
    if "jasco" in low:
        score += 0.4
    return min(score, 1.0)


def _fp_oceanoptics(path, lines, encoding) -> float:
    head = "\n".join(lines[:15]).lower()
    score = 0.0
    if any(l.lstrip().startswith("#") for l in lines[:15]):
        score += 0.4
    if "ocean" in head or "spectrasuite" in head or "oceanview" in head:
        score += 0.5
    return min(score, 1.0)


def _fp_fluoracle(path, lines, encoding) -> float:
    head = "\n".join(lines[:15]).lower()
    if "fluoracle" in head or "labels" in head and "counts" in head:
        return 0.6
    return 0.0


def _fp_whitespace(path, lines, encoding) -> float:
    # Looks like whitespace columns with no commas, and a non-numeric title row.
    for line in lines[:_PEEK_LINES]:
        if not line.strip():
            continue
        if "," in line:
            return 0.0
        parts = line.split()
        if len(parts) >= 2 and all(_is_float(p) for p in parts[:2]):
            return 0.35
    return 0.0


def _fp_generic(path, lines, encoding) -> float:
    # Always a weak match so it acts as the fallback.
    delim = sniff_delimiter(lines[:_PEEK_LINES])
    return 0.2 if find_first_numeric_row(lines, delim, 2) >= 0 else 0.0


register_xy_handler(_XYHandler("generic_xy", _fp_generic, _read_generic_xy,
                               "Two numeric columns, auto encoding+delimiter."))
register_xy_handler(_XYHandler("fluoracle_decay", _fp_fluoracle, _read_fluoracle,
                               "Edinburgh Fluoracle TRPL (time, counts)."))
register_xy_handler(_XYHandler("oceanoptics_csv", _fp_oceanoptics, _read_oceanoptics,
                               "Ocean Optics spectrometer ('#' header)."))
register_xy_handler(_XYHandler("jasco_uvvis", _fp_jasco, _read_jasco_uvvis,
                               "JASCO UV-Vis ('XYDATA' marker)."))
register_xy_handler(_XYHandler("whitespace_xy", _fp_whitespace, _read_whitespace_xy,
                               "Whitespace-separated XY (e.g. n,k files)."))


# =============================================================================
#  Grid handlers
# =============================================================================
@dataclass
class _GridHandler:
    name: str
    fingerprint: Callable[[PathLike, "object"], float]
    reader: Callable[..., Grid]
    description: str = ""


_GRID_HANDLERS: "dict[str, _GridHandler]" = {}


def register_grid_handler(handler: _GridHandler) -> None:
    _GRID_HANDLERS[handler.name] = handler


def _read_raw_table(path: PathLike):
    """Read an arbitrary CSV/Excel into a raw 2D object array (no header).

    Returns a numpy object array of strings/values; used by grid handlers that
    need positional access to metadata rows/columns.
    """
    p = Path(path)
    suf = p.suffix.lower()
    if suf in (".xlsx", ".xls", ".xlsm"):
        pd = _import_pandas()
        df = pd.read_excel(p, header=None)
        return df.to_numpy(), "excel"
    # CSV / txt
    lines, enc = read_text_lines(p)
    delim = sniff_delimiter(lines[:_PEEK_LINES])
    rows = [_split(line, delim) for line in lines if line.strip() != ""]
    width = max((len(r) for r in rows), default=0)
    arr = np.empty((len(rows), width), dtype=object)
    arr[:] = ""
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    return arr, enc


def _to_float_array(values) -> np.ndarray:
    out = np.full(len(values), np.nan, dtype=float)
    for i, v in enumerate(values):
        try:
            out[i] = float(str(v).strip())
        except (ValueError, TypeError):
            out[i] = np.nan
    return out


def _read_ta_grid(path, **_):
    """KIT Transient-Absorption grid: corner label, row0 = times (s),
    col0 = wavelengths (nm), body = dT/T [wavelength x time]."""
    raw, enc = _read_raw_table(path)
    if raw.shape[0] < 2 or raw.shape[1] < 2:
        raise DataReadError(f"{Path(path).name} is too small to be a TA grid.")
    times = _to_float_array(raw[0, 1:])
    waves = _to_float_array(raw[1:, 0])
    body = np.empty((raw.shape[0] - 1, raw.shape[1] - 1), dtype=float)
    for i in range(body.shape[0]):
        body[i, :] = _to_float_array(raw[i + 1, 1:])

    vt = np.isfinite(times)
    vw = np.isfinite(waves)
    if vt.sum() == 0 or vw.sum() == 0:
        raise DataReadError("Could not find numeric time/wavelength axes (TA).")
    data = body[np.ix_(vw, vt)]
    return Grid(
        waves[vw], times[vt], data,
        {"source_format": "ta_grid", "encoding": enc, "path": str(path),
         "row_label": "Wavelength_nm", "col_label": "Time_s", "layout": "ta"},
    )


def _read_ase_spec_matrix(path, transpose=None, meta_rows=3, **_):
    """ASE / DFB spectrum matrix.

    Normal layout : last row = wavelengths (from col 3 on), rows above = spectra
                    (intensity from col 3 on); first 3 columns are meta.
    Transpose     : last column = wavelengths (from row 3 on), columns before it
                    = spectra; first 3 rows are meta.
    Detection uses the filename ('transpose') unless ``transpose`` is given.
    """
    raw, enc = _read_raw_table(path)
    name = Path(path).name.lower()
    if transpose is None:
        transpose = "transpose" in name

    if transpose:
        waves = _to_float_array(raw[meta_rows:, -1])
        block = raw[meta_rows:, :-1]
        intensity_T = np.empty(block.shape, dtype=float)
        for j in range(block.shape[1]):
            intensity_T[:, j] = _to_float_array(block[:, j])
        intensity = intensity_T.T  # frames x wavelength
    else:
        waves = _to_float_array(raw[-1, meta_rows:])
        block = raw[:-1, meta_rows:]
        intensity = np.empty(block.shape, dtype=float)
        for i in range(block.shape[0]):
            intensity[i, :] = _to_float_array(block[i, :])

    frames = np.arange(intensity.shape[0], dtype=float)
    return Grid(
        frames, waves, intensity,
        {"source_format": "ase_spec_matrix", "encoding": enc, "path": str(path),
         "row_label": "Frame", "col_label": "Wavelength_nm",
         "layout": "ase_transpose" if transpose else "ase_normal",
         "meta_rows": meta_rows},
    )


def _read_beam_profile(path, header_lines=None, **_):
    """Beam-profiler image export: 'key,value' header (incl. PixelWidth/
    PixelHeight) followed by a 2D intensity matrix."""
    p = Path(path)
    lines, enc = read_text_lines(p)
    pixel_w = 1.0
    pixel_h = 1.0
    # Auto-detect header length: first line whose row is a long numeric vector.
    if header_lines is None:
        header_lines = 0
        for i, line in enumerate(lines[:50]):
            parts = _split(line, ",")
            numeric = [pp for pp in parts if _is_float(pp)]
            if len(parts) >= 4 and len(numeric) >= max(4, int(0.8 * len(parts))):
                header_lines = i
                break
    for line in lines[:header_lines]:
        parts = _split(line, ",")
        if len(parts) >= 2:
            key, val = parts[0].strip(), parts[1].strip()
            if key.lower() == "pixelwidth" and _is_float(val):
                pixel_w = float(val)
            elif key.lower() == "pixelheight" and _is_float(val):
                pixel_h = float(val)

    body = lines[header_lines:]
    delim = sniff_delimiter(body[:_PEEK_LINES])
    rows = []
    for line in body:
        if not line.strip():
            continue
        rows.append(_to_float_array(_split(line, delim)))
    if not rows:
        raise DataReadError(f"No matrix data found in {p.name}.")
    width = max(len(r) for r in rows)
    mat = np.full((len(rows), width), np.nan, dtype=float)
    for i, r in enumerate(rows):
        mat[i, : len(r)] = r
    mat = np.nan_to_num(mat)
    row_ax = np.arange(mat.shape[0], dtype=float) * pixel_h
    col_ax = np.arange(mat.shape[1], dtype=float) * pixel_w
    return Grid(
        row_ax, col_ax, mat,
        {"source_format": "beam_profile", "encoding": enc, "path": str(p),
         "row_label": "Y", "col_label": "X", "layout": "beam_profile",
         "pixel_width": pixel_w, "pixel_height": pixel_h,
         "header_lines": header_lines},
    )


# ---- Grid fingerprints ------------------------------------------------------
def _fp_beam_profile(path, raw_lines) -> float:
    head = "\n".join(raw_lines[:15]).lower()
    return 0.8 if ("pixelwidth" in head or "pixelheight" in head) else 0.0


def _fp_ta_grid(path, raw_lines) -> float:
    first = raw_lines[0] if raw_lines else ""
    low = first.lower()
    if "wavelength" in low and "time" in low:
        return 0.7
    return 0.0


def _fp_ase_spec(path, raw_lines) -> float:
    return 0.4 if "spec" in Path(path).name.lower() else 0.0


register_grid_handler(_GridHandler("beam_profile", _fp_beam_profile, _read_beam_profile,
                                   "Beam profiler image (PixelWidth/Height header)."))
register_grid_handler(_GridHandler("ta_grid", _fp_ta_grid, _read_ta_grid,
                                   "Transient absorption time x wavelength grid."))
register_grid_handler(_GridHandler("ase_spec_matrix", _fp_ase_spec, _read_ase_spec_matrix,
                                   "ASE/DFB spectrum matrix (normal/transpose)."))


# =============================================================================
#  Public API
# =============================================================================
def read_xy(
    path: PathLike,
    format: Optional[str] = None,
    encoding: Optional[str] = None,
    usecols: tuple[int, int] = (0, 1),
) -> Spectrum:
    """Read a single XY curve and return a :class:`Spectrum`.

    Parameters
    ----------
    path : file path.
    format : force a handler by name (see :func:`list_formats`); skip detection.
    encoding : force a text encoding; otherwise the fallback chain is tried.
    usecols : which two columns to take as (x, y).
    """
    p = Path(path)
    if not p.is_file():
        raise DataReadError(f"File not found: {p}")

    if format:
        if format not in _XY_HANDLERS:
            raise DataReadError(
                f"Unknown XY format {format!r}. Known: {sorted(_XY_HANDLERS)}"
            )
        return _XY_HANDLERS[format].reader(p, encoding=encoding, usecols=usecols)

    lines, enc = _peek(p)
    best_name, best_score = "generic_xy", -1.0
    for name, h in _XY_HANDLERS.items():
        try:
            score = float(h.fingerprint(p, lines, enc))
        except Exception:
            score = 0.0
        if score > best_score:
            best_name, best_score = name, score
    return _XY_HANDLERS[best_name].reader(p, encoding=encoding, usecols=usecols)


def read_grid(path: PathLike, layout: str = "auto", **kwargs) -> Grid:
    """Read a 2D dataset and return a :class:`Grid`.

    ``layout`` may be ``"auto"`` or a grid handler name (``"ta_grid"``,
    ``"ase_spec_matrix"``, ``"beam_profile"``).  Extra keyword arguments are
    passed through to the handler (e.g. ``transpose=True`` for ASE).
    """
    p = Path(path)
    if not p.is_file():
        raise DataReadError(f"File not found: {p}")

    if layout and layout != "auto":
        if layout not in _GRID_HANDLERS:
            raise DataReadError(
                f"Unknown grid layout {layout!r}. Known: {sorted(_GRID_HANDLERS)}"
            )
        return _GRID_HANDLERS[layout].reader(p, **kwargs)

    raw_lines, _ = _peek(p) if p.suffix.lower() not in (".xlsx", ".xls", ".xlsm") else ([], "")
    best_name, best_score = None, -1.0
    for name, h in _GRID_HANDLERS.items():
        try:
            score = float(h.fingerprint(p, raw_lines))
        except Exception:
            score = 0.0
        if score > best_score:
            best_name, best_score = name, score
    if not best_name or best_score <= 0.0:
        # No fingerprint matched: assume a generic TA-style numeric grid.
        return _read_ta_grid(p)
    return _GRID_HANDLERS[best_name].reader(p, **kwargs)


def read_workbook(path: PathLike, sheet: Optional[Union[str, int]] = None, **kwargs):
    """Read a processed Excel workbook (your own analysed outputs).

    Returns whatever pandas returns for the requested ``sheet`` (a DataFrame, or
    a dict of DataFrames when ``sheet=None``).  This is intentionally thin: these
    are stable, self-authored schemas, not instrument files to auto-detect.
    """
    pd = _import_pandas()
    p = Path(path)
    if not p.is_file():
        raise DataReadError(f"File not found: {p}")
    return pd.read_excel(p, sheet_name=sheet, **kwargs)


def read_mat(path: PathLike) -> dict:
    """Read a MATLAB ``.mat`` file, handling v5, v7.3 (HDF5) and LabVIEW-written
    non-standard v5 files via a scipy -> mat73 -> mat4py fallback chain.
    Returns the raw dict of variables.
    """
    p = Path(path)
    if not p.is_file():
        raise DataReadError(f"File not found: {p}")
    with p.open("rb") as fh:
        header = fh.read(128)
    if p.stat().st_size < 1024 or not header.startswith(b"MATLAB"):
        raise DataReadError(
            f"{p.name} does not look like a real MAT file (possibly a cloud "
            "placeholder). Make it available offline and retry."
        )
    if b"MATLAB 7.3" in header:
        mat73 = importlib.import_module("mat73")
        return mat73.loadmat(str(p))
    scipy_io = importlib.import_module("scipy.io")
    try:
        return scipy_io.loadmat(str(p), struct_as_record=False, squeeze_me=True)
    except (TypeError, ValueError, NotImplementedError):
        ml = importlib.import_module("mat4py.loadmat")
        original = ml.read_elements

        def lenient(fd, endian, mtps, is_name=False):
            return original(fd, endian, None, is_name=is_name)

        ml.read_elements = lenient
        try:
            return ml.loadmat(str(p))
        finally:
            ml.read_elements = original


def find_files_fuzzy(
    folder: PathLike,
    keywords: Iterable[str],
    exclude: Iterable[str] = (),
    exts: Iterable[str] = (".csv", ".txt", ".dat", ".xlsx", ".xls"),
) -> list[str]:
    """Return files in ``folder`` whose name contains ALL ``keywords`` (case
    insensitive) and none of ``exclude``.  Mirrors the ASE scripts' helper.
    """
    folder = Path(folder)
    kws = [k.lower() for k in keywords]
    exc = [e.lower() for e in exclude]
    out = []
    if not folder.is_dir():
        return out
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        low = entry.name.lower()
        if exts and entry.suffix.lower() not in exts:
            continue
        if all(k in low for k in kws) and not any(e in low for e in exc):
            out.append(str(entry))
    return out


def read_folder(
    folder: PathLike,
    roles: dict,
    default_shape: str = "xy",
) -> FolderBundle:
    """Read a measurement folder into a :class:`FolderBundle`.

    ``roles`` maps a role name to a spec dict, e.g.::

        roles = {
            "energy": {"keywords": ["energy"]},
            "spec":   {"keywords": ["spec"],
                       "exclude": ["extract", "process"], "shape": "grid"},
            "integ":  {"keywords": ["integ"]},
        }

    Each role's first matching file is read with :func:`read_xy` (shape "xy") or
    :func:`read_grid` (shape "grid").  Missing optional roles are skipped.
    """
    bundle = FolderBundle(meta={"folder": str(folder), "files": {}})
    for role, spec in roles.items():
        kws = spec.get("keywords", [role])
        exclude = spec.get("exclude", ())
        shape = spec.get("shape", default_shape)
        matches = find_files_fuzzy(folder, kws, exclude)
        if not matches:
            if spec.get("required", False):
                raise DataReadError(f"No file for required role {role!r} in {folder}")
            continue
        fpath = matches[0]
        bundle.meta["files"][role] = fpath
        if shape == "grid":
            bundle.items[role] = read_grid(fpath, layout=spec.get("layout", "auto"),
                                           **spec.get("kwargs", {}))
        else:
            bundle.items[role] = read_xy(fpath, format=spec.get("format"))
    return bundle


def read_auto(path: PathLike) -> Union[Spectrum, Grid]:
    """Guess whether ``path`` is an XY curve or a 2D grid, then read it.

    Heuristic: a grid fingerprint match, or a wide first numeric row (>3 numeric
    columns), routes to :func:`read_grid`; otherwise :func:`read_xy`.
    """
    p = Path(path)
    if not p.is_file():
        raise DataReadError(f"File not found: {p}")
    suf = p.suffix.lower()
    if suf in (".mat",):
        raise DataReadError("Use read_mat() for .mat files.")

    if suf in (".xlsx", ".xls", ".xlsm"):
        # Could be a workbook or a TA grid; try grid, leave workbook to caller.
        try:
            return read_grid(p)
        except DataReadError:
            raise DataReadError(
                f"{p.name} is an Excel file; use read_workbook() if it has named sheets."
            )

    lines, enc = _peek(p)
    # Strong grid signals first.
    for name, h in _GRID_HANDLERS.items():
        try:
            if float(h.fingerprint(p, lines)) >= 0.6:
                return read_grid(p, layout=name)
        except Exception:
            pass
    # Wide numeric first row => grid.
    delim = sniff_delimiter(lines[:_PEEK_LINES])
    start = find_first_numeric_row(lines, delim, 2)
    if start >= 0:
        parts = _split(lines[start], delim)
        numeric = [c for c in parts if _is_float(c)]
        if len(numeric) > 3:
            return read_grid(p)
    return read_xy(p)


def list_formats() -> dict:
    """Return a description of all registered handlers (for introspection)."""
    return {
        "xy": {name: h.description for name, h in _XY_HANDLERS.items()},
        "grid": {name: h.description for name, h in _GRID_HANDLERS.items()},
    }


# =============================================================================
#  CLI: quick inspection / smoke test
# =============================================================================
def _describe(path: str) -> None:
    p = Path(path)
    print(f"File   : {p}")
    print(f"Suffix : {p.suffix}")
    try:
        obj = read_auto(p)
    except DataReadError as exc:
        print(f"read_auto failed: {exc}")
        return
    print(f"Detected: {obj!r}")
    if isinstance(obj, Spectrum):
        print(f"  x[:3] = {obj.x[:3]}")
        print(f"  y[:3] = {obj.y[:3]}")
    elif isinstance(obj, Grid):
        print(f"  rows  = {obj.row_values[:3]} ... ({obj.row_values.size})")
        print(f"  cols  = {obj.col_values[:3]} ... ({obj.col_values.size})")
    for k, v in obj.meta.items():
        print(f"  meta.{k} = {v}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Read_data_unified.py - unified lab data reader")
        print("\nUsage:")
        print("  python Read_data_unified.py <data_file>   # detect & preview")
        print("\nRegistered formats:")
        fmts = list_formats()
        for shape, handlers in fmts.items():
            print(f"  [{shape}]")
            for name, desc in handlers.items():
                print(f"    {name:18s} {desc}")
    else:
        _describe(sys.argv[1])
