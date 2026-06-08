import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import re
import auto_threshold_module_laser as atml # Updated to your ATML module
import pandas as pd
import numpy as np
from DFB_FWHM_math_core import calculate_dfb_fwhm_adaptive
import traceback
import threading
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

try:
    trapz = np.trapezoid
except AttributeError:
    trapz = np.trapezoid if hasattr(np, 'trapezoid') else np.trapz


class _ProcessLogCollector:
    """Collect log lines inside a worker process so the GUI can print them."""
    def __init__(self):
        self.messages = []

    def log(self, msg):
        self.messages.append(str(msg))


def _process_single_worker(folder, cfg):
    logger = _ProcessLogCollector()
    try:
        result = LaserAnalysisApp.process_single(logger, folder, cfg)
        return {
            "folder": folder,
            "result": result,
            "messages": logger.messages,
            "error": None,
            "traceback": None,
        }
    except Exception as e:
        return {
            "folder": folder,
            "result": None,
            "messages": logger.messages,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }


CONFIG_DIR = r"C:\My files\Programs_codes"
CONFIG_FILE = os.path.join(CONFIG_DIR, "laser_analysis_config_dfb.json")

# ==========================================
#  HELPER FUNCTIONS
# ==========================================

def find_files_fuzzy(folder, keywords):
    matches = []
    try:
        files = os.listdir(folder)
        for f in files:
            if f.startswith('.'): continue 
            if all(re.search(k, f, re.IGNORECASE) for k in keywords) and f.lower().endswith('.csv'):
                matches.append(os.path.join(folder, f))
    except Exception:
        return []
    return sorted(matches)

def calculate_integrated_area(wavelengths, intensity, w_min, w_max):
    if w_min is None or np.isnan(w_min): w_min = np.min(wavelengths)
    if w_max is None or np.isnan(w_max): w_max = np.max(wavelengths)

    mask = (wavelengths >= w_min) & (wavelengths <= w_max)
    wave_segment = wavelengths[mask]
    int_segment = intensity[mask]

    if len(wave_segment) < 2: return 0.0
    sort_idx = np.argsort(wave_segment)
    return trapz(int_segment[sort_idx], x=wave_segment[sort_idx])

def calculate_peak_counts(wavelengths, intensity, w_min, w_max):
    if w_min is None or np.isnan(w_min): w_min = np.min(wavelengths)
    if w_max is None or np.isnan(w_max): w_max = np.max(wavelengths)

    mask = (wavelengths >= w_min) & (wavelengths <= w_max)
    int_segment = intensity[mask]

    if len(int_segment) == 0 or np.isnan(int_segment).all():
        return 0.0
    return float(np.nanmax(int_segment))

# ==========================================
#  GUI CLASS
# ==========================================

class LaserAnalysisApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DFB Laser Analysis (ATML Edition)")
        self.geometry("1100x800")
        ctk.set_appearance_mode("Dark")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkScrollableFrame(self, width=350, label_text="Configuration")
        self.sidebar.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.create_settings()

        self.right = ctk.CTkFrame(self)
        self.right.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        self.right.grid_rowconfigure(1, weight=1)
        self.right.grid_columnconfigure(0, weight=1)

        self.path_frame = ctk.CTkFrame(self.right)
        self.path_frame.grid(row=0, column=0, padx=10, pady=(10, 0), sticky="ew")
        ctk.CTkButton(self.path_frame, text="Select Folder", command=self.browse).pack(side="left", padx=10, pady=10)
        self.lbl_path = ctk.CTkLabel(self.path_frame, text="No folder selected", text_color="gray")
        self.lbl_path.pack(side="left", padx=10, pady=10, fill="x", expand=True)

        self.log_txt = ctk.CTkTextbox(self.right, font=("Consolas", 12))
        self.log_txt.grid(row=1, column=0, padx=10, pady=10, sticky="nsew")
        self._init_log_colors()

        self.btn_run = ctk.CTkButton(self.right, text="START PROCESSING", fg_color="#2CC985", 
                                     height=50, font=("Arial", 16, "bold"), command=self.start_thread)
        self.btn_run.grid(row=2, column=0, padx=10, pady=10, sticky="ew")

        self.log("Initialized DFB mode. Settings memory restored.")
        self.load_settings()

    def create_settings(self):
        def add(lbl, val, key):
            ctk.CTkLabel(self.sidebar, text=lbl, anchor="w").pack(fill="x", padx=5, pady=(10, 0))
            e = ctk.CTkEntry(self.sidebar)
            e.insert(0, str(val))
            e.pack(fill="x", padx=5, pady=(0, 5))
            setattr(self, key, e)

        add("Collapse Factor:", "0.6", "entry_collapse")
        add("Windows Large:", "21, 31, 41, 51", "entry_win_large")
        add("Windows Small:", "3, 5, 9, 11", "entry_win_small")

        ctk.CTkLabel(self.sidebar, text="--- Integration ---", text_color="gray").pack(pady=10)
        add("Auto Peak Window (± nm):", "10.0", "entry_auto_window") 
        add("Min Wavelength (Fallback):", "605", "entry_w_min")
        add("Max Wavelength (Fallback):", "635", "entry_w_max")
        ctk.CTkLabel(self.sidebar, text="Threshold Metric:", anchor="w").pack(fill="x", padx=5, pady=(10, 0))
        self.metric_var = tk.StringVar(value="Integrated Intensity")
        self.menu_metric = ctk.CTkOptionMenu(
            self.sidebar,
            variable=self.metric_var,
            values=["Integrated Intensity", "Peak Counts"]
        )
        self.menu_metric.pack(fill="x", padx=5, pady=(0, 5))

        ctk.CTkLabel(self.sidebar, text="--- Energy ---", text_color="gray").pack(pady=10)
        add("Beam Size (cm²):", "2.2E-2", "entry_beam_size")
        add("ND Filter:", "1", "entry_nd")
        
        ctk.CTkLabel(self.sidebar, text="Pump Formula:", anchor="w").pack(fill="x", padx=5)
        self.txt_formula = ctk.CTkTextbox(self.sidebar, height=80)
        self.txt_formula.insert("0.0", "(0.0485*(energy*440*10**ND)+0.052)/beam_size")
        self.txt_formula.pack(fill="x", padx=5)

    def browse(self):
        p = filedialog.askdirectory()
        if p:
            self.lbl_path.configure(text=p)
            self.base_folder_path = p

    # Color palette for the log (tag name -> hex color)
    LOG_COLORS = {
        "error":    "#FF5C5C",  # red
        "warning":  "#FFB02E",  # orange
        "success":  "#2CC985",  # green
        "info":     "#4FC3F7",  # cyan
        "fit_result": "#FF6D00",  # neon orange
        "fit":      "#B388FF",  # purple
        "window":   "#FFD54F",  # yellow
        "progress": "#90A4AE",  # blue-gray
        "skip":     "#9E9E9E",  # gray
        "default":  "#E0E0E0",  # soft white
    }

    def _init_log_colors(self):
        """Configure the color tags on the underlying tkinter Text widget."""
        try:
            txt = self.log_txt._textbox
            for tag, color in self.LOG_COLORS.items():
                txt.tag_config(tag, foreground=color)
        except Exception:
            pass

    def _classify_log(self, msg):
        """Pick a color tag based on the content of the message."""
        low = msg.lower()
        if "[error]" in low or "critical" in low or "fail" in low or "error:" in low:
            return "error"
        if "[warning]" in low or "warning" in low:
            return "warning"
        if "[success]" in low or "all done" in low or "saved" in low:
            return "success"
        if "[info]" in low:
            return "info"
        if "[auto-fit]" in low and "result:" in low:
            return "fit_result"
        if "[auto-fit]" in low:
            return "fit"
        if "[auto-window]" in low or "[manual-window]" in low:
            return "window"
        if "[progress]" in low:
            return "progress"
        if "[skip]" in low:
            return "skip"
        return "default"

    def _insert_log(self, msg):
        tag = self._classify_log(msg)
        try:
            self.log_txt._textbox.insert("end", msg + "\n", tag)
        except Exception:
            self.log_txt.insert("end", msg + "\n")
        self.log_txt.see("end")

    def log(self, msg):
        self.after(0, lambda: self._insert_log(msg))

    def get_cfg(self):
        try:
            return {
                'collapse': float(self.entry_collapse.get()),
                'win_large': [int(x) for x in self.entry_win_large.get().split(',') if x.strip()],
                'win_small': [int(x) for x in self.entry_win_small.get().split(',') if x.strip()],
                'auto_window': float(self.entry_auto_window.get()) if self.entry_auto_window.get() else 0.0, 
                'w_min': float(self.entry_w_min.get()) if self.entry_w_min.get() else None,
                'w_max': float(self.entry_w_max.get()) if self.entry_w_max.get() else None,
                'beam_size': float(self.entry_beam_size.get()),
                'nd': float(self.entry_nd.get()),
                'formula': self.txt_formula.get("0.0", "end").strip(),
                'fit_metric': 'peak_counts' if self.metric_var.get() == "Peak Counts" else 'integrated_intensity'
            }
        except Exception as e:
            messagebox.showerror("Config Error", f"Check inputs: {e}")
            return None

    def save_settings(self):
        if not os.path.exists(CONFIG_DIR):
            try: os.makedirs(CONFIG_DIR)
            except Exception as e:
                self.log(f"Warning: Could not create config directory - {e}")
                return    
        try:
            data = {
                "collapse": self.entry_collapse.get(),
                "win_large": self.entry_win_large.get(),
                "win_small": self.entry_win_small.get(),
                "auto_window": self.entry_auto_window.get(),
                "w_min": self.entry_w_min.get(),
                "w_max": self.entry_w_max.get(),
                "beam_size": self.entry_beam_size.get(),
                "nd": self.entry_nd.get(),
                "formula": self.txt_formula.get("0.0", "end").strip(),
                "fit_metric": self.metric_var.get()
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.log(f"Warning: Settings save failed - {e}")

    def load_settings(self):
        if not os.path.exists(CONFIG_FILE): return
        try:
            with open(CONFIG_FILE, "r") as f:
                d = json.load(f)
            def set_val(entry, key):
                if d.get(key) is not None:
                    entry.delete(0, "end")
                    entry.insert(0, str(d[key]))
            set_val(self.entry_collapse, "collapse")
            set_val(self.entry_win_large, "win_large")
            set_val(self.entry_win_small, "win_small")
            set_val(self.entry_auto_window, "auto_window")
            set_val(self.entry_w_min, "w_min")
            set_val(self.entry_w_max, "w_max")
            set_val(self.entry_beam_size, "beam_size")
            set_val(self.entry_nd, "nd")
            if d.get("fit_metric") in ["Integrated Intensity", "Peak Counts"]:
                self.metric_var.set(d["fit_metric"])
            if d.get("formula"):
                self.txt_formula.delete("0.0", "end")
                self.txt_formula.insert("0.0", d["formula"])
        except Exception as e:
            self.log(f"Settings load error: {e}")

    def start_thread(self):
        if not hasattr(self, 'base_folder_path'): return
        cfg = self.get_cfg()
        if not cfg: return
        self.save_settings() 
        self.btn_run.configure(state="disabled", text="Running...")
        threading.Thread(target=self.process, args=(self.base_folder_path, cfg)).start()

    def process(self, base_folder, cfg):
        try:
            folders = []
            for current_root, dirs, files in os.walk(base_folder):
                if any(f.lower().endswith('.csv') for f in files):
                    folders.append(current_root)
            if not folders: 
                self.log("No valid folders containing CSV files found."); return
            
            self.log(f"Processing {len(folders)} folders recursively...")
            threshold_summary = []

            worker_count = max(1, os.cpu_count() or 1)
            self.log(f"Using {worker_count} CPU worker processes.")

            completed_results = []
            completed_count = 0
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                future_to_index = {
                    executor.submit(_process_single_worker, f, cfg): idx
                    for idx, f in enumerate(folders)
                }

                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        worker_output = future.result()
                    except Exception as e:
                        self.log(f"  [Error] Worker failed: {e}")
                        traceback.print_exc()
                        continue

                    for msg in worker_output.get("messages", []):
                        self.log(msg)

                    completed_count += 1

                    if worker_output.get("error"):
                        folder_name = os.path.basename(worker_output.get("folder", "unknown folder"))
                        self.log(f"  [Error] {folder_name}: {worker_output['error']}")
                        if worker_output.get("traceback"):
                            print(worker_output["traceback"])
                        self.log(f"  [Progress] {completed_count}/{len(folders)} folders finished.")
                        continue

                    result = worker_output.get("result")
                    if result is not None:
                        completed_results.append((idx, result))

                    self.log(f"  [Progress] {completed_count}/{len(folders)} folders finished.")

            threshold_summary = [
                result for _, result in sorted(completed_results, key=lambda item: item[0])
            ]

            if len(folders) > 1 and threshold_summary:
                summary_df = pd.DataFrame(threshold_summary, columns=["Folder Name", "Lasing Peak (nm)", "Threshold", "Error", "Slope Ratio", "R²"])
                summary_df["ND Value"] = cfg["nd"]
                summary_df["Energy Formula"] = cfg["formula"]
                summary_path = os.path.join(base_folder, "Threshold_Summary_Batch_DFB.csv")
                summary_df.to_csv(summary_path, index=False)
                self.log(f"\n[Success] Batch threshold summary saved to:\n  -> {summary_path}")

            self.log("ALL DONE.")
        except Exception as e:
            self.log(f"CRITICAL ERROR: {e}")
            traceback.print_exc()
        finally:
            self.after(0, lambda: self.btn_run.configure(state="normal", text="START PROCESSING"))

    def process_single(self, folder, cfg):
        name = os.path.basename(folder)
        self.log(f"\n--- {name} (DFB Mode: Global Peak Search) ---")
        
        try:
            efiles = find_files_fuzzy(folder, ["energy"])
            if not efiles: self.log("  [Skip] No energy file."); return
            try:
                df_en = pd.read_csv(efiles[0], header=None)
                angle = df_en.iloc[:, 0].values
                raw_en = df_en.iloc[:, 1].values
                pump = eval(cfg['formula'], {}, {'energy': raw_en, 'beam_size': cfg['beam_size'], 'ND': cfg['nd'], 'np': np})
            except Exception as e: self.log(f"  [Error] Energy calc: {e}"); return

            spec_files = find_files_fuzzy(folder, ["spec"])
            spec_files = [f for f in spec_files if "extract" not in f and "process" not in f]
            if not spec_files: self.log("  [Skip] No spectrum file."); return
            
            if any("transpose" in os.path.basename(f).lower() for f in spec_files):
                f_path = [f for f in spec_files if "transpose" in os.path.basename(f).lower()][0]
                df_s = pd.read_csv(f_path, header=None)
                full_wave = df_s.iloc[3:, -1].values.astype(float) 
                int_matrix_T = df_s.iloc[3:, :-1].values.astype(float) 
                int_matrix = int_matrix_T.T 
            else:
                f_path = spec_files[0]
                df_s = pd.read_csv(f_path, header=None)
                full_wave = df_s.iloc[-1, 3:].values.astype(float) 
                int_matrix = df_s.iloc[:-1, 3:].values.astype(float)

            n_pts = min(len(pump), int_matrix.shape[0])
            if n_pts == 0: self.log("  [Error] No overlap."); return
            pump = pump[:n_pts]; angle = angle[:n_pts]; int_matrix = int_matrix[:n_pts]

            first_valid_idx = n_pts
            for idx in range(n_pts):
                row_data = int_matrix[idx]
                if np.isnan(row_data).all(): continue
                
                robust_peak = np.nanpercentile(np.abs(row_data), 99)
                
                if robust_peak >= 10:
                    first_valid_idx = idx
                    break
                    
            if first_valid_idx == n_pts:
                self.log("  [Error] All spectra have a robust peak < 10. Skipping folder.")
                return
            
            first_valid_idx = min(first_valid_idx, 4)
                
            self.log(f"  [Info] Auto-skipped {first_valid_idx} background frames.")
            
            pump = pump[first_valid_idx:]
            angle = angle[first_valid_idx:]
            int_matrix = int_matrix[first_valid_idx:]
            n_pts = len(pump)

            if np.any(int_matrix < 0):
                self.log("  [Warning] Negative values found in the spectral data (LabVIEW recording issue). Applied absolute-value correction automatically.")
                int_matrix = np.abs(int_matrix)
            
            max_intensity_idx = np.unravel_index(np.argmax(int_matrix, axis=None), int_matrix.shape)
            best_spectrum_idx = max_intensity_idx[0]
            
            ref_spectrum = int_matrix[best_spectrum_idx]
            peak_idx = np.argmax(ref_spectrum)
            anchor_peak = full_wave[peak_idx]

            if cfg.get('auto_window') and cfg['auto_window'] > 0:
                w_min_val = anchor_peak - cfg['auto_window']
                w_max_val = anchor_peak + cfg['auto_window']
                self.log(f"  [Auto-Window] Global Peak locked at {anchor_peak:.2f} nm. Range: [{w_min_val:.2f}, {w_max_val:.2f}]")
            else:
                w_min_val = cfg['w_min'] if (cfg['w_min'] and cfg['w_min']>0) else np.min(full_wave)
                w_max_val = cfg['w_max'] if (cfg['w_max'] and cfg['w_max']>0) else np.max(full_wave)
                self.log(f"  [Manual-Window] Range locked to [{w_min_val:.2f}, {w_max_val:.2f}]")

            res = {
                'fwhm_nm_adaptive': [],   
                'r_area_nm': [],
                'peak_counts': []
            }

            for i in range(n_pts):
                curr_nm = int_matrix[i]
                
                # Direct raw area integration in nm
                r_area = calculate_integrated_area(full_wave, curr_nm, w_min_val, w_max_val)
                res['r_area_nm'].append(r_area)

                # Peak counts in the same wavelength window
                peak_counts = calculate_peak_counts(full_wave, curr_nm, w_min_val, w_max_val)
                res['peak_counts'].append(peak_counts)
                
                # Single, robust FWHM calculation
                w_adaptive = calculate_dfb_fwhm_adaptive(full_wave, curr_nm)
                res['fwhm_nm_adaptive'].append(w_adaptive)

            # --- Update DataFrame to match ---
            df_m = pd.DataFrame({
                'Incident Pump Fluence (uJ/cm2)': pump,
                'Integrated Intensity (arb. units)': res['r_area_nm'],  
                'Peak Counts (arb. units)': res['peak_counts'],
                'FWHM (nm)': res['fwhm_nm_adaptive'],
            })

            df_m_sorted = df_m.sort_values(by='Incident Pump Fluence (uJ/cm2)', kind='mergesort')
            sort_indices = df_m_sorted.index.to_numpy()
            pump_sorted = df_m_sorted['Incident Pump Fluence (uJ/cm2)'].values
            
            raw_matrix_sorted = int_matrix[sort_indices, :]
            raw_matrix_T = raw_matrix_sorted.T
                        
            out_file = os.path.join(folder, f"{name}_DFB_Analysed.xlsx")
            headers = [f"{e:.2f}" for e in pump_sorted] 
            
            df_raw = pd.DataFrame(raw_matrix_T, index=full_wave, columns=headers)
            df_raw.index.name = "Wavelength (nm)"

            try:
                with pd.ExcelWriter(out_file) as writer:
                    df_m_sorted.to_excel(writer, sheet_name='Metrics', index=False)
                    df_raw.to_excel(writer, sheet_name='Raw Spec (nm)') 
                self.log(f"  Saved: {name}_DFB_Analysed.xlsx")
            except Exception as e:
                self.log(f"  [Error] Saving Excel: {e}")

            # ====================================================
            # AUTO THRESHOLD INTEGRATION via ATML
            # ====================================================
            self.log("  [Auto-Fit] Running ATML threshold analysis...")
            
            x_for_fit = df_m_sorted['Incident Pump Fluence (uJ/cm2)'].values
            if cfg.get('fit_metric', 'integrated_intensity') == 'peak_counts':
                y_col_for_fit = 'Peak Counts (arb. units)'
            else:
                y_col_for_fit = 'Integrated Intensity (arb. units)'
            y_for_fit = df_m_sorted[y_col_for_fit].values
            self.log(f"  [Auto-Fit] Metric for threshold fit: {y_col_for_fit}")
            
            fit_result = atml.run_threshold_analysis(
                x_for_fit, 
                y_for_fit, 
                folder, 
                name
            )
            
            status_str = fit_result.get("status", "Unknown")
            th_val = fit_result.get("threshold", np.nan)
            th_err = fit_result.get("error", np.nan)
            slope_ratio = fit_result.get("slope_ratio", np.nan)
            r_squared = fit_result.get("r_squared", np.nan)
            
            self.log(f"  [Auto-Fit] Result: {status_str}")
            if np.isfinite(r_squared):
                self.log(f"  [Auto-Fit] R² = {r_squared:.4f}")

            if pd.notna(th_val) and np.isfinite(th_val):
                try:
                    idx_closest = (np.abs(pump_sorted - th_val)).argmin()
                    n_total = len(pump_sorted)
                    idx_low = min(2, n_total - 1)
                    idx_below = max(idx_low + 1, idx_closest - 2)
                    idx_th = idx_closest
                    idx_above = min(n_total - 2, idx_closest + 2)
                    idx_max = n_total - 1
                    
                    raw_indices = [idx_low, idx_below, idx_th, idx_above, idx_max]
                    selected_indices = sorted(list(set([i for i in raw_indices if 0 <= i < n_total])))
                    
                    fig, ax = plt.subplots(figsize=(10, 6), dpi=400)
                    colors = plt.cm.viridis(np.linspace(0, 1, len(selected_indices)))
                    
                    for c_idx, idx in enumerate(selected_indices):
                        energy = pump_sorted[idx]
                        spectrum_raw = raw_matrix_T[:, idx]
                        
                        plot_y = spectrum_raw / np.max(spectrum_raw) if np.max(spectrum_raw) > 0 else spectrum_raw
                        
                        linewidth = 2.5 if idx == idx_th else 1.5
                        linestyle = '-' if idx >= idx_th else '--' 
                        
                        ax.plot(full_wave, plot_y, label=f"{energy:.2f} $\mu$J/cm$^2$", 
                                color=colors[c_idx], linewidth=linewidth, linestyle=linestyle)
                    
                    ax.set_title(f"DFB Spectral Evolution: {name}", fontsize=14, pad=15)
                    ax.set_xlabel("Wavelength (nm)", fontsize=12)
                    ax.set_ylabel("Normalized Intensity (arb. units)", fontsize=12)
                    ax.legend(title="Pump Energy", loc='upper right')
                    ax.grid(True, linestyle=':', alpha=0.6)
                    
                    save_path = os.path.join(folder, f"{name}_DFB_Spectral_Evolution.png")
                    fig.savefig(save_path, bbox_inches='tight', dpi=400)
                    plt.close(fig)
                    
                    self.log("  [Auto-Fit] Saved representative DFB spectral evolution plot.")
                except Exception as e:
                    self.log(f"  [Error] Failed to plot spectra: {e}")
                    
            return [name, round(anchor_peak, 2), th_val, th_err, slope_ratio, r_squared]
        
        except Exception as e:
            self.log(f"  [Error] {name}: {e}")
            traceback.print_exc()
            return None

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = LaserAnalysisApp()
    app.mainloop()