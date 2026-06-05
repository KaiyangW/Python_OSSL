import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import json 
import sys
import ctypes
import multiprocessing

# Import math engines
from Recon_fit_process import run_fitting_process as run_recon_fit
from Tail_fit_process import run_fitting_process as run_tail_fit
from fit_multistart import shutdown_pool
from risc_calculator_bridge import (
    create_lifetime_fit_figure, BTN_FIT_RECT, BTN_SAVE_RECT,
)

# Enable High DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def auto_format_time(tau_ns):
    """根据输入的纳秒值，自动向下转换并返回带单位的字符串格式"""
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

# Global Matplotlib settings
plt.rcParams.update({
    'figure.dpi': 150,
    'font.size': 10,
    'axes.labelsize': 12,
    'axes.titlesize': 14,
    'xtick.labelsize': 10,
    'ytick.labelsize': 10,
    'lines.linewidth': 1.0,  
    'lines.markersize': 4,
    'legend.fontsize': 10
})

def load_fluoracle_csv(filepath):
    try:
        start_row = 0
        with open(filepath, 'r', encoding='utf-8-sig', errors='replace') as f:
            for i, line in enumerate(f):
                parts = line.replace('\t', ',').split(',')
                if len(parts) >= 2:
                    try:
                        float(parts[0].strip())
                        float(parts[1].strip())
                        start_row = i
                        break  
                    except ValueError:
                        pass
        
        df = pd.read_csv(filepath, skiprows=start_row, header=None, usecols=[0, 1], engine='python')
        df.columns = ['Time', 'Counts']
        
        df['Time'] = pd.to_numeric(df['Time'], errors='coerce')
        df['Counts'] = pd.to_numeric(df['Counts'], errors='coerce')
        df = df.dropna()
        
        if df.empty:
            raise ValueError("No valid numeric data found in the file.")
            
        return df
        
    except Exception as e:
        messagebox.showerror("Read Error", f"Cannot read file:\n{os.path.basename(filepath)}\n\nError:\n{str(e)}")
        return None

# --- Path Persistence Helpers ---
SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "last_paths.json")

def get_last_path():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get('last_dir', os.getcwd())
        except:
            pass
    return os.getcwd()

def save_last_path(path):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'last_dir': os.path.dirname(path)}, f)
    except:
        pass

def center_tk_window(window, width, height):
    """Place a Tk/Toplevel window at the center of the primary screen."""
    window.update_idletasks()
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")

# --- GUI and Interactive Fitting State ---
app_state = {
    't': None, 'data': None, 'irf': None, 'irf_display': None, 'dt': None,
    'ax': None, 'ax_res': None, 'fig': None, 'fit_line': None, 'res_line': None,    
    'ax_info': None, 'text_box': None, 'risc_text_box': None, 'vlines': [], 'tk_root': None, 'fit_results': None,
    'fit_mode': None, 'data_path': None, 'irf_path': None,
}

def open_fit_dialog(event):
    dialog = tk.Toplevel(app_state['tk_root'])
    mode_title = "Reconvolution" if app_state['fit_mode'] == 'recon' else "Tail"
    dialog.title(f"{mode_title} Fit Settings (Stretched & Standard Exp)")
    dialog.geometry("1700x950")
    dialog.attributes('-topmost', True) 
    
    cb_font = ('Arial', 11, 'bold')
    lbl_font = ('Arial', 10)
    
    bkg_region_points = max(10, int(len(app_state['data']) * 0.05))
    bkg_mean = np.mean(app_state['data'][:bkg_region_points])
    bkg_std = np.std(app_state['data'][:bkg_region_points])
    
    peak_val = np.max(app_state['data'])
    peak_idx = np.argmax(app_state['data'])
    threshold = max(bkg_mean + 3 * bkg_std, 0.01 * peak_val)
    
    start_idx = peak_idx
    if app_state['fit_mode'] == 'recon':
        while start_idx > 0 and app_state['data'][start_idx] > threshold:
            start_idx -= 1
        start_idx = max(0, start_idx - 5)
    else:
        # Tail fit typically starts slightly after peak
        start_idx = min(len(app_state['data']) - 1, peak_idx + 5)
        
    auto_xmin = app_state['t'][start_idx]
    auto_xmax = app_state['t'].max() * 0.98
    
    tk.Label(dialog, text="Fit Range (ns)", font=('Arial', 11, 'bold')).grid(row=0, column=0, columnspan=7, pady=(10,5))
    
    # --- Row 1: X Min 和 步进控制 ---
    tk.Label(dialog, text="X Min:", font=lbl_font).grid(row=1, column=0, sticky='e', padx=5)
    entry_xmin = tk.Entry(dialog, width=10)
    entry_xmin.grid(row=1, column=1, pady=2, sticky='w')
    entry_xmin.insert(0, f"{auto_xmin:.2f}") 
    
    tk.Label(dialog, text="Step (ns):", font=lbl_font).grid(row=1, column=2, sticky='e', padx=5)
    entry_step = tk.Entry(dialog, width=6)
    entry_step.grid(row=1, column=3, pady=2, sticky='w')
    entry_step.insert(0, "0.5" if app_state['fit_mode'] == 'recon' else "1.0") 
    
    # --- Row 2: X Max ---
    tk.Label(dialog, text="X Max:", font=lbl_font).grid(row=2, column=0, sticky='e', padx=5)
    entry_xmax = tk.Entry(dialog, width=10)
    entry_xmax.grid(row=2, column=1, pady=2, sticky='w')
    entry_xmax.insert(0, f"{auto_xmax:.1f}") 
    
    tk.Label(dialog, text="Initial Guesses (Leave Tau blank to omit component)", font=('Arial', 11, 'bold')).grid(row=3, column=0, columnspan=7, pady=(15,5))
    
    var_fix_t1, var_fix_t2, var_fix_t3 = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
    var_fix_b1, var_fix_b2, var_fix_b3 = tk.BooleanVar(), tk.BooleanVar(), tk.BooleanVar()
    var_fix_t4, var_fix_b4 = tk.BooleanVar(), tk.BooleanVar()
    
    unit_var1 = tk.StringVar(dialog); unit_var1.set("ns")
    unit_var2 = tk.StringVar(dialog); unit_var2.set("µs") 
    unit_var3 = tk.StringVar(dialog); unit_var3.set("µs")
    unit_var4 = tk.StringVar(dialog); unit_var4.set("ms")
    unit_options = ["ns", "µs", "ms", "s"]

    # --- Row 4: Component 1 ---
    tk.Label(dialog, text="Tau 1:", font=lbl_font).grid(row=4, column=0, sticky='e', padx=5)
    entry_tau1 = tk.Entry(dialog, width=10)
    entry_tau1.grid(row=4, column=1, pady=5)
    entry_tau1.insert(0, "10") 
    tk.OptionMenu(dialog, unit_var1, *unit_options).grid(row=4, column=2, padx=2, sticky='w')
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_t1, font=cb_font).grid(row=4, column=3, sticky='w', padx=(0, 10))
    
    tk.Label(dialog, text="Beta 1:", font=lbl_font).grid(row=4, column=4, sticky='e', padx=5)
    entry_beta1 = tk.Entry(dialog, width=8)
    entry_beta1.grid(row=4, column=5, pady=5)
    entry_beta1.insert(0, "0.8")
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_b1, font=cb_font).grid(row=4, column=6, sticky='w')

    # --- Row 5: Component 2 ---
    tk.Label(dialog, text="Tau 2:", font=lbl_font).grid(row=5, column=0, sticky='e', padx=5)
    entry_tau2 = tk.Entry(dialog, width=10)
    entry_tau2.grid(row=5, column=1, pady=5)
    tk.OptionMenu(dialog, unit_var2, *unit_options).grid(row=5, column=2, padx=2, sticky='w')
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_t2, font=cb_font).grid(row=5, column=3, sticky='w', padx=(0, 10))
    
    tk.Label(dialog, text="Beta 2:", font=lbl_font).grid(row=5, column=4, sticky='e', padx=5)
    entry_beta2 = tk.Entry(dialog, width=8)
    entry_beta2.grid(row=5, column=5, pady=5)
    entry_beta2.insert(0, "0.8")
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_b2, font=cb_font).grid(row=5, column=6, sticky='w')

    # --- Row 6: Component 3 ---
    tk.Label(dialog, text="Tau 3:", font=lbl_font).grid(row=6, column=0, sticky='e', padx=5)
    entry_tau3 = tk.Entry(dialog, width=10)
    entry_tau3.grid(row=6, column=1, pady=5)
    tk.OptionMenu(dialog, unit_var3, *unit_options).grid(row=6, column=2, padx=2, sticky='w')
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_t3, font=cb_font).grid(row=6, column=3, sticky='w', padx=(0, 10))
    
    tk.Label(dialog, text="Beta 3:", font=lbl_font).grid(row=6, column=4, sticky='e', padx=5)
    entry_beta3 = tk.Entry(dialog, width=8)
    entry_beta3.grid(row=6, column=5, pady=5)
    entry_beta3.insert(0, "0.8")
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_b3, font=cb_font).grid(row=6, column=6, sticky='w')

    # Row 7: Component 4 (Phosphorescence) ---
    tk.Label(dialog, text="Tau 4:", font=lbl_font).grid(row=7, column=0, sticky='e', padx=5)
    entry_tau4 = tk.Entry(dialog, width=10)
    entry_tau4.grid(row=7, column=1, pady=5)
    tk.OptionMenu(dialog, unit_var4, *unit_options).grid(row=7, column=2, padx=2, sticky='w')
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_t4, font=cb_font).grid(row=7, column=3, sticky='w', padx=(0, 10))
    
    tk.Label(dialog, text="Beta 4:", font=lbl_font).grid(row=7, column=4, sticky='e', padx=5)
    entry_beta4 = tk.Entry(dialog, width=8)
    entry_beta4.grid(row=7, column=5, pady=5)
    entry_beta4.insert(0, "1.0") 
    tk.Checkbutton(dialog, text="Fix", variable=var_fix_b4, font=cb_font).grid(row=7, column=6, sticky='w')

    tk.Label(dialog, text="Advanced Fitting Settings", font=('Arial', 11, 'bold')).grid(row=8, column=0, columnspan=7, pady=(15,5))
    
    tk.Label(dialog, text="Phosphorescence:", font=lbl_font).grid(row=9, column=0, sticky='e', padx=5)
    phos_var = tk.StringVar(dialog)
    phos_var.set("None") 
    tk.OptionMenu(dialog, phos_var, "None", "C1", "C2", "C3", "C4").grid(row=9, column=1, sticky='w')
    
    calc_pfdf_var = tk.BooleanVar(value=False) 
    tk.Checkbutton(dialog, text="Enable Area Diff (PF/DF Ratio)", variable=calc_pfdf_var, font=lbl_font).grid(row=9, column=2, columnspan=3, sticky='w')

    use_extrap_var = tk.BooleanVar(value=True)
    subtract_scatter_var = tk.BooleanVar(value=True)
    if app_state['fit_mode'] == 'tail':
        tk.Checkbutton(dialog, text="Use Extrapolation Compensation",
                       variable=use_extrap_var, font=lbl_font).grid(row=10, column=0, columnspan=3, sticky='w', padx=(10, 0))
    else:
        tk.Checkbutton(dialog, text="Subtract Scatter Area from PF",
                       variable=subtract_scatter_var, font=lbl_font).grid(row=10, column=0, columnspan=3, sticky='w', padx=(10, 0))

    tk.Label(dialog, text="Avg Comps (e.g., 1,2):", font=lbl_font).grid(row=9, column=5, sticky='e', padx=5)
    entry_avg_comps = tk.Entry(dialog, width=8)
    entry_avg_comps.grid(row=9, column=6, sticky='w')

    # --- Row 11: PLQY input for Phi_PF / Phi_DE ---
    plqy_row = 11
    tk.Label(dialog, text="PLQY (%):", font=('Arial', 11, 'bold')).grid(row=plqy_row, column=0, sticky='e', padx=5, pady=(10, 5))
    entry_plqy = tk.Entry(dialog, width=8)
    entry_plqy.grid(row=plqy_row, column=1, sticky='w', pady=(10, 5))
    entry_plqy.insert(0, "80")
    
    tk.Label(dialog, text="PF Comp:", font=lbl_font).grid(row=plqy_row, column=2, sticky='e', padx=5)
    pf_comp_var = tk.StringVar(dialog)
    pf_comp_var.set("C1")
    tk.OptionMenu(dialog, pf_comp_var, "C1", "C2", "C3", "C4", "C_Avg").grid(row=plqy_row, column=3, sticky='w')
    
    tk.Label(dialog, text="DF Comp:", font=lbl_font).grid(row=plqy_row, column=4, sticky='e', padx=5)
    df_comp_var = tk.StringVar(dialog)
    df_comp_var.set("C2")
    tk.OptionMenu(dialog, df_comp_var, "C1", "C2", "C3", "C4", "C_Avg").grid(row=plqy_row, column=5, sticky='w')

    calc_risc_var = tk.BooleanVar(value=False)
    tk.Checkbutton(dialog, text="Compute RISC Rates (Excel)", variable=calc_risc_var,
                   font=('Arial', 10, 'bold'), fg='blue').grid(row=plqy_row, column=6, columnspan=2, sticky='w', padx=(10, 0))

    # --- Config functions ---
    def save_config():
        config = {
            "fit_mode": app_state['fit_mode'],
            "xmin": entry_xmin.get(), "xmax": entry_xmax.get(), "step": entry_step.get(),
            "tau1": entry_tau1.get(), "unit1": unit_var1.get(), "fix_t1": var_fix_t1.get(), "beta1": entry_beta1.get(), "fix_b1": var_fix_b1.get(),
            "tau2": entry_tau2.get(), "unit2": unit_var2.get(), "fix_t2": var_fix_t2.get(), "beta2": entry_beta2.get(), "fix_b2": var_fix_b2.get(),
            "tau3": entry_tau3.get(), "unit3": unit_var3.get(), "fix_t3": var_fix_t3.get(), "beta3": entry_beta3.get(), "fix_b3": var_fix_b3.get(),
            "tau4": entry_tau4.get(), "unit4": unit_var4.get(), "fix_t4": var_fix_t4.get(), "beta4": entry_beta4.get(), "fix_b4": var_fix_b4.get(),
            "phos": phos_var.get(),
            "calc_pfdf": calc_pfdf_var.get(),
            "use_extrapolation": use_extrap_var.get(),
            "subtract_scatter": subtract_scatter_var.get(),
            "avg_comps": entry_avg_comps.get(),
            "plqy": entry_plqy.get(),
            "pf_comp": pf_comp_var.get(),
            "df_comp": df_comp_var.get(),
            "calc_risc": calc_risc_var.get()
        }
        
        filepath = filedialog.asksaveasfilename(
            parent=dialog, title="Save Fit Configuration", defaultextension=".json",
            filetypes=[("JSON Config Files", "*.json"), ("All Files", "*.*")]
        )
        if filepath:
            try:
                with open(filepath, 'w') as f:
                    json.dump(config, f, indent=4)
                messagebox.showinfo("Success", "Configuration saved successfully.", parent=dialog)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save config:\n{str(e)}", parent=dialog)

    def load_config():
        filepath = filedialog.askopenfilename(
            parent=dialog, title="Load Fit Configuration",
            filetypes=[("JSON Config Files", "*.json"), ("All Files", "*.*")]
        )
        if filepath:
            try:
                with open(filepath, 'r') as f:
                    config = json.load(f)
                
                def set_entry(entry_widget, val):
                    entry_widget.delete(0, tk.END)
                    entry_widget.insert(0, str(val))
                
                set_entry(entry_xmin, config.get("xmin", ""))
                set_entry(entry_xmax, config.get("xmax", ""))
                set_entry(entry_step, config.get("step", "0.5"))
                
                set_entry(entry_tau1, config.get("tau1", "")); unit_var1.set(config.get("unit1", "ns")); var_fix_t1.set(config.get("fix_t1", False))
                set_entry(entry_beta1, config.get("beta1", "")); var_fix_b1.set(config.get("fix_b1", False))
                
                set_entry(entry_tau2, config.get("tau2", "")); unit_var2.set(config.get("unit2", "µs")); var_fix_t2.set(config.get("fix_t2", False))
                set_entry(entry_beta2, config.get("beta2", "")); var_fix_b2.set(config.get("fix_b2", False))
                
                set_entry(entry_tau3, config.get("tau3", "")); unit_var3.set(config.get("unit3", "µs")); var_fix_t3.set(config.get("fix_t3", False))
                set_entry(entry_beta3, config.get("beta3", "")); var_fix_b3.set(config.get("fix_b3", False))
                
                set_entry(entry_tau4, config.get("tau4", "")); unit_var4.set(config.get("unit4", "ms")); var_fix_t4.set(config.get("fix_t4", False))
                set_entry(entry_beta4, config.get("beta4", "")); var_fix_b4.set(config.get("fix_b4", False))
                
                phos_var.set(config.get("phos", "None"))
                calc_pfdf_var.set(config.get("calc_pfdf", False))
                use_extrap_var.set(config.get("use_extrapolation", True))
                subtract_scatter_var.set(config.get("subtract_scatter", True))
                set_entry(entry_avg_comps, config.get("avg_comps", ""))
                
                set_entry(entry_plqy, config.get("plqy", "80"))
                pf_comp_var.set(config.get("pf_comp", "C1"))
                df_comp_var.set(config.get("df_comp", "C2"))
                calc_risc_var.set(config.get("calc_risc", False))
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load config:\n{str(e)}", parent=dialog)

    def execute_fit(event=None):
        try:
            xmin = float(entry_xmin.get())
            xmax = float(entry_xmax.get())
            
            unit_multipliers = {"ns": 1.0, "µs": 1e3, "us": 1e3, "ms": 1e6, "s": 1e9}
            
            taus, fixed_t_flags, betas, fixed_b_flags = [], [], [], []
            
            entries = [
                (entry_tau1, unit_var1, var_fix_t1, entry_beta1, var_fix_b1), 
                (entry_tau2, unit_var2, var_fix_t2, entry_beta2, var_fix_b2), 
                (entry_tau3, unit_var3, var_fix_t3, entry_beta3, var_fix_b3),
                (entry_tau4, unit_var4, var_fix_t4, entry_beta4, var_fix_b4)
            ]
            
            for e_tau, u_var, v_t, e_beta, v_b in entries:
                val_t = e_tau.get().strip()
                if val_t:
                    multiplier = unit_multipliers[u_var.get()]
                    taus.append(float(val_t) * multiplier)
                    fixed_t_flags.append(v_t.get())
                    
                    val_b = e_beta.get().strip()
                    b_val = float(val_b) if val_b else 0.8
                    betas.append(b_val)
                    fixed_b_flags.append(v_b.get())
                    
            num_exp = len(taus)
            if num_exp == 0:
                messagebox.showerror("Error", "Please provide at least one Tau guess.", parent=dialog)
                return
            
            phos_str = phos_var.get()
            phos_idx = -1 if phos_str == "None" else int(phos_str[1]) - 1
            if phos_idx >= num_exp: 
                phos_idx = -1 
                
            calc_pf_df = calc_pfdf_var.get()
            avg_comps_str = entry_avg_comps.get().strip()
            
            plqy_val = float(entry_plqy.get()) if entry_plqy.get().strip() else 0.0
            pf_comp = pf_comp_var.get()
            df_comp = df_comp_var.get()
            calc_risc = calc_risc_var.get()
                
            if app_state['fit_mode'] == 'recon':
                run_recon_fit(app_state, xmin, xmax, taus, fixed_t_flags, betas, fixed_b_flags, num_exp, phos_idx, calc_pf_df, avg_comps_str, plqy_val, pf_comp, df_comp,
                             subtract_scatter=subtract_scatter_var.get(), calc_risc=calc_risc)
            else:
                run_tail_fit(app_state, xmin, xmax, taus, fixed_t_flags, betas, fixed_b_flags, num_exp, phos_idx, calc_pf_df, avg_comps_str, plqy_val, pf_comp, df_comp,
                             use_extrapolation=use_extrap_var.get(), calc_risc=calc_risc)
            
        except ValueError:
            messagebox.showerror("Error", "Please enter valid numeric values.", parent=dialog)

    def step_xmin(direction):
        try:
            current_xmin = float(entry_xmin.get())
            step_val = float(entry_step.get())
            new_xmin = current_xmin + (direction * step_val)
            
            entry_xmin.delete(0, tk.END)
            entry_xmin.insert(0, f"{new_xmin:.3f}")
            
            execute_fit()
        except ValueError:
            pass 

    btn_back = tk.Button(dialog, text="◀", command=lambda: step_xmin(-1), font=('Arial', 10, 'bold'), bg='lightgray')
    btn_back.grid(row=1, column=4, padx=2)
    
    btn_fwd = tk.Button(dialog, text="▶", command=lambda: step_xmin(1), font=('Arial', 10, 'bold'), bg='lightgray')
    btn_fwd.grid(row=1, column=5, padx=2)

    config_row = 13
    tk.Button(dialog, text="Load Config", command=load_config, bg='#E8E8E8', font=('Arial', 10)).grid(row=config_row, column=1, pady=(15, 0), sticky='ew')
    tk.Button(dialog, text="Save Config", command=save_config, bg='#E8E8E8', font=('Arial', 10)).grid(row=config_row, column=2, columnspan=2, pady=(15, 0), sticky='ew', padx=5)

    btn_run = tk.Button(dialog, text="Run Fit (Enter)", command=execute_fit, bg='lightblue', font=('Arial', 11, 'bold'))
    btn_run.grid(row=config_row + 1, column=0, columnspan=8, pady=20)
    
    dialog.bind('<Return>', execute_fit)
    entry_tau1.focus_set()

def save_results(event):
    if app_state.get('fit_results') is None:
        messagebox.showwarning("Warning", "No fit results available. Please run a fit first.")
        return
        
    save_path = filedialog.asksaveasfilename(
        title="Save Fit Results", 
        defaultextension=".xlsx",
        filetypes=[("Excel Files", "*.xlsx"), ("All Files", "*.*")]
    )
    if not save_path: return
        
    try:
        t_full = app_state['t']
        data_full = app_state['data']
        
        # --- Normalize raw decay to 15000 for plot ---
        data_peak = np.max(data_full)
        plot_norm_factor = 15000.0 / data_peak if data_peak > 0 else 1.0
        plot_counts = data_full * plot_norm_factor

        export_dict = {
            'Full_Time (ns)': t_full,
            'Raw_Counts': data_full,
            'Plot_Counts': plot_counts
        }

        # Branch logic for Recon vs Tail fit to handle baseline/IRF and alignment
        if app_state.get('fit_mode') == 'recon':
            # --- Reconvolution Fit (Keep Unchanged as requested) ---
            # Find true rising edge: first point where signal exceeds 2% of peak
            threshold_data = 0.02 * 15000.0
            rising_edge_idx_data = np.argmax(plot_counts > threshold_data)
            
            # Pre-edge baseline: mean of normalised decay points BEFORE the rising edge
            if rising_edge_idx_data > 0:
                bkg_plot_data = np.mean(plot_counts[:rising_edge_idx_data])
            else:
                bkg_plot_data = plot_counts[0]

            if app_state.get('irf_display') is not None:
                irf_data = app_state['irf_display']
                # Normalize IRF to 15000 first
                irf_peak = np.max(irf_data)
                irf_norm = irf_data * (15000.0 / irf_peak) if irf_peak > 0 else irf_data.copy()

                # Find rising edge for IRF (2% of peak)
                threshold_irf = 0.02 * 15000.0
                rising_edge_idx_irf = np.argmax(irf_norm > threshold_irf)
                
                # Pre-edge baseline of normalised IRF
                if rising_edge_idx_irf > 0:
                    bkg_plot_irf = np.mean(irf_norm[:rising_edge_idx_irf])
                else:
                    bkg_plot_irf = irf_norm[0]

                # Shift normalised IRF so its pre-edge baseline matches the decay's
                irf_plot = irf_norm + (bkg_plot_data - bkg_plot_irf)

                # Clamp any negative values to 1
                irf_plot = np.where(irf_plot < 0, 1.0, irf_plot)

                export_dict['Raw_IRF'] = irf_data
                export_dict['Plot_IRF'] = irf_plot
                export_dict['Plot_IRF_non_shifted'] = irf_norm
        
        df_full = pd.DataFrame(export_dict)
        
        # --- Handle Fit Curve Export, Normalization and Alignment ---
        df_fit = app_state['fit_results']['curve'].copy()
        
        if app_state.get('fit_mode') == 'recon':
            # Reconvolution mode: Keep existing normalization and concat (staying "unchanged")
            if 'Fitted Data' in df_fit.columns:
                fit_peak = np.max(df_fit['Fitted Data'])
                df_fit['Plot_Fitted Data'] = df_fit['Fitted Data'] * (15000.0 / fit_peak) if fit_peak > 0 else df_fit['Fitted Data']
            
            df_fit.columns = [f"Fit_{col}" for col in df_fit.columns]
            df_fit = df_fit.reset_index(drop=True) 
            df_export = pd.concat([df_full, df_fit], axis=1)
        else:
            # Tail mode: Check and fix normalization, baseline, and alignment issues
            if 'Fitted Data' in df_fit.columns:
                # Use same norm factor as raw data to ensure physical alignment with Raw data's baseline
                df_fit['Plot_Fitted Data'] = df_fit['Fitted Data'] * plot_norm_factor
            
            # Align by merging on Time to prevent row misalignment in Excel (bug fix)
            df_fit.columns = [f"Fit_{col}" for col in df_fit.columns]
            df_export = pd.merge(df_full, df_fit, left_on='Full_Time (ns)', right_on='Fit_Time (ns)', how='left')

        with pd.ExcelWriter(save_path) as writer:
            params_df = app_state['fit_results']['params'].copy()
            
            if 'Parameter' in params_df.columns and 'Value' in params_df.columns:
                formatted_values = []
                for idx, row in params_df.iterrows():
                    param_name = str(row['Parameter'])
                    val = row['Value']
                    if not param_name.strip():
                        formatted_values.append('')
                    elif '(us)' in param_name.lower() or param_name.startswith('Excel_D8'):
                        formatted_values.append(val)
                    elif 'tau' in param_name.lower():
                        formatted_values.append(auto_format_time(val))
                    else:
                        formatted_values.append(val)
                params_df['Formatted_Value'] = formatted_values

            params_df.to_excel(writer, sheet_name='Parameters', index=False)
            df_export.to_excel(writer, sheet_name='Fit_Curve', index=False)
            
        base_path, _ = os.path.splitext(save_path)
        app_state['fig'].savefig(base_path + ".png", dpi=400, bbox_inches='tight')
        messagebox.showinfo("Success", "Results and plot image saved successfully.")
        
    except Exception as e:
        messagebox.showerror("Save Error", f"Failed to save file:\n{str(e)}")

def select_mode_and_run():
    mode_win = tk.Toplevel(app_state['tk_root'])
    mode_win.title("Select Fitting Mode")
    mode_win.attributes('-topmost', True)
    
    tk.Label(mode_win, text="Choose TRPL Fitting Method:", font=('Arial', 12, 'bold')).pack(pady=10)
    
    def set_mode(mode):
        app_state['fit_mode'] = mode
        mode_win.destroy()
        run_data_loading()
        
    tk.Button(mode_win, text="Reconvolution Fit", font=('Arial', 10), command=lambda: set_mode('recon')).pack(pady=5, fill='x', padx=50)
    tk.Button(mode_win, text="Tail Fit", font=('Arial', 10), command=lambda: set_mode('tail')).pack(pady=5, fill='x', padx=50)
    
    center_tk_window(mode_win, 800, 500)
    app_state['tk_root'].wait_window(mode_win)

def run_data_loading():
    if not app_state['fit_mode']:
        return # User closed dialog without selecting
        
    data_path = None
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith('.csv'):
        data_path = sys.argv[1]
    
    if not data_path:
        initial_dir = get_last_path()
        data_path = filedialog.askopenfilename(
            title="Select Decay Data (CSV)", 
            initialdir=initial_dir,
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
        )
    
    if not data_path: return
    save_last_path(data_path)
    data_path = os.path.abspath(data_path)
    app_state['data_path'] = data_path
    app_state['irf_path'] = None
    data_filename = os.path.basename(data_path)
    
    df_data = load_fluoracle_csv(data_path)
    if df_data is None: return

    # --- Mode Branching for IRF ---
    if app_state['fit_mode'] == 'recon':
        irf_path = None
        data_dir = os.path.dirname(data_path)
        potential_irfs = [f for f in os.listdir(data_dir) if "IRF" in f.upper() and f.lower().endswith(".csv")]
        
        if potential_irfs:
            suggested_irf = os.path.join(data_dir, potential_irfs[0])
            if messagebox.askyesno("IRF Auto-Found", f"Found a potential IRF file in the same folder:\n{potential_irfs[0]}\n\nDo you want to use it?"):
                irf_path = suggested_irf

        if not irf_path:
            irf_path = filedialog.askopenfilename(
                title="Select IRF Data (CSV)", 
                initialdir=data_dir,
                filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")]
            )
        
        if not irf_path: return
        irf_path = os.path.abspath(irf_path)
        app_state['irf_path'] = irf_path

        df_irf = load_fluoracle_csv(irf_path)
        if df_irf is None: return

        data_max = df_data['Counts'].max()
        irf_max = df_irf['Counts'].max()
        threshold = 0.05 
        
        t_rise_data = df_data.loc[df_data['Counts'] >= threshold * data_max, 'Time'].iloc[0]
        t_rise_irf = df_irf.loc[df_irf['Counts'] >= threshold * irf_max, 'Time'].iloc[0]

        df_irf['Time_Aligned'] = df_irf['Time'] + (t_rise_data - t_rise_irf)

        t_min = min(df_data['Time'].min(), df_irf['Time_Aligned'].min())
        t_max = max(df_data['Time'].max(), df_irf['Time_Aligned'].max())
        dt = np.median(np.diff(df_data['Time'])) 
        t_uniform = np.arange(t_min, t_max, dt)
        t_uniform = t_uniform - t_min

        data_counts = np.interp(t_uniform, df_data['Time'], df_data['Counts'], left=0, right=0)
        irf_counts = np.interp(t_uniform, df_irf['Time_Aligned'], df_irf['Counts'], left=0, right=0)

        irf_area = np.sum(irf_counts) * dt
        if irf_area > 0: irf_counts = irf_counts / irf_area
            
        scale_factor = np.max(data_counts) / np.max(irf_counts) if np.max(irf_counts) > 0 else 1
        irf_display = irf_counts * scale_factor

        app_state['t'] = t_uniform
        app_state['data'] = data_counts
        app_state['irf'] = irf_counts
        app_state['irf_display'] = irf_display
        app_state['dt'] = dt
        
    else:
        # Tail fit logic (no IRF)
        t_uniform = df_data['Time'].to_numpy()
        data_counts = df_data['Counts'].to_numpy()
        dt = np.median(np.diff(t_uniform))

        app_state['t'] = t_uniform
        app_state['data'] = data_counts
        app_state['dt'] = dt
        app_state['irf'] = None
        app_state['irf_display'] = None

    # --- Plotting (left info column + main/residual plot column) ---
    plt.style.use('dark_background')
    fig, ax_info, ax, ax_res = create_lifetime_fit_figure()

    app_state['fig'] = fig
    app_state['ax_info'] = ax_info
    app_state['ax'] = ax
    app_state['ax_res'] = ax_res

    ax.semilogy(app_state['t'], app_state['data'], color='cyan', linestyle='solid', linewidth=1, alpha=0.6, label='Decay Data')
    
    if app_state['fit_mode'] == 'recon' and app_state['irf_display'] is not None:
        ax.semilogy(app_state['t'], app_state['irf_display'], color='yellow', linestyle='solid', linewidth=1, alpha=0.7, label='IRF')
        
    mode_name = "Reconvolution" if app_state['fit_mode'] == 'recon' else "Tail"
    ax.set_ylabel('Intensity (Counts)')
    ax.set_title(f'{mode_name} Fitting: {data_filename}')
    ax.grid(True, which="both", linestyle='solid', color='gray', alpha=0.3)

    ax.tick_params(labelbottom=False) 

    ax_res.axhline(0, color='gray', linestyle='--')
    ax_res.set_xlabel('Time (ns)')
    ax_res.set_ylabel('Residuals')
    ax_res.grid(True, which="both", linestyle='solid', color='gray', alpha=0.3)
    
    ax.set_xlim(app_state['t'].min(), app_state['t'].max())
    
    valid_data = app_state['data'][app_state['data'] > 0]
    min_data = np.min(valid_data) if len(valid_data) > 0 else 1
    
    if app_state['fit_mode'] == 'recon' and app_state['irf_display'] is not None:
        valid_irf_disp = app_state['irf_display'][app_state['irf_display'] > 0]
        min_irf_disp = np.min(valid_irf_disp) if len(valid_irf_disp) > 0 else 1e-4
        ax.set_ylim(min(min_data, min_irf_disp) * 0.5, np.max(app_state['data']) * 2)
    else:
        ax.set_ylim(min_data * 0.5, np.max(app_state['data']) * 2)

    ax_button_fit = plt.axes(BTN_FIT_RECT)
    btn_fit = Button(
        ax_button_fit,
        'Open Fit Menu',
        color=(1.0, 1.0, 1.0, 0.35),
        hovercolor=(1.0, 1.0, 1.0, 0.35),
    )
    btn_fit.label.set_color('black')
    btn_fit.on_clicked(open_fit_dialog)
    app_state['btn_fit'] = btn_fit 

    ax_button_save = plt.axes(BTN_SAVE_RECT)
    btn_save = Button(
        ax_button_save,
        'Save Results (.xlsx)',
        color=(1.0, 1.0, 1.0, 0.35),
        hovercolor=(1.0, 1.0, 1.0, 0.35),
    )
    btn_save.label.set_color('black')
    btn_save.on_clicked(save_results)
    app_state['btn_save'] = btn_save

    print(f"\n{mode_name} Plot generated successfully.")
    print("Click 'Open Fit Menu' at the bottom of the plot window to start fitting.")
    
    plt.show()

def main():
    root = tk.Tk()
    root.withdraw()
    app_state['tk_root'] = root 
    
    # Delay showing the dialog slightly so Tkinter initializes fully
    root.after(100, select_mode_and_run)
    try:
        root.mainloop()
    finally:
        shutdown_pool()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
