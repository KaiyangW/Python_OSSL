import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import ctypes
import sys
import threading
import time
import csv

# --- IMPORTS ---
import Bentham_mono_control_emb 
from Bentham_mono_control_emb import MonoDriver
from Read_ocean_optics_emb import OceanDriver

# --- HIGH DPI SETTING (Windows) ---
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1) 
except Exception:
    pass 

class CalibrationApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Laser Lab Calibration System (Dual Factor)")
        self.root.geometry("1200x850") 

        self.DETECT_MIN_WL = 400
        self.DETECT_MAX_WL = 900
        
        # [修改 1] 初始化显示双 Factor
        print(f"[UI Init] Factor UP: {Bentham_mono_control_emb.CALIBRATION_FACTOR_UP}")
        print(f"[UI Init] Factor DOWN: {Bentham_mono_control_emb.CALIBRATION_FACTOR_DOWN}")
        
        # 初始化光谱仪
        self.mono = MonoDriver() 
        self.ocean = OceanDriver(integration_time_ms=30)
        self.ocean_connected = self.ocean.connect()
        
        if self.ocean_connected:
            self.ocean.start_acquisition()
            time.sleep(0.2) 
            self.auto_calibrate_startup()
        else:
            print("Spectrometer not found. Showing dummy data.")

        # --- GUI LAYOUT ---
        self.main_pane = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        self.main_pane.pack(fill=tk.BOTH, expand=True)

        self.left_frame = ttk.Frame(self.main_pane, padding=20)
        self.main_pane.add(self.left_frame, weight=1) 
        self.build_left_panel()

        self.right_frame = ttk.Frame(self.main_pane, padding=10)
        self.main_pane.add(self.right_frame, weight=4) 
        self.build_right_panel()

        self.is_paused = False
        self.is_scanning = False 
        self.update_plot_loop()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def build_left_panel(self):
        # Header
        lbl_title = ttk.Label(self.left_frame, text="Monochromator", font=("Segoe UI", 16, "bold"))
        lbl_title.pack(pady=(0, 10))

        # Status Display
        self.lbl_current_pos = ttk.Label(self.left_frame, text=f"Current: {self.mono.current_wavelength} nm", 
                                         font=("Segoe UI", 14), foreground="blue")
        self.lbl_current_pos.pack(pady=5)

        # --- [修改 2] DUAL MOTOR CALIBRATION TUNING ---
        cal_factor_frame = ttk.LabelFrame(self.left_frame, text="Calibration Factors (Steps/nm)", padding=10)
        cal_factor_frame.pack(fill='x', pady=10)
        
        # Row 1: UP Factor
        f_up = ttk.Frame(cal_factor_frame)
        f_up.pack(fill='x', pady=2)
        ttk.Label(f_up, text="UP (>):", width=8).pack(side=tk.LEFT)
        self.ent_factor_up = ttk.Entry(f_up, width=10)
        self.ent_factor_up.pack(side=tk.LEFT, padx=5)
        self.ent_factor_up.insert(0, str(Bentham_mono_control_emb.CALIBRATION_FACTOR_UP))

        # Row 2: DOWN Factor
        f_down = ttk.Frame(cal_factor_frame)
        f_down.pack(fill='x', pady=2)
        ttk.Label(f_down, text="DOWN (<):", width=8).pack(side=tk.LEFT)
        self.ent_factor_down = ttk.Entry(f_down, width=10)
        self.ent_factor_down.pack(side=tk.LEFT, padx=5)
        self.ent_factor_down.insert(0, str(Bentham_mono_control_emb.CALIBRATION_FACTOR_DOWN))

        # Update Button
        btn_update_factor = ttk.Button(cal_factor_frame, text="Update Both Factors", command=self.update_calibration_factor)
        btn_update_factor.pack(fill='x', pady=(5, 0))
        # ----------------------------------------

        # --- MANUAL CALIBRATION / FORCE SET ---
        cal_frame = ttk.LabelFrame(self.left_frame, text="Force Position (Re-Zero)", padding=10)
        cal_frame.pack(fill='x', pady=10)

        f_manual = ttk.Frame(cal_frame)
        f_manual.pack(fill='x', pady=5)
        self.ent_force_wl = ttk.Entry(f_manual, width=10)
        self.ent_force_wl.pack(side=tk.LEFT, padx=(0, 5))
        btn_force = ttk.Button(f_manual, text="Set", width=5, command=self.manual_force_set)
        btn_force.pack(side=tk.LEFT)

        btn_set_peak = ttk.Button(cal_frame, text="Set to Current Peak", command=self.auto_calibrate_startup)
        btn_set_peak.pack(fill='x', pady=5)

        ttk.Separator(self.left_frame, orient='horizontal').pack(fill='x', pady=15)

        # Move Controls
        ttk.Label(self.left_frame, text="Target Wavelength (nm):").pack(anchor='w')
        self.entry_target = ttk.Entry(self.left_frame, font=("Segoe UI", 12))
        self.entry_target.pack(fill='x', pady=5)
        self.entry_target.bind('<Return>', lambda event: self.start_move())

        self.btn_move = ttk.Button(self.left_frame, text="MOVE MOTOR", command=self.start_move)
        self.btn_move.pack(fill='x', pady=10, ipady=5)

        ttk.Separator(self.left_frame, orient='horizontal').pack(fill='x', pady=15)

        # --- AUTO SCAN SECTION ---
        ttk.Label(self.left_frame, text="Auto Calibration Scan", font=("Segoe UI", 12, "bold")).pack(pady=(10, 5))
        
        f_scan = ttk.Frame(self.left_frame)
        f_scan.pack(fill='x')
        
        ttk.Label(f_scan, text="Start:").grid(row=0, column=0, padx=5)
        self.ent_scan_start = ttk.Entry(f_scan, width=6)
        self.ent_scan_start.grid(row=0, column=1)
        self.ent_scan_start.insert(0, "400")

        ttk.Label(f_scan, text="End:").grid(row=0, column=2, padx=5)
        self.ent_scan_end = ttk.Entry(f_scan, width=6)
        self.ent_scan_end.grid(row=0, column=3)
        self.ent_scan_end.insert(0, "800")

        ttk.Label(f_scan, text="Step:").grid(row=1, column=0, padx=5, pady=5)
        self.ent_scan_step = ttk.Entry(f_scan, width=6)
        self.ent_scan_step.grid(row=1, column=1, pady=5)
        self.ent_scan_step.insert(0, "10")

        self.btn_scan = ttk.Button(self.left_frame, text="START AUTO SCAN", command=self.start_auto_scan)
        self.btn_scan.pack(fill='x', pady=10)

        # Status Log
        self.lbl_status = ttk.Label(self.left_frame, text="Status: Idle", wraplength=200)
        self.lbl_status.pack(pady=10)

    def build_right_panel(self):
        self.fig, self.ax = plt.subplots(figsize=(5, 4), dpi=100)
        self.ax.set_title("Real-Time Spectrum")
        self.ax.set_xlabel("Wavelength (nm)")
        self.ax.set_ylabel("Intensity")
        self.ax.grid(True, linestyle='--', alpha=0.5)
        
        self.line, = self.ax.plot([], [], 'b-', linewidth=1, label='Spectrum')
        self.peak_dot, = self.ax.plot([], [], 'ro', markersize=5, label='Peak')
        self.peak_text = self.ax.text(0.95, 0.95, "", transform=self.ax.transAxes, 
                                      color='red', fontsize=12, fontweight='bold', 
                                      horizontalalignment='right', verticalalignment='top')
        self.cursor_text = self.ax.text(0.02, 0.95, "Hover mouse...", transform=self.ax.transAxes, 
                                        color='green', fontsize=10, verticalalignment='top')

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.right_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)

        btn_bar = ttk.Frame(self.right_frame)
        btn_bar.pack(fill='x', pady=10)
        self.btn_pause = ttk.Button(btn_bar, text="PAUSE", command=self.toggle_pause)
        self.btn_pause.pack(side=tk.LEFT, padx=5)
        self.btn_save = ttk.Button(btn_bar, text="SAVE SNAPSHOT", command=self.save_data)
        self.btn_save.pack(side=tk.LEFT, padx=5)

    # --- [修改 3] 更新两个 Factor ---
    def update_calibration_factor(self):
        try:
            val_up = float(self.ent_factor_up.get())
            val_down = float(self.ent_factor_down.get())
            
            if val_up <= 0 or val_down <= 0:
                messagebox.showerror("Error", "Factors must be positive.")
                return

            # 直接修改导入的模块变量
            Bentham_mono_control_emb.CALIBRATION_FACTOR_UP = val_up
            Bentham_mono_control_emb.CALIBRATION_FACTOR_DOWN = val_down
            
            msg = f"Factors Updated.\nUP: {val_up}\nDOWN: {val_down}"
            print(f"[UI] {msg}")
            self.lbl_status.config(text="Factors Updated.")
            messagebox.showinfo("Updated", msg)
            
        except ValueError:
            messagebox.showerror("Error", "Invalid Number provided.")

    def auto_calibrate_startup(self):
        if not self.ocean_connected: return
        try:
            wl, inten = self.ocean.get_data()
            if len(wl) > 0 and len(inten) > 0:
                mask = (wl >= self.DETECT_MIN_WL) & (wl <= self.DETECT_MAX_WL)
                
                if np.any(mask):
                    valid_wl = wl[mask]
                    valid_inten = inten[mask]
                else:
                    valid_wl = wl
                    valid_inten = inten

                max_idx = np.argmax(valid_inten)
                peak_wl = valid_wl[max_idx]
                peak_int = valid_inten[max_idx]

                if peak_int < 10: 
                    print("Signal too low for auto-calibration.")
                    return
                self.mono.current_wavelength = peak_wl
                self.root.after(0, lambda: self.lbl_current_pos.config(
                    text=f"Current: {self.mono.current_wavelength:.2f} nm"
                ))
                print(f"System calibrated to peak: {peak_wl:.2f} nm")
        except Exception as e:
            print(f"Auto-calibration failed: {e}")

    def manual_force_set(self):
        try:
            val = float(self.ent_force_wl.get())
            self.mono.current_wavelength = val
            self.lbl_current_pos.config(text=f"Current: {self.mono.current_wavelength} nm")
            self.lbl_status.config(text=f"Manually set to {val} nm")
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid number.")

    def on_mouse_move(self, event):
        if event.inaxes == self.ax:
            self.cursor_text.set_text(f"Cursor: {event.xdata:.1f} nm\nInt: {event.ydata:.0f}")
            self.canvas.draw_idle()

    def start_move(self):
        try:
            target = float(self.entry_target.get())
            self.disable_controls()
            
            # DEBUG
            print(f"[Move] Target: {target}. UP: {Bentham_mono_control_emb.CALIBRATION_FACTOR_UP}, DOWN: {Bentham_mono_control_emb.CALIBRATION_FACTOR_DOWN}")
            
            self.mono.move_threaded(
                target_nm=target,
                status_callback=lambda msg: self.root.after(0, lambda: self.lbl_status.config(text=msg)),
                finish_callback=self.enable_controls
            )
        except ValueError:
            messagebox.showerror("Error", "Invalid Number")

    def disable_controls(self):
        self.btn_move.config(state="disabled")
        self.btn_scan.config(state="disabled")

    def enable_controls(self):
        self.root.after(0, self._enable_controls_ui)

    def _enable_controls_ui(self):
        self.btn_move.config(state="normal")
        self.btn_scan.config(state="normal")
        self.lbl_current_pos.config(text=f"Current: {self.mono.current_wavelength} nm")
    
    def start_auto_scan(self):
        try:
            start = float(self.ent_scan_start.get())
            end = float(self.ent_scan_end.get())
            step = float(self.ent_scan_step.get())
            
            if start > end and step > 0:
                step = -abs(step)
            elif start < end and step < 0:
                step = abs(step)
                
            epsilon = step * 0.01
            self.scan_points = np.arange(start, end + epsilon, step)
            
            if len(self.scan_points) == 0:
                messagebox.showerror("Error", "Scan range empty")
                return

            print(f"[AutoScan] Plan: {len(self.scan_points)} points.")

            self.scan_filename = filedialog.asksaveasfilename(
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv")],
                title="Save Calibration Data"
            )
            if not self.scan_filename: return

            with open(self.scan_filename, 'w', newline='') as f:
                writer = csv.writer(f)
                # [修改 4] 记录两个 Factor
                writer.writerow(["Metadata", f"Factor_UP={Bentham_mono_control_emb.CALIBRATION_FACTOR_UP}", f"Factor_DOWN={Bentham_mono_control_emb.CALIBRATION_FACTOR_DOWN}"])
                writer.writerow(["Timestamp", "Mono_Wavelength_Set(nm)", "Spectrum_Peak_Observed(nm)", "Peak_Intensity"])

            self.is_scanning = True
            self.disable_controls()
            
            t = threading.Thread(target=self._scan_loop_thread, daemon=True)
            t.start()
            
        except ValueError:
            messagebox.showerror("Error", "Invalid Scan Parameters")

    def _scan_loop_thread(self):
        print("[Thread] Scan loop started.")
        # [修改 5] 移除 backlash 引用
        # backlash_dist = Bentham_mono_control_emb.BACKLASH_OFFSET

        try:
            for i, target_wl in enumerate(self.scan_points):
                if not self.is_scanning: 
                    break

                self.root.after(0, lambda t=target_wl: self.lbl_status.config(text=f"Scanning: Moving to {t:.1f} nm..."))
                
                move_done = threading.Event()
                def on_move_finish(): move_done.set()
                
                time.sleep(0.1) 
                
                # 简单粗暴的超时设置，不再根据 backlash 计算
                timeout_val = 25.0 

                try:
                    self.mono.move_threaded(target_wl, lambda msg: None, on_move_finish)
                except Exception as e:
                    print(f"[Thread] Error: {e}")
                    break

                is_finished = move_done.wait(timeout=timeout_val)
                if not is_finished:
                    print(f"[Thread] Timeout at {target_wl} nm!")

                time.sleep(0.8) 
                
                wl, inten = self.ocean.get_data()
                if len(wl) > 0:
                    mask = (wl >= self.DETECT_MIN_WL) & (wl <= self.DETECT_MAX_WL)
                    if np.any(mask):
                        valid_wl = wl[mask]
                        valid_inten = inten[mask]
                        max_idx = np.argmax(valid_inten)
                        peak_wl = valid_wl[max_idx]
                        peak_int = valid_inten[max_idx]
                    else:
                        peak_wl = 0
                        peak_int = 0
                                        
                    timestamp = time.strftime("%H:%M:%S")
                    try:
                        with open(self.scan_filename, 'a', newline='') as f:
                            writer = csv.writer(f)
                            writer.writerow([timestamp, target_wl, peak_wl, peak_int])
                    except: pass
                    print(f"[Scan] Set: {target_wl:.1f} | Read Peak: {peak_wl:.2f}")

        except Exception as e:
            print(f"[Thread] Error: {e}")
        finally:
            self.is_scanning = False
            self.root.after(0, lambda: self.lbl_status.config(text="Scan Complete."))
            self.enable_controls()
            self.root.after(0, lambda: messagebox.showinfo("Done", "Calibration Scan Complete."))

    def update_plot_loop(self):
        if not self.is_paused:
            wl, inten = self.ocean.get_data()
            self.line.set_data(wl, inten)
            
            if len(wl) > 0:
                mask = (wl >= self.DETECT_MIN_WL) & (wl <= self.DETECT_MAX_WL)
                if np.any(mask):
                    valid_wl = wl[mask]
                    valid_inten = inten[mask]
                    max_idx = np.argmax(valid_inten)
                    peak_wl = valid_wl[max_idx]
                    peak_int = valid_inten[max_idx]
                else:
                    peak_wl = wl[np.argmax(inten)]
                    peak_int = np.max(inten)

                self.peak_dot.set_data([peak_wl], [peak_int])
                self.peak_text.set_text(f"Peak: {peak_wl:.2f} nm\nMax: {peak_int:.0f}")

                self.ax.set_xlim(wl[0], wl[-1])
                curr_ylim = self.ax.get_ylim()[1]
                if peak_int > curr_ylim * 0.9 or peak_int < curr_ylim * 0.5:
                     self.ax.set_ylim(0, peak_int * 1.2)

            self.canvas.draw()
            self.current_display_wl = wl
            self.current_display_inten = inten

        self.root.after(50, self.update_plot_loop)

    def toggle_pause(self):
        self.is_paused = not self.is_paused
        self.btn_pause.config(text="RESUME" if self.is_paused else "PAUSE")

    def save_data(self):
        if hasattr(self, 'current_display_wl'):
            fname = self.ocean.save_current_data(self.current_display_wl, self.current_display_inten)
            messagebox.showinfo("Saved", f"Snapshot saved to:\n{fname}")
        else:
            messagebox.showwarning("Warning", "No data.")

    def on_close(self):
        self.mono.close()
        self.ocean.close()
        self.root.destroy()
        sys.exit()

if __name__ == "__main__":
    root = tk.Tk()
    app = CalibrationApp(root)
    root.mainloop()