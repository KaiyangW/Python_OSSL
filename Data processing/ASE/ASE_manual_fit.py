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
import sys

matplotlib.use('TkAgg')

_READER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _READER_ROOT not in sys.path:
    sys.path.insert(0, _READER_ROOT)

from Read_data_unified import read_workbook

# Enable High DPI awareness on Windows
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


# ==========================================
#  Piecewise Linear Model Factory
# ==========================================
def make_piecewise_model(n_turns):
    """
    Create a continuous piecewise linear model with n_turns turn points.
    
    Parameters layout: [x_th1, ..., x_th_n, k1, ..., k_{n+1}, b1]
    - x_th1..x_th_n : turn point x-coordinates
    - k1..k_{n+1}   : slopes for each of the (n+1) segments
    - b1             : y-intercept of the first segment
    
    For n_turns=1 this is equivalent to the original hinge model.
    """
    def model(x, *params):
        # By parameterizing as [x0, dx1, dx2, ...], we naturally enforce left-to-right ordering
        # mathematically with bounds, avoiding permutation symmetry entirely.
        x_ths = [params[0]]
        for i in range(1, n_turns):
            x_ths.append(x_ths[-1] + params[i])

        ks = list(params[n_turns:2 * n_turns + 1])
        b1 = params[2 * n_turns + 1]

        # Value at each turn point (continuity constraint)
        vals = [ks[0] * x_ths[0] + b1]
        for i in range(1, n_turns):
            vals.append(vals[i - 1] + ks[i] * (x_ths[i] - x_ths[i - 1]))

        # Build piecewise result – start with the first segment
        result = ks[0] * x + b1
        for i in range(n_turns):
            result = np.where(x >= x_ths[i],
                              vals[i] + ks[i + 1] * (x - x_ths[i]),
                              result)
        return result

    return model


def find_files_by_keyword(folder, keyword, extension='.xlsx'):
    """Find files in folder whose name contains keyword and has given extension."""
    matches = []
    try:
        for f in os.listdir(folder):
            if f.startswith('.'):
                continue
            if keyword.lower() in f.lower() and f.lower().endswith(extension):
                matches.append(os.path.join(folder, f))
    except OSError:
        return []
    return sorted(matches)


def extract_base_name(filepath):
    """Extract dataset prefix from Analysed_ultra or legacy Analysed filenames."""
    filename = os.path.basename(filepath)
    m = re.match(r'(.+?)_Analysed_ultra\.xlsx$', filename, re.IGNORECASE)
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
        self.x_data = None
        self.y_data = None
        self.fit_mask = None
        self.plot_mask = None
        self.fwhm_data = None
        self.current_file = None
        self.current_folder = None
        self.fit_params = None
        self.x_th_errs = []       # 各转折点拟合误差列表
        self.r_squared = np.nan
        self.annot = None         # 用于存储悬停提示框对象
        self.n_turns = 1          # 默认 1 个转折点（2 段线性）
        
        self.setup_ui()

    def setup_ui(self):
        # 左侧控制面板
        self.sidebar = ctk.CTkFrame(self, width=250)
        self.sidebar.pack(side="left", fill="y", padx=10, pady=10)
        
        self.btn_load = ctk.CTkButton(self.sidebar, text="Select Folder (Analysed_ultra)", command=self.load_data)
        self.btn_load.pack(pady=20, padx=10, fill="x")

        # ==== 转折点数量选择器 ====
        self.lbl_turns = ctk.CTkLabel(self.sidebar, text="Number of Linear Slopes:",
                                      font=("Arial", 13))
        self.lbl_turns.pack(pady=(15, 5), padx=10, fill="x")

        self.seg_turns = ctk.CTkSegmentedButton(
            self.sidebar, values=["2", "3", "4"],
            command=self.on_turns_changed
        )
        self.seg_turns.set("2")
        self.seg_turns.pack(pady=(0, 10), padx=10, fill="x")
        # ============================
        
        self.lbl_info = ctk.CTkLabel(self.sidebar, text="No file loaded.", justify="left", wraplength=200)
        self.lbl_info.pack(pady=(10, 4), padx=10, fill="x")
        self.lbl_hint = ctk.CTkLabel(
            self.sidebar,
            text="",
            justify="left",
            wraplength=200,
            text_color="#FF4444",
            font=("Arial", 14, "bold"),
        )
        self.lbl_hint.pack(pady=(0, 10), padx=10, fill="x")
        
        self.lbl_fit_res = ctk.CTkLabel(self.sidebar, text="Fit Results:\n--", justify="left",
                                        text_color="#2CC985", font=("Arial", 15, "bold"),
                                        wraplength=220)
        self.lbl_fit_res.pack(pady=20, padx=10, fill="x")
        
        self.btn_save = ctk.CTkButton(self.sidebar, text="SAVE RESULTS", command=self.save_results,
                                      fg_color="#C92C45", state="disabled")
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

    def on_turns_changed(self, value):
        """当用户切换转折点数量时，重新拟合并绘图。"""
        self.n_turns = int(value) - 1
        if self.x_data is not None:
            self.update_fit_and_plot()

    def load_data(self):
        folder = filedialog.askdirectory(title="Select data folder")
        if not folder:
            return

        matches = find_files_by_keyword(folder, 'Analysed_ultra')
        if not matches:
            messagebox.showerror(
                "Error",
                f"No Analysed_ultra.xlsx file found in:\n{folder}"
            )
            return
        if len(matches) > 1:
            names = '\n'.join(os.path.basename(p) for p in matches)
            messagebox.showerror(
                "Error",
                f"Multiple Analysed_ultra files found. Please keep only one:\n{names}"
            )
            return

        filepath = matches[0]
        try:
            df = read_workbook(filepath, sheet='Metrics')
            self.x_data = df.iloc[:, 0].values
            self.y_data = df.iloc[:, 1].values
            
            fwhm_col_found = False
            for col in df.columns:
                # 将列名转为字符串以防万一，并检索关键词
                col_name = str(col)
                if "FWHM" in col_name or "Dynamic smooth window FWHM" in col_name:
                    self.fwhm_data = df[col].values
                    fwhm_col_found = True
                    break  # 找到第一个符合的列就停止
            
            # 如果遍历完所有列都没找到包含关键词的，则填充空值
            if not fwhm_col_found:
                self.fwhm_data = np.full(len(self.x_data), np.nan)
            
            self.fit_mask = np.ones(len(self.x_data), dtype=bool)
            self.plot_mask = np.ones(len(self.x_data), dtype=bool)
            self.current_file = filepath
            self.current_folder = folder
            
            filename = os.path.basename(filepath)
            self.lbl_info.configure(text=f"Folder:\n{folder}\n\nLoaded:\n{filename}")
            self.lbl_hint.configure(
                text=(
                    "Left click: exclude from fit and plot\n"
                    "Right click: exclude from fit, keep in plot"
                )
            )
            self.btn_save.configure(state="normal")
            
            self.update_fit_and_plot()
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load file:\n{e}")

    def _toggle_point_state(self, idx, button):
        """左键：拟合与绘图都排除；右键：仅拟合排除、绘图保留。"""
        in_fit = self.fit_mask[idx]
        in_plot = self.plot_mask[idx]

        if button == 1:
            if in_fit and in_plot:
                self.fit_mask[idx] = False
                self.plot_mask[idx] = False
            elif not in_fit and not in_plot:
                self.fit_mask[idx] = True
                self.plot_mask[idx] = True
            elif not in_fit and in_plot:
                self.fit_mask[idx] = False
                self.plot_mask[idx] = False
        elif button == 3:
            if in_fit and in_plot:
                self.fit_mask[idx] = False
                self.plot_mask[idx] = True
            elif not in_fit and in_plot:
                self.fit_mask[idx] = True
                self.plot_mask[idx] = True
            elif not in_fit and not in_plot:
                self.fit_mask[idx] = False
                self.plot_mask[idx] = True

    def on_click(self, event):
        if event.inaxes != self.ax or self.x_data is None:
            return
        if event.button not in (1, 3):
            return
            
        xy_pixels = self.ax.transData.transform(np.vstack([self.x_data, self.y_data]).T)
        click_pixel = np.array([event.x, event.y])
        
        distances = np.linalg.norm(xy_pixels - click_pixel, axis=1)
        closest_idx = np.argmin(distances)
        
        if distances[closest_idx] < 15:
            self._toggle_point_state(closest_idx, event.button)
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
        x_clean = self.x_data[self.fit_mask]
        y_clean = self.y_data[self.fit_mask]
        
        self.fit_params = None
        self.x_th_errs = []
        self.r_squared = np.nan
        
        n = self.n_turns
        n_params = 2 * n + 2   # total parameters: n turn-points + (n+1) slopes + 1 intercept

        if len(x_clean) < n_params + 1:
            return False
            
        sort_idx = np.argsort(x_clean)
        x_clean = x_clean[sort_idx]
        y_clean = y_clean[sort_idx]
        
        x_min, x_max = x_clean[0], x_clean[-1]
        x_range = x_max - x_min
        y_min, y_max = np.min(y_clean), np.max(y_clean)
        
        # --- Initial guesses ---
        # Turn points: evenly spaced guesses
        x_th_guesses = [x_min + (i + 1) * x_range / (n + 1) for i in range(n)]

        # Parameterize intervals [x0, dx1, dx2...]
        p0_x = [x_th_guesses[0]]
        for i in range(1, n):
            p0_x.append(x_th_guesses[i] - x_th_guesses[i-1])

        # Slopes: dynamically segment real data instead of simple ramp
        k_guesses = []
        chunk_bounds = [x_min] + x_th_guesses + [x_max]
        avg_slope = (y_max - y_min) / (x_range + 1e-9)

        for i in range(n + 1):
            mask_chunk = (x_clean >= chunk_bounds[i]) & (x_clean <= chunk_bounds[i+1])
            if np.sum(mask_chunk) >= 2:
                k, _ = np.polyfit(x_clean[mask_chunk], y_clean[mask_chunk], 1)
                k_guesses.append(k)
            else:
                k_guesses.append(avg_slope)

        # Intercept guess from the first segment
        mask_first = (x_clean >= chunk_bounds[0]) & (x_clean <= chunk_bounds[1])
        if np.sum(mask_first) >= 2:
            _, b1_guess = np.polyfit(x_clean[mask_first], y_clean[mask_first], 1)
        else:
            b1_guess = y_min

        p0 = p0_x + k_guesses + [b1_guess]

        # --- Bounds ---
        # Force distance between turn points to be positive (ordering constrained to dx > epsilon)
        min_dx = 1e-5 * x_range
        lower = [x_min] + [min_dx] * (n - 1) + [-np.inf] * (n + 1) + [-np.inf]
        upper = [x_max] + [x_range] * (n - 1) + [ np.inf] * (n + 1) + [ np.inf]

        model = make_piecewise_model(n)
        
        try:
            popt, pcov = curve_fit(model, x_clean, y_clean, p0=p0,
                                   bounds=(lower, upper), maxfev=10000)
            self.fit_params = popt
            
            # Extract turn-point errors properly from cumulative sum variance
            if pcov is not None and not np.isinf(pcov).any():
                cov_x = pcov[:n, :n]
                self.x_th_errs = []
                for i in range(n):
                    # Var(sum(dx[:i+1])) = sum of the cov_x block up to i
                    var_x_i = np.sum(cov_x[:i+1, :i+1])
                    self.x_th_errs.append(np.sqrt(max(0, var_x_i)))
            else:
                self.x_th_errs = [np.nan] * n
            
            y_fit = model(x_clean, *popt)
            ss_res = np.sum((y_clean - y_fit)**2)
            ss_tot = np.sum((y_clean - np.mean(y_clean))**2)
            self.r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
            return True
        except Exception:
            return False

    def update_fit_and_plot(self):
        self.ax.clear()
        
        fit_idx = self.fit_mask
        plot_only_idx = self.plot_mask & ~self.fit_mask
        excluded_idx = ~self.plot_mask

        x_fit = self.x_data[fit_idx]
        y_fit = self.y_data[fit_idx]
        x_plot_only = self.x_data[plot_only_idx]
        y_plot_only = self.y_data[plot_only_idx]
        x_out = self.x_data[excluded_idx]
        y_out = self.y_data[excluded_idx]

        if len(x_fit) > 0:
            self.ax.scatter(x_fit, y_fit, c='#4287f5', s=80, marker='o', edgecolors='white',
                            label='Included in fit', zorder=5)
        if len(x_plot_only) > 0:
            self.ax.scatter(x_plot_only, y_plot_only, c='#f5a442', s=80, marker='o', edgecolors='white',
                            label='Plot only', zorder=5)
        if len(x_out) > 0:
            self.ax.scatter(x_out, y_out, c='#f54242', s=100, marker='x', linewidths=2,
                            label='Excluded', zorder=5)
            
        n = self.n_turns
        fit_success = self.do_fit()

        if fit_success and self.fit_params is not None:
            # Extract cumulative turn points
            x_ths = [self.fit_params[0]]
            for i in range(1, n):
                x_ths.append(x_ths[-1] + self.fit_params[i])
            ks = list(self.fit_params[n:2 * n + 1])
            b1 = self.fit_params[2 * n + 1]

            model = make_piecewise_model(n)
            x_plot = np.linspace(min(self.x_data), max(self.x_data), 200)
            y_plot = model(x_plot, *self.fit_params)
            self.ax.plot(x_plot, y_plot, color='#2CC985', linewidth=2.5,
                         linestyle='-', label='Fit Line', zorder=4)
            
            # Vertical dashed lines at each turn point
            tp_colors = ['white', '#FFD700', '#FF69B4']
            for i, xth in enumerate(x_ths):
                color = tp_colors[i % len(tp_colors)]
                self.ax.axvline(x=xth, color=color, linestyle='--', alpha=0.5, zorder=3)

            # --- Build display text ---
            lines = []
            for i, xth in enumerate(x_ths):
                err = self.x_th_errs[i] if i < len(self.x_th_errs) else np.nan
                err_str = f" ± {err:.4f}" if np.isfinite(err) else ""
                lines.append(f"TP{i+1}: {xth:.4f}{err_str}")

            for i, k in enumerate(ks):
                lines.append(f"k{i+1}: {k:.2f}")

            # Slope ratio (last / first) for quick reference
            if abs(ks[0]) > 1e-9:
                slope_ratio = ks[-1] / ks[0]
                lines.append(f"Slope Ratio (k{n+1}/k1): {slope_ratio:.1f}")

            lines.append(f"R²: {self.r_squared:.4f}")

            result_text = "\n".join(lines)
            self.lbl_fit_res.configure(text=result_text)
            
            # In-plot text box
            fontsize = max(9, 12 - n)  # scale down for more turn points
            self.ax.text(0.05, 0.95, result_text, transform=self.ax.transAxes,
                         fontsize=fontsize, verticalalignment='top', color='white',
                         bbox=dict(boxstyle='round,pad=0.5', facecolor='#2b2b2b',
                                   alpha=0.8, edgecolor='white'),
                         zorder=10)
            
        else:
            self.lbl_fit_res.configure(text="Fit Failed or\nNot Enough Points")

        n_segs = n + 1
        self.ax.set_title(f"Piecewise Linear Fit ({n_segs} segments)", color='white', pad=10)
        # ==== 更新了坐标轴标签 ====
        self.ax.set_xlabel("Incident Fluence (uJ/cm2)", color='white')
        self.ax.set_ylabel("Integrated Intensity (a.u.)", color='white')
        self.ax.tick_params(colors='white')
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
        
        n = self.n_turns

        if self.fit_params is not None:
            x_ths = [self.fit_params[0]]
            for i in range(1, n):
                x_ths.append(x_ths[-1] + self.fit_params[i])
            ks = list(self.fit_params[n:2 * n + 1])
            b1 = self.fit_params[2 * n + 1]
        else:
            x_ths = [np.nan] * n
            ks = [np.nan] * (n + 1)
            b1 = np.nan

        # Build parameter dictionary
        params_dict = {'N_Turn_Points': n, 'N_Segments': n + 1}
        for i, xth in enumerate(x_ths):
            params_dict[f'Turn_Point_{i+1}'] = xth
            err = self.x_th_errs[i] if i < len(self.x_th_errs) else np.nan
            params_dict[f'Turn_Point_{i+1}_Error'] = err
        for i, k in enumerate(ks):
            params_dict[f'Slope_{i+1} (k{i+1})'] = k
        params_dict['Intercept (b1)'] = b1
        if abs(ks[0]) > 1e-9 if not np.isnan(ks[0]) else False:
            params_dict['Slope_Ratio (last/first)'] = ks[-1] / ks[0]
        else:
            params_dict['Slope_Ratio (last/first)'] = np.nan
        params_dict['R_Squared'] = self.r_squared
        params_dict['Manual_Dropouts'] = int(np.sum(~self.fit_mask))
        params_dict['Plot_Only_Points'] = int(np.sum(self.plot_mask & ~self.fit_mask))

        df_params = pd.DataFrame([params_dict])
        
        df_data = pd.DataFrame({
            'Incident Fluence (uJ/cm2)': self.x_data,
            'Integrated Intensity (a.u.)': self.y_data,
            'Used_in_Fit': self.fit_mask,
            'Included_in_Plot': self.plot_mask,
        })
        
        df_plot_data = pd.DataFrame({
            'Incident Fluence (uJ/cm2)': self.x_data[self.plot_mask],
            'Integrated Intensity (a.u.)': self.y_data[self.plot_mask],
            'FWHM': self.fwhm_data[self.plot_mask]
        })
        
        if self.fit_params is not None:
            model = make_piecewise_model(n)
            x_fit_line = np.linspace(min(self.x_data), max(self.x_data), 200)
            y_fit_line = model(x_fit_line, *self.fit_params)
            df_fit_line = pd.DataFrame({'Fit_Line_X': x_fit_line, 'Fit_Line_Y': y_fit_line})
        else:
            df_fit_line = pd.DataFrame()
        
        save_path = os.path.join(base_dir, f"ManualFit_Result_{base_name}.xlsx")
        
        try:
            with pd.ExcelWriter(save_path) as writer:
                df_params.to_excel(writer, sheet_name='Parameters', index=False)
                df_data.to_excel(writer, sheet_name='Data_and_Mask', index=False)
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