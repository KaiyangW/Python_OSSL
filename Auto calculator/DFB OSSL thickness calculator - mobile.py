import numpy as np
from scipy.optimize import root

# ==========================================
#  1. Physics Core Functions
# ==========================================

def calculate_cutoff_thickness(wavelength, n2, n1, n3):
    try:
        term1 = wavelength / (2 * np.pi * np.sqrt(n2**2 - n1**2))
        sqrt_term = np.sqrt((n1**2 - n3**2)/(n2**2 - n1**2))
        h_c_TE0 = term1 * np.arctan(sqrt_term)
        h_c_TE1 = term1 * (np.arctan(sqrt_term) + np.pi)
        return h_c_TE0, h_c_TE1
    except: 
        return 0, 0

def calculate_tm0_cutoff_thickness(wavelength, n2, n1, n3):
    try:
        kz = 2 * np.pi / wavelength
        nF, n_max, n_min = n2, n1, n3
        denominator = kz * np.sqrt(nF**2 - n_max**2)
        term1 = (nF / n_min)**2
        sqrt_term = np.sqrt((n_max**2 - n_min**2)/(nF**2 - n_max**2))
        return (1 / denominator) * np.arctan(term1 * sqrt_term)
    except: 
        return 0

def equation(neff, d, k0, n1, n2, n3):
    try:
        term1 = k0 * d * np.sqrt(n2**2 - neff**2)
        term2 = np.arctan(np.sqrt((neff**2 - n1**2) / (n2**2 - neff**2)))
        term3 = np.arctan(np.sqrt((neff**2 - n3**2) / (n2**2 - neff**2)))
        return term1 - term2 - term3
    except: 
        return 1e10

def neff_vs_d(d_values, wavelength, n1, n2, n3):
    k0 = 2 * np.pi / (wavelength * 1e-9)
    neff_results = []
    neff_guess = (max(n1, n3) + n2) / 2
    for d in d_values:
        d_meters = d * 1e-9
        try:
            res = root(equation, neff_guess, args=(d_meters, k0, n1, n2, n3))
            if res.success:
                neff_results.append(res.x[0])
                neff_guess = res.x[0]
            else:
                neff_results.append(np.nan)
        except: 
            neff_results.append(np.nan)
    return np.array(neff_results)

def find_optimal_thickness_stopband(wavelength, n2, n1, n3, grating_period, stopband):
    # Target Bragg condition shifted to BLUE side so Long-Edge matches ASE
    target_lambda_bragg = wavelength - (stopband / 2.0)
    n_eff_target = target_lambda_bragg / grating_period

    d_TE0, d_TE1 = calculate_cutoff_thickness(wavelength, n2, n1, n3)
    d_TM0 = calculate_tm0_cutoff_thickness(wavelength, n2, n1, n3)

    d_min = max(1, d_TE0 * 0.5)
    d_max = max(d_TM0, d_TE1) * 1.5
    d_values = np.linspace(d_min, d_max, 500)
    neff_values = neff_vs_d(d_values, wavelength, n1, n2, n3)

    mask = (d_values >= d_TE0) & (d_values <= d_TE1) & (~np.isnan(neff_values))
    if not np.any(mask): 
        raise ValueError("No Mode Found")
    
    idx = np.argmin(np.abs(neff_values[mask] - n_eff_target))
    actual_idx = np.where(mask)[0][idx]
    
    return {
        'd_optimal': d_values[actual_idx],
        'n_eff_achieved': neff_values[actual_idx],
        'lasing_wavelength': neff_values[actual_idx] * grating_period + stopband/2,
        'd_TE0': d_TE0, 
        'd_TE1': d_TE1, 
        'd_TM0': d_TM0
    }

def calculate_rpm(ref_thick, ref_rpm, target_thick):
    if ref_thick <= 0 or ref_rpm <= 0: 
        return 0
    return ref_rpm * (ref_thick / target_thick)**2

# ==========================================
#  2. CLI Interface & Helper Functions
# ==========================================

def get_input(prompt, default_value):
    """
    Handles user input, allowing them to press Enter to use the default value.
    """
    user_input = input(f"{prompt} [{default_value}]: ").strip()
    if user_input.lower() == 'q':
        return 'q'
    
    # If the user just pressed Enter, return the default value
    if user_input == "":
        return default_value
        
    try:
        return float(user_input)
    except ValueError:
        print("[!] Invalid input. Using default value instead.")
        return default_value

def main():
    print("\n--- DFB OSL Thickness & Spin Speed Calc ---")
    print("按enter使用默认值 [ ].")
    print("按q退出.\n")
    
    # Define fixed substrate and superstrate indices (Glass/Air)
    n1 = 1.52
    n3 = 1.0

    # Initial default parameters
    def_wl = 530.0
    def_n2 = 1.75
    def_gp = 350.0
    def_sb = 4.0

    while True:
        print("-" * 30)
        
        # 1. Get Laser Parameters
        wl = get_input("DFB Wl (nm)", def_wl)
        if wl == 'q': break
        def_wl = wl  # Update default for the next loop iteration
        
        n2 = get_input("n_org (n2)", def_n2)
        if n2 == 'q': break
        def_n2 = n2
        
        gp = get_input("Gra_Per (nm)", def_gp)
        if gp == 'q': break
        def_gp = gp
        
        sb = get_input("Stopband (nm)", def_sb)
        if sb == 'q': break
        def_sb = sb

        # Run primary calculation
        try:
            res = find_optimal_thickness_stopband(wl, n2, n1, n3, gp, sb)
            opt_d = res['d_optimal']
            
            print("\n==============================")
            print(f"目标厚度: {opt_d:.1f} nm")
            print("==============================\n")
            print(f"激光波长: {res['lasing_wavelength']:.1f} nm")
            print(f"TE0最小厚度: {res['d_TE0']:.0f} nm")
            print(f"TE1最小厚度: {res['d_TE1']:.0f} nm")
            print("\n------------------------------")
            
        except Exception as e:
            print(f"\n[!] Calculation Error: {e}\n")
            continue

        # 2. Optional Calibration (Spin Speed)
        print("可选：旋涂转速（enter跳过）")
        ref_d = get_input("当下厚度 (nm)", 0.0)
        if ref_d == 'q': break
        
        if ref_d > 0:
            ref_rpm = get_input("当下转速 (RPM)", 0.0)
            if ref_rpm == 'q': break
            
            new_rpm = calculate_rpm(ref_d, ref_rpm, opt_d)
            print("\n==============================")
            print(f"目标转速: {int(new_rpm)} RPM")
            print("==============================\n")
        else:
            print("\nCalibration skipped.\n")

if __name__ == "__main__":
    main()