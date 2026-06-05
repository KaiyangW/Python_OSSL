import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import csv
import os
from matplotlib import style
import numpy as np
from datetime import datetime
from Bentham_mono_control_emb import MonoDriver
from Read_osci_V_emb import OscilloscopeReader

# --- HIGH DPI SETTING (Windows) ---
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1) 
except Exception:
    pass 

class ScanApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Automated Monochromator & Oscilloscope Scan")
        self.root.geometry("1050x850")
        self.root.resizable(False, False)

        # --- 变量绑定 ---
        self.start_wl = tk.DoubleVar(value=400.0)
        self.end_wl = tk.DoubleVar(value=700.0)
        self.step_wl = tk.DoubleVar(value=10.0)
        self.dwell_time = tk.DoubleVar(value=1.0)
        self.is_scanning = False
        self.scan_thread = None

        self._build_ui()

    def _build_ui(self):

        style = ttk.Style()
        default_font = ("Microsoft YaHei", 13)   # 可改为 ("Segoe UI", 10) 或 ("Arial", 10)
        style.configure(".", font=default_font)   # "." 表示应用到所有 ttk 控件
        style.configure("TLabelframe.Label", font=default_font)  # 确保 LabelFrame 标题也生效

        # 参数设置框架
        param_frame = ttk.LabelFrame(self.root, text="Scan Parameters")
        param_frame.pack(padx=10, pady=10, fill="x")

        ttk.Label(param_frame, text="Start Wavelength (nm):").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(param_frame, textvariable=self.start_wl).grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(param_frame, text="End Wavelength (nm):").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(param_frame, textvariable=self.end_wl).grid(row=1, column=1, padx=5, pady=5)

        ttk.Label(param_frame, text="Step (nm):").grid(row=2, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(param_frame, textvariable=self.step_wl).grid(row=2, column=1, padx=5, pady=5)

        ttk.Label(param_frame, text="Dwell Time (s):").grid(row=3, column=0, padx=5, pady=5, sticky="w")
        ttk.Entry(param_frame, textvariable=self.dwell_time).grid(row=3, column=1, padx=5, pady=5)

        # 控制按钮
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(padx=10, pady=5, fill="x")

        self.btn_start = ttk.Button(btn_frame, text="Start Scan", command=self.start_scan)
        self.btn_start.pack(side="left", expand=True, fill="x", padx=5)

        self.btn_stop = ttk.Button(btn_frame, text="Stop / Abort", command=self.stop_scan, state="disabled")
        self.btn_stop.pack(side="right", expand=True, fill="x", padx=5)

        # 日志输出窗口
        log_frame = ttk.LabelFrame(self.root, text="System Log")
        log_frame.pack(padx=10, pady=10, fill="both", expand=True)

        self.log_text = tk.Text(log_frame, height=12, state="disabled", bg="#f4f4f4")
        self.log_text.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=scrollbar.set)

    def log(self, message):
        """线程安全的日志输出函数"""
        def append():
            self.log_text.config(state="normal")
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(0, append)

    def start_scan(self):
        if self.is_scanning:
            return

        start = self.start_wl.get()
        end = self.end_wl.get()
        step = self.step_wl.get()

        if step == 0 or (start < end and step < 0) or (start > end and step > 0):
            messagebox.showerror("Error", "Invalid step value relative to start/end wavelengths.")
            return

        self.is_scanning = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        
        # 启动后台扫描线程以防阻塞 UI
        self.scan_thread = threading.Thread(target=self.run_scan_logic, args=(start, end, step), daemon=True)
        self.scan_thread.start()

    def stop_scan(self):
        self.log("Abort requested. Stopping after current operation...")
        self.is_scanning = False

    def run_scan_logic(self, start, end, step):
        self.log("Initializing hardware...")
        mono = None
        scope = None
        
        # 提前生成当次扫描的文件名，并写入表头
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"ScanData_{timestamp}.csv"
        filepath = os.path.join(os.getcwd(), filename)
        
        try:
            with open(filepath, mode='w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Wavelength (nm)", "Time (s)", "Voltage (V)"])
            self.log(f"Data file created: {filename}")
        except Exception as e:
            self.log(f"Failed to create data file: {e}")
            self.is_scanning = False
            return

        # 生成波长列表
        if start <= end:
            wavelengths = np.arange(start, end + step, step)
        else:
            wavelengths = np.arange(start, end - step, -step) # 修复了从高扫到低的步进逻辑

        try:
            mono = MonoDriver()
            if not mono.connected:
                self.log("WARNING: Mono in Simulation Mode.")
            
            try:
                scope = OscilloscopeReader()
                self.log("Oscilloscope connected successfully.")
            except Exception as e:
                self.log(f"Oscilloscope connection failed: {e}")
                return

            for wl in wavelengths:
                if not self.is_scanning:
                    self.log("Scan aborted by user.")
                    break
                
                # --- 步骤 1: 单色仪移动 ---
                self.log(f"Moving monochromator to {wl:.1f} nm...")
                move_done_event = threading.Event()
                mono.move_threaded(wl, lambda msg: None, lambda: move_done_event.set())
                move_done_event.wait() # 阻塞，直到单色仪物理移动完成

                # --- 步骤 2: 停留 (Dwell Time) ---
                dwell = self.dwell_time.get()
                self.log(f"Arrived at {wl:.1f} nm. Dwelling for {dwell} s...")
                time.sleep(dwell) # 在这里严格停留设定的时间

                if not self.is_scanning:
                    break

                # --- 步骤 3: 采集数据 ---
                self.log("Dwell finished. Waiting for oscilloscope trigger and acquiring data...")
                try:
                    t_array, v_array = scope.acquire_trace()
                    self.log(f"Data acquired. Trace length: {len(t_array)} points.")
                    
                    # --- 步骤 4: 立即保存当前波长的数据 ---
                    self.log(f"Saving data for {wl:.1f} nm...")
                    with open(filepath, mode='a', newline='') as f: # 使用 'a' (append) 模式追加写入
                        writer = csv.writer(f)
                        for t, v in zip(t_array, v_array):
                            writer.writerow([round(wl, 2), t, v])
                            
                except TimeoutError:
                    self.log("WARNING: Oscilloscope trigger timed out. Laser fired? Skipping save for this wavelength.")
                except Exception as e:
                    self.log(f"Error reading/saving scope at {wl:.1f} nm: {e}")

                # --- 步骤 5: 循环结束，准备向下一个波长移动 ---

        except Exception as e:
            self.log(f"Critical error during scan: {e}")
        
        finally:
            self.log("Closing hardware connections...")
            if mono: mono.close()
            if scope: scope.close()

            self.is_scanning = False
            self.root.after(0, lambda: self.btn_start.config(state="normal"))
            self.root.after(0, lambda: self.btn_stop.config(state="disabled"))
            self.log("Scan routine finished.")

if __name__ == "__main__":
    root = tk.Tk()
    app = ScanApp(root)
    root.mainloop()