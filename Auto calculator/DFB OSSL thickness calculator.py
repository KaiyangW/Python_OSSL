import numpy as np
from scipy.optimize import root
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import json
import os

# Define constants
DEFAULT_N1 = 1.52   # Glass substrate
DEFAULT_N3 = 1      # Air/cover
DEFAULT_GRATING_PERIOD = 355  # nm
DEFAULT_STOPBAND = 2.0 # nm
CONFIG_FILE = os.path.join(r"C:\My files\Programs_codes", "dfb_OSSL_settings.json")

def calculate_cutoff_thickness(wavelength, n2, n1, n3): 
    """Calculate cut-off thickness for TE0 and TE1 modes."""
    try:
        if n2 <= n1 or n1 <= n3:
            raise ValueError("Refractive indices must satisfy n2 > n1 > n3")
        if wavelength <= 0:
            raise ValueError("Wavelength must be positive")
            
        term1 = wavelength / (2 * np.pi * np.sqrt(n2**2 - n1**2))
        sqrt_term = np.sqrt((n1**2 - n3**2)/(n2**2 - n1**2))
        
        term2_TE0 = np.arctan(sqrt_term)
        h_c_TE0 = term1 * term2_TE0
        
        term2_TE1 = np.arctan(sqrt_term) + np.pi
        h_c_TE1 = term1 * term2_TE1
        
        return h_c_TE0, h_c_TE1
    except ValueError as e:
        raise e
    except Exception as e:
        raise ValueError(f"Calculation error: {str(e)}")

def calculate_tm0_cutoff_thickness(wavelength, n2, n1, n3):
    """Calculate cut-off thickness for TM0 mode."""
    try:
        if n2 <= n1 or n1 <= n3:
            raise ValueError("Refractive indices must satisfy n2 > n1 > n3")
        
        kz = 2 * np.pi / wavelength
        nF = n2
        n_max = n1
        n_min = n3
        rho = 1
        m = 0
        
        denominator = kz * np.sqrt(nF**2 - n_max**2)
        term1 = (nF / n_min)**(2*rho)
        sqrt_term = np.sqrt((n_max**2 - n_min**2)/(nF**2 - n_max**2))
        arctan_term = term1 * sqrt_term
        h_c_TM0 = (1 / denominator) * (np.arctan(arctan_term) + np.pi * m)
        
        return h_c_TM0
    except ValueError as e:
        raise e
    except Exception as e:
        raise ValueError(f"TM0 calculation error: {str(e)}")

def equation(neff, d, k0, n1, n2, n3):
    """Implicit equation for waveguide mode solution."""
    # Singularity check to prevent runtime warnings in solver
    if neff >= n2 or neff <= max(n1, n3):
        return 1e10 
        
    try:
        term1 = k0 * d * np.sqrt(n2**2 - neff**2)
        term2 = np.arctan(np.sqrt((neff**2 - n1**2) / (n2**2 - neff**2)))
        term3 = np.arctan(np.sqrt((neff**2 - n3**2) / (n2**2 - neff**2)))
        return term1 - term2 - term3
    except:
        return 1e10

def neff_vs_d(d_values, wavelength, n1, n2, n3):
    """
    Calculate n_eff for a range of thickness values.
    Updated Logic: Strictly starts guessing from the substrate index (n1) upwards.
    This prevents the 'sudden drop' artifact caused by resetting the guess to the
    average index when the solver is below cutoff.
    """
    k0 = 2 * np.pi / (wavelength * 1e-9)
    neff_results = []
    
    # Bound limits for the fundamental mode
    lower_bound = max(n1, n3)
    upper_bound = n2
    
    # Pre-calculate cutoff to avoid solving in the forbidden region
    try:
        cutoff_te0, _ = calculate_cutoff_thickness(wavelength, n2, n1, n3)
    except:
        cutoff_te0 = 0

    # Initial guess: Start just above substrate index (TE0 mode starts here)
    current_guess = lower_bound + 1e-4

    for d in d_values:
        # Optimization: Don't run solver if we are below cutoff
        if d < cutoff_te0:
            neff_results.append(np.nan)
            current_guess = lower_bound + 1e-4 # Reset guess for when we cross cutoff
            continue

        d_meters = d * 1e-9
        
        # Helper to check if a solution is physically valid
        def is_valid(n):
            return lower_bound < n < upper_bound

        try:
            # Use previous guess (continuity)
            result = root(equation, current_guess, args=(d_meters, k0, n1, n2, n3))
            
            if result.success and is_valid(result.x[0]):
                val = result.x[0]
                neff_results.append(val)
                current_guess = val # Follow the curve up
            else:
                # If solver fails, append NaN
                neff_results.append(np.nan)
                # Important: Do not reset guess to average (1.6). 
                # Reset to lower bound so we catch the mode again if it reappears.
                current_guess = lower_bound + 1e-4
                    
        except Exception:
            neff_results.append(np.nan)
            current_guess = lower_bound + 1e-4
    
    return np.array(neff_results)

def find_optimal_thickness_with_stopband(wavelength, n2, n1, n3, grating_period, stopband_width):
    """
    Find optimal thicknesses considering the stopband shift.
    """
    # CASE 1: Lasing at Long Edge (Red edge) -> Shift Bragg center BLUE
    target_lambda_bragg_blue_shift = wavelength - (stopband_width / 2)
    n_eff_target_long_lasing = target_lambda_bragg_blue_shift / grating_period
    
    # CASE 2: Lasing at Short Edge (Blue edge) -> Shift Bragg center RED
    target_lambda_bragg_red_shift = wavelength + (stopband_width / 2)
    n_eff_target_short_lasing = target_lambda_bragg_red_shift / grating_period

    n_eff_center = wavelength / grating_period

    # Get cutoff thicknesses
    d_TE0, d_TE1 = calculate_cutoff_thickness(wavelength, n2, n1, n3)
    d_TM0 = calculate_tm0_cutoff_thickness(wavelength, n2, n1, n3)
    
    # Range setup
    d_min = max(1, d_TE0 * 0.25)
    d_max = max(d_TM0, d_TE1) * 2.0
    d_values = np.linspace(d_min, d_max, 500)
    
    # Calculate n_eff curve
    neff_values = neff_vs_d(d_values, wavelength, n1, n2, n3)

    # Find solutions
    mask = (d_values >= d_TE0) & (~np.isnan(neff_values))
    
    if not np.any(mask):
        return {
            'd_long_edge': np.nan, 'n_eff_long': 0, 'target_n_long': n_eff_target_long_lasing,
            'd_short_edge': np.nan, 'n_eff_short': 0, 'target_n_short': n_eff_target_short_lasing,
            'n_eff_center': n_eff_center,
            'd_values': d_values, 'neff_values': neff_values,
            'd_TE0': d_TE0, 'd_TE1': d_TE1, 'd_TM0': d_TM0
        }

    # Helper to find intersection
    def get_intersection(target_n):
        diff = np.abs(neff_values[mask] - target_n)
        best_idx = np.argmin(diff)
        actual_indices = np.where(mask)[0]
        best_actual_idx = actual_indices[best_idx]
        
        if diff[best_idx] > 0.05: # Threshold for "no intersection found"
            return np.nan, np.nan
            
        return d_values[best_actual_idx], neff_values[best_actual_idx]

    # Calculate for Long Edge Lasing 
    d_opt_long, neff_opt_long = get_intersection(n_eff_target_long_lasing)
    
    # Calculate for Short Edge Lasing
    d_opt_short, neff_opt_short = get_intersection(n_eff_target_short_lasing)
    
    return {
        'd_long_edge': d_opt_long, 'n_eff_long': neff_opt_long, 'target_n_long': n_eff_target_long_lasing,
        'd_short_edge': d_opt_short, 'n_eff_short': neff_opt_short, 'target_n_short': n_eff_target_short_lasing,
        'n_eff_center': n_eff_center,
        'd_values': d_values, 'neff_values': neff_values,
        'd_TE0': d_TE0, 'd_TE1': d_TE1, 'd_TM0': d_TM0
    }

def calculate_new_rpm(current_thickness, current_rpm, desired_thickness):
    if current_thickness <= 0 or current_rpm <= 0 or desired_thickness <= 0:
        raise ValueError("All inputs must be positive.")
    return round(current_rpm * (current_thickness / desired_thickness) ** 2, 2)

class CutoffCalculatorGUI:
    def __init__(self):
        ctk.set_appearance_mode("Light")
        self.root = ctk.CTk()
        self.root.title("DFB Organic Laser Calculator")
        
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = int(screen_width * 0.9)
        window_height = int(screen_height * 0.9)
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        
        self.n1_value = DEFAULT_N1
        self.n3_value = DEFAULT_N3
        self.grating_period = DEFAULT_GRATING_PERIOD
        self.stopband_val = DEFAULT_STOPBAND
        
        self.create_widgets()
        self.load_settings()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def save_settings(self):
        # Gather current input values into a dictionary
        settings = {
            "wavelength": self.wavelength_entry.get(),
            "n2": self.n2_entry.get(),
            "stopband": self.stopband_entry.get(),
            "current_thickness": self.current_thickness_entry.get(),
            "current_rpm": self.current_rpm_entry.get(),
            "n3": self.n3_entry.get(),
            "grating": self.grating_entry.get()
        }
        
        # Write the dictionary to a JSON file
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Could not save settings: {e}")

    def load_settings(self):
        # Check if the settings file exists before trying to read it
        if not os.path.exists(CONFIG_FILE):
            return

        # Read the JSON file
        try:
            with open(CONFIG_FILE, "r") as f:
                settings = json.load(f)

            # Helper function to safely update an entry widget
            def update_entry(entry_widget, key):
                if key in settings and settings[key]:
                    entry_widget.delete(0, "end")
                    entry_widget.insert(0, str(settings[key]))

            # Update all fields with saved data
            update_entry(self.wavelength_entry, "wavelength")
            update_entry(self.n2_entry, "n2")
            update_entry(self.stopband_entry, "stopband")
            update_entry(self.current_thickness_entry, "current_thickness")
            update_entry(self.current_rpm_entry, "current_rpm")
            update_entry(self.n3_entry, "n3")
            update_entry(self.grating_entry, "grating")
            
        except Exception as e:
            print(f"Could not load settings: {e}")

    def on_closing(self):
        # Save settings when the application window is closed
        self.save_settings()
        self.root.destroy()
    
    def create_widgets(self):
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(padx=20, pady=20, fill='both', expand=True)
        
        title_label = ctk.CTkLabel(main_frame, text="DFB Laser Thickness Calculator (Stopband Corrected)", font=("Microsoft YaHei UI", 24, "bold"))
        title_label.pack(pady=10)
        
        grid_frame = ctk.CTkFrame(main_frame)
        grid_frame.pack(padx=10, pady=10, fill='both', expand=True)
        
        grid_frame.grid_columnconfigure(0, weight=1)
        grid_frame.grid_columnconfigure(1, weight=1)
        grid_frame.grid_columnconfigure(2, weight=4)
        grid_frame.grid_rowconfigure(0, weight=1)
        grid_frame.grid_rowconfigure(1, weight=2)
        
        # --- INPUTS ---
        input_frame = ctk.CTkFrame(grid_frame)
        input_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        
        ctk.CTkLabel(input_frame, text="Laser Parameters", font=("Microsoft YaHei UI", 20, "bold")).pack(pady=5)
        
        ctk.CTkLabel(input_frame, text="Target Wavelength (ASE Peak) [nm]:", font=("Microsoft YaHei UI", 18)).pack(pady=2)
        self.wavelength_entry = ctk.CTkEntry(input_frame, font=("Microsoft YaHei UI", 18))
        self.wavelength_entry.pack(pady=2, padx=10, fill='x')
        
        ctk.CTkLabel(input_frame, text="n2 (Organic Film):", font=("Microsoft YaHei UI", 18)).pack(pady=2)
        self.n2_entry = ctk.CTkEntry(input_frame, font=("Microsoft YaHei UI", 18))
        self.n2_entry.pack(pady=2, padx=10, fill='x')
        
        ctk.CTkLabel(input_frame, text="Stopband Width [nm]:", font=("Microsoft YaHei UI", 18), text_color="#A93226").pack(pady=2)
        self.stopband_entry = ctk.CTkEntry(input_frame, font=("Microsoft YaHei UI", 18))
        self.stopband_entry.insert(0, str(self.stopband_val))
        self.stopband_entry.pack(pady=2, padx=10, fill='x')
        
        # --- SPIN COATING ---
        spin_frame = ctk.CTkFrame(grid_frame)
        spin_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        
        ctk.CTkLabel(spin_frame, text="Process Calibration", font=("Microsoft YaHei UI", 20, "bold")).pack(pady=5)
        ctk.CTkLabel(spin_frame, text="Ref. Thickness [nm]:", font=("Microsoft YaHei UI", 16)).pack(pady=2)
        self.current_thickness_entry = ctk.CTkEntry(spin_frame, font=("Microsoft YaHei UI", 16))
        self.current_thickness_entry.pack(pady=2, padx=10, fill='x')
        ctk.CTkLabel(spin_frame, text="Ref. Speed [RPM]:", font=("Microsoft YaHei UI", 16)).pack(pady=2)
        self.current_rpm_entry = ctk.CTkEntry(spin_frame, font=("Microsoft YaHei UI", 16))
        self.current_rpm_entry.pack(pady=2, padx=10, fill='x')
        
        # --- CONSTANTS & RESULTS ---
        bottom_frame = ctk.CTkFrame(grid_frame)
        bottom_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        
        const_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        const_frame.pack(pady=5, fill='x')
        
        ctk.CTkLabel(const_frame, text=f"n1={self.n1_value} | ", font=("Microsoft YaHei UI", 14)).pack(side='left', padx=5)
        ctk.CTkLabel(const_frame, text="n3=", font=("Microsoft YaHei UI", 14)).pack(side='left')
        self.n3_entry = ctk.CTkEntry(const_frame, width=60)
        self.n3_entry.insert(0, str(self.n3_value))
        self.n3_entry.pack(side='left', padx=2)
        
        ctk.CTkLabel(const_frame, text="| Period[nm]=", font=("Microsoft YaHei UI", 14)).pack(side='left')
        self.grating_entry = ctk.CTkEntry(const_frame, width=60)
        self.grating_entry.insert(0, str(self.grating_period))
        self.grating_entry.pack(side='left', padx=2)
        
        self.calc_button = ctk.CTkButton(bottom_frame, text="CALCULATE", font=("Microsoft YaHei UI", 18, "bold"), 
                                        height=40, command=self.calculate)
        self.calc_button.pack(pady=10, padx=20, fill='x')
        
        self.results_text = ctk.CTkTextbox(bottom_frame, font=("Microsoft YaHei UI", 18), height=200)
        self.results_text.pack(pady=5, padx=10, fill='both', expand=True)
        
        try:
            self.results_text.tag_config("error_style", foreground="red", font=("Microsoft YaHei UI", 18, "bold"))
        except:
            pass 

        graph_frame = ctk.CTkFrame(grid_frame)
        graph_frame.grid(row=0, column=2, rowspan=2, padx=10, pady=10, sticky="nsew")
        
        self.fig = Figure(figsize=(5, 4), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=graph_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=10)
    
    def update_graph(self, res_dict, wavelength, stopband_width):
        self.fig.clf()
        self.ax = self.fig.add_subplot(111)
        
        d_values = res_dict['d_values']
        neff_values = res_dict['neff_values']
        valid = ~np.isnan(neff_values)
        
        self.ax.plot(d_values[valid], neff_values[valid], 'k-', linewidth=2, label='Dispersion (TE0)')
        
        n1 = self.n1_value
        n2 = float(self.n2_entry.get())
        
        grating = float(self.grating_entry.get())
        neff_upper_bound = (wavelength + stopband_width/2) / grating
        neff_lower_bound = (wavelength - stopband_width/2) / grating
        neff_center = wavelength / grating
        
        self.ax.axhspan(neff_lower_bound, neff_upper_bound, color='red', alpha=0.15, label='Stopband @ ASE')
        self.ax.axhline(neff_center, color='red', linestyle='--', alpha=0.5)
        
        if not np.isnan(res_dict['d_long_edge']):
            self.ax.plot(res_dict['d_long_edge'], res_dict['n_eff_long'], 'bo', markersize=12, 
                         label='Long-Edge Mode Match (Recommended)')
        
        if not np.isnan(res_dict['d_short_edge']):
            self.ax.plot(res_dict['d_short_edge'], res_dict['n_eff_short'], 'go', markersize=12, 
                         label='Short-Edge Mode Match')

        self.ax.tick_params(axis='both', which='major', labelsize=22)
        self.ax.set_ylim(n1 - 0.005, n2 + 0.005)
        self.ax.set_xlabel('Thickness (nm)', fontsize=24)
        self.ax.set_ylabel('Effective Index ($n_{eff}$)', fontsize=24)
        self.ax.set_title(f'Design for $\lambda_{{ASE}}={wavelength}nm$ (SB={stopband_width}nm)', fontsize=24)
        
        self.ax.legend(loc='lower right', fontsize=22)
        self.ax.grid(True, linestyle='--', alpha=0.6)
        
        self.fig.tight_layout()
        self.canvas.draw()
    
    def calculate(self):
        try:
            wl = float(self.wavelength_entry.get())
            n2 = float(self.n2_entry.get())
            n3 = float(self.n3_entry.get())
            gp = float(self.grating_entry.get())
            sb = float(self.stopband_entry.get())
            
            res = find_optimal_thickness_with_stopband(wl, n2, self.n1_value, n3, gp, sb)
            
            # Clear text
            self.results_text.configure(state="normal")
            self.results_text.delete("1.0", "end")
            
            # Helper for RPM
            def get_rpm_str(target_d):
                try:
                    c_thick = float(self.current_thickness_entry.get())
                    c_rpm = float(self.current_rpm_entry.get())
                    return f"{calculate_new_rpm(c_thick, c_rpm, target_d)} RPM"
                except:
                    return "(Enter calibration data)"

            # Print Header
            header = f"=== RESULTS FOR {wl} nm ASE PEAK ===\n"
            header += f"Stopband Width: {sb} nm\n\n"
            self.results_text.insert("end", header)
            
            # --- OPTION 1 ---
            self.results_text.insert("end", "LASING AT LONG-WAVELENGTH EDGE (Typical for Organics)\nStrategy: Shift Bragg center to Blue side\n")
            
            target_neff_long = res['target_n_long']
            
            if target_neff_long <= self.n1_value:
                msg = f"-> Target Neff: {target_neff_long:.4f} (<= Substrate n1={self.n1_value})\n"
                self.results_text.insert("end", msg)
                self.results_text.insert("end", "[ERROR: Target Neff < n_substrate! Mode is leaky/cutoff.]\n", "error_style")
            
            elif not np.isnan(res['d_long_edge']):
                msg = f"-> TARGET THICKNESS: {res['d_long_edge']:.2f} nm\n"
                msg += f"   (Bragg Center: {(res['n_eff_long']*gp):.2f} nm)\n"
                msg += f"   -> Required Speed: {get_rpm_str(res['d_long_edge'])}\n"
                self.results_text.insert("end", msg)
            else:
                self.results_text.insert("end", "-> NO SOLUTION FOUND (Thickness out of range or solver failed)\n", "error_style")

            self.results_text.insert("end", "\n----------------------------------------")

            # Cutoffs
            self.results_text.insert("end", f"\n=== CUT-OFFS ===\nTE0: {res['d_TE0']:.1f} nm | TE1: {res['d_TE1']:.1f} nm")
            
            self.results_text.configure(state="disabled")
            self.update_graph(res, wl, sb)
            
        except ValueError as e:
            messagebox.showerror("Input Error", str(e))
        except Exception as e:
            messagebox.showerror("Error", f"An error occurred: {str(e)}")
    
    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = CutoffCalculatorGUI()
    app.run()