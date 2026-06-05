import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import os

# ==========================================
#  Core Algorithm (Math only)
#  Note: graphs saved are for reference only
# ==========================================

def trim_saturation_constrained(x, y, max_trim=3):
    """
    Attempts to trim the last 0~max_trim points to find the steepest slope.
    Used to remove saturation/rollover effects at high energy.
    """
    n_total = len(x)
    n_check = max(5, int(n_total * 0.4))
    
    best_slope = -float('inf')
    best_k = 0
    
    for k in range(max_trim + 1):
        if k == 0:
            x_sub = x[-n_check:]
            y_sub = y[-n_check:]
        else:
            x_sub = x[-n_check:-k]
            y_sub = y[-n_check:-k]
            
        if len(x_sub) < 3: 
            continue

        try:
            m, c = np.polyfit(x_sub, y_sub, 1)
            if m > best_slope:
                best_slope = m
                best_k = k
        except:
            continue

    if best_k == 0:
        return x, y, 0
    else:
        return x[:-best_k], y[:-best_k], best_k

def hinge_model(x, x_th, k1, k2, b1):
    """
    Continuous piecewise function (Hinge Model).
    x < x_th: Fluorescence region (slope k1)
    x > x_th: Lasing region (slope k2)
    """
    return np.where(x < x_th, 
                    k1 * x + b1, 
                    k1 * x_th + b1 + k2 * (x - x_th))

def make_piecewise_model(n_turns):
    """
    Create a continuous piecewise linear model with n_turns turn points.

    Parameters layout: [x_th1, dx2, ..., k1, ..., k_{n+1}, b1]
    The dx parameters keep the turn points ordered from left to right.
    """
    def model(x, *params):
        x_ths = [params[0]]
        for i in range(1, n_turns):
            x_ths.append(x_ths[-1] + params[i])

        ks = list(params[n_turns:2 * n_turns + 1])
        b1 = params[2 * n_turns + 1]

        vals = [ks[0] * x_ths[0] + b1]
        for i in range(1, n_turns):
            vals.append(vals[i - 1] + ks[i] * (x_ths[i] - x_ths[i - 1]))

        result = ks[0] * x + b1
        for i in range(n_turns):
            result = np.where(
                x >= x_ths[i],
                vals[i] + ks[i + 1] * (x - x_ths[i]),
                result,
            )
        return result

    return model

def cumulative_turn_points(params, n_turns):
    """Convert [x_th1, dx2, ...] parameters to absolute turn-point positions."""
    x_ths = [params[0]]
    for i in range(1, n_turns):
        x_ths.append(x_ths[-1] + params[i])
    return x_ths

def _evaluate_model_r_squared(x_eval, y_eval, model_key, res):
    """Compute R² on a common evaluation set for fair model comparison."""
    if model_key == "three_line":
        n_turns = 2
        fit_model = make_piecewise_model(n_turns)
        y_pred = fit_model(x_eval, *res['params'])
    else:
        x_th, k1, k2, b1 = res['params']
        y_pred = hinge_model(x_eval, x_th, k1, k2, b1)

    ss_res = np.sum((y_eval - y_pred) ** 2)
    ss_tot = np.sum((y_eval - np.mean(y_eval)) ** 2)
    return 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan


def find_threshold_robust(x_raw, y_raw, apply_saturation_trim=True):
    # 1. Trim saturation points (helps the 2-segment hinge model)
    if apply_saturation_trim:
        x, y, trimmed_count = trim_saturation_constrained(x_raw, y_raw, max_trim=3)
    else:
        x, y, trimmed_count = x_raw, y_raw, 0
    
    if len(x) < 4: return None 

    # 2. Initial Parameter Estimation
    x_min, x_max = np.min(x), np.max(x)
    y_min, y_max = np.min(y), np.max(y)
    
    # Estimate slopes
    # k1 (Fluorescence): Fit the first 25% points
    n_fluo = max(3, int(len(x) * 0.25))
    try:
        p_fluo = np.polyfit(x[:n_fluo], y[:n_fluo], 1)
        k1_guess = p_fluo[0]
        b1_guess = p_fluo[1]
    except:
        k1_guess = 0
        b1_guess = y_min

    # k2 (Lasing): Fit the last 40% points (More robust than min/max)
    n_lasing = max(3, int(len(x) * 0.4))
    try:
        p_lasing = np.polyfit(x[-n_lasing:], y[-n_lasing:], 1)
        k2_guess = p_lasing[0]
    except:
        k2_guess = (y_max - y_min) / (x_max - x_min + 1e-9)

    # Threshold guess: Middle of the dynamic range
    x_th_guess = (x_min + x_max) / 2

    # Initial guess [x_th, k1, k2, b1]
    p0 = [x_th_guess, k1_guess, k2_guess, b1_guess]
    
    # Bounds: allow negative intercepts and negative fluorescence slopes for better convergence
    bounds = (
        [x_min, -np.inf, 0,      -np.inf], 
        [x_max, np.inf,  np.inf, np.inf]   
    )
    
    try:
        popt, pcov = curve_fit(hinge_model, x, y, p0=p0, bounds=bounds)
    except Exception:
        return None
        
    x_th_fit, k1_fit, k2_fit, b1_fit = popt
    
    x_th_err = np.nan
    if pcov is not None:
        try:
            perr = np.sqrt(np.diag(pcov))
            x_th_err = perr[0]
        except: pass

    return {
        'threshold': x_th_fit,
        'threshold_err': x_th_err,
        'params': popt,
        'x_used': x,
        'y_used': y,
        'trimmed_count': trimmed_count
    }

def find_threshold_three_line(x_raw, y_raw, apply_saturation_trim=False):
    """
    Fit a continuous 3-line model with two ordered turn points.
    The primary threshold returned is the first turn point.

    Saturation trimming is off by default: the third segment and TP2 need
    high-energy points that trim_saturation_constrained would remove.
    """
    n_turns = 2
    if apply_saturation_trim:
        x, y, trimmed_count = trim_saturation_constrained(x_raw, y_raw, max_trim=3)
    else:
        x, y, trimmed_count = x_raw, y_raw, 0

    n_params = 2 * n_turns + 2
    if len(x) < n_params + 1:
        return None

    x_min, x_max = np.min(x), np.max(x)
    x_range = x_max - x_min
    y_min, y_max = np.min(y), np.max(y)

    if x_range <= 0:
        return None

    x_th_guesses = [x_min + (i + 1) * x_range / (n_turns + 1) for i in range(n_turns)]
    p0_x = [x_th_guesses[0], x_th_guesses[1] - x_th_guesses[0]]

    k_guesses = []
    chunk_bounds = [x_min] + x_th_guesses + [x_max]
    avg_slope = (y_max - y_min) / (x_range + 1e-9)

    for i in range(n_turns + 1):
        mask_chunk = (x >= chunk_bounds[i]) & (x <= chunk_bounds[i + 1])
        if np.sum(mask_chunk) >= 2:
            try:
                k, _ = np.polyfit(x[mask_chunk], y[mask_chunk], 1)
                k_guesses.append(k)
            except Exception:
                k_guesses.append(avg_slope)
        else:
            k_guesses.append(avg_slope)

    mask_first = (x >= chunk_bounds[0]) & (x <= chunk_bounds[1])
    if np.sum(mask_first) >= 2:
        try:
            _, b1_guess = np.polyfit(x[mask_first], y[mask_first], 1)
        except Exception:
            b1_guess = y_min
    else:
        b1_guess = y_min

    p0 = p0_x + k_guesses + [b1_guess]
    min_dx = max(1e-9, 1e-5 * x_range)
    bounds = (
        [x_min, min_dx, -np.inf, -np.inf, -np.inf, -np.inf],
        [x_max, x_range, np.inf, np.inf, np.inf, np.inf],
    )

    model = make_piecewise_model(n_turns)

    try:
        popt, pcov = curve_fit(model, x, y, p0=p0, bounds=bounds, maxfev=10000)
    except Exception:
        return None

    x_ths = cumulative_turn_points(popt, n_turns)

    x_th_errs = [np.nan] * n_turns
    if pcov is not None:
        try:
            if not np.isinf(pcov).any():
                cov_x = pcov[:n_turns, :n_turns]
                x_th_errs = []
                for i in range(n_turns):
                    var_x_i = np.sum(cov_x[:i + 1, :i + 1])
                    x_th_errs.append(np.sqrt(max(0, var_x_i)))
        except Exception:
            x_th_errs = [np.nan] * n_turns

    return {
        'threshold': x_ths[0],
        'threshold_err': x_th_errs[0],
        'turn_points': x_ths,
        'turn_point_errs': x_th_errs,
        'params': popt,
        'x_used': x,
        'y_used': y,
        'trimmed_count': trimmed_count
    }


def find_threshold_auto(x_raw, y_raw):
    """
    Fit both hinge (2-segment) and 3-line models, then pick the higher R².

    Hinge uses saturation trimming; 3-line keeps all points so TP2/saturation
    can be modelled. R² is evaluated on the full clean dataset for both fits.
    """
    candidates = []

    hinge_res = find_threshold_robust(x_raw, y_raw, apply_saturation_trim=True)
    if hinge_res is not None:
        hinge_r2 = _evaluate_model_r_squared(x_raw, y_raw, "hinge", hinge_res)
        candidates.append(("hinge", hinge_res, hinge_r2))

    three_line_res = find_threshold_three_line(x_raw, y_raw, apply_saturation_trim=False)
    if three_line_res is not None:
        three_line_r2 = _evaluate_model_r_squared(x_raw, y_raw, "three_line", three_line_res)
        candidates.append(("three_line", three_line_res, three_line_r2))

    if not candidates:
        return None

    model_key, best_res, best_r2 = max(
        candidates,
        key=lambda item: item[2] if np.isfinite(item[2]) else -np.inf,
    )

    alt_r2 = {
        "hinge": np.nan,
        "three_line": np.nan,
    }
    for key, _, r2 in candidates:
        alt_r2[key] = r2

    best_res = dict(best_res)
    best_res["selected_model"] = model_key
    best_res["r_squared"] = best_r2
    best_res["r_squared_hinge"] = alt_r2["hinge"]
    best_res["r_squared_three_line"] = alt_r2["three_line"]
    return best_res

# ==========================================
#  Interface (Called by Main Program)
# ==========================================

def run_threshold_analysis(x_data, y_data, save_folder, file_prefix, model_type="auto"):
    """
    Executes threshold analysis, saves 3:1 subplots (Fit + Residuals), 
    and exports detailed fit parameters to Excel.
    """
    # Data Cleaning and Sorting
    sorted_indices = np.argsort(x_data)
    x_sorted = np.array(x_data)[sorted_indices]
    y_sorted = np.array(y_data)[sorted_indices]
    
    mask = np.isfinite(x_sorted) & np.isfinite(y_sorted)
    x_clean = x_sorted[mask]
    y_clean = y_sorted[mask]

    model_key = str(model_type).strip().lower().replace("-", "_").replace(" ", "_")
    if model_key in ("3_line", "three_line", "piecewise_3", "two_turn_points"):
        model_key = "three_line"
    elif model_key in ("auto", "best", "best_r2"):
        model_key = "auto"
    else:
        model_key = "hinge"

    if len(x_clean) < 5:
        return {"status": "Not enough data points", "threshold": np.nan, "error": np.nan}

    auto_meta = {}
    if model_key == "auto":
        res = find_threshold_auto(x_clean, y_clean)
        if res is not None:
            model_key = res.pop("selected_model")
            auto_meta = {
                "r_squared_hinge": res.pop("r_squared_hinge", np.nan),
                "r_squared_three_line": res.pop("r_squared_three_line", np.nan),
                "r_squared_selected": res.pop("r_squared", np.nan),
            }
    elif model_key == "three_line":
        res = find_threshold_three_line(x_clean, y_clean, apply_saturation_trim=False)
    else:
        res = find_threshold_robust(x_clean, y_clean, apply_saturation_trim=True)
    
    if res is None:
        return {"status": "Fit failed to converge", "threshold": np.nan, "error": np.nan}

    x_th = res['threshold']
    x_th_err = res['threshold_err']
    
    # Extract ONLY the points used in the final fit (excluding saturated/trimmed points)
    x_used = res['x_used']
    y_used = res['y_used']

    if model_key == "three_line":
        n_turns = 2
        fit_model = make_piecewise_model(n_turns)
        x_ths = res['turn_points']
        x_th_errs = res['turn_point_errs']
        ks = list(res['params'][n_turns:2 * n_turns + 1])
        b1 = res['params'][2 * n_turns + 1]
        y_fit_eval = fit_model(x_used, *res['params'])
        slope_ratio = ks[-1] / ks[0] if abs(ks[0]) > 1e-9 else np.inf
        model_label = "3-Line Fit Model"
    else:
        k1, k2, b1 = res['params'][1], res['params'][2], res['params'][3]
        y_fit_eval = hinge_model(x_used, x_th, k1, k2, b1)
        slope_ratio = k2/k1 if k1 > 1e-9 else 9999
        if k1 < 0: slope_ratio = np.inf
        model_label = "Hinge Fit Model"

    residuals = y_used - y_fit_eval
    
    # R² on points used for fitting (for plot title / Excel)
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y_used - np.mean(y_used))**2)
    r_squared_fit = 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
    # R² on full clean data (used for auto model selection)
    r_squared_full = _evaluate_model_r_squared(x_clean, y_clean, model_key, res)
    r_squared = r_squared_full if np.isfinite(r_squared_full) else r_squared_fit
    
    # --- Plotting ---
    # Create subplots with 3:1 height ratio and shared X-axis
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(19.2, 14.4), dpi=600, 
                                   gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    plt.subplots_adjust(hspace=0.05) 
    
    # --- Top Plot (ax1): Main Fit ---
    ax1.scatter(x_clean, y_clean, c='gray', alpha=0.4, s=80, label='Trimmed / Raw Points')
    ax1.scatter(x_used, y_used, c='black', s=100, label='Data Used for Fit')
    
    # Plot smooth fit curve
    x_plot = np.linspace(min(x_clean), max(x_used), 200)
    if model_key == "three_line":
        y_plot = fit_model(x_plot, *res['params'])
    else:
        y_plot = hinge_model(x_plot, x_th, k1, k2, b1)
    
    # Visual Polish: Only CLIP the PLOT view, not the data
    y_plot_view = np.copy(y_plot)
    y_plot_view[y_plot_view < 0] = 0
    
    ax1.plot(x_plot, y_plot_view, 'r-', linewidth=3, label=model_label)
    
    # Mark threshold(s)
    if model_key == "three_line":
        turn_colors = ['green', 'purple']
        for i, x_tp in enumerate(x_ths):
            color = turn_colors[i % len(turn_colors)]
            ax1.axvline(x=x_tp, color=color, linestyle='--', alpha=0.8, linewidth=2)
            y_marker = fit_model(np.array([x_tp]), *res['params'])[0]
            if y_marker < 0: y_marker = 0
            ax1.plot(x_tp, y_marker, '*', color=color, markersize=25, label=f'Turn Point {i + 1}')
    else:
        ax1.axvline(x=x_th, color='green', linestyle='--', alpha=0.8, linewidth=2)
        y_marker = hinge_model(x_th, x_th, k1, k2, b1)
        if y_marker < 0: y_marker = 0 
        ax1.plot(x_th, y_marker, 'g*', markersize=25, label='Threshold')
    
    # Title and Labels for ax1
    err_str = f"± {x_th_err:.2e}" if (np.isfinite(x_th_err) and x_th_err > 0) else ""
    
    auto_note = ""
    if auto_meta:
        auto_note = (
            f"  |  Auto: Hinge R$^2$={auto_meta['r_squared_hinge']:.4f}, "
            f"3-Line R$^2$={auto_meta['r_squared_three_line']:.4f}"
        )

    if model_key == "three_line":
        err2_str = f" ± {x_th_errs[1]:.2e}" if (len(x_th_errs) > 1 and np.isfinite(x_th_errs[1]) and x_th_errs[1] > 0) else ""
        title_text = (f"Automated 3-Line Threshold Fit: {file_prefix}\n"
                      f"TP1 = {x_ths[0]:.4e} {err_str} uJ/cm2  |  TP2 = {x_ths[1]:.4e}{err2_str} uJ/cm2\n"
                      f"Slope Ratio (k3/k1) = {slope_ratio:.1f}  |  R$^2$ = {r_squared:.4f}{auto_note}")
    else:
        title_text = (f"Automated Threshold Fit: {file_prefix}\n"
                      f"Threshold = {x_th:.4e} {err_str} uJ/cm2\n"
                      f"Slope Efficiency Ratio (Lasing/Fluo) = {slope_ratio:.1f}  |  R$^2$ = {r_squared:.4f}{auto_note}")
    
    ax1.set_title(title_text, fontsize=20, pad=20)
    ax1.set_ylabel('Integrated Intensity (PL Subtracted, arb. units)', fontsize=16)
    ax1.tick_params(axis='both', which='major', labelsize=14)
    ax1.legend(fontsize=14)
    ax1.grid(True, linestyle=':', alpha=0.6)
    
    # --- Bottom Plot (ax2): Residuals ---
    # Plot residuals ONLY for the x_used points
    ax2.scatter(x_used, residuals, c='blue', alpha=0.6, s=80, label='Residuals (Fitted Data Only)')
    ax2.axhline(0, color='black', linestyle='--', linewidth=2) 
    
    # Labels for ax2
    ax2.set_xlabel('Pump Energy Density (uJ/cm$^2$)', fontsize=16)
    ax2.set_ylabel('Residuals', fontsize=16)
    ax2.tick_params(axis='both', which='major', labelsize=14)
    ax2.legend(fontsize=14)
    ax2.grid(True, linestyle=':', alpha=0.6)
    
    # Save Image
    img_path = os.path.join(save_folder, f"Fit_Plot_{file_prefix}.png")
    fig.savefig(img_path, dpi=100, bbox_inches='tight')
    plt.close(fig) 
    
    # --- Save Excel ---
    df_curve = pd.DataFrame({'X_Fit': x_plot, 'Y_Fit': y_plot})
    if model_key == "three_line":
        df_params = pd.DataFrame([{
            'Model': 'Three-Line (Two Turn Points)',
            'Selection_Mode': 'Auto' if auto_meta else 'Manual',
            'Threshold': x_ths[0],
            'Error': x_th_errs[0],
            'Turn_Point_1': x_ths[0],
            'Turn_Point_1_Error': x_th_errs[0],
            'Turn_Point_2': x_ths[1],
            'Turn_Point_2_Error': x_th_errs[1] if len(x_th_errs) > 1 else np.nan,
            'Slope_1 (k1)': ks[0],
            'Slope_2 (k2)': ks[1],
            'Slope_3 (k3)': ks[2],
            'Intercept (b1)': b1,
            'Slope_Ratio': slope_ratio,
            'R_Squared_Full_Data': r_squared,
            'R_Squared_Fit_Points': r_squared_fit,
            'R_Squared_Hinge': auto_meta.get('r_squared_hinge', np.nan),
            'R_Squared_3Line': auto_meta.get('r_squared_three_line', np.nan),
            'Trimmed_Points': res['trimmed_count']
        }])
    else:
        df_params = pd.DataFrame([{
            'Model': 'Hinge (Two Segments)',
            'Selection_Mode': 'Auto' if auto_meta else 'Manual',
            'Threshold': x_th,
            'Error': x_th_err,
            'Slope_Fluo (k1)': k1,
            'Slope_Lasing (k2)': k2,
            'Intercept (b1)': b1,
            'Slope_Ratio': slope_ratio,
            'R_Squared_Full_Data': r_squared,
            'R_Squared_Fit_Points': r_squared_fit,
            'R_Squared_Hinge': auto_meta.get('r_squared_hinge', np.nan),
            'R_Squared_3Line': auto_meta.get('r_squared_three_line', np.nan),
            'Trimmed_Points': res['trimmed_count']
        }])
    df_data = pd.DataFrame({'Energy_Input': x_clean, 'Intensity_Input': y_clean})
    
    # Save residuals ONLY for the fitted data points in Sheet 3
    df_residuals = pd.DataFrame({'Energy_Input_Used': x_used, 'Residuals': residuals}) 

    xlsx_path = os.path.join(save_folder, f"{file_prefix}_auto_fit.xlsx")
    try:
        with pd.ExcelWriter(xlsx_path) as writer:
            df_params.to_excel(writer, sheet_name='Parameters', index=False)      
            df_data.to_excel(writer, sheet_name='Input_Data', index=False)        
            df_curve.to_excel(writer, sheet_name='Fit_Curve', index=False)        
            df_residuals.to_excel(writer, sheet_name='Residuals', index=False)    
        model_label = "3-Line" if model_key == "three_line" else "Hinge"
        result = {
            "status": f"Success ({model_label}): {x_th:.2e}",
            "threshold": x_th,
            "error": x_th_err,
            "slope_ratio": slope_ratio,
            "model": model_label,
            "r_squared": r_squared,
        }
        if auto_meta:
            result["r_squared_hinge"] = auto_meta["r_squared_hinge"]
            result["r_squared_three_line"] = auto_meta["r_squared_three_line"]
        if model_key == "three_line":
            result["turn_points"] = x_ths
            result["turn_point_errors"] = x_th_errs
        return result
    except Exception as e:
        model_label = "3-Line" if model_key == "three_line" else "Hinge"
        return {
            "status": f"Save Error: {e}",
            "threshold": x_th,
            "error": x_th_err,
            "slope_ratio": slope_ratio,
            "model": model_label,
        }