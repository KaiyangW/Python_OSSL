"""
Poisson-weighted parameter uncertainty for TRPL lifetime fits.

TCSPC histogram counts in each time bin are treated as Poisson-distributed:
    Var(N_i) ≈ N_i  =>  weight w_i = 1 / sqrt(max(N_i, 1))

This is already used in the residual functions. Parameter standard errors are
estimated from the Jacobian of those weighted residuals at the optimum:

    Cov(p) ≈ (J^T J)^(-1)

Derived lifetimes (num_ave, int_ave, selected averages) use the delta method
with the full parameter covariance matrix.
"""

import numpy as np
from scipy.optimize import least_squares
from scipy.special import gamma


def _is_fixed_param(lo, hi, atol=1e-5):
    return abs(float(hi) - float(lo)) < atol


def estimate_poisson_covariance(residuals_fn, p_opt, lower, upper, args):
    """
    Estimate the parameter covariance matrix from Poisson-weighted residuals.

    Uses linear (L2) loss at the optimum so the Hessian matches weighted
    least-squares theory; the fit itself may still use a robust loss.
    """
    p_opt = np.asarray(p_opt, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)

    res = least_squares(
        residuals_fn,
        p_opt,
        bounds=(lower, upper),
        args=args,
        jac='2-point',
        x_scale='jac',
        loss='linear',
        ftol=1e-15,
        xtol=1e-15,
        gtol=1e-15,
        max_nfev=1,
    )

    if res.jac is None or res.jac.size == 0:
        return None

    jtj = res.jac.T @ res.jac
    try:
        cov = np.linalg.inv(jtj)
    except np.linalg.LinAlgError:
        cov = np.linalg.pinv(jtj)

    for i in range(len(p_opt)):
        if _is_fixed_param(lower[i], upper[i]):
            cov[i, :] = 0.0
            cov[:, i] = 0.0

    return cov


def _param_stderr(cov, index):
    if cov is None:
        return np.nan
    var = cov[index, index]
    if not np.isfinite(var) or var < 0:
        return np.nan
    return float(np.sqrt(var))


def _numerical_gradient(func, p_opt, rel_step=1e-6):
    p_opt = np.asarray(p_opt, dtype=float)
    grad = np.zeros_like(p_opt)
    for i in range(len(p_opt)):
        step = rel_step * max(abs(p_opt[i]), 1.0)
        if step == 0.0:
            continue
        p_plus = p_opt.copy()
        p_minus = p_opt.copy()
        p_plus[i] += step
        p_minus[i] -= step
        grad[i] = (func(p_plus) - func(p_minus)) / (2.0 * step)
    return grad


def delta_method_stderr(func, p_opt, cov):
    """Standard error of a scalar derived quantity via the delta method."""
    if cov is None:
        return np.nan
    grad = _numerical_gradient(func, p_opt)
    var = float(grad @ cov @ grad)
    if not np.isfinite(var) or var < 0:
        return np.nan
    return float(np.sqrt(var))


def num_ave_lifetime(tau, beta):
    return (tau / beta) * gamma(1.0 / beta)


def int_ave_lifetime(tau, beta):
    return tau * (gamma(2.0 / beta) / gamma(1.0 / beta))


def compute_component_uncertainties(p_opt, cov, num_exp, tau_idx_fn, beta_idx_fn,
                                    fixed_t_flags, fixed_b_flags):
    """
    Return per-component stderr dicts keyed like the component result dicts.
    """
    if cov is None:
        empty = {
            'tau_stderr': np.nan,
            'beta_stderr': np.nan,
            'num_ave_stderr': np.nan,
            'int_ave_stderr': np.nan,
        }
        return [empty.copy() for _ in range(num_exp)]

    uncerts = []
    for i in range(num_exp):
        tau_idx = tau_idx_fn(i)
        beta_idx = beta_idx_fn(i)

        if fixed_t_flags[i]:
            tau_stderr = 0.0
        else:
            tau_stderr = _param_stderr(cov, tau_idx)

        if fixed_b_flags[i]:
            beta_stderr = 0.0
        else:
            beta_stderr = _param_stderr(cov, beta_idx)

        def _num_ave(p, ti=tau_idx, bi=beta_idx):
            return num_ave_lifetime(p[ti], p[bi])

        def _int_ave(p, ti=tau_idx, bi=beta_idx):
            return int_ave_lifetime(p[ti], p[bi])

        if fixed_t_flags[i] and fixed_b_flags[i]:
            num_ave_stderr = 0.0
            int_ave_stderr = 0.0
        else:
            num_ave_stderr = delta_method_stderr(_num_ave, p_opt, cov)
            int_ave_stderr = delta_method_stderr(_int_ave, p_opt, cov)

        uncerts.append({
            'tau_stderr': tau_stderr,
            'beta_stderr': beta_stderr,
            'num_ave_stderr': num_ave_stderr,
            'int_ave_stderr': int_ave_stderr,
        })

    return uncerts


def compute_selected_avg_stderr(p_opt, cov, components, valid_indices, use_int_ave):
    """
    Delta-method stderr for the selected intensity- or number-averaged lifetime.
    """
    if cov is None or not valid_indices:
        return np.nan

    def _selected_avg(p):
        total_area = 0.0
        weighted = 0.0
        for idx in valid_indices:
            tau = p[components[idx]['tau_param_idx']]
            beta = p[components[idx]['beta_param_idx']]
            amp = p[components[idx]['amp_param_idx']]
            num_ave = num_ave_lifetime(tau, beta)
            area = amp * num_ave
            lifetime = int_ave_lifetime(tau, beta) if use_int_ave else num_ave
            weighted += area * lifetime
            total_area += area
        if total_area <= 0:
            return np.nan
        return weighted / total_area

    return delta_method_stderr(_selected_avg, p_opt, cov)


def attach_param_indices(components, num_exp, amp_idx_fn, tau_idx_fn, beta_idx_fn):
    """Store parameter-vector indices on each component dict for propagation."""
    for i in range(num_exp):
        components[i]['amp_param_idx'] = amp_idx_fn(i)
        components[i]['tau_param_idx'] = tau_idx_fn(i)
        components[i]['beta_param_idx'] = beta_idx_fn(i)


def format_val_err(val, err, formatter):
    if err is None or not np.isfinite(err):
        return formatter(val)
    if err == 0.0:
        return formatter(val)
    return f"{formatter(val)} ± {formatter(err)}"


def format_beta_err(beta, err):
    if err is None or not np.isfinite(err):
        return f"{beta:.2f}"
    if err == 0.0:
        return f"{beta:.2f}"
    return f"{beta:.2f} ± {err:.2f}"
