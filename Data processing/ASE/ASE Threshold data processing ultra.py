import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import re
import auto_threshold_module as atm #auto-threshold check
import pandas as pd
import numpy as np
from scipy.signal import savgol_filter, medfilt
from scipy.interpolate import UnivariateSpline
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


''' 
Note: this auto processing script will read energy.csv and spectrum.csv.
So it requires only energy.csv and spectrum.csv to work. For auto-threshold check, place the 
auto_threshold_module.py in the same folder as this script. 

In energy.csv, the 2nd column is voltage readings by the PD
In spectrum.csv, the last raw is wavelength. 
    
This script provides: 
1.PL background substraction via Jacobian conversion (nm to eV)
2.FWHM calculation via data smoothing (Savitzky-Golay) with dynamic window
3.Auto threshold calculation via Hinge fit model with saturation detection
4.Auto background-check points skip (as when spectrometer record background)
5.Spectrum quality filter: rows with column2 == 0.5 are dropped (and matching energy angles)
6.Results will be saved: graphs of auto-fitting for eye-check, xlsx files of processed data

'''

# Define the local directory for configuration files
CONFIG_DIR = r"C:\My files\Programs_codes"
CONFIG_FILE = os.path.join(CONFIG_DIR, "laser_analysis_config.json")

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

def calculate_robust_fwhm(wavelength, intensity, window_list):
    if len(intensity) == 0 or np.all(np.isnan(intensity)):
        return np.nan
    
    intensity_despiked = medfilt(intensity, kernel_size=5)
    baseline = np.percentile(intensity_despiked, 5) 
    intensity_corr = intensity_despiked - baseline
    intensity_corr[intensity_corr < 0] = 0 
    
    if np.max(intensity_corr) == 0: return np.nan
    best_fwhm = np.nan

    for w in window_list:
        try:
            if w >= len(intensity_corr): w = len(intensity_corr) // 2 * 2 + 1
            if w < 3: w = 3
            
            y_smooth = savgol_filter(intensity_corr, window_length=w, polyorder=3)
            max_val = np.max(y_smooth)
            if max_val == 0: continue
            
            y_norm = y_smooth / max_val
            spline = UnivariateSpline(wavelength, y_norm - 0.5, s=0)
            roots = spline.roots()
            
            if len(roots) >= 2:
                best_fwhm = abs(roots[-1] - roots[0])
                break 
            else:
                # Fallback: Direct Index Scan
                peak_idx = np.argmax(y_smooth)
                left_idx = -1
                for i in range(peak_idx, 0, -1):
                    if y_norm[i] < 0.5:
                        left_idx = i; break
                right_idx = -1
                for i in range(peak_idx, len(y_norm)):
                    if y_norm[i] < 0.5:
                        right_idx = i; break
                
                if left_idx != -1 and right_idx != -1:
                    y1, y2 = y_norm[left_idx], y_norm[left_idx+1]
                    x1, x2 = wavelength[left_idx], wavelength[left_idx+1]
                    w_left = x1 + (0.5 - y1) * (x2 - x1) / (y2 - y1 + 1e-9)

                    y1, y2 = y_norm[right_idx-1], y_norm[right_idx]
                    x1, x2 = wavelength[right_idx-1], wavelength[right_idx]
                    w_right = x1 + (0.5 - y1) * (x2 - x1) / (y2 - y1 + 1e-9)
                    
                    best_fwhm = abs(w_right - w_left)
                    break 
        except Exception:
            continue
    return best_fwhm

def calculate_integrated_area(wavelengths, intensity, w_min, w_max):
    if w_min is None or np.isnan(w_min): w_min = np.min(wavelengths)
    if w_max is None or np.isnan(w_max): w_max = np.max(wavelengths)

    mask = (wavelengths >= w_min) & (wavelengths <= w_max)
    wave_segment = wavelengths[mask]
    int_segment = intensity[mask]

    if len(wave_segment) < 2: return 0.0
    sort_idx = np.argsort(wave_segment)
    return trapz(int_segment[sort_idx], x=wave_segment[sort_idx])

def get_pl_reference_shape(intensity_matrix, bg_skip):
    total_frames = intensity_matrix.shape[0]
    num_frames = 3
    start_idx = bg_skip
    end_idx = start_idx + num_frames
    
    if start_idx >= total_frames:
        start_idx = 0; end_idx = min(num_frames, total_frames)
        if total_frames == 0: return None

    if end_idx > total_frames: end_idx = total_frames
    if end_idx <= start_idx: return intensity_matrix[0, :]

    pl_ref = np.mean(intensity_matrix[start_idx:end_idx, :], axis=0)
    try:
        w_len = 11 if len(pl_ref) > 11 else (len(pl_ref)//2*2 + 1 if len(pl_ref)>4 else 3)
        if len(pl_ref) > 4: pl_ref = savgol_filter(pl_ref, window_length=w_len, polyorder=3)
    except: pass 
    
    pl_ref = pl_ref - np.min(pl_ref)
    pl_ref[pl_ref < 0] = 0
    return pl_ref

def strip_pl_background(wavelengths, current_spectrum, pl_ref_spectrum, w_min, w_max):
    if pl_ref_spectrum is None: return current_spectrum, np.zeros_like(current_spectrum)
    
    if w_min is not None and w_max is not None and not np.isnan(w_min) and not np.isnan(w_max):
        mask = (wavelengths < w_min) | (wavelengths > w_max)
        if np.sum(mask) == 0: mask = np.ones(len(wavelengths), dtype=bool) 
    else:
        margin = max(1, int(len(wavelengths) * 0.1))
        mask = np.zeros(len(wavelengths), dtype=bool)
        mask[:margin] = True; mask[-margin:] = True
    
    curr_region = current_spectrum[mask]
    ref_region = pl_ref_spectrum[mask]
    ref_sum = np.sum(ref_region)
    
    scaling_factor = 0 if ref_sum < 1e-9 else np.sum(curr_region) / ref_sum
    if scaling_factor < 0: scaling_factor = 0

    # Calculate the scaled background (the residual being removed)
    scaled_bg = pl_ref_spectrum * scaling_factor
    
    pure_ase = current_spectrum - scaled_bg
    pure_ase[pure_ase < 0] = 0
    
    return pure_ase, scaled_bg

# ==========================================
#  GUI CLASS
# ==========================================

class LaserAnalysisApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TADF Laser Analysis (v3.1 Fixed)")
        self.geometry("1100x800")
        ctk.set_appearance_mode("Dark")
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # UI Components
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

        self.log("Initialized. Settings memory restored.")
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
        add("Auto Peak Window (± nm):", "10.0", "entry_auto_window") # Added auto window
        add("Min Wavelength (Fallback):", "605", "entry_w_min")
        add("Max Wavelength (Fallback):", "635", "entry_w_max")

        ctk.CTkLabel(self.sidebar, text="--- Threshold Fit ---", text_color="gray").pack(pady=10)
        ctk.CTkLabel(
            self.sidebar,
            text="Auto Threshold: fit Hinge + 3-Line, pick higher R²",
            anchor="w",
            wraplength=320,
        ).pack(fill="x", padx=5, pady=(0, 5))

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

    # --- FIX: Explicit Save/Load Logic ---
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
                'formula': self.txt_formula.get("0.0", "end").strip()
            }
        except Exception as e:
            messagebox.showerror("Config Error", f"Check inputs: {e}")
            return None

    def save_settings(self):

        if not os.path.exists(CONFIG_DIR):
            try:
                os.makedirs(CONFIG_DIR)
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
                "formula": self.txt_formula.get("0.0", "end").strip()
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.log(f"Warning: Settings save failed - {e}")

    def load_settings(self):
        # EXPLICIT loading
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

            if d.get("formula"):
                self.txt_formula.delete("0.0", "end")
                self.txt_formula.insert("0.0", d["formula"])
                
        except Exception as e:
            self.log(f"Settings load error: {e}")

    def start_thread(self):
        if not hasattr(self, 'base_folder_path'): return
        cfg = self.get_cfg()
        if not cfg: return
        
        self.save_settings() # Save on start
        self.btn_run.configure(state="disabled", text="Running...")
        threading.Thread(target=self.process, args=(self.base_folder_path, cfg)).start()

    def process(self, base_folder, cfg):
        try:
            # Recursively walk through all directories and subdirectories
            folders = []
            for current_root, dirs, files in os.walk(base_folder):
                # Only add folders that contain at least one CSV file to save time
                if any(f.lower().endswith('.csv') for f in files):
                    folders.append(current_root)
            
            if not folders: 
                self.log("No valid folders containing CSV files found."); return
            
            self.log(f"Processing {len(folders)} folders recursively...")

            # [NEW] Initialize a list to collect threshold results from all subfolders
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
            
            # Save summary to CSV only if it's a parent folder (test == False)
            if len(folders) > 1 and threshold_summary:
                summary_df = pd.DataFrame(threshold_summary, columns=[
                    "Folder Name", "Lasing Peak (nm)", "Threshold", "Error", "Slope Ratio",
                    "Threshold Model", "R² Selected", "R² Hinge", "R² 3-Line",
                ])
                # Append global parameters to the batch summary for future reference
                summary_df["ND Value"] = cfg["nd"]
                summary_df["Energy Formula"] = cfg["formula"]
                
                summary_path = os.path.join(base_folder, "Threshold_Summary_Batch.csv")
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
        self.log(f"\n--- {name} (V4: Raw=Area, Pure=nm View) ---")
        
        try:
            # ====================================================
            # 1. Read files
            # ====================================================
            
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
                full_wave = df_s.iloc[3:, -1].values.astype(float) # nm
                int_matrix_T = df_s.iloc[3:, :-1].values.astype(float) 
                int_matrix = int_matrix_T.T 
                spec_angles_full = pd.to_numeric(df_s.iloc[0, :-1], errors='coerce').to_numpy()
                spec_col2_full = pd.to_numeric(df_s.iloc[2, :-1], errors='coerce').to_numpy()
            else:
                f_path = spec_files[0]
                df_s = pd.read_csv(f_path, header=None)
                full_wave = df_s.iloc[-1, 3:].values.astype(float) # nm
                int_matrix = df_s.iloc[:-1, 3:].values.astype(float)
                spec_meta_rows = df_s.iloc[:-1]
                spec_angles_full = pd.to_numeric(spec_meta_rows.iloc[:, 0], errors='coerce').to_numpy()
                spec_col2_full = pd.to_numeric(spec_meta_rows.iloc[:, 2], errors='coerce').to_numpy()

            n_pts = min(len(pump), int_matrix.shape[0])
            if n_pts == 0: self.log("  [Error] No overlap."); return
            pump = pump[:n_pts]; angle = angle[:n_pts]; int_matrix = int_matrix[:n_pts]
            spec_angles_full = spec_angles_full[:n_pts]
            spec_col2_full = spec_col2_full[:n_pts]

            # 1. Load LabVIEW data FIRST so it aligns with the original n_pts
            lv_c = np.full(n_pts, np.nan); lv_f = np.full(n_pts, np.nan)
            int_files = find_files_fuzzy(folder, ["integ"])
            if int_files:
                try:
                    df_i = pd.read_csv(int_files[0], header=None)
                    n_lv = min(len(df_i), n_pts)
                    lv_c[:n_lv] = df_i.iloc[:n_lv, 1].values
                    lv_f[:n_lv] = df_i.iloc[:n_lv, 2].values
                except: pass

            # ====================================================
            # 2. AUTO-DETECT BACKGROUND (Robust Peak >= 10)
            # ====================================================
            first_valid_idx = n_pts
            
            # Use the 99th percentile instead of absolute max to ignore random noise spikes
            for idx in range(n_pts):
                row_data = int_matrix[idx]
                
                # Check for NaN values to prevent comparison errors
                if np.isnan(row_data).all():
                    continue
                    
                robust_peak = np.nanpercentile(row_data, 99)
                
                if robust_peak >= 10:
                    first_valid_idx = idx
                    break
                    
            if first_valid_idx == n_pts:
                self.log("  [Error] All spectra have a robust peak < 10. Skipping folder.")
                return
                
            self.log(f"  [Info] Auto-skipped {first_valid_idx} background frames.")

            # 3. Trim all arrays to strictly feed only processed data forward
            pump = pump[first_valid_idx:]
            angle = angle[first_valid_idx:]
            int_matrix = int_matrix[first_valid_idx:]
            lv_c = lv_c[first_valid_idx:]
            lv_f = lv_f[first_valid_idx:]
            spec_angles_full = spec_angles_full[first_valid_idx:]
            spec_col2_full = spec_col2_full[first_valid_idx:]
            n_pts = len(pump)

            # 4. Drop spectrum rows flagged by column2 == 0.5 (and matching energy angles)
            valid_mask = ~np.isclose(spec_col2_full, 0.5, rtol=0, atol=1e-9, equal_nan=False)
            bad_count = int(np.sum(~valid_mask))
            if bad_count:
                bad_angles = spec_angles_full[~valid_mask]
                angle_list = ", ".join(f"{a:g}" for a in bad_angles if pd.notna(a))
                if not angle_list:
                    angle_list = "unknown"
                self.log(
                    f"  [Info] Skipped {bad_count} bad frames "
                    f"(spectrum column2 == 0.5); angles: {angle_list}"
                )

            pump = pump[valid_mask]
            angle = angle[valid_mask]
            int_matrix = int_matrix[valid_mask]
            lv_c = lv_c[valid_mask]
            lv_f = lv_f[valid_mask]
            n_pts = len(pump)

            if n_pts == 0:
                self.log("  [Error] No valid frames after spectrum quality filter.")
                return
            
            # 5. Override bg_skip for the rest of the script
            effective_bg_skip = 0

            # ====================================================
            # 3. Data Preparation: Jacobian Transformation
            # ====================================================
            hc = 1239.84193
            
            # 1. Convert axis nm -> eV
            axis_E_unsorted = hc / full_wave
            
            # 2. Strict Jacobian Correction: I(E) = I(nm) * (hc / E^2)
            jacobian_factor = hc / (axis_E_unsorted ** 2)
            int_matrix_E_unsorted = int_matrix * jacobian_factor
            
            # 3. Sort (eV ascending)
            sort_idx = np.argsort(axis_E_unsorted)
            axis_E = axis_E_unsorted[sort_idx]
            int_matrix_E = int_matrix_E_unsorted[:, sort_idx]
            
            # 4. Calculate PL Reference in eV domain
            pl_ref_E = get_pl_reference_shape(int_matrix_E, effective_bg_skip)
            
            # 5. Integration limits (Anchor and Lock)
            # Find the spectrum with the highest pump energy to anchor the peak
            max_pump_idx = np.argmax(pump)
            ref_spectrum = int_matrix[max_pump_idx]
            peak_idx = np.argmax(ref_spectrum)
            anchor_peak = full_wave[peak_idx]

            # Lock the integration window
            if cfg.get('auto_window') and cfg['auto_window'] > 0:
                w_min_val = anchor_peak - cfg['auto_window']
                w_max_val = anchor_peak + cfg['auto_window']
                self.log(f"  [Auto-Window] Peak locked at {anchor_peak:.2f} nm. Range: [{w_min_val:.2f}, {w_max_val:.2f}]")
            else:
                w_min_val = cfg['w_min'] if (cfg['w_min'] and cfg['w_min']>0) else np.min(full_wave)
                w_max_val = cfg['w_max'] if (cfg['w_max'] and cfg['w_max']>0) else np.max(full_wave)
                self.log(f"  [Manual-Window] Range locked to [{w_min_val:.2f}, {w_max_val:.2f}]")
            
            limit_E_min = hc / w_max_val
            limit_E_max = hc / w_min_val

            # ====================================================
            # 4. Processing Loop
            # ====================================================
            
            res = {
                'r_fwhm_nm_dynamic': [], # Dynamic smooth window (Robust FWHM)
                'r_area_nm': [], 
                'p_area_ev': [],
                'scaled_pl_area_nm': [],
                'peak_wavelength_nm': []
            }
            
            pure_spectra_nm_list = [] 
            residual_spectra_nm_list = [] # For Sheet 3
            
            # State machine for the dynamic FWHM calculation
            state_r = {'L': True, 'M': 0.0}

            for i in range(n_pts):
                curr_nm = int_matrix[i]

                peak_idx = np.argmax(curr_nm)
                res['peak_wavelength_nm'].append(full_wave[peak_idx])
                
                # Calculate Raw Area
                r_area = calculate_integrated_area(full_wave, curr_nm, w_min_val, w_max_val)
                res['r_area_nm'].append(r_area)
                
                # 3. Calculate FWHM using the Dynamic Smooth Window (Robust method)
                win_r = cfg['win_large'] if state_r['L'] else cfg['win_small']
                w_dyn = calculate_robust_fwhm(full_wave, curr_nm, win_r)
                
                if not pd.isna(w_dyn) and i >= effective_bg_skip:
                    # Track the maximum FWHM encountered
                    state_r['M'] = max(state_r['M'], w_dyn)
                    # Check for ASE/Lasing threshold collapse
                    if state_r['M'] > 0 and w_dyn < (state_r['M'] * cfg['collapse']):
                        state_r['L'] = False  # Switch state to small window forever
                        w_dyn = calculate_robust_fwhm(full_wave, curr_nm, cfg['win_small'])
                            
                res['r_fwhm_nm_dynamic'].append(w_dyn)

                # --- Part B: Pure ASE & Residual ---
                curr_E = int_matrix_E[i]
                
                # Unpack both pure ASE and the subtracted residual
                pure_E, residual_E = strip_pl_background(axis_E, curr_E, pl_ref_E, limit_E_min, limit_E_max)
                
                p_area = calculate_integrated_area(axis_E, pure_E, limit_E_min, limit_E_max)
                res['p_area_ev'].append(p_area)
                
                unsort_idx = np.argsort(sort_idx) 
                pure_E_original_order = pure_E[unsort_idx]
                residual_E_original_order = residual_E[unsort_idx]
                
                # Convert both back to nm view using the identical Jacobian factor
                pure_nm_view = pure_E_original_order / jacobian_factor
                residual_nm_view = residual_E_original_order / jacobian_factor
                
                pure_nm_view[pure_nm_view < 0] = 0
                pure_spectra_nm_list.append(pure_nm_view)
                residual_spectra_nm_list.append(residual_nm_view)

                # Calculate full-domain integrated area for the scaled PL background
                pl_area = calculate_integrated_area(full_wave, residual_nm_view, None, None)
                res['scaled_pl_area_nm'].append(pl_area)

            # ====================================================
            # 5. Data Sorting & Saving (同步排序修正版)
            # ====================================================
            
            df_m = pd.DataFrame({
                'Incident Pump Fluence (uJ/cm2)': pump,
                'Substracted Integrated Intensity (eV integ ×10)': res['p_area_ev'], 
                'Integrated Intensity with raw data(×10)': res['r_area_nm'],  
                'FWHM (nm)': res['r_fwhm_nm_dynamic'],
                'Scaled PL Integrated Intensity': res['scaled_pl_area_nm'],
                'Peak Wavelength (nm)': res['peak_wavelength_nm'],
            })

            df_m_sorted = df_m.sort_values(by='Incident Pump Fluence (uJ/cm2)', kind='mergesort')
            
            sort_indices = df_m_sorted.index.to_numpy()
            pump_sorted = df_m_sorted['Incident Pump Fluence (uJ/cm2)'].values
            
            pure_matrix_nm_T = np.array([pure_spectra_nm_list[i] for i in sort_indices]).T
            
            raw_matrix_sorted = int_matrix[sort_indices, :]
            raw_matrix_T = raw_matrix_sorted.T
            
            r_min = np.nanmin(raw_matrix_T, axis=0)
            r_max = np.nanmax(raw_matrix_T, axis=0)
            r_range = r_max - r_min
            r_range[r_range == 0] = 1.0 
            raw_matrix_norm = (raw_matrix_T - r_min) / r_range
            
            residual_matrix_nm_T = np.array([residual_spectra_nm_list[i] for i in sort_indices]).T

            out_file = os.path.join(folder, f"{name}_Analysed_ultra.xlsx")
            headers = [f"{e:.2f}" for e in pump_sorted] 

            df_pure = pd.DataFrame(pure_matrix_nm_T, index=full_wave, columns=headers)
            df_pure.index.name = "Wavelength (nm)"
            
            df_raw = pd.DataFrame(raw_matrix_norm, index=full_wave, columns=headers)
            df_raw.index.name = "Wavelength (nm)"
            
            df_residual = pd.DataFrame(residual_matrix_nm_T, index=full_wave, columns=headers)
            df_residual.index.name = "Wavelength (nm)"

            try:
                with pd.ExcelWriter(out_file) as writer:
                    df_m_sorted.to_excel(writer, sheet_name='Metrics', index=False)
                    df_pure.to_excel(writer, sheet_name='Pure ASE (nm View)')
                    df_raw.to_excel(writer, sheet_name='Raw Spec Normalized (nm)')
                    df_residual.to_excel(writer, sheet_name='Residual PL (nm)')
                    df_peak_shift = df_m_sorted[['Incident Pump Fluence (uJ/cm2)', 'Peak Wavelength (nm)']]
                    df_peak_shift.to_excel(writer, sheet_name='Peak Shift', index=False) 
                    
                self.log(f"  Saved: {name}_Analysed_ultra.xlsx (Sorted)")
            except Exception as e:
                self.log(f"  [Error] Saving Excel: {e}")

            # ====================================================
            # 6. AUTO THRESHOLD INTEGRATION
            # ====================================================
            self.log("  [Auto-Fit] Running threshold analysis (Hinge + 3-Line, pick higher R²)...")
            
            x_for_fit = df_m_sorted['Incident Pump Fluence (uJ/cm2)'].values
            y_for_fit = df_m_sorted['Substracted Integrated Intensity (eV integ ×10)'].values
            
            # Execute the module
            fit_result = atm.run_threshold_analysis(
                x_for_fit, 
                y_for_fit, 
                folder, 
                name,
                model_type="auto"
            )
            
            status_str = fit_result.get("status", "Unknown")
            th_val = fit_result.get("threshold", np.nan)
            th_err = fit_result.get("error", np.nan)
            selected_model = fit_result.get("model", "Unknown")

            slope_ratio = fit_result.get("slope_ratio", np.nan)
            r2_selected = fit_result.get("r_squared", np.nan)
            r2_hinge = fit_result.get("r_squared_hinge", np.nan)
            r2_3line = fit_result.get("r_squared_three_line", np.nan)
            self.log(f"  [Auto-Fit] Result: {status_str}")
            if np.isfinite(r2_hinge) or np.isfinite(r2_3line):
                self.log(
                    f"  [Auto-Fit] R² comparison: Hinge={r2_hinge:.4f}, "
                    f"3-Line={r2_3line:.4f} -> selected {selected_model}"
                )
            if selected_model == "3-Line":
                turn_points = fit_result.get("turn_points", [])
                turn_point_errors = fit_result.get("turn_point_errors", [])
                if len(turn_points) >= 2:
                    tp1_err = turn_point_errors[0] if len(turn_point_errors) > 0 else np.nan
                    tp2_err = turn_point_errors[1] if len(turn_point_errors) > 1 else np.nan
                    tp1_err_str = f" +/- {tp1_err:.3g}" if np.isfinite(tp1_err) else ""
                    tp2_err_str = f" +/- {tp2_err:.3g}" if np.isfinite(tp2_err) else ""
                    self.log(f"  [Auto-Fit] TP1 / Threshold: {turn_points[0]:.6g}{tp1_err_str} uJ/cm2")
                    self.log(f"  [Auto-Fit] TP2 / Second turn point: {turn_points[1]:.6g}{tp2_err_str} uJ/cm2")
                    self.log(f"  [Auto-Fit] Slope ratio uses final/first slope (k3/k1): {slope_ratio:.3g}")
            elif pd.notna(th_val) and np.isfinite(th_val):
                err_str = f" +/- {th_err:.3g}" if np.isfinite(th_err) else ""
                self.log(f"  [Auto-Fit] Threshold: {th_val:.6g}{err_str} uJ/cm2")

            # Plot representative raw spectra ---
            if pd.notna(th_val) and np.isfinite(th_val):
                try:
                    # Find the index of the pump energy closest to the threshold
                    idx_closest = (np.abs(pump_sorted - th_val)).argmin()
                    n_total = len(pump_sorted)
                    
                    # Define target indices based on dynamic range
                    # 1. Second or third smallest (index 2, usually pure PL)
                    idx_low = min(2, n_total - 1)
                    
                    # 2. One below threshold (e.g., 2 steps before threshold)
                    idx_below = max(idx_low + 1, idx_closest - 2)
                    
                    # 3. The threshold itself
                    idx_th = idx_closest
                    
                    # 4. One above threshold (e.g., 2 steps after threshold)
                    idx_above = min(n_total - 2, idx_closest + 2)
                    
                    # 5. Maximum energy (last index)
                    idx_max = n_total - 1
                    
                    # Collect and deduplicate indices to prevent overlaps 
                    # (in case the threshold is very close to the start or end)
                    raw_indices = [idx_low, idx_below, idx_th, idx_above, idx_max]
                    selected_indices = sorted(list(set([i for i in raw_indices if 0 <= i < n_total])))
                    
                    # Initialize plot with 400 DPI
                    fig, ax = plt.subplots(figsize=(10, 6), dpi=400)
                    
                    # Use a colormap to differentiate the lines visually
                    colors = plt.cm.viridis(np.linspace(0, 1, len(selected_indices)))
                    
                    for c_idx, idx in enumerate(selected_indices):
                        energy = pump_sorted[idx]
                        spectrum = raw_matrix_norm[:, idx]
                        
                        # Emphasize the threshold line
                        linewidth = 2.5 if idx == idx_th else 1.5
                        linestyle = '-' if idx >= idx_th else '--' # Optional: dashed for PL, solid for lasing
                        
                        ax.plot(full_wave, spectrum, label=f"{energy:.2f} $\\mu$J/cm$^2$", 
                                color=colors[c_idx], linewidth=linewidth, linestyle=linestyle)
                    
                    # Format the plot
                    ax.set_title(f"Spectral Evolution: {name}", fontsize=14, pad=15)
                    ax.set_xlabel("Wavelength (nm)", fontsize=12)
                    ax.set_ylabel("Normalized Intensity (arb. units)", fontsize=12)
                    ax.legend(title="Pump Energy", loc='upper right')
                    ax.grid(True, linestyle=':', alpha=0.6)
                    
                    # Save as PNG
                    save_path = os.path.join(folder, f"{name}_Spectral_Evolution.png")
                    fig.savefig(save_path, bbox_inches='tight', dpi=400)
                    plt.close(fig)
                    
                    self.log("  [Auto-Fit] Saved representative spectral evolution plot.")
                except Exception as e:
                    self.log(f"  [Error] Failed to plot spectra: {e}")

                try:
                    # Initialize a new figure for the pure ASE spectra
                    fig2, ax2 = plt.subplots(figsize=(10, 6), dpi=400)
                    
                    for c_idx, idx in enumerate(selected_indices):
                        energy = pump_sorted[idx]
                        
                        # Extract the pure ASE spectrum (PL subtracted, NOT normalized)
                        # pure_matrix_nm_T is already sorted and aligned with pump_sorted
                        spectrum_pure = pure_matrix_nm_T[:, idx]
                        
                        # Emphasize the threshold line
                        linewidth = 2.5 if idx == idx_th else 1.5
                        linestyle = '-' if idx >= idx_th else '--' 
                        
                        ax2.plot(full_wave, spectrum_pure, label=f"{energy:.2f} $\\mu$J/cm$^2$", 
                                color=colors[c_idx], linewidth=linewidth, linestyle=linestyle)
                    
                    # Format the plot
                    ax2.set_title(f"Pure ASE Spectral Evolution (PL Subtracted): {name}", fontsize=14, pad=15)
                    ax2.set_xlabel("Wavelength (nm)", fontsize=12)
                    ax2.set_ylabel("Absolute Intensity (arb. units)", fontsize=12)
                    ax2.legend(title="Pump Energy", loc='upper right')
                    ax2.grid(True, linestyle=':', alpha=0.6)
                    
                    # Save as PNG
                    save_path_pure = os.path.join(folder, f"{name}_Pure_ASE_Evolution.png")
                    fig2.savefig(save_path_pure, bbox_inches='tight', dpi=400)
                    plt.close(fig2)
                    
                    self.log("  [Auto-Fit] Saved pure ASE spectral evolution plot.")
                except Exception as e:
                    self.log(f"  [Error] Failed to plot pure ASE spectra: {e}")
                # --- END OF SECOND NEW BLOCK ---
                    
            return [
                name, round(anchor_peak, 2), th_val, th_err, slope_ratio, selected_model,
                r2_selected, r2_hinge, r2_3line,
            ]

        except Exception as e:
            self.log(f"  [Error] {name}: {e}")
            traceback.print_exc()
            return None

        except Exception as e:
            self.log(f"  [Error] {name}: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = LaserAnalysisApp()
    app.mainloop()