import os
import re
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import savgol_filter

# ==========================================
# Configuration and Parameters
# ==========================================
TARGET_FOLDER = r"C:\My files\Google drive sync\St Andrews\Data\Strasbourg materials\14 Mar-2 136 157 CBP ASE\136 ASE\136 test 1.7 air"
CONFIG = {
    'w_min': 605.0,
    'w_max': 635.0,
    'beam_size': 2.2E-2,
    'nd': 0.6,
    'formula': "(0.0485*(energy*440*10**ND)+0.052)/beam_size"
}

# ==========================================
# Helper Functions
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

def get_pl_reference_shape(intensity_matrix, bg_skip=0):
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

def strip_pl_background_details(wavelengths, current_spectrum, pl_ref_spectrum, w_min, w_max, full_spectrum=False):
    """
    Calculates scaling factor and subtracts background.
    If full_spectrum is True, it matches using the entire wavelength range.
    Returns: pure_ase, scaled_bg, scaling_factor, fit_mask
    """
    if pl_ref_spectrum is None: 
        return current_spectrum, np.zeros_like(current_spectrum), 0.0, np.ones(len(wavelengths), dtype=bool)
    
    # Define which parts of the spectrum are used to calculate the scaling ratio
    if full_spectrum:
        mask = np.ones(len(wavelengths), dtype=bool)
    elif w_min is not None and w_max is not None and not np.isnan(w_min) and not np.isnan(w_max):
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

    scaled_bg = pl_ref_spectrum * scaling_factor
    pure_ase = current_spectrum - scaled_bg
    pure_ase[pure_ase < 0] = 0
    
    return pure_ase, scaled_bg, scaling_factor, mask

def create_and_save_plot(full_wave, axis_E, raw_nm, raw_E, scaled_bg_E, pure_nm, scale_factor, fit_mask, save_path, title_prefix):
    """Reusable plotting function to generate the 4-panel image."""
    fig, axs = plt.subplots(2, 2, figsize=(16, 12), dpi=150)
    plt.subplots_adjust(hspace=0.3, wspace=0.2)
    
    plot_idx_nm = np.argsort(full_wave)
    wave_sorted = full_wave[plot_idx_nm]
    
    # Plot 1: Raw Spectrum (nm)
    axs[0, 0].plot(wave_sorted, raw_nm[plot_idx_nm], 'k-', linewidth=2)
    axs[0, 0].set_title(f"1. {title_prefix} - Raw Spectrum (nm)", fontsize=14, pad=10)
    axs[0, 0].set_xlabel("Wavelength (nm)", fontsize=12)
    axs[0, 0].set_ylabel("Intensity (arb. units)", fontsize=12)
    axs[0, 0].grid(True, linestyle=':', alpha=0.6)
    
    # Plot 2: Domain Conversion & Jacobian Correction (eV)
    axs[0, 1].plot(axis_E, raw_E, 'b-', linewidth=2)
    axs[0, 1].set_title("2. Domain Conversion (Energy Domain)", fontsize=14, pad=10)
    axs[0, 1].set_xlabel("Energy (eV)", fontsize=12)
    axs[0, 1].set_ylabel("Intensity * Jacobian Factor", fontsize=12)
    axs[0, 1].grid(True, linestyle=':', alpha=0.6)
    
    # Plot 3: PL Scaling 
    axs[1, 0].plot(axis_E, raw_E, 'b-', linewidth=2, label='Raw Emission')
    axs[1, 0].plot(axis_E, scaled_bg_E, 'r--', linewidth=2, label=f'Scaled PL Ref (Factor: {scale_factor:.2f})')
    # Fill only the region used for fitting calculation based on the mask
    axs[1, 0].fill_between(axis_E, 0, np.max(raw_E), where=fit_mask, color='gray', alpha=0.1, label='Region Used for Scaling')
    
    axs[1, 0].set_title("3. PL Scaling Matching (Energy Domain)", fontsize=14, pad=10)
    axs[1, 0].set_xlabel("Energy (eV)", fontsize=12)
    axs[1, 0].legend(fontsize=10)
    axs[1, 0].grid(True, linestyle=':', alpha=0.6)
    
    # Plot 4: Subtracted Pure ASE
    axs[1, 1].plot(wave_sorted, pure_nm[plot_idx_nm], 'g-', linewidth=2, label='Pure ASE/Lasing')
    axs[1, 1].plot(wave_sorted, raw_nm[plot_idx_nm], 'k-', linewidth=1, alpha=0.3, label='Original Raw')
    axs[1, 1].set_title("4. Isolated ASE/Lasing (Reverted to nm Domain)", fontsize=14, pad=10)
    axs[1, 1].set_xlabel("Wavelength (nm)", fontsize=12)
    axs[1, 1].legend(fontsize=10)
    axs[1, 1].grid(True, linestyle=':', alpha=0.6)
    
    plt.savefig(save_path, bbox_inches='tight')
    plt.close(fig) # Close figure to free memory
    print(f"Saved: {os.path.basename(save_path)}")

# ==========================================
# Main Processing & Visualization
# ==========================================
def process_and_visualize():
    print(f"Reading data from:\n{TARGET_FOLDER}")
    
    # Read files
    efiles = find_files_fuzzy(TARGET_FOLDER, ["energy"])
    if not efiles: return
    df_en = pd.read_csv(efiles[0], header=None)
    raw_en = df_en.iloc[:, 1].values
    
    spec_files = find_files_fuzzy(TARGET_FOLDER, ["spec"])
    spec_files = [f for f in spec_files if "extract" not in f and "process" not in f]
    if not spec_files: return
        
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

    n_pts = min(len(raw_en), int_matrix.shape[0])
    int_matrix = int_matrix[:n_pts]

    # Auto-detect background frames to skip
    first_valid_idx = n_pts
    for idx in range(n_pts):
        row_data = int_matrix[idx]
        if np.isnan(row_data).all(): continue
        robust_peak = np.nanpercentile(row_data, 99)
        if robust_peak >= 10:
            first_valid_idx = idx
            break
            
    if first_valid_idx == n_pts:
        print("Error: Signal too weak.")
        return
        
    int_matrix = int_matrix[first_valid_idx:]
    raw_en = raw_en[first_valid_idx:] 
    effective_bg_skip = 0

    # Domain Conversion
    hc = 1239.84193
    axis_E_unsorted = hc / full_wave
    jacobian_factor = hc / (axis_E_unsorted ** 2)
    int_matrix_E_unsorted = int_matrix * jacobian_factor
    
    sort_idx = np.argsort(axis_E_unsorted)
    axis_E = axis_E_unsorted[sort_idx]
    int_matrix_E = int_matrix_E_unsorted[:, sort_idx]
    
    pl_ref_E = get_pl_reference_shape(int_matrix_E, effective_bg_skip)
    
    limit_E_min = hc / CONFIG['w_max']
    limit_E_max = hc / CONFIG['w_min']

    # --- IDENTIFY THE TWO TARGET FRAMES ---
    high_energy_idx = np.argmax(raw_en)
    # Ensure there are at least 5 valid frames, otherwise pick the lowest available
    low_energy_idx = np.argsort(raw_en)[min(4, len(raw_en)-1)] 
    
    unsort_idx = np.argsort(sort_idx) 

    print("\nProcessing 4 plotting scenarios...")

    # ==========================================
    # Scenario 1: High Energy, Wing Matching
    # ==========================================
    raw_nm_H = int_matrix[high_energy_idx]
    raw_E_H = int_matrix_E[high_energy_idx]
    pure_E_H_wing, scaled_bg_E_H_wing, scale_H_wing, mask_H_wing = strip_pl_background_details(
        axis_E, raw_E_H, pl_ref_E, limit_E_min, limit_E_max, full_spectrum=False)
    
    pure_nm_H_wing = (pure_E_H_wing[unsort_idx] / jacobian_factor).clip(min=0)
    
    create_and_save_plot(
        full_wave, axis_E, raw_nm_H, raw_E_H, scaled_bg_E_H_wing, pure_nm_H_wing, scale_H_wing, mask_H_wing,
        os.path.join(TARGET_FOLDER, "1_HighEnergy_WingMatch.png"), "High Energy"
    )

    # ==========================================
    # Scenario 2: High Energy, Full Spectrum
    # ==========================================
    pure_E_H_full, scaled_bg_E_H_full, scale_H_full, mask_H_full = strip_pl_background_details(
        axis_E, raw_E_H, pl_ref_E, limit_E_min, limit_E_max, full_spectrum=True)
    
    pure_nm_H_full = (pure_E_H_full[unsort_idx] / jacobian_factor).clip(min=0)
    
    create_and_save_plot(
        full_wave, axis_E, raw_nm_H, raw_E_H, scaled_bg_E_H_full, pure_nm_H_full, scale_H_full, mask_H_full,
        os.path.join(TARGET_FOLDER, "2_HighEnergy_FullMatch.png"), "High Energy (Full Spec Fit)"
    )

    # ==========================================
    # Scenario 3: Low Energy, Wing Matching
    # ==========================================
    raw_nm_L = int_matrix[low_energy_idx]
    raw_E_L = int_matrix_E[low_energy_idx]
    pure_E_L_wing, scaled_bg_E_L_wing, scale_L_wing, mask_L_wing = strip_pl_background_details(
        axis_E, raw_E_L, pl_ref_E, limit_E_min, limit_E_max, full_spectrum=False)
    
    pure_nm_L_wing = (pure_E_L_wing[unsort_idx] / jacobian_factor).clip(min=0)
    
    create_and_save_plot(
        full_wave, axis_E, raw_nm_L, raw_E_L, scaled_bg_E_L_wing, pure_nm_L_wing, scale_L_wing, mask_L_wing,
        os.path.join(TARGET_FOLDER, "3_LowEnergy_WingMatch.png"), "Low Energy"
    )

    # ==========================================
    # Scenario 4: Low Energy, Full Spectrum
    # ==========================================
    pure_E_L_full, scaled_bg_E_L_full, scale_L_full, mask_L_full = strip_pl_background_details(
        axis_E, raw_E_L, pl_ref_E, limit_E_min, limit_E_max, full_spectrum=True)
    
    pure_nm_L_full = (pure_E_L_full[unsort_idx] / jacobian_factor).clip(min=0)
    
    create_and_save_plot(
        full_wave, axis_E, raw_nm_L, raw_E_L, scaled_bg_E_L_full, pure_nm_L_full, scale_L_full, mask_L_full,
        os.path.join(TARGET_FOLDER, "4_LowEnergy_FullMatch.png"), "Low Energy (Full Spec Fit)"
    )

    print("All tasks completed.")

if __name__ == "__main__":
    process_and_visualize()