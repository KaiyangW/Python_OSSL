import os
import re
import sys
import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import matplotlib.pyplot as plt

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_table, read_workbook

# 引入 PlotUtils 的通用模块
import tkinter as tk
from tkinter import filedialog

from PlotUtils import (
    setup_matplotlib_style,
    create_matched_fig_ax,
    apply_matplotlib_export_axes_style,
    set_matched_twin_y_right_margin,
    DynamicPlotExplorer,
    GLOBAL_FONT_SIZE,
    SPECTRA_COLOR_PALETTES,
    sample_spectra_palette,
)

def select_folder(prompt_text):
    """弹出文件夹选择对话框，返回文件夹路径"""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title=prompt_text)
    root.destroy()
    if not folder:
        print("❌ 未选择文件夹，操作取消！")
        sys.exit()
    return folder

def find_xlsx_in_folder(folder):
    """在文件夹中找所有 xlsx 文件"""
    files = [os.path.join(folder, f) for f in os.listdir(folder)
             if f.lower().endswith('.xlsx') and not f.startswith('~')]
    if not files:
        print(f"❌ 文件夹中没有找到 .xlsx 文件：{folder}")
        sys.exit()
    return files


THRESHOLD_VALUE_COLUMNS = ("threshold", "turn_point_1")


def _column_key(col):
    """Normalize Excel/CSV column names for threshold column matching."""
    return str(col).strip().lower().replace(" ", "_")


def _is_threshold_value_column(col):
    key = _column_key(col)
    return key in THRESHOLD_VALUE_COLUMNS or (
        'threshold' in key and 'error' not in key
    )


def _is_threshold_error_column(col):
    key = _column_key(col)
    return 'error' in key or 'threshold_error' in key


def identify_and_load_data(file_paths):
    """从多个 Excel 中识别并读取 Plot_data、Fit_Line，以及 Analysed_ultra 文件中的光谱数据"""
    df_other = None
    df_fit = None
    df_plot_data = None
    spectra_data = None  # 光谱演化数据 dict

    for filepath in file_paths:
        try:
            all_sheets = read_workbook(filepath, sheet=None)
        except Exception as e:
            continue
        
        if 'Plot_data' in all_sheets:
            df = all_sheets['Plot_data']
            df_plot_data = df.iloc[:, 0:3].copy()
            df_plot_data.columns = ['Fluence', 'Intensity', 'FWHM']
            df_plot_data = df_plot_data.dropna()
            
            if 'Fit_Line' in all_sheets:
                df_f = all_sheets['Fit_Line']
                cols = [str(c).strip() for c in df_f.columns]
                if 'Fit_Line_X' in cols and 'Fit_Line_Y' in cols:
                    df_fit = df_f[['Fit_Line_X', 'Fit_Line_Y']].copy()
                    df_fit.columns = ['x', 'y']
                    df_fit = df_fit.dropna()

        # 检查是否为 Analysed_ultra 文件（含光谱数据和阈值）
        if 'Raw Spec Normalized (nm)' in all_sheets and 'Metrics' in all_sheets:
            try:
                df_spec = all_sheets['Raw Spec Normalized (nm)']
                wavelengths = df_spec.iloc[:, 0].values.astype(float)
                pump_fluences = np.array([float(c) for c in df_spec.columns[1:]])
                spec_matrix = df_spec.iloc[:, 1:].values.astype(float)  # (n_wave, n_pump)

                # 从 Plot_data 文件的 Fit_Line 推断的阈值，现在改为从 Metrics 第一行里找
                # Metrics 列：Fluence, Intensity, FWHM, Scaled PL, Peak Wavelength
                # 阈值由 auto_threshold_module 写入同目录的 CSV，但更简洁的是
                # 检查是否有 'Threshold' 相关列（Batch summary CSV）或读Fit_data sheet
                df_metrics = all_sheets['Metrics']
                auto_threshold = None
                auto_threshold_err = None
                # 搜索 Threshold 或 Turn_Point_1 阈值列
                auto_threshold, auto_threshold_err = _read_threshold_from_dataframe(df_metrics)

                # 尝试读取同目录下 Batch CSV 中的 Threshold 和 Error
                # （由 ASE 脚本写入 Threshold_Summary_Batch.csv）
                folder_of_file = os.path.dirname(filepath)
                batch_csv = os.path.join(folder_of_file, 'Threshold_Summary_Batch.csv')
                if os.path.exists(batch_csv):
                    try:
                        df_batch = read_table(batch_csv)
                        batch_threshold, batch_threshold_err = _read_threshold_from_dataframe(df_batch)
                        if batch_threshold is not None:
                            auto_threshold = batch_threshold
                        if batch_threshold_err is not None:
                            auto_threshold_err = batch_threshold_err
                    except Exception:
                        pass

                spectra_data = {
                    'wavelengths': wavelengths,
                    'pump_fluences': pump_fluences,
                    'spec_matrix': spec_matrix,
                    'auto_threshold': auto_threshold,
                    'auto_threshold_err': auto_threshold_err,
                }
            except Exception as e:
                print(f"  [Warning] 读取光谱数据失败: {e}")

        elif 'Plot_data' not in all_sheets:
            for sheet_name, df in all_sheets.items():
                cols_lower = [str(c).lower() for c in df.columns]
                if any('fluence' in c for c in cols_lower):
                    df_other = df.iloc[:, 0:3].copy()
                    df_other.columns = ['Fluence', 'Intensity', 'FWHM']
                    df_other = df_other.dropna()
                    break
            if df_other is None:
                try:
                    df_other = read_workbook(filepath, sheet=0, usecols=[0, 1, 2])
                    df_other.columns = ['Fluence', 'Intensity', 'FWHM']
                    df_other = df_other.dropna()
                except Exception:
                    pass

    if df_plot_data is None:
        print("❌ 找不到包含 'Plot_data' 页的文件。")
        sys.exit()

    return df_plot_data, df_fit, df_other, spectra_data

def _read_threshold_from_batch_csv(folder):
    """从同目录 Batch CSV 读取 Threshold/Turn_Point_1 和 Error"""
    batch_csv = os.path.join(folder, 'Threshold_Summary_Batch.csv')
    if not os.path.exists(batch_csv):
        return None, None
    try:
        df = read_table(batch_csv)
        return _read_threshold_from_dataframe(df)
    except Exception:
        return None, None


def _read_threshold_from_dataframe(df):
    """从单个 DataFrame 中识别 Threshold/Turn_Point_1 与 Error 列"""
    th, err = None, None
    cols_map = {_column_key(c): c for c in df.columns}
    for threshold_col in THRESHOLD_VALUE_COLUMNS:
        if threshold_col in cols_map:
            vals = df[cols_map[threshold_col]].dropna()
            if len(vals):
                try:
                    th = float(vals.iloc[0])
                    break
                except Exception:
                    pass
    if 'error' in cols_map:
        vals = df[cols_map['error']].dropna()
        if len(vals):
            try:
                err = float(vals.iloc[0])
            except Exception:
                pass
    for col in df.columns:
        if th is None and _is_threshold_value_column(col):
            vals = df[col].dropna()
            if len(vals):
                try:
                    th = float(vals.iloc[0])
                except Exception:
                    pass
        if err is None and _is_threshold_error_column(col):
            vals = df[col].dropna()
            if len(vals):
                try:
                    err = float(vals.iloc[0])
                except Exception:
                    pass
    return th, err


def detect_threshold_and_error(file_paths, folder):
    """从 Excel / Batch CSV 自动识别 Threshold 与 Error"""
    th, err = None, None
    for filepath in file_paths:
        try:
            all_sheets = read_workbook(filepath, sheet=None)
        except Exception:
            continue
        for df in all_sheets.values():
            sheet_th, sheet_err = _read_threshold_from_dataframe(df)
            if th is None and sheet_th is not None:
                th = sheet_th
            if err is None and sheet_err is not None:
                err = sheet_err
            if th is not None and err is not None:
                return th, err
    csv_th, csv_err = _read_threshold_from_batch_csv(folder)
    if th is None:
        th = csv_th
    if err is None:
        err = csv_err
    return th, err


def format_threshold_note(th_val, th_err, *, for_mpl=False):
    """根据阈值与误差生成默认注释文本"""
    eth = r"$E_{\mathrm{th}}$" if for_mpl else "E<sub>th</sub>"
    if th_val is None:
        return f"{eth}: ~ XX μJ/cm²"
    if th_err is not None and np.isfinite(th_err):
        return f"{eth}: {th_val:.2f} ± {th_err:.2f} μJ/cm²"
    return f"{eth}: {th_val:.2f} μJ/cm²"


def plotly_note_to_matplotlib(note):
    """将 Plotly 注释/标签中的 HTML 上下标转为 Matplotlib 数学文本。"""
    if not note:
        return note
    text = str(note)
    text = text.replace("E<sub>th</sub>", r"$E_{\mathrm{th}}$")
    text = re.sub(r"×\s*10<sup>([^<]+)</sup>", r"$\\times 10^{\1}$", text)
    text = re.sub(r"<sup>([^<]+)</sup>", r"$^{\1}$", text)
    text = re.sub(r"<sub>([^<]+)</sub>", r"$_{\1}$", text)
    return text

def prepare_data(df_plot_data, df_fit):
    """自动识别 Intensity 的数量级并进行归一化除法"""
    max_intensity = df_plot_data['Intensity'].max()
    exponent = 0
    if max_intensity > 0:
        exponent = int(math.floor(math.log10(max_intensity)))

    # 当最大值大于等于 100 时提取数量级
    if exponent >= 2:
        scale_factor = 10**exponent
        df_plot_data['Intensity'] = df_plot_data['Intensity'] / scale_factor
        if df_fit is not None and not df_fit.empty:
            df_fit['y'] = df_fit['y'] / scale_factor
            
    return df_plot_data, df_fit, exponent


def select_spectral_indices(pump_fluences, threshold_val):
    """根据阈值选取5个代表性 fluence 索引（与 ASE 脚本逻辑一致）"""
    n_total = len(pump_fluences)
    if threshold_val is not None and np.isfinite(threshold_val):
        idx_closest = int(np.argmin(np.abs(pump_fluences - threshold_val)))
    else:
        idx_closest = n_total // 2  # 无阈值时取中间

    idx_low   = min(2, n_total - 1)
    idx_below = max(idx_low + 1, idx_closest - 2)
    idx_th    = idx_closest
    idx_above = min(n_total - 2, idx_closest + 2)
    idx_max   = n_total - 1

    raw_indices = [idx_low, idx_below, idx_th, idx_above, idx_max]
    selected = sorted(list(set([i for i in raw_indices if 0 <= i < n_total])))
    return selected, idx_th


def selected_spectral_indices_from_config(spectra_data, config):
    """Return manually selected spectra indices from the left-panel checklist."""
    if spectra_data is None:
        return []
    n_total = len(spectra_data['pump_fluences'])
    raw_selected = (config or {}).get("spectra_selected_indices", [])
    selected = []
    for idx in raw_selected:
        try:
            idx_int = int(idx)
        except (TypeError, ValueError):
            continue
        if 0 <= idx_int < n_total and idx_int not in selected:
            selected.append(idx_int)
    return selected


def build_spectra_selection_options(spectra_data, default_indices):
    """Build checklist labels from the measured pump fluence values."""
    if spectra_data is None:
        return [], []
    pump_fluences = spectra_data['pump_fluences']
    options = [
        {"label": f"{energy:g} μJ/cm²", "value": int(idx)}
        for idx, energy in enumerate(pump_fluences)
    ]
    defaults = [int(idx) for idx in default_indices if 0 <= idx < len(pump_fluences)]
    return options, defaults


# =====================================================================
# 1. Plotly 交互式绘图函数
# =====================================================================


# =====================================================================
# 1b. 光谱演化图 —— Plotly
# =====================================================================
def build_plotly_spectra(data_list, config):
    """构建光谱随能量变化的 Plotly 交互图（格式与阈值图一致）"""
    spectra_data = data_list[3]
    
    if config is None:
        config = {}
    if spectra_data is None:
        fig = go.Figure()
        fig.add_annotation(text="未找到光谱数据（需要 Analysed_ultra 文件）",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=16, color="red"))
        return fig

    wavelengths   = spectra_data['wavelengths']
    pump_fluences = spectra_data['pump_fluences']
    spec_matrix   = spectra_data['spec_matrix']

    selected = selected_spectral_indices_from_config(spectra_data, config)
    if not selected:
        fig = go.Figure()
        fig.add_annotation(text="请在左侧勾选要显示的光谱数据",
                           xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(size=16, color="gray"))
        fig.update_layout(
            font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
            xaxis=dict(title="Wavelength (nm)", showline=True, linewidth=1.5, linecolor='black', mirror=True),
            yaxis=dict(title="Normalized Intensity (arb. units)", showline=True, linewidth=1.5, linecolor='black', mirror=True),
            plot_bgcolor='white',
            paper_bgcolor='white',
            uirevision="stable_spectra",
            margin=dict(l=90, r=90, t=40, b=90),
        )
        return fig

    palette_id = config.get("spectra_palette", "viridis")
    colors_rgba = sample_spectra_palette(palette_id, len(selected), as_rgba=True)

    fig = go.Figure()
    for c_idx, idx in enumerate(selected):
        energy   = pump_fluences[idx]
        spectrum = spec_matrix[:, idx]
        fig.add_trace(go.Scatter(
            x=wavelengths, y=spectrum,
            mode='lines',
            name=f"{energy:.2f} μJ/cm²",
            line=dict(color=colors_rgba[c_idx], width=1.8, dash='solid'),
        ))

    xaxis_dict = dict(
        title="Wavelength (nm)",
        showline=True, linewidth=1.5, linecolor='black', mirror=True,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
    )
    if config.get("xrange_spec") and config["xrange_spec"][0] is not None:
        xaxis_dict["range"] = config["xrange_spec"]

    yaxis_dict = dict(
        title="Normalized Intensity (arb. units)",
        showline=True, linewidth=1.5, linecolor='black', mirror=True,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
    )
    if config.get("yrange_spec") and config["yrange_spec"][0] is not None:
        yaxis_dict["range"] = config["yrange_spec"]

    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=xaxis_dict,
        yaxis=yaxis_dict,
        plot_bgcolor='white',
        paper_bgcolor='white',
        uirevision="stable_spectra",
        margin=dict(l=90, r=90, t=40, b=90),
        title=None,
        legend=dict(
            title="E<sub>in</sub>",
            x=config.get("legend_pos_spec", {}).get("x", 0.97),
            y=config.get("legend_pos_spec", {}).get("y", 0.97),
            xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.8)", borderwidth=0,
        ),
    )
    return fig

def build_plotly_figure(data_list, config):
    df_plot_data, df_fit, exponent = data_list[:3]
    fig = go.Figure()
    
    # 获取颜色和线宽配置
    c_int = config["colors"].get("intensity", "#000000")
    c_fit = config["colors"].get("fit", "#FF0000")
    c_fwhm = config["colors"].get("fwhm", "#0000FF")
    
    # Plotly 中散点的 marker size
    w_int = config["widths"].get("intensity", 1.0)
    w_fit = config["widths"].get("fit", 2.0)
    w_fwhm = config["widths"].get("fwhm", 1.0)
    
    # 绘制 Intensity 散点 (左 Y 轴)
    fig.add_trace(go.Scatter(
        x=df_plot_data['Fluence'], y=df_plot_data['Intensity'],
        mode='markers', name='Intensity',
        marker=dict(color=c_int, size=10, line=dict(width=w_int, color=c_int)),
        yaxis='y'
    ))
    
    # 绘制 Fit 曲线 (左 Y 轴)
    if df_fit is not None and not df_fit.empty:
        fig.add_trace(go.Scatter(
            x=df_fit['x'], y=df_fit['y'],
            mode='lines', name='Fit',
            line=dict(color=c_fit, width=w_fit),
            yaxis='y'
        ))
        
    # 绘制 FWHM 散点 (右 Y 轴)
    fig.add_trace(go.Scatter(
        x=df_plot_data['Fluence'], y=df_plot_data['FWHM'],
        mode='lines+markers', name='FWHM',
        marker=dict(color=c_fwhm, size=10, symbol='diamond', line=dict(width=w_fwhm, color=c_fwhm)),
        line=dict(width=w_fwhm, color=c_fwhm),
        yaxis='y2'
    ))
    
    # Y 轴标签（根据提取的数量级动态调整）
    y_label_str = f"Integrated Intensity (a.u. × 10<sup>{exponent}</sup>)" if exponent >= 2 else "Integrated Intensity (a.u.)"
    
    xaxis_dict = dict(
        title="Incident Pump Fluence (μJ/cm<sup>2</sup>)",
        showline=True, linewidth=1.5, linecolor='black', mirror=True,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
    )
    if config.get("xrange") and config["xrange"][0] is not None:
        xaxis_dict["range"] = config["xrange"]
        
    yaxis_dict = dict(
        title=y_label_str,
        showline=True, linewidth=1.5, linecolor='black', mirror=False,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
    )
    if config.get("yrange") and config["yrange"][0] is not None:
        yaxis_dict["range"] = config["yrange"]
    
    yaxis2_dict = dict(
        title="FWHM (nm)",
        overlaying='y',
        side='right',
        showline=True, linewidth=1.5, linecolor='black', mirror=False,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
        # 设置 FWHM 的合理默认范围，避免触底
        rangemode='tozero'
    )
    if config.get("yrange2") and config["yrange2"][0] is not None:
        yaxis2_dict["range"] = config["yrange2"]
    
    # 设置布局参数
    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=xaxis_dict,
        yaxis=yaxis_dict,
        yaxis2=yaxis2_dict,
        plot_bgcolor='white',
        paper_bgcolor='white',
        uirevision="stable",
        margin=dict(l=90, r=90, t=40, b=90), # 为右侧 Y 轴预留 margin
        title=None,
        legend=dict(
            x=config["legend_pos"].get("x", 0.97),
            y=config["legend_pos"].get("y", 0.97),
            xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="gray", borderwidth=0
        )
    )
    
    # 绘制附加文本注释
    text_val = config["text_params"].get("note", "")
    if text_val:
        fig.add_annotation(
            x=config["text_pos"].get("x", 0.05), 
            y=config["text_pos"].get("y", 0.95),
            xref="paper", yref="paper",
            text=text_val, showarrow=False,
            font=dict(size=GLOBAL_FONT_SIZE, color="black"),
            xanchor="left", yanchor="top"
        )
        
    return fig


# =====================================================================
# 2. Matplotlib 静态图导出函数
# =====================================================================


# =====================================================================
# 2b. 光谱演化图 —— Matplotlib 静态导出
# =====================================================================
def plot_matplotlib_spectra_static(spectra_data, save_dir, config=None):
    """将光谱演化图导出为 PDF/SVG"""
    if spectra_data is None:
        print("⚠️  无光谱数据，跳过光谱图导出。")
        return

    wavelengths   = spectra_data['wavelengths']
    pump_fluences = spectra_data['pump_fluences']
    spec_matrix   = spectra_data['spec_matrix']  # (n_wave, n_pump)

    selected = selected_spectral_indices_from_config(spectra_data, config or {})
    if not selected:
        print("⚠️  未选择光谱数据，跳过光谱图导出。")
        return

    palette_id = "viridis"
    if config is not None:
        palette_id = config.get("spectra_palette", palette_id)
    colors = sample_spectra_palette(palette_id, len(selected))

    setup_matplotlib_style()
    fig, ax = create_matched_fig_ax()

    for c_idx, idx in enumerate(selected):
        energy   = pump_fluences[idx]
        spectrum = spec_matrix[:, idx]
        ax.plot(wavelengths, spectrum, color=colors[c_idx],
                linewidth=1.5, linestyle='-',
                label=f"{energy:.2f} $\\mu$J/cm$^2$")

    ax.set_xlabel("Wavelength (nm)")
    ax.set_ylabel("Normalized Intensity (arb. units)")
    ax.spines['top'].set_visible(True)
    ax.spines['right'].set_visible(True)
    ax.tick_params(top=False, right=True, labeltop=False)
    ax.legend(title=r"$E_{\mathrm{in}}$", loc='upper right', frameon=False,
              fontsize=GLOBAL_FONT_SIZE)

    pdf_path = os.path.join(save_dir, "Spectral_Evolution.pdf")
    svg_path = os.path.join(save_dir, "Spectral_Evolution.svg")
    fig.savefig(pdf_path, format="pdf", transparent=True)
    fig.savefig(svg_path, format="svg", transparent=True)
    print(f"✅ 已保存光谱演化图: {pdf_path}")
    print(f"✅ 已保存光谱演化图: {svg_path}")
    plt.close(fig)

def plot_matplotlib_static(data_list, config, save_dir):
    df_plot_data, df_fit, exponent = data_list[:3]
    
    setup_matplotlib_style()
    fig, ax1 = create_matched_fig_ax()
    
    # 调整右侧 Margin，给双 Y 轴留出空间
    set_matched_twin_y_right_margin(fig)
    
    c_int = config["colors"].get("intensity", "#000000")
    c_fit = config["colors"].get("fit", "#FF0000")
    c_fwhm = config["colors"].get("fwhm", "#0000FF")
    
    w_fit = config["widths"].get("fit", 2.0)
    w_fwhm = config["widths"].get("fwhm", 1.0)
    
    # 绘制左 Y 轴
    ax1.scatter(df_plot_data['Fluence'], df_plot_data['Intensity'], 
                color=c_int, s=80, marker='o', label='Intensity', edgecolors='none', zorder=3)
                
    if df_fit is not None and not df_fit.empty:
        ax1.plot(df_fit['x'], df_fit['y'], color=c_fit, linewidth=w_fit, label='Fit', zorder=2)
        
    y_label_str = f"Integrated Intensity (a.u. $\\times 10^{{{exponent}}}$)" if exponent >= 2 else "Integrated Intensity (a.u.)"
    ax1.set_xlabel(r"Incident Pump Fluence ($\mu$J/cm$^2$)")
    ax1.set_ylabel(y_label_str)
    
    # 应用用户在 Plotly 中设定的坐标轴范围（如果存在）
    if config.get("xrange") and config["xrange"][0] is not None:
        ax1.set_xlim(*config["xrange"])
    if config.get("yrange") and config["yrange"][0] is not None:
        ax1.set_ylim(*config["yrange"])
        
    # 显式去除上边框的 tick（Matplotlib 默认顶部已通过 PlotUtils 关掉 tick，这里为保险起见再设置一次，并确保有上边框）
    ax1.spines['top'].set_visible(True)
    ax1.tick_params(top=False)
        
    # 绘制右 Y 轴
    ax2 = ax1.twinx()
    apply_matplotlib_export_axes_style(ax1, ax2)
    ax2.plot(df_plot_data['Fluence'], df_plot_data['FWHM'], 
             color=c_fwhm, marker='D', markersize=9, linewidth=w_fwhm, label='FWHM', zorder=4)
    ax2.set_ylabel("FWHM (nm)")
    ax2.tick_params(axis='y', right=True, direction='in')
    ax2.set_ylim(bottom=0)  # FWHM 默认从 0 开始以匹配 Plotly
    
    if config.get("yrange2") and config["yrange2"][0] is not None:
        ax2.set_ylim(*config["yrange2"])
    
    # 合并图例
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    
    leg_x = config["legend_pos"].get("x", 0.97)
    leg_y = config["legend_pos"].get("y", 0.97)
    # Matplotlib 的图例位置 (bbox_to_anchor)
    ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper right', 
               bbox_to_anchor=(leg_x, leg_y), frameon=False)
    
    # 绘制文本注释
    text_val = config["text_params"].get("note", "")
    if text_val:
        ax1.text(config["text_pos"].get("x", 0.05), config["text_pos"].get("y", 0.95), 
                 plotly_note_to_matplotlib(text_val), transform=ax1.transAxes, ha='left', va='top',
                 fontsize=GLOBAL_FONT_SIZE)
                 
    # 保存文件
    pdf_path = os.path.join(save_dir, "Threshold_graph.pdf")
    svg_path = os.path.join(save_dir, "Threshold_graph.svg")
    
    fig.savefig(pdf_path, format="pdf", transparent=True)
    fig.savefig(svg_path, format="svg", transparent=True)
    
    print(f"✅ 已保存静态图: {pdf_path}")
    print(f"✅ 已保存静态图: {svg_path}")
    plt.close(fig)


# =====================================================================
# 3. 主程序入口
# =====================================================================
def main():
    print("启动 Threshold 交互式绘图...")
    folder_path = select_folder("请选择包含分析数据的文件夹（包含 Plot_data 和 Analysed_ultra）")
    file_paths = find_xlsx_in_folder(folder_path)
    
    # 1. 提取数据（含光谱数据）
    df_plot_data, df_fit, df_other, spectra_data = identify_and_load_data(file_paths)
    
    # 查找阈值和误差
    th_val, th_err = detect_threshold_and_error(file_paths, folder_path)
    default_note = format_threshold_note(th_val, th_err)
        
    # 2. 预处理数据（数量级除法）
    df_plot_data, df_fit, exponent = prepare_data(df_plot_data, df_fit)
    data_list = [df_plot_data, df_fit, exponent, spectra_data, th_val]
    
    # 3. 初始化配置和保存路径
    config_path = os.path.join(folder_path, "threshold_plot_config.json")
    
    # 4. 实例化动态探索器（双图支持）
    explorer = DynamicPlotExplorer(
        data_list=data_list,
        config_path=config_path,
        build_plotly_func=build_plotly_figure,
        build_plotly_func2=build_plotly_spectra if spectra_data else None,
        plot_mpl_func=plot_matplotlib_static,
        show_offset=False
    )
    
    # 5. 注册控制台 UI 参数
    explorer.add_curve_style("Intensity (Scatter)", "intensity", "#000000", default_width=1.0)
    if df_fit is not None and not df_fit.empty:
        explorer.add_curve_style("Fit Line", "fit", "#FF0000", default_width=2.0)
    explorer.add_curve_style("FWHM (Scatter)", "fwhm", "#0000FF", default_width=1.0)
    if spectra_data:
        explorer.add_spectra_palette_selector(SPECTRA_COLOR_PALETTES, default="viridis")
        default_spectra_indices, _ = select_spectral_indices(
            spectra_data['pump_fluences'], th_val
        )
        spectra_options, default_spectra_indices = build_spectra_selection_options(
            spectra_data, default_spectra_indices
        )
        explorer.add_spectra_selection(spectra_options, default_spectra_indices)
    
    explorer.add_text_param("文本注释", "note", default_note)
    if th_val is not None:
        explorer.config["text_params"]["note"] = default_note
    
    # 6. 运行并等待确认
    final_config = explorer.run()
    
    # 7. 静态导出：光谱图使用左侧面板中当前勾选的数据集
    plot_matplotlib_static(data_list, final_config, folder_path)
    plot_matplotlib_spectra_static(spectra_data, folder_path, final_config)

    print("✨ 程序执行结束！")

if __name__ == "__main__":
    main()
