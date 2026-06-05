import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import gamma
import Area_Analysis_Engine
from fit_multistart import run_multistart_tail
from fit_uncertainty import (
    estimate_poisson_covariance,
    compute_component_uncertainties,
    compute_selected_avg_stderr,
    attach_param_indices,
    format_val_err,
    format_beta_err,
)

# --- Dynamic Tail-Fit Math Engine ---
# This module is the computational counterpart to Recon_fit_process.py.
# It performs a direct (no-convolution) multi-component stretched-exponential
# tail fit, starting from a user-defined xmin.

def auto_format_time(tau_ns):
    """Auto-converts a nanosecond value to a human-readable string with units."""
    try:
        val = float(tau_ns)
        if val == 0:
            return "0.00 ns"
        elif val < 1e3:
            return f"{val:.2f} ns"
        elif val < 1e6:
            return f"{val/1e3:.2f} µs"
        elif val < 1e9:
            return f"{val/1e6:.2f} ms"
        else:
            return f"{val/1e9:.2f} s"
    except (ValueError, TypeError):
        return tau_ns


def multi_exp_tail(params, t_uniform, xmin, num_exp):
    """
    Multi-component stretched-exponential decay model for tail fitting.
    The decay starts at xmin (the tail start time).

    params order: [bkg, amp1, tau1, beta1, amp2, tau2, beta2, ...]
    """
    bkg = params[0]
    model_total = np.full_like(t_uniform, bkg)

    decay_mask = t_uniform >= xmin
    if not np.any(decay_mask):
        return model_total

    t_decay = t_uniform[decay_mask] - xmin
    decay = np.zeros_like(t_decay)

    for i in range(num_exp):
        amp_i  = params[1 + i*3]
        tau_i  = params[2 + i*3]
        beta_i = params[3 + i*3]
        decay += amp_i * np.exp(-np.power(t_decay / tau_i, beta_i))

    model_total[decay_mask] += decay
    return model_total


def residuals(params, t_uniform, data_uniform, fit_mask, xmin, num_exp):
    """Poisson-weighted residuals for the tail model."""
    model = multi_exp_tail(params, t_uniform, xmin, num_exp)
    model_slice = model[fit_mask]
    data_slice  = data_uniform[fit_mask]
    weights = 1.0 / np.sqrt(np.maximum(data_slice, 1))
    return (model_slice - data_slice) * weights


# --- Main Fitting Process ---

def run_fitting_process(app_state, xmin, xmax, initial_taus, fixed_t_flags,
                        initial_betas, fixed_b_flags, num_exp,
                        phos_idx, calc_pf_df, avg_comps_str, plqy_val=0.0, pf_comp='C1', df_comp='C2',
                        use_extrapolation=True, calc_risc=False):
    """
    Executes the tail-fit optimisation and updates the plot.

    Signature mirrors Recon_fit_process.run_fitting_process so the unified
    UI can call either engine with the same arguments.
    app_state must contain: 't', 'data', 'dt', 'ax', 'ax_res', 'fig',
                            'fit_line', 'res_line', 'text_box', 'vlines',
                            'fit_results'.
    irf / irf_display keys are accepted but ignored.

    use_extrapolation : bool
        If True  (default), compensates the PF area for signal missed before
        xmin using an extrapolation factor derived from C1 parameters.
        If False, uses the simple area-difference method (legacy behaviour).
    """
    t      = app_state['t']
    data   = app_state['data']
    dt     = app_state.get('dt', np.median(np.diff(t)))
    ax     = app_state['ax']
    ax_res = app_state['ax_res']
    fig    = app_state['fig']

    fit_mask = (t >= xmin) & (t <= xmax)
    if not np.any(fit_mask):
        return

    print(f"\nRunning {num_exp}-component tail fit between {xmin:.2f} ns and {xmax:.2f} ns...")

    # --- Update fit-range marker lines ---
    for vline in app_state['vlines']:
        vline.remove()
    app_state['vlines'] = [
        ax.axvline(xmin, color='red', linewidth=1, alpha=0.8, linestyle='solid'),
        ax.axvline(xmax, color='red', linewidth=1, alpha=0.8, linestyle='solid'),
        ax_res.axvline(xmin, color='red', linewidth=1, alpha=0.8, linestyle='solid'),
        ax_res.axvline(xmax, color='red', linewidth=1, alpha=0.8, linestyle='solid'),
    ]

    max_counts = np.max(data[fit_mask])
    bkg_guess  = np.min(data[fit_mask])

    # --- Parameter initialisation: [bkg, amp1, tau1, beta1, ...] ---
    p0           = [bkg_guess]
    lower_bounds = [0]
    upper_bounds = [max_counts]

    epsilon = 1e-6
    for i in range(num_exp):
        current_amp_guess = max(1.0, max_counts / (10 ** i))
        t_guess = initial_taus[i]
        b_guess = initial_betas[i]

        p0.extend([current_amp_guess, t_guess, b_guess])

        lower_bounds.append(0)
        upper_bounds.append(np.inf)

        if fixed_t_flags[i]:
            lower_bounds.append(max(0.01, t_guess - epsilon))
            upper_bounds.append(t_guess + epsilon)
        else:
            lower_bounds.append(0.01)
            upper_bounds.append(np.inf)

        if fixed_b_flags[i]:
            lower_bounds.append(max(0.1, b_guess - epsilon))
            upper_bounds.append(min(1.0, b_guess + epsilon))
        else:
            lower_bounds.append(0.3)
            upper_bounds.append(1.0)

    bounds = (lower_bounds, upper_bounds)

    # --- Optimisation ---
    res = run_multistart_tail(
        p0, lower_bounds, upper_bounds,
        t, data, fit_mask, xmin, num_exp,
        fixed_t_flags, fixed_b_flags
    )
    print(f"Multi-start finished: {res.n_success}/{res.n_starts} successful starts.")

    # --- Goodness of fit ---
    model_full  = multi_exp_tail(res.x, t, xmin, num_exp)
    res_squared = (data[fit_mask] - model_full[fit_mask])**2
    variance    = np.maximum(data[fit_mask], 1)
    chi_sq      = np.sum(res_squared / variance) / (len(data[fit_mask]) - len(p0))

    # --- Extract component parameters ---
    bkg = res.x[0]

    components = []
    total_area = 0.0

    for i in range(num_exp):
        B_i    = res.x[1 + i*3]
        tau_i  = res.x[2 + i*3]
        beta_i = res.x[3 + i*3]

        # num_ave: 1st moment <t> = (tau/beta)*Gamma(1/beta)
        num_ave = (tau_i / beta_i) * gamma(1.0 / beta_i)
        # int_ave: intensity-averaged lifetime for this component
        int_ave  = tau_i * (gamma(2.0 / beta_i) / gamma(1.0 / beta_i))
        area_i   = B_i * num_ave
        total_area += area_i

        components.append({
            'B': B_i, 'tau': tau_i, 'beta': beta_i,
            'num_ave': num_ave, 'int_ave': int_ave,
            'area': area_i, 'is_phos': False
        })

    if total_area == 0:
        total_area = 1e-9

    for i in range(num_exp):
        components[i]['rel_percent'] = (components[i]['area'] / total_area) * 100

    attach_param_indices(
        components, num_exp,
        amp_idx_fn=lambda i: 1 + 3 * i,
        tau_idx_fn=lambda i: 2 + 3 * i,
        beta_idx_fn=lambda i: 3 + 3 * i,
    )
    param_cov = estimate_poisson_covariance(
        residuals, res.x, lower_bounds, upper_bounds,
        args=(t, data, fit_mask, xmin, num_exp),
    )
    comp_uncerts = compute_component_uncertainties(
        res.x, param_cov, num_exp,
        tau_idx_fn=lambda i: 2 + 3 * i,
        beta_idx_fn=lambda i: 3 + 3 * i,
        fixed_t_flags=fixed_t_flags,
        fixed_b_flags=fixed_b_flags,
    )
    for comp, unc in zip(components, comp_uncerts):
        comp.update(unc)

    components = sorted(components, key=lambda x: x['tau'])

    # --- Optional: intensity-averaged lifetime for selected components ---
    selected_avg_tau = None
    selected_avg_tau_stderr = None
    selected_mean_avg_tau_stderr = None
    selected_indices_display = ""
    valid_indices = []
    if avg_comps_str:
        try:
            target_indices = [int(x.strip()) - 1 for x in avg_comps_str.split(',')]
            valid_indices  = [idx for idx in target_indices if 0 <= idx < len(components)]
            if valid_indices:
                sum_area_tau = sum(components[idx]['area'] * components[idx]['int_ave']
                                   for idx in valid_indices)
                sum_area = sum(components[idx]['area'] for idx in valid_indices)
                if sum_area > 0:
                    selected_avg_tau = sum_area_tau / sum_area
                    selected_indices_display = ",".join(str(idx + 1) for idx in valid_indices)
                    selected_avg_tau_stderr = compute_selected_avg_stderr(
                        res.x, param_cov, components, valid_indices, use_int_ave=True,
                    )
        except ValueError:
            pass

    # --- Hidden number-averaged lifetime (used for kinetic rates, not displayed) ---
    selected_mean_avg_tau = None
    if valid_indices:
        try:
            sum_area = sum(components[idx]['area'] for idx in valid_indices)
            if sum_area > 0:
                sum_area_mean_tau = sum(components[idx]['area'] * components[idx]['num_ave'] for idx in valid_indices)
                selected_mean_avg_tau = sum_area_mean_tau / sum_area
                selected_mean_avg_tau_stderr = compute_selected_avg_stderr(
                    res.x, param_cov, components, valid_indices, use_int_ave=False,
                )
        except ValueError:
            pass

    # --- Advanced physics: phosphorescence & PF/DF area ---
    # Delegate all area calculations to the standalone engine.
    area_phos = 0.0
    area_pf_calc = area_df_fit = total_data_area = pf_ratio_clean = df_ratio_clean = phos_percent_total = 0.0
    extrapolation_factor = None

    # Tag phosphorescence (needed for text display even if calc_pf_df is False)
    if phos_idx != -1 and phos_idx < len(components):
        components[phos_idx]['is_phos'] = True
        area_phos = components[phos_idx]['area']

    if calc_pf_df:
        area_result          = Area_Analysis_Engine.calc_tail_difference_areas(
            t, data, xmin, bkg, components, phos_idx,
            use_extrapolation=use_extrapolation
        )
        area_pf_calc         = area_result['area_pf']
        area_df_fit          = area_result['area_df']
        area_phos            = area_result['area_phos']
        total_data_area      = area_result['total_data_area']
        pf_ratio_clean       = area_result['pf_ratio_clean']
        df_ratio_clean       = area_result['df_ratio_clean']
        phos_percent_total   = area_result['phos_percent_total']
        extrapolation_factor = area_result['extrapolation_factor']

    # --- Update plot: fit curve ---
    if app_state['fit_line']:
        app_state['fit_line'].remove()

    t_plot     = t[fit_mask]
    model_plot = model_full[fit_mask]
    app_state['fit_line'], = ax.plot(
        t_plot, model_plot, color='magenta', linewidth=1.5, linestyle='solid', label='Fit'
    )

    # --- Update plot: residuals ---
    if app_state['res_line']:
        app_state['res_line'].remove()

    weighted_res = (data[fit_mask] - model_plot) / np.sqrt(np.maximum(data[fit_mask], 1))
    app_state['res_line'], = ax_res.plot(
        t_plot, weighted_res, color='limegreen', linewidth=1, alpha=0.8
    )

    # --- Build annotation text ---
    from risc_calculator_bridge import clear_risc_text_box, set_fit_summary_panel
    clear_risc_text_box(app_state)
    SEP = "----------------------------------------------\n"
    text_str = (
        f"X2_R: {chi_sq:.3f} | Bkg: {bkg:.1f}\n"
        + SEP
    )
    for i, comp in enumerate(components):
        phos_tag = " [Phos]" if comp['is_phos'] else ""
        t_str = format_val_err(comp['tau'], comp.get('tau_stderr'), auto_format_time)
        b_str = format_beta_err(comp['beta'], comp.get('beta_stderr'))
        n_str = format_val_err(comp['num_ave'], comp.get('num_ave_stderr'), auto_format_time)
        i_str = format_val_err(comp['int_ave'], comp.get('int_ave_stderr'), auto_format_time)
        text_str += f"C{i+1}{phos_tag}: t={t_str}, %={comp['rel_percent']:.1f}\n"
        text_str += f"  ß={b_str}\n"
        text_str += f"  num_ave={n_str}\n"
        text_str += f"  int_ave={i_str}\n"

    if selected_avg_tau is not None:
        text_str += f"Avg (C{selected_indices_display}):\n"
        text_str += f"  int_ave={format_val_err(selected_avg_tau, selected_avg_tau_stderr, auto_format_time)}\n"
        if selected_mean_avg_tau is not None:
            text_str += f"  num_ave={format_val_err(selected_mean_avg_tau, selected_mean_avg_tau_stderr, auto_format_time)}\n"

    if calc_pf_df:
        text_str += SEP
        method_label = "[Area Diff + Extrap]" if use_extrapolation else "[Area Diff]"
        text_str += f"{method_label}\n"
        ratio_line = f"PF: {pf_ratio_clean:.1f}% | DF: {df_ratio_clean:.1f}%"
        if phos_idx != -1:
            ratio_line += f" | Phos: {phos_percent_total:.1f}%"
        text_str += ratio_line + "\n"
        if use_extrapolation and extrapolation_factor is not None:
            text_str += f"Extrap. Factor: {extrapolation_factor:.3f}\n"

    phi_params = []
    excel_input_params = []
    risc_output_params = []
    risc_approx_params = []
    risc_context = None
    if calc_pf_df and plqy_val > 0:
        def get_tau(comp_str):
            if comp_str == 'C_Avg':
                return selected_mean_avg_tau if selected_mean_avg_tau is not None else 0.0
            elif comp_str.startswith('C'):
                try:
                    idx = int(comp_str[1:]) - 1
                    if 0 <= idx < len(components):
                        return components[idx]['num_ave']
                except:
                    pass
            return 0.0

        tau_p_ns = get_tau(pf_comp)
        tau_d_ns = get_tau(df_comp)

        if tau_p_ns > 0 and tau_d_ns > 0:
            try:
                PLQY_tadf = (plqy_val / 100.0) * (1.0 - phos_percent_total / 100.0)
                Phi_p_raw = PLQY_tadf * (pf_ratio_clean / 100.0)
                Phi_d_raw = PLQY_tadf * (df_ratio_clean / 100.0)
                k_ratio = tau_p_ns / tau_d_ns if tau_d_ns > 0 else 0

                Phi_PF = Phi_p_raw + k_ratio * Phi_d_raw
                Phi_DE = (1.0 - k_ratio) * Phi_d_raw

                phi_params = [
                    {'Parameter': '--- Phi Correction ---', 'Value': '---'},
                    {'Parameter': 'Input_Total_PLQY (%)', 'Value': plqy_val},
                    {'Parameter': 'TADF_PLQY_Used (fraction)', 'Value': PLQY_tadf},
                    {'Parameter': 'Phi_PF (fraction)', 'Value': Phi_PF},
                    {'Parameter': 'Phi_DE (fraction)', 'Value': Phi_DE},
                    {'Parameter': 'Tau_P_Used (ns)', 'Value': tau_p_ns},
                    {'Parameter': 'Tau_D_Used (ns)', 'Value': tau_d_ns},
                ]

                if calc_risc:
                    from params_export import build_risc_excel_input_rows
                    risc_context = {
                        'tau_p_ns': tau_p_ns,
                        'tau_d_ns': tau_d_ns,
                        'Phi_PF': Phi_PF,
                        'PLQY_tadf': PLQY_tadf,
                    }
                    excel_input_params = build_risc_excel_input_rows(
                        tau_p_ns, tau_d_ns, Phi_PF, PLQY_tadf,
                        pf_comp, df_comp, selected_indices_display,
                    )
            except Exception as e:
                print(f"Phi correction failed: {e}")

    display_text = text_str.strip()
    if risc_context is not None:
        from risc_calculator_bridge import format_risc_calc_panel
        risc_wait_text = format_risc_calc_panel(waiting_message="Waiting for Excel...\n  Preparing Excel...")
        display_text = f"{display_text}\n\n{risc_wait_text}"

    set_fit_summary_panel(app_state, display_text)

    if risc_context is not None:
        from risc_calculator_bridge import (
            compute_risc_rates,
            make_plot_status_callback, flush_plot_status, format_risc_calc_panel,
        )
        from params_export import build_risc_approx_output_rows
        flush_plot_status(app_state)
        status_cb = make_plot_status_callback(app_state, text_str.strip())
        try:
            print("  -> Running RISC Calculator (Excel, read-only)...")
            rates = compute_risc_rates(
                tau_p_ns=risc_context['tau_p_ns'],
                tau_d_ns=risc_context['tau_d_ns'],
                Phi_PF=risc_context['Phi_PF'],
                Phi_PLQY_tadf=risc_context['PLQY_tadf'],
                status_callback=status_cb,
            )
            text_str += SEP
            text_str += format_risc_calc_panel(rates=rates) + "\n"

            risc_output_params = [
                {'Parameter': '--- RISC Rates (Excel Outputs) ---', 'Value': '---'},
                {'Parameter': 'k^S_r (s^-1)',  'Value': rates['k_r_S']},
                {'Parameter': 'k^S_nr (s^-1)', 'Value': rates['k_nr_S']},
                {'Parameter': 'k^S (s^-1)',    'Value': rates['k_S']},
                {'Parameter': 'k_ISC (s^-1)',  'Value': rates['k_ISC']},
                {'Parameter': 'k_RISC (s^-1)', 'Value': rates['k_RISC']},
                {'Parameter': 'RISC_Converged','Value': rates.get('converged')},
                {'Parameter': 'RISC_Residual |D69-D79|','Value': rates.get('residual')},
            ]
            risc_approx_params = build_risc_approx_output_rows(rates)
        except Exception as e:
            print(f"  RISC calculation failed: {e}")
            text_str += SEP + format_risc_calc_panel(waiting_message=f"FAILED:\n{str(e)[:60]}") + "\n"

        app_state['text_box'].set_text(text_str.strip())
        flush_plot_status(app_state)

    # --- Rescale axes ---
    x_margin = (xmax - xmin) * 0.05
    ax.set_xlim(xmin - x_margin, xmax + x_margin)

    valid_y_data = data[fit_mask]
    if len(valid_y_data) > 0:
        y_max = np.max(valid_y_data)
        min_data_val = np.min(valid_y_data[valid_y_data > 0]) if np.any(valid_y_data > 0) else 1
        ax.set_ylim(max(0.5, min_data_val * 0.5), y_max * 1.5)

    max_res = np.max(np.abs(weighted_res))
    ax_res.set_ylim(-max(5, max_res * 1.2), max(5, max_res * 1.2))

    fig.canvas.draw_idle()

    # --- Store results for export ---
    df_curve = pd.DataFrame({
        'Time (ns)':        t_plot,
        'Counts':           data[fit_mask],
        'Fitted Data':      model_plot,
        'Residuals':        data[fit_mask] - model_plot,
        'Weighted Residuals': weighted_res
    })

    params_list = [
        {'Parameter': 'Reduced Chi-Squared', 'Value': chi_sq},
        {'Parameter': 'Background',          'Value': bkg},
    ]

    from params_export import append_section

    component_rows = []
    for i, comp in enumerate(components):
        component_rows.extend([
            {'Parameter': f'Tau_{i+1} (ns)',           'Value': comp['tau']},
            {'Parameter': f'Tau_{i+1}_StdErr (ns)',    'Value': comp.get('tau_stderr')},
            {'Parameter': f'Beta_Stretching_{i+1}',    'Value': comp['beta']},
            {'Parameter': f'Beta_Stretching_{i+1}_StdErr', 'Value': comp.get('beta_stderr')},
            {'Parameter': f'Num_Ave_Tau_{i+1} (ns)',       'Value': comp['num_ave']},
            {'Parameter': f'Num_Ave_Tau_{i+1}_StdErr (ns)', 'Value': comp.get('num_ave_stderr')},
            {'Parameter': f'Int_Ave_Tau_{i+1} (ns)',        'Value': comp['int_ave']},
            {'Parameter': f'Int_Ave_Tau_{i+1}_StdErr (ns)', 'Value': comp.get('int_ave_stderr')},
            {'Parameter': f'Amplitude_{i+1}',           'Value': comp['B']},
            {'Parameter': f'Relative_Area_{i+1} (%)',   'Value': comp['rel_percent']},
            {'Parameter': f'Is_Phosphorescence_{i+1}',  'Value': comp['is_phos']},
        ])
    append_section(params_list, component_rows)

    avg_rows = []
    if selected_avg_tau is not None:
        avg_rows.append({
            'Parameter': f'Avg_Int_Ave_Tau_C{selected_indices_display} (ns)',
            'Value': selected_avg_tau
        })
        avg_rows.append({
            'Parameter': f'Avg_Int_Ave_Tau_C{selected_indices_display}_StdErr (ns)',
            'Value': selected_avg_tau_stderr
        })
    if selected_mean_avg_tau is not None:
        avg_rows.append({
            'Parameter': f'Avg_Num_Ave_Tau_C{selected_indices_display} (ns)',
            'Value': selected_mean_avg_tau
        })
        avg_rows.append({
            'Parameter': f'Avg_Num_Ave_Tau_C{selected_indices_display}_StdErr (ns)',
            'Value': selected_mean_avg_tau_stderr
        })
    append_section(params_list, avg_rows)

    if calc_pf_df:
        method_label = '--- Area Diff + Extrapolation Method ---' if use_extrapolation else '--- Area Difference Method ---'
        area_rows = [
            {'Parameter': method_label,                                 'Value': '---'},
            {'Parameter': 'Total_Data_Area',                            'Value': total_data_area},
            {'Parameter': 'Calculated_PF_Area',                        'Value': area_pf_calc},
            {'Parameter': 'Fitted_DF_Area',                            'Value': area_df_fit},
            {'Parameter': 'Phosphorescence_Area',                      'Value': area_phos},
            {'Parameter': 'Phosphorescence_Total_Percentage (%)',      'Value': phos_percent_total},
            {'Parameter': 'Clean_PF_Percentage (PF vs DF) (%)',        'Value': pf_ratio_clean},
            {'Parameter': 'Clean_DF_Percentage (PF vs DF) (%)',        'Value': df_ratio_clean},
        ]
        if use_extrapolation and extrapolation_factor is not None:
            area_rows.append(
                {'Parameter': 'Extrapolation_Factor', 'Value': extrapolation_factor}
            )
        append_section(params_list, area_rows)

    append_section(params_list, phi_params)
    append_section(params_list, excel_input_params)
    append_section(params_list, risc_output_params)
    append_section(params_list, risc_approx_params)

    df_params = pd.DataFrame(params_list)
    app_state['fit_results'] = {'curve': df_curve, 'params': df_params}
