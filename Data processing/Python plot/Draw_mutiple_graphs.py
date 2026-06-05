import os
import pandas as pd
import numpy as np
from trpl_processor import align_baseline_to_one
from scipy.signal import savgol_filter, medfilt
from scipy.interpolate import UnivariateSpline
import ctypes
import matplotlib.pyplot as plt
import tkinter as tk
from tkinter import filedialog, messagebox
import concurrent.futures

'''This script uses Python multiprocessing to process CSV and TXT files.
It groups files by subfolder. For TRPL data, it finds the curve with the latest 
rising edge (peak) as a reference, aligns all other TRPL curves to it, 
subtracts the background baseline, and prevents negative time/counts. 
Finally, it generates plots of the RAW data for previewing shapes.'''

# Enable High DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def calculate_robust_fwhm(wavelength, intensity, window_list):
    """
    Calculates FWHM using median filtering to despike, baseline subtraction,
    and Savitzky-Golay smoothing with dynamic window fallback.
    Also returns absolute integrated intensity.
    """
    if len(intensity) == 0 or np.all(np.isnan(intensity)):
        return np.nan, np.nan
    
    intensity_despiked = medfilt(intensity, kernel_size=5)
    baseline = np.percentile(intensity_despiked, 5) 
    intensity_corr = intensity_despiked - baseline
    intensity_corr[intensity_corr < 0] = 0 
    
    # Calculate absolute integrated intensity
    integrated_intensity = np.trapz(intensity_corr, x=wavelength)
    
    if np.max(intensity_corr) == 0: return np.nan, integrated_intensity
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
    return best_fwhm, integrated_intensity

def calculate_robust_peak_wavelength(wavelength, intensity, window_list=(5, 7, 9, 11), max_wavelength=700):
    """
    Finds peak wavelength by Savitzky-Golay smoothing with multiple window sizes,
    then takes the median of per-window peak positions. Excludes data beyond max_wavelength.
    """
    mask = wavelength <= max_wavelength
    x = wavelength[mask]
    y = intensity[mask]

    if len(y) == 0 or np.all(np.isnan(y)):
        return np.nan

    peak_waves = []
    for w in window_list:
        try:
            if w >= len(y):
                w = len(y) // 2 * 2 + 1
            if w < 3:
                w = 3

            y_smooth = savgol_filter(y, window_length=w, polyorder=3)
            peak_idx = np.argmax(y_smooth)
            peak_waves.append(x[peak_idx])
        except Exception:
            continue

    if not peak_waves:
        return np.nan

    return np.median(peak_waves)

def process_folder(folder_data):
    """
    Processes all files within a specific subfolder.
    Finds the 'latest' peak among TRPL files and aligns all others to it.
    Saves raw data previews.
    """
    folder_path, file_list = folder_data
    
    processed_files = []
    max_peak_time = -float('inf')
    
    # --- PASS 1: Read data and find the reference TRPL peak ---
    for filepath in file_list:
        scan_type = ""
        data_start_row = -1
        filename = os.path.basename(filepath)
        
        try:
            is_uvvis = False
            with open(filepath, 'r', encoding='windows-1252') as f:
                for i, line in enumerate(f):
                    parts = [p.strip() for p in line.replace('\t', ',').split(',')]
                    
                    if i == 1 and len(parts) >= 2:
                        scan_type = parts[1].lower()
                        
                    if i == 2 and len(parts) >= 2:
                        if parts[1].lower() == 'jasco':
                            is_uvvis = True
                            scan_type = "uv-vis"
                            
                    if is_uvvis:
                        if 'xydata' in line.lower():
                            data_start_row = i + 1
                            break
                    else:
                        if len(parts) >= 2:
                            try:
                                float(parts[0])
                                float(parts[1])
                                data_start_row = i
                                break 
                            except ValueError:
                                pass
            
            if data_start_row == -1:
                continue 
                
            # Skip irrelevant files that are not standard scans
            if "time scan" not in scan_type and "emission scan" not in scan_type and "uv-vis" not in scan_type:
                continue 
                
            df = pd.read_csv(filepath, skiprows=data_start_row, header=None, usecols=[0, 1], engine='python', encoding='windows-1252')
            df.columns = ['X', 'Y']
            df['X'] = pd.to_numeric(df['X'], errors='coerce')
            df['Y'] = pd.to_numeric(df['Y'], errors='coerce')
            df = df.dropna().reset_index(drop=True)
            
            if df.empty:
                continue
            
            # Identify the peak time for TRPL scans to find the maximum (latest) one
            if "time scan" in scan_type:
                peak_time = df['X'].iloc[df['Y'].idxmax()]
                if peak_time > max_peak_time:
                    max_peak_time = peak_time
                    
            # Store standard scans
            processed_files.append({
                'filepath': filepath,
                'df': df,
                'raw_df': df.copy(), # Keep original data for previews
                'scan_type': scan_type,
                'filename': filename
            })
            
        except Exception as e:
            print(f"Failed to read {filename}: {e}")

    if not processed_files:
        return folder_path, 0
        
    # --- PASS 2: Align TRPL data, subtract baseline, and plot ---
    plot_count = 0
    min_x_overall = float('inf')
    
    # First loop in Pass 2: apply alignments and baselines to the working df
    for item in processed_files:
        df = item['df']
        scan_type = item['scan_type']
        
        if "time scan" in scan_type:
            # 1. Alignment: Shift the X-axis so its peak matches the latest peak (max_peak_time)
            current_peak_time = df['X'].iloc[df['Y'].idxmax()]
            time_shift = max_peak_time - current_peak_time
            df['X'] = df['X'] + time_shift
            
            # 2. Baseline calculation and alignment (调用我们新建的外部脚本)
            df, _ = align_baseline_to_one(df, x_col='X', y_col='Y')
            item['df'] = df # 【重要】将处理后的新 DataFrame 写回字典
            
            # 记录整体最小的时间点，以便稍后统一平移时间轴防止负数
            if df['X'].min() < min_x_overall:
                min_x_overall = df['X'].min()

    # If any time value became negative, shift ALL TRPL data in this folder to start at >= 0
    global_time_correction = 0
    if min_x_overall < 0 and min_x_overall != float('inf'):
        global_time_correction = abs(min_x_overall)
        
    # Second loop in Pass 2: Apply final time correction and plot
    for item in processed_files:
        df = item['df']
        raw_df = item['raw_df'] 
        scan_type = item['scan_type']
        filepath = item['filepath']
        filename = item['filename']
        if "time scan" in scan_type:
            df['X'] = df['X'] + global_time_correction
            
        # Plotting using the raw data
        try:
            plt.figure(figsize=(6, 4))
            plt.style.use('default') 
            
            if "time scan" in scan_type:
                # 直接使用抹平基线到 y=1 后的 df 画图，而不是 raw_df
                plt.semilogy(df['X'], df['Y'], color='blue', linewidth=1)
                plt.title(f"TRPL Preview (Processed baseline=1)\n{filename}", fontsize=10)
                plt.xlabel("Time (ns)")
                plt.ylabel("Counts")
                
            elif "emission scan" in scan_type:
                plt.plot(raw_df['X'], raw_df['Y'], color='red', linewidth=1)
                
                # --- NEW: Peak and FWHM Calculation ---
                x_vals = raw_df['X'].values
                y_vals = raw_df['Y'].values
                peak_wave = calculate_robust_peak_wavelength(x_vals, y_vals)
                
                # Using smaller window list for FWHM calculation
                fwhm_val, int_intensity = calculate_robust_fwhm(x_vals, y_vals, [3, 5, 9, 11])
                
                # Format the title to include the metrics
                title_str = f"PL Preview (Raw Data)\n{filename}"
                if not np.isnan(peak_wave):
                    title_str += f"\nPeak: {peak_wave:.1f} nm"
                else:
                    title_str += f"\nPeak: N/A"
                if not np.isnan(fwhm_val):
                    title_str += f" | FWHM: {fwhm_val:.1f} nm"
                else:
                    title_str += f" | FWHM: N/A"
                
                title_str += f" | Int. Intensity: {int_intensity:.2e}"
                
                plt.title(title_str, fontsize=10)
                # --------------------------------------
                
                plt.xlabel("Wavelength (nm)")
                plt.ylabel("Counts")

            elif "uv-vis" in scan_type:
                plt.plot(raw_df['X'], raw_df['Y'], color='green', linewidth=1)
                
                x_vals = raw_df['X'].values
                y_vals = raw_df['Y'].values
                
                mask_below = x_vals < 400
                mask_above = x_vals >= 400
                
                title_str = f"UV-Vis Preview (Raw Data)\n{filename}"
                
                # Mark value at 330 nm
                target_wave = 330
                idx_330 = np.argmin(np.abs(x_vals - target_wave))
                wave_330 = x_vals[idx_330]
                abs_330 = y_vals[idx_330]
                percent_t_330 = 10 ** (2 - abs_330)
                percent_abs_330 = 100 - percent_t_330
                plt.plot(wave_330, abs_330, 'v', color='blue')
                plt.annotate(f"{wave_330:.1f} nm\nAbs: {abs_330:.2f} ({percent_abs_330:.1f}%)", 
                             xy=(wave_330, abs_330),
                             xytext=(0, 10), textcoords="offset points", ha='center', va='bottom', fontsize=8, color='blue')
                
                # Find peak >= 400 nm
                if np.any(mask_above):
                    idx_above = np.argmax(y_vals[mask_above])
                    peak_wave_above = x_vals[mask_above][idx_above]
                    peak_abs_above = y_vals[mask_above][idx_above]
                    percent_t_above = 10 ** (2 - peak_abs_above)
                    percent_abs_above = 100 - percent_t_above
                    plt.plot(peak_wave_above, peak_abs_above, 'v', color='purple')
                    plt.annotate(f"{peak_wave_above:.1f} nm\nAbs: {peak_abs_above:.2f} ({percent_abs_above:.1f}%)", 
                                 xy=(peak_wave_above, peak_abs_above),
                                 xytext=(0, 10), textcoords="offset points", ha='center', va='bottom', fontsize=8, color='purple')
                
                plt.title(title_str, fontsize=10)
                plt.xlabel("Wavelength (nm)")
                plt.ylabel("Absorbance")

            # plt.grid(True, which="both", linestyle='--', alpha=0.5)
            
            save_path = os.path.splitext(filepath)[0] + '.png'
            plt.savefig(save_path, dpi=400, bbox_inches='tight')
            plt.close()
            plot_count += 1
                
        except Exception as e:
            print(f"Failed to plot or process {filename}: {e}")
            
    return folder_path, plot_count

def main():
    root = tk.Tk()
    root.withdraw()
    
    target_folder = filedialog.askdirectory(title="Select Folder to Scan for CSV/TXT Files")
    
    if not target_folder:
        print("No folder selected. Exiting.")
        return
        
    print(f"Scanning directory and subdirectories:\n{target_folder}\n")
    
    # Group files by their parent directory
    folder_dict = {}
    for current_root, dirs, files in os.walk(target_folder):
        for file in files:
            if file.lower().endswith(('.csv', '.txt')):
                if current_root not in folder_dict:
                    folder_dict[current_root] = []
                folder_dict[current_root].append(os.path.join(current_root, file))
                
    total_folders = len(folder_dict)
    if total_folders == 0:
        print("No CSV or TXT files found.")
        return
        
    print(f"Found files in {total_folders} folders. Starting parallel processing...")
    
    # Prepare data for multiprocessing
    folder_tasks = list(folder_dict.items())
    
    processed_count = 0
    
    # Run multiprocessing Pool by FOLDER
    with concurrent.futures.ProcessPoolExecutor() as executor:
        results = executor.map(process_folder, folder_tasks)
        
        for folder_path, plot_count in results:
            processed_count += plot_count

    print(f"\nDone! Created {processed_count} PNG previews.")
    
    root.update() # Process any pending events
    messagebox.showinfo(
        "Batch Processing Complete", 
        f"Successfully generated {processed_count} PNG previews.",
        parent=root
    )
    root.destroy()

if __name__ == "__main__":
    main()