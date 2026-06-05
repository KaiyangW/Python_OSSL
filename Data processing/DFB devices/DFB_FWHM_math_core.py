"""
Deterministic FWHM extractor for organic DFB laser spectra.

The classical "narrowest-peak" rule fails on noisy pre-threshold spectra:
a single hot pixel or cosmic ray can mimic a Dirac-like spike on top of
the broad spontaneous-emission pedestal and cause the FWHM to collapse
prematurely. This module combines two independent diagnostics so that
result and prevents that collapse:

    1. Topology  - peak prominence on the residual (sharp features only).
    2. Physics   - the energy fraction carried by those sharp features.

A spike is only allowed to drive the FWHM down when BOTH agree that the
spectrum has crossed the coherence transition.

Public API:
    calculate_dfb_fwhm_adaptive(wavelength, intensity, ...)
"""

import numpy as np
from scipy.signal import medfilt, savgol_filter, find_peaks, peak_prominences


# numpy >= 2.0 renamed trapz to trapezoid; fall back transparently.
_trapezoid = getattr(np, "trapezoid", None) or np.trapz


__all__ = ["calculate_dfb_fwhm_adaptive"]


def calculate_dfb_fwhm_adaptive(
    wavelength,
    intensity,
    coherent_energy_threshold: float = 0.15,
    prominence_fraction: float = 0.10,
    return_diagnostics: bool = False,
):
    """
    Compute the FWHM of an organic DFB emission spectrum.

    The routine simultaneously evaluates two independent indicators of
    coherent emission and only collapses to the sharp-peak measurement
    when both agree, eliminating false positives from pre-threshold
    pixel spikes.

    Parameters
    ----------
    wavelength : 1-D array_like
        Monotonic wavelength axis (any consistent unit, typically nm).
    intensity : 1-D array_like
        Measured intensity sampled on ``wavelength``.
    coherent_energy_threshold : float, default 0.15
        Minimum fraction of the total spectral energy that must be
        carried by the residual (sharp) component for the spectrum to
        be classified as "lasing".
    prominence_fraction : float, default 0.10
        Required prominence of a residual peak, expressed as a fraction
        of the envelope peak value. Peaks below this are rejected as
        noise.
    return_diagnostics : bool, default False
        If True, return ``(fwhm, diagnostics_dict)``.

    Returns
    -------
    float or (float, dict)
        FWHM in the same units as ``wavelength`` (NaN on failure). When
        ``return_diagnostics`` is True a diagnostics dictionary is also
        returned; ``diagnostics["status"]`` is one of
        ``"non-lasing"``, ``"ongoing"``, ``"lasing"``.
    """
    wavelength = np.asarray(wavelength, dtype=float)
    intensity = np.asarray(intensity, dtype=float)

    diagnostics = {
        "status": "non-lasing",
        "narrow_fraction": np.nan,
        "candidate_peak_index": None,
        "candidate_peak_wavelength": np.nan,
        "candidate_prominence": np.nan,
        "envelope_peak_index": None,
        "envelope_peak_wavelength": np.nan,
        "savgol_window": None,
        "measurement_target": None,
        "peak_index_used": None,
        "half_max": np.nan,
        "reason": "",
    }

    n = intensity.size
    if n == 0 or wavelength.size != n or np.all(np.isnan(intensity)):
        diagnostics["reason"] = "empty or invalid input"
        return (np.nan, diagnostics) if return_diagnostics else np.nan

    # ------------------------------------------------------------------
    # 1. Preprocessing
    # ------------------------------------------------------------------
    # Median filter scrubs single-pixel artefacts without smearing real
    # narrow features (FWHM of a real DFB mode spans many pixels).
    y_clean = medfilt(intensity, kernel_size=3)

    # Savitzky-Golay window ~6.5% of the spectrum, forced odd, never
    # below 21 points. Wide enough to wash out the lasing spike while
    # tracking the slowly-varying ASE pedestal.
    win = int(round(0.065 * n))
    if win % 2 == 0:
        win += 1
    win = max(win, 21)
    if win > n:
        win = n if n % 2 == 1 else n - 1
    if win < 5:
        diagnostics["reason"] = "spectrum too short for envelope estimation"
        return (np.nan, diagnostics) if return_diagnostics else np.nan
    polyorder = min(3, win - 1)
    y_envelope = savgol_filter(intensity, window_length=win, polyorder=polyorder)
    diagnostics["savgol_window"] = int(win)

    base_clean = np.percentile(y_clean, 5)
    y_clean = np.maximum(y_clean - base_clean, 0.0)

    base_env = np.percentile(y_envelope, 5)
    y_envelope = np.maximum(y_envelope - base_env, 0.0)

    # ------------------------------------------------------------------
    # 2. Residual extraction (isolates sharp spectral features)
    # ------------------------------------------------------------------
    y_residual = np.maximum(y_clean - y_envelope, 0.0)

    env_max = float(np.max(y_envelope))
    clean_max = float(np.max(y_clean))
    if clean_max <= 0.0:
        diagnostics["reason"] = "no signal above baseline"
        return (np.nan, diagnostics) if return_diagnostics else np.nan

    # ------------------------------------------------------------------
    # 3. Topological candidate peak selection
    # ------------------------------------------------------------------
    candidate_peak_idx = None
    candidate_prominence = np.nan

    if env_max > 0.0:
        prom_threshold = prominence_fraction * env_max
        peaks, _ = find_peaks(y_residual)
        if peaks.size > 0:
            prominences = peak_prominences(y_residual, peaks)[0]
            mask = prominences > prom_threshold
            if np.any(mask):
                kept_peaks = peaks[mask]
                kept_prom = prominences[mask]
                best = int(np.argmax(kept_prom))
                candidate_peak_idx = int(kept_peaks[best])
                candidate_prominence = float(kept_prom[best])

    candidate_found = candidate_peak_idx is not None
    diagnostics["candidate_peak_index"] = candidate_peak_idx
    diagnostics["candidate_prominence"] = candidate_prominence
    if candidate_found:
        diagnostics["candidate_peak_wavelength"] = float(
            wavelength[candidate_peak_idx]
        )

    # ------------------------------------------------------------------
    # 4. Energetic lasing classification
    # ------------------------------------------------------------------
    total_energy = float(_trapezoid(y_clean, wavelength))
    narrow_energy = float(_trapezoid(y_residual, wavelength))
    narrow_fraction = (narrow_energy / total_energy) if total_energy > 0.0 else 0.0
    diagnostics["narrow_fraction"] = narrow_fraction

    energetic_enough = narrow_fraction >= coherent_energy_threshold
    is_lasing = candidate_found and energetic_enough

    if is_lasing:
        status = "lasing"
        reason = "prominent residual peak and sufficient narrow-band energy"
    elif candidate_found and not energetic_enough:
        status = "ongoing"
        reason = (
            f"prominent peak detected but narrow_fraction="
            f"{narrow_fraction:.3f} < {coherent_energy_threshold}"
        )
    else:
        status = "non-lasing"
        reason = "no residual peak above prominence threshold"
    diagnostics["status"] = status
    diagnostics["reason"] = reason

    # ------------------------------------------------------------------
    # 5. FWHM measurement
    # ------------------------------------------------------------------
    if is_lasing:
        target = y_clean
        peak_idx = candidate_peak_idx
        diagnostics["measurement_target"] = "y_clean"
    else:
        target = y_envelope
        peak_idx = int(np.argmax(y_envelope)) if env_max > 0 else None
        diagnostics["measurement_target"] = "y_envelope"

    if env_max > 0.0:
        env_peak_idx = int(np.argmax(y_envelope))
        diagnostics["envelope_peak_index"] = env_peak_idx
        diagnostics["envelope_peak_wavelength"] = float(wavelength[env_peak_idx])

    if peak_idx is None or target[peak_idx] <= 0.0:
        diagnostics["reason"] = (
            diagnostics["reason"] + "; no usable peak for FWHM measurement"
        ).lstrip("; ")
        return (np.nan, diagnostics) if return_diagnostics else np.nan

    peak_value = float(target[peak_idx])
    half_max = peak_value / 2.0
    diagnostics["peak_index_used"] = int(peak_idx)
    diagnostics["half_max"] = half_max

    fwhm = _fwhm_subpixel(wavelength, target, peak_idx, half_max)
    return (fwhm, diagnostics) if return_diagnostics else fwhm


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _fwhm_subpixel(wavelength, signal, peak_idx, half_max):
    """
    Strict-descent half-max crossings with linear sub-pixel interpolation.

    Walks outward from ``peak_idx`` on each side until the signal drops
    to or below ``half_max``, then linearly interpolates the bracketing
    pair to recover sub-pixel precision. Any failure (e.g. truncated
    peak hitting the array boundary) yields NaN.
    """
    try:
        n = signal.size

        left_idx = int(peak_idx)
        while left_idx > 0 and signal[left_idx] > half_max:
            left_idx -= 1

        right_idx = int(peak_idx)
        while right_idx < n - 1 and signal[right_idx] > half_max:
            right_idx += 1

        # Refuse to extrapolate beyond the data: the peak must actually
        # drop below half-max on both sides.
        if signal[left_idx] > half_max or signal[right_idx] > half_max:
            return float("nan")

        x1_l, x2_l = wavelength[left_idx], wavelength[left_idx + 1]
        y1_l, y2_l = signal[left_idx], signal[left_idx + 1]
        w_left = x1_l + (half_max - y1_l) * (x2_l - x1_l) / (y2_l - y1_l + 1e-9)

        x1_r, x2_r = wavelength[right_idx - 1], wavelength[right_idx]
        y1_r, y2_r = signal[right_idx - 1], signal[right_idx]
        w_right = x1_r + (half_max - y1_r) * (x2_r - x1_r) / (y2_r - y1_r + 1e-9)

        return abs(w_right - w_left)
    except Exception:
        return float("nan")
