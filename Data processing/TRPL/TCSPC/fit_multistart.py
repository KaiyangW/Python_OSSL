"""
Multi-start parallel optimisation helper for TRPL lifetime fits.

This module is a drop-in wrapper around the single ``scipy.optimize.least_squares``
call used by ``Recon_fit_process.run_fitting_process`` and
``Tail_fit_process.run_fitting_process``. It does NOT change the residual /
model math: it only runs the existing residual function from many different
initial guesses in parallel and returns the one with the lowest reduced
chi-squared.

Design notes
------------
* All worker-facing functions are module-level so they pickle cleanly under
  Windows ``spawn`` semantics.
* The residual and model functions are imported lazily inside the worker
  kernels to avoid importing GUI modules in worker processes.
* A single ``ProcessPoolExecutor`` is created lazily on the first call and
  reused for the lifetime of the Python process (registered for atexit
  shutdown). This keeps per-fit latency low after the first run.
* The user's exact initial guess is always included as start #0, so the
  multi-start result can never be worse than the previous single-start fit.
* Tune the scan width / cost via the module-level DEFAULT_* constants below.
"""

import os
import atexit
from itertools import product
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from scipy.optimize import least_squares


# ----------------------------------------------------------------------
# Tunables. Edit these to widen/narrow the parameter scan.
# ----------------------------------------------------------------------
DEFAULT_N_TAU_PER_COMP  = 2      # number of log-spaced tau seeds per component (reduced from 3)
DEFAULT_TAU_FACTOR      = 3.0    # tau scan range = [tau_user / f, tau_user * f] (reduced from 5.0)
DEFAULT_N_BETA_PER_COMP = 1      # number of beta seeds per component (reduced from 2)
DEFAULT_N_MAX_STARTS    = 8      # cap on parallel starts; combos beyond this are subsampled (reduced from 32)
DEFAULT_MAX_NFEV        = 2000   # per-start function-evaluation cap (reduced from 5000)


# ----------------------------------------------------------------------
# Result container (mimics scipy OptimizeResult enough for the engines)
# ----------------------------------------------------------------------
class _BestFit:
    __slots__ = ('x', 'chi_sq', 'n_starts', 'n_success')

    def __init__(self, x, chi_sq, n_starts, n_success):
        self.x = x
        self.chi_sq = chi_sq
        self.n_starts = n_starts
        self.n_success = n_success


def _reduced_chi_sq(data, model, fit_mask, n_params):
    r2 = (data[fit_mask] - model[fit_mask]) ** 2
    var = np.maximum(data[fit_mask], 1)
    dof = max(1, len(data[fit_mask]) - n_params)
    return float(np.sum(r2 / var) / dof)


# ----------------------------------------------------------------------
# Pickleable single-start kernels (run inside worker processes).
# They re-use the residual / model functions from the existing engines
# without any math change.
# ----------------------------------------------------------------------
def _fit_once_recon(p0, lower, upper, t, data, irf, dt, fit_mask, num_exp):
    """Run one reconvolution least_squares. Returns (x, chi_sq, ok)."""
    try:
        from Recon_fit_process import residuals, stretched_exp_reconv
        res = least_squares(
            residuals, p0, bounds=(lower, upper),
            args=(t, data, irf, dt, fit_mask, num_exp),
            loss='soft_l1', f_scale=1.0, x_scale='jac',
            ftol=1e-8, xtol=1e-8, max_nfev=DEFAULT_MAX_NFEV,
        )
        model = stretched_exp_reconv(res.x, t, irf, dt, num_exp)
        chi = _reduced_chi_sq(data, model, fit_mask, len(p0))
        if not np.isfinite(chi):
            return None, np.inf, False
        return res.x, chi, True
    except Exception:
        return None, np.inf, False


def _fit_once_tail(p0, lower, upper, t, data, fit_mask, xmin, num_exp):
    """Run one tail least_squares. Returns (x, chi_sq, ok)."""
    try:
        from Tail_fit_process import residuals, multi_exp_tail
        res = least_squares(
            residuals, p0, bounds=(lower, upper),
            args=(t, data, fit_mask, xmin, num_exp),
            loss='soft_l1', f_scale=1.0, x_scale='jac',
            ftol=1e-8, xtol=1e-8, max_nfev=DEFAULT_MAX_NFEV,
        )
        model = multi_exp_tail(res.x, t, xmin, num_exp)
        chi = _reduced_chi_sq(data, model, fit_mask, len(p0))
        if not np.isfinite(chi):
            return None, np.inf, False
        return res.x, chi, True
    except Exception:
        return None, np.inf, False


# ----------------------------------------------------------------------
# Initial-guess generation
# ----------------------------------------------------------------------
def _tau_grid(tau_user, factor, n):
    """Log-spaced tau seeds around the user's value."""
    if n <= 1 or factor <= 1.0:
        return np.array([float(tau_user)])
    lo = max(1e-3, float(tau_user) / factor)
    hi = float(tau_user) * factor
    return np.logspace(np.log10(lo), np.log10(hi), n)


def _beta_grid(beta_user, fixed, n):
    """A small grid of beta seeds inside [0.3, 1.0]."""
    if fixed or n <= 1:
        return np.array([float(beta_user)])
    candidates = sorted({round(v, 2) for v in (0.5, 0.7, 0.85, 1.0, float(beta_user))})
    candidates = [b for b in candidates if 0.3 <= b <= 1.0]
    if len(candidates) > n:
        idx = np.linspace(0, len(candidates) - 1, n).round().astype(int)
        keep = sorted({int(i) for i in idx})
        candidates = [candidates[i] for i in keep]
        if round(float(beta_user), 2) not in [round(c, 2) for c in candidates]:
            candidates[-1] = float(beta_user)
    return np.array(candidates, dtype=float)


def _component_combos(num_exp, tau_grids, beta_grids, n_max, rng):
    """
    Per-component (tau, beta) cartesian product, capped at n_max via random
    subsampling. Returns a list whose elements are lists of (tau, beta)
    tuples (one tuple per component).
    """
    per_comp = [[(t, b) for t in tg for b in bg]
                for tg, bg in zip(tau_grids, beta_grids)]
    total = 1
    for p in per_comp:
        total *= len(p)
    if total <= n_max:
        return [list(c) for c in product(*per_comp)]

    seen, sampled = set(), []
    attempts = 0
    max_attempts = n_max * 20
    while len(sampled) < n_max and attempts < max_attempts:
        attempts += 1
        combo = [tuple(p[rng.integers(0, len(p))]) for p in per_comp]
        key = tuple((round(c[0], 6), round(c[1], 4)) for c in combo)
        if key in seen:
            continue
        seen.add(key)
        sampled.append(combo)
    return sampled


def _build_starts(base_p0, lower, upper, fixed_t_flags, fixed_b_flags,
                  num_exp, tau_idx, beta_idx,
                  n_tau, tau_factor, n_beta, n_max, seed):
    """
    Build a list of (p0, lower, upper) triples for the multi-start.
    ``tau_idx(i)`` and ``beta_idx(i)`` map component index i to its
    position inside the parameter vector (different layouts for recon vs tail).
    """
    rng = np.random.default_rng(seed)
    base = np.asarray(base_p0, dtype=float)
    lo_arr = np.asarray(lower, dtype=float)
    hi_arr = np.asarray(upper, dtype=float)

    tau_grids, beta_grids = [], []
    for i in range(num_exp):
        ti, bi = tau_idx(i), beta_idx(i)
        tg = _tau_grid(base[ti], tau_factor,
                       1 if fixed_t_flags[i] else n_tau)
        bg = _beta_grid(base[bi], fixed_b_flags[i], n_beta)
        tg = np.clip(tg, lo_arr[ti], hi_arr[ti])
        bg = np.clip(bg, lo_arr[bi], hi_arr[bi])
        tau_grids.append(np.unique(tg))
        beta_grids.append(np.unique(bg))

    combos = _component_combos(num_exp, tau_grids, beta_grids, n_max, rng)

    # Always include the user's exact guess as start #0.
    starts = [(base.copy(), lo_arr.copy(), hi_arr.copy())]
    seen = {tuple(np.round(base, 8))}
    for combo in combos:
        p0 = base.copy()
        for i, (tau_i, beta_i) in enumerate(combo):
            p0[tau_idx(i)]  = tau_i
            p0[beta_idx(i)] = beta_i
        key = tuple(np.round(p0, 8))
        if key in seen:
            continue
        seen.add(key)
        starts.append((p0, lo_arr.copy(), hi_arr.copy()))
    return starts


# ----------------------------------------------------------------------
# Persistent process pool
# ----------------------------------------------------------------------
_pool_state = {'pool': None, 'workers': None}


def get_pool(max_workers=None):
    """Return a shared ProcessPoolExecutor, creating it lazily."""
    pool = _pool_state['pool']
    if pool is not None:
        return pool
    if max_workers is None:
        max_workers = max(1, os.cpu_count() or 2)
    try:
        pool = ProcessPoolExecutor(max_workers=max_workers)
    except Exception as e:
        print(f"[multistart] could not start process pool ({e}); will run serially.")
        return None
    _pool_state['pool'] = pool
    _pool_state['workers'] = max_workers
    atexit.register(shutdown_pool)
    return pool


def shutdown_pool():
    pool = _pool_state.get('pool')
    if pool is None:
        return
    try:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            pool.shutdown(wait=False)
    except Exception:
        pass
    finally:
        _pool_state['pool'] = None


# ----------------------------------------------------------------------
# Public drivers — these are what the fit engines call
# ----------------------------------------------------------------------
def _run_serial(starts, kernel, kernel_args):
    """Serial fallback used when the process pool is unavailable."""
    best_x, best_chi, n_ok = None, np.inf, 0
    for p0, lo, hi in starts:
        x, chi, ok = kernel(p0, lo, hi, *kernel_args)
        if ok and x is not None:
            n_ok += 1
            if chi < best_chi:
                best_chi, best_x = chi, x
    if best_x is None:
        return _BestFit(starts[0][0], np.inf, len(starts), 0)
    return _BestFit(best_x, best_chi, len(starts), n_ok)


def _run(starts, kernel, kernel_args):
    """Submit all starts to the pool, return the _BestFit."""
    # Trivial case: one start (e.g., all params fixed) -> run in-process.
    if len(starts) == 1:
        p0, lo, hi = starts[0]
        x, chi, ok = kernel(p0, lo, hi, *kernel_args)
        return _BestFit(x if x is not None else p0,
                        chi if ok else np.inf,
                        1, int(ok))

    pool = get_pool()
    if pool is None:
        return _run_serial(starts, kernel, kernel_args)

    try:
        futures = [pool.submit(kernel, p0, lo, hi, *kernel_args)
                   for (p0, lo, hi) in starts]
    except Exception as e:
        print(f"[multistart] pool submit failed ({e}); falling back to serial.")
        shutdown_pool()
        return _run_serial(starts, kernel, kernel_args)

    best_x, best_chi, n_ok = None, np.inf, 0
    for fut in as_completed(futures):
        try:
            x, chi, ok = fut.result()
        except Exception:
            continue
        if ok and x is not None:
            n_ok += 1
            if chi < best_chi:
                best_chi, best_x = chi, x

    if best_x is None:
        # Every start failed in the pool -> retry the user's guess in-process
        # so the calling engine still has a usable result.
        p0, lo, hi = starts[0]
        x, chi, ok = kernel(p0, lo, hi, *kernel_args)
        return _BestFit(x if x is not None else p0,
                        chi if ok else np.inf,
                        len(starts), int(ok))
    return _BestFit(best_x, best_chi, len(starts), n_ok)


def run_multistart_recon(p0_user, lower, upper,
                         t, data, irf, dt, fit_mask, num_exp,
                         fixed_t_flags, fixed_b_flags,
                         n_tau=DEFAULT_N_TAU_PER_COMP,
                         tau_factor=DEFAULT_TAU_FACTOR,
                         n_beta=DEFAULT_N_BETA_PER_COMP,
                         n_max=DEFAULT_N_MAX_STARTS,
                         seed=0):
    """
    Reconvolution parameter layout:
        [shift, bkg, scatter, B1, tau1, beta1, B2, tau2, beta2, ...]
    => tau idx = 4 + 3*i ,  beta idx = 5 + 3*i
    """
    starts = _build_starts(
        p0_user, lower, upper, fixed_t_flags, fixed_b_flags, num_exp,
        tau_idx=lambda i: 4 + 3 * i,
        beta_idx=lambda i: 5 + 3 * i,
        n_tau=n_tau, tau_factor=tau_factor,
        n_beta=n_beta, n_max=n_max, seed=seed,
    )
    return _run(starts, _fit_once_recon,
                kernel_args=(t, data, irf, dt, fit_mask, num_exp))


def run_multistart_tail(p0_user, lower, upper,
                        t, data, fit_mask, xmin, num_exp,
                        fixed_t_flags, fixed_b_flags,
                        n_tau=DEFAULT_N_TAU_PER_COMP,
                        tau_factor=DEFAULT_TAU_FACTOR,
                        n_beta=DEFAULT_N_BETA_PER_COMP,
                        n_max=DEFAULT_N_MAX_STARTS,
                        seed=0):
    """
    Tail parameter layout:
        [bkg, B1, tau1, beta1, B2, tau2, beta2, ...]
    => tau idx = 2 + 3*i ,  beta idx = 3 + 3*i
    """
    starts = _build_starts(
        p0_user, lower, upper, fixed_t_flags, fixed_b_flags, num_exp,
        tau_idx=lambda i: 2 + 3 * i,
        beta_idx=lambda i: 3 + 3 * i,
        n_tau=n_tau, tau_factor=tau_factor,
        n_beta=n_beta, n_max=n_max, seed=seed,
    )
    return _run(starts, _fit_once_tail,
                kernel_args=(t, data, fit_mask, xmin, num_exp))
