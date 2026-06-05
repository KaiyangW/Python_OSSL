import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import pandas as pd
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from scipy.optimize import curve_fit
import os
import re

matplotlib.use('TkAgg')

# Enable High DPI awareness on Windows
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


# ==========================================
#  Hinge Model (Bilinear Fit)
# ==========================================
def hinge_model(x, x_th, k1, k2, b1):
    """
    Continuous piecewise function (Hinge Model).
    x < x_th: Fluorescence region (slope k1)
    x > x_th: Lasing region (slope k2)
    """
    return np.where(x < x_th, 
                    k1 * x + b1, 
                    k1 * x_th + b1 + k2 * (x - x_th))


def find_files_by_keyword(folder, keyword, extension='.xlsx'):
    """Find files in folder whose name contains keyword and has given extension."""
    matches = []
    try:
        for f in os.listdir(folder):
            if f.startswith('.') or f.startswith('~$'):
                continue
            if keyword.lower() in f.lower() and f.lower().endswith(extension):
                matches.append(os.path.join(folder, f))
    except OSError:
        return []
    return sorted(matches)


def extract_base_name(filepath):
    """Extract dataset prefix from DFB_Analysed or legacy filenames."""
    filename = os.path.basename(filepath)
    m = re.match(r'(.+?)_DFB_Analysed\.xlsx$', filename, re.IGNORECASE)
    if m:
        return m.group(1)
    return filename.replace('_DFB_Analysed.xlsx', '').replace('.xlsx', '')


# ==========================================
#  Interactive GUI Class
# ==========================================
class ManualThresholdApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("DFB Manual Threshold Analyzer")
        self.geometry("1200x800")
        ctk.set_appearance_mode("Dark")
        
        # 数据存储
        self.x_data = None      # numeric indices used for fitting
        self.x_labels = None    # string labels for X axis ticks (Filename, or None)
        self.y_data = None
        self.x_label_str = "Incident Fluence (uJ/cm2)"  # axis label
        self.y_label_str = "Integrated Intensity (a.u.)"  # axis label
        self.mask = None 
        self.fwhm_data = None
        self.current_file = None
        self.current_folder = None
        self.fit_params = None
        self.x_th_err = np.nan  # 新增：用于存储阈值的拟合误差
        self.r_squared = np.nan
        self.annot = None       # 新增：用于存储悬停提示框对象
        self.file_list = []     # 当前目录可导航的 Excel 文件列表
        self.current_file_idx = None
        
        self.setup_ui()

    def setup_ui(self):
        # 左侧控制面板
        self.sidebar = ctk.CTkFrame(self, width=250)
        self.sidebar.pack(side="left", fill="y", padx=10, pady=10)
        
        self.btn_load = ctk.CTkButton(self.sidebar, text="Select Folder (DFB_Analysed)", command=self.load_data)
        self.btn_load.pack(pady=20, padx=10, fill="x")

        self.btn_reselect = ctk.CTkButton(self.sidebar, text="Reselect Folder", command=self.reselect_file)
        self.btn_reselect.pack(pady=6, padx=10, fill="x")

        self.btn_next = ctk.CTkButton(self.sidebar, text="Next File", command=self.load_next_file, state="disabled")
        self.btn_next.pack(pady=6, padx=10, fill="x")

        self.btn_reset = ctk.CTkButton(self.sidebar, text="Reset All", command=self.reset_all)
        self.btn_reset.pack(pady=6, padx=10, fill="x")
        
        self.lbl_info = ctk.CTkLabel(self.sidebar, text="No file loaded.", justify="left", wraplength=200)
        self.lbl_info.pack(pady=10, padx=10, fill="x")
        
        self.lbl_fit_res = ctk.CTkLabel(self.sidebar, text="Fit Results:\n--", justify="left", text_color="#2CC985", font=("Arial", 14, "bold"))
        self.lbl_fit_res.pack(pady=20, padx=10, fill="x")
        
        self.btn_save = ctk.CTkButton(self.sidebar, text="SAVE RESULTS", command=self.save_results, fg_color="#C92C45", state="disabled")
        self.btn_save.pack(side="bottom", pady=20, padx=10, fill="x")
        
        # 右侧绘图区域
        self.plot_frame = ctk.CTkFrame(self)
        self.plot_frame.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)
        
        # 初始化黑色背景画布
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.fig.patch.set_facecolor('#1a1a1a')
        self.ax.set_facecolor('#1a1a1a')
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        
        # 绑定点击事件和悬停事件
        self.cid_click = self.fig.canvas.mpl_connect('button_press_event', self.on_click)
        self.cid_hover = self.fig.canvas.mpl_connect('motion_notify_event', self.on_hover)
        
        self.update_plot_empty()

    def natural_sort_key(self, path):
        """Natural sort key for filenames with numbers."""
        name = os.path.basename(path)
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]

    def build_file_list(self, folder):
        """Build DFB_Analysed file list from folder for Next File navigation."""
        files = find_files_by_keyword(folder, 'DFB_Analysed')
        return sorted(files, key=self.natural_sort_key)

    def update_nav_state(self):
        """Enable/disable navigation buttons based on file list position."""
        if self.current_file is None or self.current_file_idx is None or len(self.file_list) == 0:
            self.btn_next.configure(state="disabled")
            return
        if self.current_file_idx < len(self.file_list) - 1:
            self.btn_next.configure(state="normal")
        else:
            self.btn_next.configure(state="disabled")

    def reselect_file(self):
        self.load_data()

    def load_data(self):
        folder = filedialog.askdirectory(title="Select data folder")
        if not folder:
            return

        self.file_list = self.build_file_list(folder)
        if not self.file_list:
            messagebox.showerror(
                "Error",
                f"No DFB_Analysed.xlsx file found in:\n{folder}"
            )
            return

        self.current_folder = folder
        self.current_file_idx = 0
        self.load_file(self.file_list[0])

    def load_file(self, filepath):
        try:
            # Try 'Metrics' sheet first, then default to the first sheet (Sheet1)
            try:
                df = pd.read_excel(filepath, sheet_name='Metrics')
            except Exception:
                df = pd.read_excel(filepath, sheet_name=0)

            # Identification logic for Oceanoptics data process.py format
            if 'Integrated Intensity (PL Removed)' in df.columns:
                self.y_data = df['Integrated Intensity (PL Removed)'].values
                # Use sequential integers as the numeric X for fitting;
                # store Filenames as display labels for the X axis.
                n = len(self.y_data)
                self.x_data = np.arange(n, dtype=float)
                if 'Filename' in df.columns:
                    # Strip .csv extension for cleaner labels
                    self.x_labels = [os.path.splitext(str(f))[0] for f in df['Filename']]
                else:
                    self.x_labels = [str(i) for i in range(n)]
                self.x_label_str = "File (Filename)"
                self.y_label_str = "Integrated Intensity (PL Removed, arb.)"
            else:
                # Default logic for original format (Column 0: X, Column 1: Y)
                self.x_data = df.iloc[:, 0].values
                self.y_data = df.iloc[:, 1].values
                self.x_labels = None
                self.x_label_str = "Incident Fluence (uJ/cm2)"
                self.y_label_str = "Integrated Intensity (a.u.)"
            
            # Ensure data is numeric for fitting and plotting
            self.x_data = pd.to_numeric(self.x_data, errors='coerce')
            self.y_data = pd.to_numeric(self.y_data, errors='coerce')
            
            fwhm_col_found = False
            for col in df.columns:
                col_name = str(col)
                if "FWHM" in col_name or "Dynamic smooth window FWHM" in col_name:
                    self.fwhm_data = df[col].values
                    fwhm_col_found = True
                    break
            
            if not fwhm_col_found:
                self.fwhm_data = np.full(len(self.x_data), np.nan)
            
            self.mask = np.ones(len(self.x_data), dtype=bool)
            self.current_file = filepath
            
            filename = os.path.basename(filepath)
            if self.current_file_idx is not None and len(self.file_list) > 0:
                nav_info = f"File {self.current_file_idx + 1}/{len(self.file_list)}"
            else:
                nav_info = "File 1/1"
            folder_line = f"Folder:\n{self.current_folder}\n\n" if self.current_folder else ""
            self.lbl_info.configure(
                text=f"{folder_line}Loaded:\n{filename}\n{nav_info}\n\nClick points to toggle inclusion."
            )
            self.btn_save.configure(state="normal")
            self.update_nav_state()
            
            self.update_fit_and_plot()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}")

    def load_next_file(self):
        if not self.file_list or self.current_file_idx is None:
            messagebox.showinfo("Next File", "No file sequence available. Please load a file first.")
            return
        next_idx = self.current_file_idx + 1
        if next_idx >= len(self.file_list):
            messagebox.showinfo("Next File", "Already at the last file.")
            self.update_nav_state()
            return
        self.current_file_idx = next_idx
        next_file = self.file_list[self.current_file_idx]
        self.load_file(next_file)

    def reset_all(self):
        """Clear all loaded data and return to initial state."""
        self.x_data = None
        self.x_labels = None
        self.y_data = None
        self.x_label_str = "Incident Fluence (uJ/cm2)"
        self.y_label_str = "Integrated Intensity (a.u.)"
        self.mask = None
        self.fwhm_data = None
        self.current_file = None
        self.current_folder = None
        self.fit_params = None
        self.x_th_err = np.nan
        self.r_squared = np.nan
        self.annot = None
        self.file_list = []
        self.current_file_idx = None

        self.lbl_info.configure(text="No file loaded.")
        self.lbl_fit_res.configure(text="Fit Results:\n--")
        self.btn_save.configure(state="disabled")
        self.update_nav_state()
        self.update_plot_empty()

    def on_click(self, event):
        if event.inaxes != self.ax or self.x_data is None:
            return
            
        xy_pixels = self.ax.transData.transform(np.vstack([self.x_data, self.y_data]).T)
        click_pixel = np.array([event.x, event.y])
        
        distances = np.linalg.norm(xy_pixels - click_pixel, axis=1)
        closest_idx = np.argmin(distances)
        
        if distances[closest_idx] < 15:
            self.mask[closest_idx] = not self.mask[closest_idx] 
            self.update_fit_and_plot()

    def on_hover(self, event):
        if event.inaxes != self.ax or self.x_data is None or self.annot is None:
            # 如果鼠标移出坐标轴，隐藏提示框
            if self.annot is not None and self.annot.get_visible():
                self.annot.set_visible(False)
                self.canvas.draw_idle()
            return

        # 计算鼠标与数据点的像素距离
        xy_pixels = self.ax.transData.transform(np.vstack([self.x_data, self.y_data]).T)
        hover_pixel = np.array([event.x, event.y])
        distances = np.linalg.norm(xy_pixels - hover_pixel, axis=1)
        closest_idx = np.argmin(distances)

        # 悬停判定半径也是 15 像素
        if distances[closest_idx] < 15:
            x_val = self.x_data[closest_idx]
            y_val = self.y_data[closest_idx]
            
            # 更新提示框的位置和文本
            self.annot.xy = (x_val, y_val)
            self.annot.set_text(f"E: {x_val:.4f}\nI: {y_val:.1f}")
            self.annot.set_visible(True)
            self.canvas.draw_idle()
        else:
            if self.annot.get_visible():
                self.annot.set_visible(False)
                self.canvas.draw_idle()

    def do_fit(self):
        x_clean = self.x_data[self.mask]
        y_clean = self.y_data[self.mask]
        
        self.fit_params = None
        self.x_th_err = np.nan
        self.r_squared = np.nan
        
        if len(x_clean) < 4:
            return False
            
        x_min, x_max = np.min(x_clean), np.max(x_clean)
        y_min, y_max = np.min(y_clean), np.max(y_clean)
        
        x_th_guess = (x_min + x_max) / 2
        p0 = [x_th_guess, 0, (y_max - y_min) / (x_max - x_min + 1e-9), y_min]
        bounds = ([x_min, -np.inf, 0, -np.inf], [x_max, np.inf, np.inf, np.inf])
        
        try:
            # 获取 popt (最优参数) 和 pcov (协方差矩阵)
            popt, pcov = curve_fit(hinge_model, x_clean, y_clean, p0=p0, bounds=bounds)
            self.fit_params = popt
            
            # 提取 threshold 的误差 (对角线第一个元素的平方根)
            if pcov is not None and not np.isinf(pcov).any():
                self.x_th_err = np.sqrt(np.diag(pcov))[0]
            
            y_fit = hinge_model(x_clean, *popt)
            ss_res = np.sum((y_clean - y_fit)**2)
            ss_tot = np.sum((y_clean - np.mean(y_clean))**2)
            self.r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
            return True
        except Exception:
            return False

    def update_fit_and_plot(self):
        self.ax.clear()
        
        x_in = self.x_data[self.mask]
        y_in = self.y_data[self.mask]
        x_out = self.x_data[~self.mask]
        y_out = self.y_data[~self.mask]
        
        if len(x_in) > 0:
            self.ax.scatter(x_in, y_in, c='#4287f5', s=80, marker='o', edgecolors='white', label='Included', zorder=5)
        if len(x_out) > 0:
            self.ax.scatter(x_out, y_out, c='#f54242', s=100, marker='x', linewidths=2, label='Excluded', zorder=5)
            
        fit_success = self.do_fit()
        if fit_success and self.fit_params is not None:
            x_th, k1, k2, b1 = self.fit_params
            
            x_plot = np.linspace(min(self.x_data), max(self.x_data), 200)
            y_plot = hinge_model(x_plot, x_th, k1, k2, b1)
            self.ax.plot(x_plot, y_plot, color='#2CC985', linewidth=2.5, linestyle='-', label='Fit Line', zorder=4)
            
            self.ax.axvline(x=x_th, color='white', linestyle='--', alpha=0.5, zorder=3)
            
            slope_ratio = k2/k1 if k1 > 1e-9 else np.inf
            
            # 格式化输出带误差的 Threshold
            err_str = f" ± {self.x_th_err:.4f}" if np.isfinite(self.x_th_err) else ""
            self.lbl_fit_res.configure(text=f"Threshold:\n{x_th:.4f}{err_str}\n\nSlope Ratio:\n{slope_ratio:.1f}\n\nR²:\n{self.r_squared:.4f}")
            
            # ==== 将拟合结果文字添加到图表中 ====
            plot_text = f"Threshold: {x_th:.4f}{err_str}\nSlope Ratio: {slope_ratio:.1f}\nR²: {self.r_squared:.4f}"
            self.ax.text(0.05, 0.95, plot_text, transform=self.ax.transAxes, fontsize=12,
                         verticalalignment='top', color='white',
                         bbox=dict(boxstyle='round,pad=0.5', facecolor='#2b2b2b', alpha=0.8, edgecolor='white'),
                         zorder=10)
            
            # 如果是 Oceanoptics 格式 (使用索引作为 X)，添加额外提示
            if self.x_labels is not None:
                reminder_text = "显示的拟合拐点是文件顺序编号"
                self.ax.text(0.05, 0.75, reminder_text, transform=self.ax.transAxes, 
                             fontsize=14, family='Microsoft YaHei',
                             verticalalignment='top', color='#FFD700',
                             zorder=11)
            
        else:
            self.lbl_fit_res.configure(text="Fit Failed or\nNot Enough Points")

        self.ax.set_title("Bilinear Fit", color='white', pad=10)
        self.ax.set_xlabel(self.x_label_str, color='white')
        self.ax.set_ylabel(self.y_label_str, color='white')
        self.ax.tick_params(colors='white')
        # If Filename labels are available, apply them as X tick labels
        if self.x_labels is not None:
            self.ax.set_xticks(self.x_data)
            self.ax.set_xticklabels(self.x_labels, rotation=45, ha='right', fontsize=8, color='white')
        self.ax.grid(True, linestyle=':', alpha=0.3)
        self.ax.legend(facecolor='#1a1a1a', edgecolor='white', labelcolor='white')
        
        # 重新创建悬停提示框 (因为 clear() 会把它删掉)
        self.annot = self.ax.annotate("", xy=(0,0), xytext=(15, 15), textcoords="offset points",
                                      bbox=dict(boxstyle="round4,pad=0.5", fc="#2b2b2b", ec="white", alpha=0.9),
                                      color="white", fontsize=10, zorder=15)
        self.annot.set_visible(False)
        
        self.fig.tight_layout()
        self.canvas.draw()

    def update_plot_empty(self):
        self.ax.clear()
        self.ax.text(0.5, 0.5, 'Load data to begin', color='gray', fontsize=16, ha='center', va='center')
        self.ax.axis('off')
        self.canvas.draw()

    def save_results(self):
        if self.current_file is None: return
        
        base_dir = self.current_folder or os.path.dirname(self.current_file)
        base_name = extract_base_name(self.current_file)
        
        if self.fit_params is not None:
            x_th, k1, k2, b1 = self.fit_params
            slope_ratio = k2/k1 if k1 > 1e-9 else np.inf
        else:
            x_th, k1, k2, b1, slope_ratio = [np.nan]*5
            
        df_params = pd.DataFrame([{
            'Threshold': x_th,
            'Threshold_Error': self.x_th_err, # 新增保存误差
            'Slope_Fluo (k1)': k1,
            'Slope_Lasing (k2)': k2,
            'Intercept (b1)': b1,
            'Slope_Ratio': slope_ratio,
            'R_Squared': self.r_squared,
            'Manual_Dropouts': np.sum(~self.mask)
        }])
        
        df_data = pd.DataFrame({
            'Incident Fluence (uJ/cm2)': self.x_data,
            'Integrated Intensity (a.u.)': self.y_data,
            'Used_in_Fit': self.mask
        })
        
        df_plot_data = pd.DataFrame({
            'Incident Fluence (uJ/cm2)': self.x_data[self.mask],
            'Integrated Intensity (a.u.)': self.y_data[self.mask],
            'FWHM': self.fwhm_data[self.mask] 
        })
        
        if self.fit_params is not None:
            x_fit_line = np.linspace(min(self.x_data), max(self.x_data), 200)
            y_fit_line = hinge_model(x_fit_line, *self.fit_params)
            df_fit_line = pd.DataFrame({'Fit_Line_X': x_fit_line, 'Fit_Line_Y': y_fit_line})
        else:
            df_fit_line = pd.DataFrame()
        
        save_path = os.path.join(base_dir, f"{base_name}_ManualFit_Result.xlsx")
        
        try:
            with pd.ExcelWriter(save_path) as writer:
                df_params.to_excel(writer, sheet_name='Parameters', index=False)
                df_data.to_excel(writer, sheet_name='Data_and_Mask', index=False)
                # ==== 新增：将筛选后的数据保存到 Plot_data sheet ====
                df_plot_data.to_excel(writer, sheet_name='Plot_data', index=False)
                
                if not df_fit_line.empty:
                    df_fit_line.to_excel(writer, sheet_name='Fit_Line', index=False)
            
            img_path = os.path.join(base_dir, f"{base_name}_ManualFit_Plot.png")
            self.fig.savefig(img_path, dpi=200, bbox_inches='tight', facecolor='#1a1a1a')

            auto_fit_path = os.path.join(base_dir, f"{base_name}_auto_fit.xlsx")
            deleted_msg = ""
            if os.path.isfile(auto_fit_path):
                os.remove(auto_fit_path)
                deleted_msg = f"\n\nDeleted: {os.path.basename(auto_fit_path)}"
            
            messagebox.showinfo("Success", f"Saved successfully to:\n{save_path}{deleted_msg}")
            
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save:\n{e}")

if __name__ == "__main__":
    app = ManualThresholdApp()
    app.mainloop()