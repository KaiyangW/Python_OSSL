import os
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import tkinter as tk
from tkinter import filedialog
import ctypes
import re
from scipy.signal import savgol_filter, medfilt
from scipy.interpolate import UnivariateSpline

# Enable High DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ==========================================
# CONFIGURATION
# ==========================================
PLOT_DPI = 300

# --- Wavelength range for ALL data processing ---
# Only data within [WAVELENGTH_MIN, WAVELENGTH_MAX] will be used.
WAVELENGTH_MIN = 390.0   # nm
WAVELENGTH_MAX = 800.0   # nm

# --- Integration window: peak ± INTEGRATION_HALF_WINDOW nm ---
INTEGRATION_HALF_WINDOW = 10.0  # nm

# --- FWHM smoothing windows (median of valid results is used) ---
FWHM_WINDOWS = [5, 7, 11, 21, 31]

# --- PL background: use the N weakest spectra as the PL reference ---
PL_REFERENCE_N_SPECTRA = 3

# ==========================================
# HELPER FUNCTIONS (ported from ASE Threshold data processing ultra.py)
# ==========================================

try:
    trapz = np.trapezoid
except AttributeError:
    trapz = np.trapz


def sanitize_filename(name):
    """Removes or replaces characters that are unsafe for filenames."""
    return re.sub(r'[\\/*?:"<>|]', "_", name)


def natural_sort_key(filename):
    """Key for Windows-style natural sort (numbers sorted numerically)."""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split(r'(\d+)', filename)]


def calculate_robust_fwhm(wavelength, intensity, window_list):
    """
    Smoothes the data using multiple windows, calculates FWHM for each,
    and returns the median of the valid FWHM values.
    """
    if len(intensity) == 0 or np.all(np.isnan(intensity)):
        return np.nan

    fwhm_candidates = []
    for w in window_list:
        try:
            if w >= len(intensity):
                w = len(intensity) // 2 * 2 + 1
            if w < 3:
                w = 3
            # Ensure w is odd
            if w % 2 == 0:
                w += 1

            # 1. Smooth the data directly
            y_smooth = savgol_filter(intensity, window_length=w, polyorder=3)
            
            # 2. Subtract baseline
            baseline = np.percentile(y_smooth, 5)
            y_smooth_corr = y_smooth - baseline
            
            max_val = np.max(y_smooth_corr)
            if max_val <= 0:
                continue

            y_norm = y_smooth_corr / max_val
            
            # 3. Find peak
            peak_idx = np.argmax(y_norm)
            
            # 4. Scan outwards to find half-max
            left_idx = -1
            for i in range(peak_idx, 0, -1):
                if y_norm[i] < 0.5:
                    left_idx = i
                    break
                    
            right_idx = -1
            for i in range(peak_idx, len(y_norm) - 1):
                if y_norm[i] > 0.5 and y_norm[i+1] <= 0.5:
                    right_idx = i + 1
                    break
                    
            if left_idx != -1 and right_idx != -1:
                # Interpolate left crossing
                y1, y2 = y_norm[left_idx], y_norm[left_idx + 1]
                x1, x2 = wavelength[left_idx], wavelength[left_idx + 1]
                w_left = x1 + (0.5 - y1) * (x2 - x1) / (y2 - y1 + 1e-9)

                # Interpolate right crossing
                y1, y2 = y_norm[right_idx - 1], y_norm[right_idx]
                x1, x2 = wavelength[right_idx - 1], wavelength[right_idx]
                w_right = x1 + (0.5 - y1) * (x2 - x1) / (y2 - y1 + 1e-9)

                fwhm_candidates.append(abs(w_right - w_left))
                
        except Exception:
            continue

    if not fwhm_candidates:
        return np.nan
        
    return np.median(fwhm_candidates)


def calculate_integrated_area(wavelengths, intensity, w_min, w_max):
    """Trapezoid integration over [w_min, w_max]."""
    if w_min is None or np.isnan(w_min):
        w_min = np.min(wavelengths)
    if w_max is None or np.isnan(w_max):
        w_max = np.max(wavelengths)

    mask = (wavelengths >= w_min) & (wavelengths <= w_max)
    wave_segment = wavelengths[mask]
    int_segment = intensity[mask]

    if len(wave_segment) < 2:
        return 0.0
    sort_idx = np.argsort(wave_segment)
    return trapz(int_segment[sort_idx], x=wave_segment[sort_idx])


def get_pl_reference_shape(intensity_matrix, n_frames):
    """
    Build a PL reference spectrum from the n_frames WEAKEST rows of intensity_matrix.
    Returns a smoothed, baseline-zeroed reference array.
    """
    total_frames = intensity_matrix.shape[0]
    n_frames = min(n_frames, total_frames)
    if n_frames == 0:
        return None

    # Calculate max intensity for each spectrum
    max_intensities = np.max(intensity_matrix, axis=1)
    # Get indices of the weakest spectra
    weakest_indices = np.argsort(max_intensities)[:n_frames]
    
    pl_ref = np.mean(intensity_matrix[weakest_indices, :], axis=0)
    try:
        w_len = 11 if len(pl_ref) > 11 else (len(pl_ref) // 2 * 2 + 1 if len(pl_ref) > 4 else 3)
        if len(pl_ref) > 4:
            pl_ref = savgol_filter(pl_ref, window_length=w_len, polyorder=3)
    except Exception:
        pass

    pl_ref = pl_ref - np.min(pl_ref)
    pl_ref[pl_ref < 0] = 0
    return pl_ref


def strip_pl_background(wavelengths, current_spectrum, pl_ref_spectrum, w_min, w_max):
    """
    Scale the PL reference to match current_spectrum in the OUTSIDE region,
    then subtract it. Returns (pure_signal, scaled_background).
    """
    if pl_ref_spectrum is None:
        return current_spectrum.copy(), np.zeros_like(current_spectrum)

    if w_min is not None and w_max is not None:
        # Scale using the regions OUTSIDE the integration window
        mask = (wavelengths < w_min) | (wavelengths > w_max)
        if np.sum(mask) == 0:
            mask = np.ones(len(wavelengths), dtype=bool)
    else:
        margin = max(1, int(len(wavelengths) * 0.1))
        mask = np.zeros(len(wavelengths), dtype=bool)
        mask[:margin] = True
        mask[-margin:] = True

    curr_region = current_spectrum[mask]
    ref_region = pl_ref_spectrum[mask]
    ref_sum = np.sum(ref_region)

    scaling_factor = 0.0 if ref_sum < 1e-9 else np.sum(curr_region) / ref_sum
    if scaling_factor < 0:
        scaling_factor = 0.0

    scaled_bg = pl_ref_spectrum * scaling_factor
    pure_signal = current_spectrum - scaled_bg
    pure_signal[pure_signal < 0] = 0

    return pure_signal, scaled_bg


# ==========================================
# CORE PROCESSING FUNCTION
# ==========================================

def process_folder(folder_path):
    """
    Reads all Ocean Optics CSV files in a folder, applies wavelength range
    filtering, PL background subtraction, FWHM calculation, and peak integration.
    Saves two summary plots:
      1. File index (Date order) vs Integrated Intensity (PL removed)
      2. File index (Date order) vs FWHM
    """
    # --- 1. Gather and sort CSV files (Date Modified Ascending) ---
    try:
        all_files = os.listdir(folder_path)
    except Exception as e:
        print(f"Error accessing {folder_path}: {e}")
        return

    csv_files = sorted(
        [f for f in all_files if f.lower().endswith('.csv') and 'background' not in f.lower()],
        key=lambda f: os.path.getmtime(os.path.join(folder_path, f))
    )

    if not csv_files:
        return

    # --- 2. Read each file and filter to the configured wavelength range ---
    valid_spectra = []   # list of (filename, wavelength_array, intensity_array)

    for fname in csv_files:
        fpath = os.path.join(folder_path, fname)
        try:
            df = pd.read_csv(fpath, comment='#', header=None)
            if df.shape[1] < 2 or len(df) < 10:
                continue

            wl = df.iloc[:, 0].values.astype(float)
            it = df.iloc[:, 1].values.astype(float)

            # Apply wavelength range filter
            mask = (wl >= WAVELENGTH_MIN) & (wl <= WAVELENGTH_MAX)
            wl_crop = wl[mask]
            it_crop = it[mask]

            if len(wl_crop) < 10:
                continue

            valid_spectra.append((fname, wl_crop, it_crop))
        except Exception:
            continue

    if len(valid_spectra) < 2:
        print(f"  [Skip] {folder_path}: fewer than 2 usable spectra in range "
              f"[{WAVELENGTH_MIN}, {WAVELENGTH_MAX}] nm.")
        return

    print(f"Processing folder: {folder_path} ({len(valid_spectra)} files)")

    # Unpack into arrays
    filenames  = [s[0] for s in valid_spectra]
    wavelength = valid_spectra[0][1]          # assume all files share the same wavelength axis
    intensity_matrix = np.array([s[2] for s in valid_spectra])  # shape: (N_files, N_wavelengths)

    # --- 3. Determine integration window: auto-find peak on the highest-intensity spectrum ---
    max_mean_idx = np.argmax(np.max(intensity_matrix, axis=1))
    ref_spectrum_for_peak = intensity_matrix[max_mean_idx]
    peak_idx  = np.argmax(ref_spectrum_for_peak)
    peak_nm   = wavelength[peak_idx]
    w_min_int = peak_nm - INTEGRATION_HALF_WINDOW
    w_max_int = peak_nm + INTEGRATION_HALF_WINDOW
    print(f"  Peak detected at {peak_nm:.2f} nm → integration window: "
          f"[{w_min_int:.2f}, {w_max_int:.2f}] nm")

    # --- 4. Build PL reference from the weakest PL_REFERENCE_N_SPECTRA spectra ---
    pl_ref = get_pl_reference_shape(intensity_matrix, PL_REFERENCE_N_SPECTRA)

    # --- 5. Main processing loop ---
    integrated_intensities = []
    fwhm_values            = []

    for i, (fname, wl_i, it_i) in enumerate(valid_spectra):
        # PL background subtraction
        pure_signal, _ = strip_pl_background(wavelength, it_i, pl_ref, w_min_int, w_max_int)

        # Integrated intensity (PL removed) over the auto window
        area = calculate_integrated_area(wavelength, pure_signal, w_min_int, w_max_int)
        integrated_intensities.append(area)

        # FWHM on the RAW signal (to properly capture the transition from broad PL to narrow ASE)
        fwhm = calculate_robust_fwhm(wavelength, it_i, FWHM_WINDOWS)
        fwhm_values.append(fwhm)

    # --- 6. Reverse order for output (change from High->Low to Low->High energy) ---
    filenames.reverse()
    integrated_intensities.reverse()
    fwhm_values.reverse()

    x_indices = np.arange(1, len(valid_spectra) + 1)   # 1-based file order index

    # Build short x-tick labels (strip .csv extension, truncate long names)
    x_labels = [os.path.splitext(f)[0] for f in filenames]

    # --- 7. Save results to Excel ---
    folder_name = os.path.basename(folder_path)
    df_results = pd.DataFrame({
        'File Index (Date Order)': x_indices,
        'Filename': filenames,
        'Integrated Intensity (PL Removed)': integrated_intensities,
        'FWHM (nm)': fwhm_values,
        'Integration Window Min (nm)': w_min_int,
        'Integration Window Max (nm)': w_max_int,
        'Peak Wavelength (nm)': peak_nm,
    })
    xlsx_path = os.path.join(folder_path, f"Results_{sanitize_filename(folder_name)}.xlsx")
    try:
        df_results.to_excel(xlsx_path, index=False)
        print(f"  >>> Saved results to: {xlsx_path}")
    except Exception as e:
        print(f"  [Warning] Could not save Excel: {e}")

    # --- 8. Combined Plot: Integrated Intensity (Left Y) & FWHM (Right Y) ---
    fig, ax1 = plt.subplots(figsize=(10, 6))

    # Left Y-axis: Integrated Intensity
    color_int = '#4C9BE8'  # Blue-ish
    ax1.plot(x_indices, integrated_intensities,
             marker='o', linewidth=2, markersize=6,
             color=color_int, label='Integrated Intensity')
    ax1.set_xlabel("File (Low to High Energy)", fontsize=12)
    ax1.set_ylabel("Integrated Intensity (PL removed, arb.)", color=color_int, fontsize=12)
    ax1.tick_params(axis='y', labelcolor=color_int)
    ax1.set_xticks(x_indices)
    ax1.set_xticklabels(x_labels, rotation=45, ha='right', fontsize=8)
    ax1.grid(True, linestyle='--', alpha=0.3)

    # Right Y-axis: FWHM
    ax2 = ax1.twinx()
    color_fwhm = '#E84C4C' # Red-ish
    valid_mask = ~np.isnan(fwhm_values)
    if np.any(valid_mask):
        ax2.plot(x_indices[valid_mask],
                 np.array(fwhm_values)[valid_mask],
                 marker='s', linewidth=2, markersize=6,
                 color=color_fwhm, label='FWHM')
    ax2.set_ylabel("FWHM (nm)", color=color_fwhm, fontsize=12)
    ax2.tick_params(axis='y', labelcolor=color_fwhm)

    # Combined Title and Legend
    plt.title(f"Summary: {folder_name}\n"
              f"Integration Window: {w_min_int:.1f}–{w_max_int:.1f} nm", 
              fontsize=13, fontweight='bold', pad=15)
    
    # Add legends from both axes
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', frameon=True, fontsize=10)

    fig.tight_layout()
    save_path_summary = os.path.join(folder_path, f"Summary_Intensity_FWHM_{sanitize_filename(folder_name)}.png")
    fig.savefig(save_path_summary, dpi=PLOT_DPI, bbox_inches='tight')
    plt.close(fig)
    print(f"  >>> Saved summary plot: {save_path_summary}")

    # --- 9. Combined Normalized Spectral Plot ---
    COMBINED_PLOT_DPI = 600
    fig_comb, ax_comb = plt.subplots(figsize=(10, 6))
    
    # Use a colormap to differentiate spectra
    colors = plt.cm.viridis(np.linspace(0, 1, len(valid_spectra)))
    
    for (fname, wl_crop, it_crop), color in zip(valid_spectra[::-1], colors):
        max_it = np.max(it_crop)
        if max_it > 0:
            it_norm = it_crop / max_it
        else:
            it_norm = it_crop
            
        ax_comb.plot(wl_crop, it_norm, color=color, linewidth=1.5, alpha=0.8, 
                     label=os.path.splitext(fname)[0])
        
    ax_comb.set_xlabel("Wavelength (nm)", fontsize=12)
    ax_comb.set_ylabel("Normalized Intensity", fontsize=12)
    ax_comb.set_title(f"Normalized Spectra: {folder_name}\nRange: {WAVELENGTH_MIN}–{WAVELENGTH_MAX} nm", 
                     fontsize=13, fontweight='bold')
    ax_comb.grid(True, linestyle=':', alpha=0.6)
    
    # Highlight integration window
    if w_min_int >= WAVELENGTH_MIN and w_max_int <= WAVELENGTH_MAX:
        ax_comb.axvspan(w_min_int, w_max_int, color='yellow', alpha=0.2, label='Integration Window')
    
    # Handle legend: place outside plot if many items, omit if too many
    if len(valid_spectra) <= 25:
        ax_comb.legend(fontsize=8, bbox_to_anchor=(1.05, 1), loc='upper left')
    
    fig_comb.tight_layout()
    comb_plot_name = f"All_Normalized_Spectra_{sanitize_filename(folder_name)}.png"
    comb_plot_path = os.path.join(folder_path, comb_plot_name)
    fig_comb.savefig(comb_plot_path, dpi=COMBINED_PLOT_DPI)
    plt.close(fig_comb)

    print(f"  >>> Saved combined normalized spectral plot: {comb_plot_name}")


# ==========================================
# MAIN
# ==========================================

def main():
    root = tk.Tk()
    root.withdraw()

    print("=" * 60)
    print("ASE OCEAN OPTICS — INTEGRATED INTENSITY & FWHM PROCESSOR")
    print("=" * 60)
    print(f"Wavelength range : [{WAVELENGTH_MIN}, {WAVELENGTH_MAX}] nm")
    print(f"Integration window: peak ± {INTEGRATION_HALF_WINDOW} nm (auto-detected)")
    print(f"PL reference     : weakest {PL_REFERENCE_N_SPECTRA} spectra")
    print()
    print("Select the root folder where your data is stored.")

    root_data_dir = filedialog.askdirectory(title="Select Root Data Directory")

    if not root_data_dir:
        print("Operation cancelled.")
        return

    print(f"\nScanning recursively from: {root_data_dir}\n")

    processed_count = 0
    for current_root, dirs, files in os.walk(root_data_dir):
        if any(f.lower().endswith('.csv') for f in files):
            process_folder(current_root)
            processed_count += 1

    print("\n" + "=" * 60)
    print(f"Done! Processed {processed_count} folders.")
    print("=" * 60)


if __name__ == "__main__":
    main()
