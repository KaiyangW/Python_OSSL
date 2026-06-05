"""Filtering utilities for photoluminescence (PL) spectra."""

import numpy as np
from scipy.signal import savgol_filter


def smooth_savgol(intensity, window_length=11, polyorder=3, mode="interp"):
    """
    Smooth raw PL intensity data with a Savitzky-Golay filter.

    The Savitzky-Golay method fits a low-order polynomial inside a moving
    window and evaluates the polynomial at the centre point. Unlike a simple
    moving average, this preserves local slopes and peak shape to the order of
    the fitted polynomial, which is important when the onset is later obtained
    from the derivative of the spectrum.

    Parameters
    ----------
    intensity : array-like
        Raw PL intensity values sampled on a 1D x-axis.
    window_length : int, optional
        Number of points in the smoothing window. The value must be odd. If an
        even value is supplied it is increased by one. For short spectra the
        window is reduced to the largest valid odd length.
    polyorder : int, optional
        Polynomial order used in each local fit. A quadratic or cubic fit is
        usually sufficient for PL spectra because it suppresses high-frequency
        noise without flattening the peak edge.
    mode : str, optional
        Edge handling mode passed to scipy.signal.savgol_filter.

    Returns
    -------
    numpy.ndarray
        Smoothed intensity array with the same shape as the input.

    Raises
    ------
    ValueError
        If the input is not one-dimensional, contains too few finite points, or
        the requested polynomial order is invalid for the available data.
    """
    y = np.asarray(intensity, dtype=float)

    if y.ndim != 1:
        raise ValueError("intensity must be a one-dimensional array")

    if len(y) < 3:
        raise ValueError("at least three intensity points are required")

    if not np.all(np.isfinite(y)):
        raise ValueError("intensity must contain only finite values")

    if polyorder < 0:
        raise ValueError("polyorder must be non-negative")

    window_length = int(window_length)
    polyorder = int(polyorder)

    if window_length <= polyorder:
        window_length = polyorder + 2

    if window_length % 2 == 0:
        window_length += 1

    max_window = len(y) if len(y) % 2 == 1 else len(y) - 1
    window_length = min(window_length, max_window)

    if window_length <= polyorder:
        raise ValueError("not enough points for the requested polynomial order")

    return savgol_filter(y, window_length=window_length, polyorder=polyorder, mode=mode)
