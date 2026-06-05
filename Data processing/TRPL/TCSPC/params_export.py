"""Helpers for building parameter rows exported to Excel."""

BLANK_ROW = {'Parameter': '', 'Value': ''}


def append_section(params_list, rows):
    """Append a parameter block, inserting a blank row before each new module."""
    if not rows:
        return
    if params_list:
        params_list.append(BLANK_ROW.copy())
    params_list.extend(rows)


def build_risc_excel_input_rows(tau_p_ns, tau_d_ns, Phi_PF, Phi_PLQY_tadf,
                                pf_comp, df_comp, avg_indices_display=""):
    """Rows documenting exact values written into the RISC Calculator workbook."""
    tau_d_us = float(tau_d_ns) / 1000.0

    def _tau_source(comp):
        if comp == 'C_Avg':
            if avg_indices_display:
                return f'Avg_Num_Ave_Tau_C{avg_indices_display} (ns)'
            return 'C_Avg (Avg Comps not set)'
        if comp.startswith('C') and comp[1:].isdigit():
            return f'Num_Ave_Tau_{comp[1]} (ns)'
        return str(comp)

    return [
        {'Parameter': '--- RISC Excel Inputs ---', 'Value': '---'},
        {'Parameter': 'PF_Comp_Selection', 'Value': pf_comp},
        {'Parameter': 'DF_Comp_Selection', 'Value': df_comp},
        {'Parameter': 'Tau_P_Lifetime_Source', 'Value': _tau_source(pf_comp)},
        {'Parameter': 'Tau_D_Lifetime_Source', 'Value': _tau_source(df_comp)},
        {'Parameter': 'Excel_D5_tau_p (ns)', 'Value': float(tau_p_ns)},
        {'Parameter': 'Excel_D8_tau_d (us)', 'Value': tau_d_us},
        {'Parameter': 'Excel_D11_Phi_PF (fraction)', 'Value': float(Phi_PF)},
        {'Parameter': 'Excel_D13_Phi_PLQY (fraction)', 'Value': float(Phi_PLQY_tadf)},
        {'Parameter': 'Excel_D15_R_DF_DE (fixed, not written)', 'Value': 1.0},
    ]


def build_risc_approx_output_rows(rates):
    """Rows for approximate k_RISC values read from Excel D32, D38, D44."""
    if not rates:
        return []
    return [
        {'Parameter': '--- RISC Approx k_RISC (Excel) ---', 'Value': '---'},
        {'Parameter': 'Excel_D32_k_RISC_Masui (s^-1)', 'Value': rates.get('k_RISC_Masui')},
        {'Parameter': 'Excel_D38_k_RISC_Dias (s^-1)',  'Value': rates.get('k_RISC_Dias')},
        {'Parameter': 'Excel_D44_k_RISC_Wada (s^-1)',  'Value': rates.get('k_RISC_Wada')},
    ]
