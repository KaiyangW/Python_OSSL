"""
Area_Analysis_Engine.py
=======================
Standalone physics module for calculating Prompt Fluorescence (PF) and
Delayed Fluorescence (DF) areas from stretched-exponential fit components.

Two methods are provided:

1.  calc_recon_difference_areas  — Area Difference Method (used by Recon fit).
    PF area is inferred as the difference between the total background-
    subtracted raw data area inside the fit window and the sum of all modelled
    slow (DF) and phosphorescence component areas.  No extrapolation is applied
    because the reconvolution fit window already starts at the IRF peak and
    therefore captures the full prompt emission.

2.  calc_tail_difference_areas   — Area Difference Method with optional
    Extrapolation Compensation (used by Tail fit).
    PF area is inferred as the difference between the total raw data area
    and the sum of all fitted slow components.  When use_extrapolation=True
    the raw PF area observed in the tail window is scaled up by an
    extrapolation factor to account for the PF signal that occurred before
    xmin (i.e. was not captured in the tail fit window).

Physics background
------------------
For a stretched-exponential component  f(t) = B * exp(-(t/tau)^beta):

    Full area (0 -> inf):  A = B * (tau / beta) * Gamma(1/beta)

    Partial area (xmin -> inf):  computed numerically via scipy.integrate.quad
        because the incomplete gamma integral of a stretched exponential does
        not have a simple closed form for arbitrary beta.

Both functions return the same standardised dictionary so that callers can
unpack them identically regardless of the method used.
"""

import numpy as np
from scipy.special import gamma
from scipy.integrate import quad


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calc_recon_difference_areas(t_array, data_array, irf_array, xmin, xmax, bkg, shift, scatter_amp, components, phos_idx, subtract_scatter=True):
    """
    Area Difference Method — designed for reconvolution fits (no extrapolation).

    Because the reconvolution fit window already encompasses the rising edge of
    the IRF, no prompt-emission signal is missed before ``xmin``.  The PF area
    is therefore defined as:

        PF Area = Total Raw Data Area (in fit window) - DF Area - Phos Area [- Scatter Area]

    Subtracting the scatter area (optional) allows finding the absolute
    lower bound of the PF area. If not subtracted, the scatter area is still
    calculated and returned for reporting.

    Parameters
    ----------
    t_array    : 1-D ndarray  – full time axis (ns)
    data_array : 1-D ndarray  – full count data
    irf_array  : 1-D ndarray  – uniform IRF array
    xmin       : float        – fit window start time (ns)
    xmax       : float        – fit window end time (ns)
    bkg        : float        – fitted background level (counts)
    shift      : float        – fitted IRF time shift (ns)
    scatter_amp : float       – fitted scatter amplitude (counts/IRF_unit)
    components : list of dict – sorted (ascending tau) component list;
                               each dict must contain 'area' (float) and
                               'is_phos' (bool).
    phos_idx   : int          – zero-based phosphorescence index, or -1 if none
    subtract_scatter : bool    – if True, subtracts the scatter area from PF residual

    Returns
    -------
    dict with keys:
        area_pf             – PF area (difference method)
        area_df             – sum of non-phosphorescence DF component areas
        area_phos           – absolute area of the phosphorescence component
        total_fluo_area     – area_pf + area_df  (used for ratio normalisation)
        pf_ratio_clean      – PF / (PF + DF) * 100  [%]
        df_ratio_clean      – DF / (PF + DF) * 100  [%]
        phos_percent_total  – Phos / (PF + DF + Phos) * 100  [%]
        total_data_area     – raw integrated data area in the fit window
        scatter_area        – integrated area of the scatter component
        extrapolation_factor – always None (no extrapolation applied)
    """
    if not components:
        return _zero_result()

    dt = np.median(np.diff(t_array))

    # Mask covering the full fit window [xmin, xmax]
    fit_mask = (t_array >= xmin) & (t_array <= xmax)

    # Total background-subtracted raw data area inside the fit window
    total_data_area = float(np.sum(np.maximum(data_array[fit_mask] - bkg, 0)) * dt)

    # Tag phosphorescence component and read its analytical area
    area_phos = 0.0
    if phos_idx != -1 and phos_idx < len(components):
        components[phos_idx]['is_phos'] = True
        area_phos = components[phos_idx]['area']

    # DF = analytical area of all components beyond C1, phosphorescence excluded
    area_df = sum(comp['area'] for comp in components[1:] if not comp['is_phos'])

    # --- Scatter Area Calculation ---
    # Shift IRF using the same logic as the fit engine
    irf_shifted = np.interp(t_array, t_array - shift, irf_array, left=0, right=0)
    # Integrate scatter signal within the fit window
    scatter_area = float(np.sum(irf_shifted[fit_mask] * scatter_amp) * dt)

    # PF = residual after removing DF, Phos, and optionally Scatter from the measured total
    area_pf_raw = total_data_area - area_df - area_phos
    if subtract_scatter:
        area_pf = max(area_pf_raw - scatter_area, 1e-9)
    else:
        area_pf = max(area_pf_raw, 1e-9)

    total_fluo_area = max(area_pf + area_df, 1e-9)
    total_all       = max(total_fluo_area + area_phos, 1e-9)

    pf_ratio_clean     = (area_pf  / total_fluo_area) * 100.0
    df_ratio_clean     = (area_df  / total_fluo_area) * 100.0
    phos_percent_total = (area_phos / total_all)       * 100.0

    return {
        'area_pf':              area_pf,
        'area_df':              area_df,
        'area_phos':            area_phos,
        'total_fluo_area':      total_fluo_area,
        'pf_ratio_clean':       pf_ratio_clean,
        'df_ratio_clean':       df_ratio_clean,
        'phos_percent_total':   phos_percent_total,
        'total_data_area':      total_data_area,
        'scatter_area':         scatter_area,
        'extrapolation_factor': None,
    }


def calc_tail_difference_areas(t_array, data_array, xmin, bkg, components,
                                phos_idx, use_extrapolation=True):
    """
    Area Difference Method — designed for tail fits.

    In a tail fit the prompt fluorescence (PF) peak is not explicitly modelled
    because the fit window starts after the IRF.  The PF area is therefore
    inferred as the difference between the total measured signal and the sum of
    all fitted slow (DF) components.

    Simple mode  (use_extrapolation=False)
    ----------------------------------------
    Reproduces the original inline logic exactly:
        total_data_area  = sum(max(data - bkg, 0)) * dt   [over t >= xmin]
        area_pf          = max(total_data_area - total_area_fitted, 1e-9)

    Extrapolation mode  (use_extrapolation=True)
    ---------------------------------------------
    The tail window misses the portion of the PF emission that occurred before
    xmin.  We correct for this by scaling the observed PF difference by a
    purely geometric extrapolation factor derived from the C1 shape (tau1, beta1)
    WITHOUT the amplitude B1, which cancels in the ratio:

        shape_total_area = (tau1/beta1) * Gamma(1/beta1)
                         = unit-amplitude full area (0 → ∞)

        shape_tail_area  = integral from xmin to ∞ of exp(-(t/tau1)^beta1) dt
                         (numerical, ABSOLUTE time — not shifted to zero)

        extrapolation_factor = shape_total_area / shape_tail_area

    Because stretched exponentials are NOT memoryless, integrating from xmin
    in absolute time gives shape_tail_area < shape_total_area, so the factor
    is correctly > 1.  Integrating a shifted coordinate (t - xmin) from 0
    would recover the full area and produce a factor of exactly 1.0 — that
    was the bug in the previous implementation.

        area_slow_fitted  = sum of areas of components C2, C3, ... (excl. phos)
        pf_diff_in_window = max(total_data_area - area_slow_fitted, 1e-9)
        area_pf           = pf_diff_in_window * extrapolation_factor

    Parameters
    ----------
    t_array      : 1-D ndarray   – full time axis (ns)
    data_array   : 1-D ndarray   – full count data
    xmin         : float         – tail-fit start time (ns)
    bkg          : float         – fitted background level
    components   : list of dict  – sorted (ascending tau) component list
                                   each with keys 'B', 'tau', 'beta', 'area', 'is_phos'
    phos_idx     : int           – zero-based phosphorescence index, or -1
    use_extrapolation : bool     – True  → extrapolation-compensated PF area
                                   False → simple difference (legacy behaviour)

    Returns
    -------
    Same standardised dict as calc_recon_analytical_areas, plus:
        total_data_area      – raw integrated data area in the tail window
        extrapolation_factor – factor applied to PF (None if simple mode)
    """
    if not components:
        return _zero_result()

    dt = np.median(np.diff(t_array))

    # Tag phosphorescence component
    area_phos = 0.0
    if phos_idx != -1 and phos_idx < len(components):
        components[phos_idx]['is_phos'] = True
        area_phos = components[phos_idx]['area']

    # Total fitted area (all components, including phos)
    total_area_fitted = sum(comp['area'] for comp in components)

    # Raw data area in the tail window (t >= xmin)
    tail_mask        = t_array >= xmin
    total_data_area  = float(np.sum(np.maximum(data_array[tail_mask] - bkg, 0)) * dt)

    extrapolation_factor = None

    if use_extrapolation:
        # --- Extrapolation Compensation ---
        c1 = components[0]
        tau1, beta1 = c1['tau'], c1['beta']

        # 1. Theoretical total pure shape area (from t = 0 to infinity, amplitude = 1)
        shape_total_area = (tau1 / beta1) * gamma(1.0 / beta1)

        # 2. Theoretical tail pure shape area (from t = xmin to infinity, amplitude = 1)
        #    Integrated in ABSOLUTE time — NOT shifted to zero — so that the
        #    non-memoryless nature of the stretched exponential is captured.
        #    This makes shape_tail_area < shape_total_area and the factor > 1.
        def pure_shape_integrand(t):
            return np.exp(-np.power(t / tau1, beta1))

        shape_tail_area, _ = quad(pure_shape_integrand, xmin, np.inf)
        shape_tail_area = max(shape_tail_area, 1e-30)  # guard against division by zero

        # 3. The geometric extrapolation factor
        extrapolation_factor = shape_total_area / shape_tail_area

        # Slow area = everything except C1 (DF components), phosphorescence excluded
        area_slow_fitted     = sum(comp['area'] for comp in components[1:]
                                   if not comp['is_phos'])
        pf_diff_in_window    = max(total_data_area - area_slow_fitted, 1e-9)

        # True PF Area is the residual window area scaled up by the geometric missing portion
        area_pf              = pf_diff_in_window * extrapolation_factor
        area_df              = area_slow_fitted

    else:
        # --- Simple Difference (legacy) ---
        area_df  = total_area_fitted - area_phos
        area_pf  = max(total_data_area - total_area_fitted, 1e-9)

    total_fluo_area    = max(area_pf + area_df, 1e-9)
    total_all          = max(total_fluo_area + area_phos, 1e-9)

    pf_ratio_clean     = (area_pf  / total_fluo_area) * 100.0
    df_ratio_clean     = (area_df  / total_fluo_area) * 100.0
    phos_percent_total = (area_phos / total_data_area) * 100.0 if total_data_area > 0 else 0.0

    return {
        'area_pf':              area_pf,
        'area_df':              area_df,
        'area_phos':            area_phos,
        'total_fluo_area':      total_fluo_area,
        'pf_ratio_clean':       pf_ratio_clean,
        'df_ratio_clean':       df_ratio_clean,
        'phos_percent_total':   phos_percent_total,
        'total_data_area':      total_data_area,
        'extrapolation_factor': extrapolation_factor,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _zero_result():
    """Return a zeroed result dict for edge cases (empty component list)."""
    return {
        'area_pf':              0.0,
        'area_df':              0.0,
        'area_phos':            0.0,
        'total_fluo_area':      1e-9,
        'pf_ratio_clean':       0.0,
        'df_ratio_clean':       0.0,
        'phos_percent_total':   0.0,
        'total_data_area':      0.0,
        'scatter_area':         0.0,
        'extrapolation_factor': None,
    }
