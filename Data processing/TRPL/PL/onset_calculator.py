"""PL onset detection using the Tangent-Baseline Intersection method."""

import csv
from pathlib import Path

import numpy as np

try:
    from filters import smooth_savgol
except ImportError:  # Allows package-style imports if this folder is packaged later.
    from .filters import smooth_savgol


HC_EV_NM = 1240.0


def _parse_float(value):
    """Return a float for numeric CSV fields, otherwise None."""
    try:
        text = str(value).strip()
        if text == "":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def load_pl_numeric_rows(filepath, x_col=0, y_col=1, encoding=None):
    """
    Load PL data from instrument CSV/TXT files by keeping rows with numeric x/y columns.

    Many spectrometer exports contain a metadata header followed by plain
    numeric rows, for example:

        Labels,PL 330ex vac RT,
        Type,Emission Scan,
        ...
        350.00,3.30086011E+3,

    This reader scans the file row-by-row and keeps only rows where both the
    selected x and intensity columns can be parsed as floating-point numbers.
    Header lines, blank lines, comments, and trailing empty fields are ignored.

    Parameters
    ----------
    filepath : str or pathlib.Path
        PL export file to read.
    x_col, y_col : int, optional
        Zero-based column indices containing the x-axis and intensity values.
    encoding : str, optional
        File encoding. If omitted, common encodings are tried automatically.

    Returns
    -------
    tuple
        (x, intensity, metadata), where x and intensity are numpy arrays and
        metadata includes the first numeric line number and the number of rows
        skipped before numeric data was found.
    """
    path = Path(filepath)
    encodings = [encoding] if encoding is not None else ["utf-8-sig", "windows-1252", "latin-1"]
    last_error = None

    for candidate_encoding in encodings:
        try:
            with path.open("r", encoding=candidate_encoding, newline="") as handle:
                sample = handle.read(4096)
                handle.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
                except csv.Error:
                    dialect = csv.excel

                x_values = []
                y_values = []
                first_numeric_line = None
                skipped_rows = 0

                for line_number, row in enumerate(csv.reader(handle, dialect), start=1):
                    required_col = max(x_col, y_col)
                    if len(row) <= required_col:
                        skipped_rows += 1
                        continue

                    x_value = _parse_float(row[x_col])
                    y_value = _parse_float(row[y_col])

                    if x_value is None or y_value is None:
                        skipped_rows += 1
                        continue

                    if first_numeric_line is None:
                        first_numeric_line = line_number

                    x_values.append(x_value)
                    y_values.append(y_value)

            if len(x_values) < 5:
                raise ValueError(f"found only {len(x_values)} numeric x/y rows in {path}")

            metadata = {
                "filepath": str(path),
                "encoding": candidate_encoding,
                "first_numeric_line": first_numeric_line,
                "skipped_rows": skipped_rows,
                "n_points": len(x_values),
                "x_col": x_col,
                "y_col": y_col,
            }
            return np.asarray(x_values, dtype=float), np.asarray(y_values, dtype=float), metadata
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    raise UnicodeDecodeError(
        last_error.encoding,
        last_error.object,
        last_error.start,
        last_error.end,
        f"could not decode {path} with the attempted encodings",
    )


def _clean_and_sort_xy(x, y):
    """Return finite 1D x/y arrays sorted by x."""
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)

    if x_arr.ndim != 1 or y_arr.ndim != 1:
        raise ValueError("x and y must be one-dimensional arrays")

    if len(x_arr) != len(y_arr):
        raise ValueError("x and y must have the same length")

    finite_mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    x_arr = x_arr[finite_mask]
    y_arr = y_arr[finite_mask]

    if len(x_arr) < 5:
        raise ValueError("at least five finite data points are required")

    sort_idx = np.argsort(x_arr)
    x_arr = x_arr[sort_idx]
    y_arr = y_arr[sort_idx]

    if np.any(np.diff(x_arr) <= 0):
        raise ValueError("x values must be unique after removing non-finite points")

    return x_arr, y_arr


def _make_baseline_mask(x, baseline_region, side="low"):
    """
    Build the mask used to estimate the non-emitting background baseline.

    baseline_region can be:
    - None: first/last 10 percent of the sorted spectrum.
    - float between 0 and 1: first/last fraction of the sorted spectrum.
    - int: first/last N points of the sorted spectrum.
    - tuple(min_x, max_x): explicit x-axis interval.
    - boolean array: direct mask with the same length as x.
    """
    n_points = len(x)

    if side not in {"low", "high"}:
        raise ValueError("baseline side must be 'low' or 'high'")

    def _edge_mask(n_base):
        mask = np.zeros(n_points, dtype=bool)
        if side == "low":
            mask[:n_base] = True
        else:
            mask[-n_base:] = True
        return mask

    if baseline_region is None:
        n_base = max(3, int(np.ceil(0.10 * n_points)))
        return _edge_mask(n_base)

    if isinstance(baseline_region, (float, np.floating)):
        if not 0 < baseline_region <= 1:
            raise ValueError("float baseline_region must be in the interval (0, 1]")
        n_base = max(3, int(np.ceil(float(baseline_region) * n_points)))
        return _edge_mask(n_base)

    if isinstance(baseline_region, (int, np.integer)):
        if baseline_region < 3:
            raise ValueError("integer baseline_region must select at least three points")
        n_base = min(int(baseline_region), n_points)
        return _edge_mask(n_base)

    baseline_arr = np.asarray(baseline_region)

    if baseline_arr.dtype == bool:
        if baseline_arr.shape != x.shape:
            raise ValueError("boolean baseline_region must match the shape of x")
        return baseline_arr.copy()

    if len(baseline_arr) == 2:
        x_min, x_max = float(baseline_arr[0]), float(baseline_arr[1])
        if x_min > x_max:
            x_min, x_max = x_max, x_min
        return (x >= x_min) & (x <= x_max)

    raise ValueError("baseline_region must be None, a fraction, an integer, a tuple, or a boolean mask")


def calculate_pl_onset_tangent_baseline(
    x,
    intensity,
    baseline_region=None,
    smooth=True,
    window_length=11,
    polyorder=3,
    derivative_mode="absolute",
    edge_region="pre_peak",
    baseline_side="low",
):
    """
    Calculate the PL onset by intersecting the steepest-edge tangent with the baseline.

    Mathematical justification
    --------------------------
    In a PL spectrum the non-emitting region is approximated by a constant
    background level, y_base. After Savitzky-Golay smoothing, the first
    derivative dy/dx identifies the point of maximum local spectral change. The
    tangent at this point,

        y = m * (x - x_t) + y_t

    is a first-order local model of the rising edge. The onset is defined as the
    x-coordinate where this tangent first reaches the independently measured
    baseline:

        x_onset = x_t + (y_base - y_t) / m

    This construction avoids arbitrary intensity-percentage thresholds and is
    therefore easier to justify in methods sections, provided that the baseline
    interval is reported and the validation plot is inspected.

    Parameters
    ----------
    x : array-like
        Monotonic or unsorted x-axis values, e.g. wavelength or energy.
    intensity : array-like
        Raw PL intensity values.
    baseline_region : None, float, int, tuple, or boolean array, optional
        Region used to calculate y_base. By default the first 10 percent of the
        sorted data is treated as non-emitting background. Use an explicit
        x-range tuple for publication-quality analyses.
    smooth : bool, optional
        If True, smooth intensity using the Savitzky-Golay filter before taking
        the derivative.
    window_length : int, optional
        Smoothing window passed to smooth_savgol.
    polyorder : int, optional
        Smoothing polynomial order passed to smooth_savgol.
    derivative_mode : {"absolute", "positive"}, optional
        "absolute" follows the maximum absolute derivative criterion within the
        selected edge region. "positive" restricts detection to the steepest
        positive slope, which is useful when the x-axis orientation is
        guaranteed to make the onset rise.
    edge_region : {"pre_peak", "post_peak", "full"}, optional
        "pre_peak" searches from the low-x side up to the PL maximum, which
        prevents a symmetric spectrum from selecting the falling edge.
        "post_peak" searches from the PL maximum to the high-x side. "full"
        applies the derivative criterion across the complete spectrum.
    baseline_side : {"low", "high"}, optional
        Side used for fractional/integer baseline regions in sorted x.

    Returns
    -------
    dict
        Dictionary containing onset_x, baseline_y, tangent parameters, smoothed
        data, derivative data, and diagnostic indices for validation plotting.
    """
    x_arr, y_raw = _clean_and_sort_xy(x, intensity)

    baseline_mask = _make_baseline_mask(x_arr, baseline_region, side=baseline_side)
    if np.count_nonzero(baseline_mask) < 3:
        raise ValueError("baseline_region must select at least three points")

    y_base = float(np.mean(y_raw[baseline_mask]))
    y_smoothed = smooth_savgol(y_raw, window_length=window_length, polyorder=polyorder) if smooth else y_raw.copy()
    derivative = np.gradient(y_smoothed, x_arr)
    peak_index = int(np.argmax(y_smoothed))

    if edge_region == "pre_peak":
        candidate_indices = np.arange(0, peak_index + 1)
        if len(candidate_indices) < 2:
            raise ValueError("PL maximum occurs too close to the low-x boundary for pre_peak onset detection")
    elif edge_region == "post_peak":
        candidate_indices = np.arange(peak_index, len(x_arr))
        if len(candidate_indices) < 2:
            raise ValueError("PL maximum occurs too close to the high-x boundary for post_peak onset detection")
    elif edge_region == "full":
        candidate_indices = np.arange(len(x_arr))
    else:
        raise ValueError("edge_region must be 'pre_peak' or 'full'")

    candidate_derivative = derivative[candidate_indices]

    if derivative_mode == "absolute":
        edge_index = int(candidate_indices[np.argmax(np.abs(candidate_derivative))])
    elif derivative_mode == "positive":
        if np.nanmax(candidate_derivative) <= 0:
            raise ValueError("no positive derivative found for derivative_mode='positive'")
        edge_index = int(candidate_indices[np.argmax(candidate_derivative)])
    else:
        raise ValueError("derivative_mode must be 'absolute' or 'positive'")

    tangent_slope = float(derivative[edge_index])
    if np.isclose(tangent_slope, 0.0):
        raise ValueError("steepest derivative is zero; tangent-baseline intersection is undefined")

    tangent_x = float(x_arr[edge_index])
    tangent_y = float(y_smoothed[edge_index])
    tangent_intercept = tangent_y - tangent_slope * tangent_x
    onset_x = float((y_base - tangent_intercept) / tangent_slope)

    return {
        "onset_x": onset_x,
        "onset_y": y_base,
        "baseline_y": y_base,
        "baseline_mask": baseline_mask,
        "peak_index": peak_index,
        "edge_index": edge_index,
        "edge_x": tangent_x,
        "edge_y": tangent_y,
        "tangent_slope": tangent_slope,
        "tangent_intercept": float(tangent_intercept),
        "x": x_arr,
        "raw_intensity": y_raw,
        "smoothed_intensity": y_smoothed,
        "derivative": derivative,
        "smoothing": {
            "enabled": bool(smooth),
            "window_length": int(window_length),
            "polyorder": int(polyorder),
        },
        "edge_region": edge_region,
        "baseline_side": baseline_side,
        "method": "Tangent-Baseline Intersection",
    }


def wavelength_nm_to_energy_ev(wavelength_nm):
    """Photon energy (eV) from wavelength (nm)."""
    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    return HC_EV_NM / wavelength_nm


def energy_ev_to_wavelength_nm(energy_ev):
    """Wavelength (nm) from photon energy (eV)."""
    energy_ev = np.asarray(energy_ev, dtype=float)
    return HC_EV_NM / energy_ev


def wavelength_spectrum_to_energy(wavelength_nm, intensity_lambda):
    """
    Convert I(lambda) to I(E) with the Jacobian dλ/dE = hc / E^2 = λ^2 / hc.

    Returned arrays are sorted by increasing photon energy.
    """
    wavelength_arr, intensity_arr = _clean_and_sort_xy(wavelength_nm, intensity_lambda)

    if np.any(wavelength_arr <= 0):
        raise ValueError("wavelength values must be positive for energy conversion")

    energy_ev = wavelength_nm_to_energy_ev(wavelength_arr)
    intensity_energy = intensity_arr * (wavelength_arr**2) / HC_EV_NM
    order = np.argsort(energy_ev)
    return energy_ev[order], intensity_energy[order]


def _energy_result_to_wavelength_plot_result(energy_result):
    """Expose an energy-domain onset result on a wavelength axis for plotting."""
    energy_ev = np.asarray(energy_result["x"], dtype=float)
    wavelength_nm = energy_ev_to_wavelength_nm(energy_ev)
    order = np.argsort(wavelength_nm)
    wavelength_plot = wavelength_nm[order]

    def energy_intensity_to_lambda(intensity_energy, wavelength_values):
        return np.asarray(intensity_energy, dtype=float) * HC_EV_NM / (wavelength_values**2)

    raw_lambda = energy_intensity_to_lambda(energy_result["raw_intensity"], wavelength_nm)[order]
    smooth_lambda = energy_intensity_to_lambda(energy_result["smoothed_intensity"], wavelength_nm)[order]

    onset_energy = float(energy_result["onset_x"])
    onset_nm = float(energy_ev_to_wavelength_nm(onset_energy))
    edge_energy = float(energy_result["edge_x"])
    edge_nm = float(energy_ev_to_wavelength_nm(edge_energy))

    onset_y_lambda = float(energy_result["onset_y"] * HC_EV_NM / (onset_nm**2))
    edge_y_lambda = float(energy_result["edge_y"] * HC_EV_NM / (edge_nm**2))

    result = dict(energy_result)
    result.update(
        {
            "calculation_domain": "energy",
            "plot_domain": "wavelength",
            "x_unit": "nm",
            "calculation_x_unit": "eV",
            "onset_energy_ev": onset_energy,
            "edge_energy_ev": edge_energy,
            "energy_x": energy_ev,
            "raw_intensity_energy": energy_result["raw_intensity"],
            "smoothed_intensity_energy": energy_result["smoothed_intensity"],
            "derivative_energy": energy_result["derivative"],
            "baseline_y_energy": energy_result["baseline_y"],
            "tangent_slope_energy": energy_result["tangent_slope"],
            "tangent_intercept_energy": energy_result["tangent_intercept"],
            "x": wavelength_plot,
            "raw_intensity": raw_lambda,
            "smoothed_intensity": smooth_lambda,
            "onset_x": onset_nm,
            "onset_y": onset_y_lambda,
            "edge_x": edge_nm,
            "edge_y": edge_y_lambda,
            "method": "Energy-domain Tangent-Baseline Intersection",
        }
    )
    return result


def calculate_pl_onset_from_wavelength(
    wavelength_nm,
    intensity,
    baseline_region=None,
    smooth=True,
    window_length=11,
    polyorder=3,
    derivative_mode="absolute",
    edge_region="post_peak",
):
    """
    Calculate PL onset in photon-energy space from a wavelength spectrum.

    The input I(lambda) spectrum is converted to I(E) using the Jacobian
    I(E) = I(lambda) * lambda^2 / hc. The onset is found on the high-energy
    side of the PL peak, then returned on a wavelength x-axis for plotting.
    """
    energy_ev, intensity_energy = wavelength_spectrum_to_energy(wavelength_nm, intensity)
    energy_result = calculate_pl_onset_tangent_baseline(
        energy_ev,
        intensity_energy,
        baseline_region=baseline_region,
        smooth=smooth,
        window_length=window_length,
        polyorder=polyorder,
        derivative_mode=derivative_mode,
        edge_region=edge_region,
        baseline_side="high",
    )
    return _energy_result_to_wavelength_plot_result(energy_result)


def calculate_pl_onset_from_file(
    filepath,
    x_col=0,
    y_col=1,
    baseline_region=None,
    smooth=True,
    window_length=11,
    polyorder=3,
    derivative_mode="absolute",
    edge_region="post_peak",
    encoding=None,
):
    """
    Load a PL export file and calculate the onset from its numeric x/y rows.

    This is the convenience entry point for wavelength-domain spectrometer CSV
    files. Numeric rows are loaded as wavelength/intensity, converted to
    energy-domain intensity with the Jacobian, and passed to the onset detector.

    Returns
    -------
    dict
        The standard onset result dictionary with an additional "source_file"
        metadata entry describing how the file was parsed.
    """
    x, intensity, metadata = load_pl_numeric_rows(
        filepath,
        x_col=x_col,
        y_col=y_col,
        encoding=encoding,
    )
    result = calculate_pl_onset_from_wavelength(
        x,
        intensity,
        baseline_region=baseline_region,
        smooth=smooth,
        window_length=window_length,
        polyorder=polyorder,
        derivative_mode=derivative_mode,
        edge_region=edge_region,
    )
    result["source_file"] = metadata
    return result
