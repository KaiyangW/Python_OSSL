import os
import re
import sys
import math
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_workbook

from PlotUtils import (
    setup_matplotlib_style,
    create_matched_fig_ax,
    DynamicPlotExplorer,
    GLOBAL_FONT_SIZE,
    SPECTRA_COLOR_PALETTES,
    sample_spectra_palette,
)
from ASE_graph import (
    format_threshold_note,
    plotly_note_to_matplotlib,
    _read_threshold_from_dataframe,
    selected_spectral_indices_from_config,
    build_spectra_selection_options,
    select_spectral_indices,
    select_folder,
)


def find_laser_xlsx_files(folder):
    """在文件夹中自动查找 ManualFit_Result 与 DFB_Analysed 的 xlsx 文件。"""
    files = [
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith('.xlsx') and not f.startswith('~')
    ]
    manual = next((f for f in files if "ManualFit_Result" in os.path.basename(f)), None)
    analysed = next((f for f in files if "DFB_Analysed" in os.path.basename(f)), None)
    if not manual:
        print(f"❌ 文件夹中未找到包含 'ManualFit_Result' 的 xlsx：{folder}")
        sys.exit()
    if not analysed:
        print(f"❌ 文件夹中未找到包含 'DFB_Analysed' 的 xlsx：{folder}")
        sys.exit()
    print(f"📂 ManualFit: {os.path.basename(manual)}")
    print(f"📂 Analysed:  {os.path.basename(analysed)}")
    return [manual, analysed]


def identify_and_load_data(file_paths):
    """从多个 Excel 中识别并读取 Plot_data 及 Fit_Line"""
    df_fit = None
    df_plot_data = None

    for filepath in file_paths:
        try:
            all_sheets = read_workbook(filepath, sheet=None)
        except Exception:
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

    return df_plot_data, df_fit


def default_left_y_label(exponent, *, for_mpl=False):
    if exponent >= 2:
        if for_mpl:
            return f"Integrated Intensity (a.u. $\\times 10^{{{exponent}}}$)"
        return f"Integrated Intensity (a.u. × 10<sup>{exponent}</sup>)"
    return "Integrated Intensity (a.u.)"


def resolve_left_y_label(config, exponent, *, for_mpl=False):
    custom = (config.get("text_params") or {}).get("y_label_left", "")
    if custom and str(custom).strip():
        label = str(custom).strip()
        return plotly_note_to_matplotlib(label) if for_mpl else label
    return default_left_y_label(exponent, for_mpl=for_mpl)


FLUENCE_COL = "Incident Pump Fluence (uJ/cm2)"
SPEC_Y_LABEL = "Normalized Intensity + Offset"
SPEC_OFFSET_STEP = 1.2


def auto_spec_offsets(selected_indices, step=SPEC_OFFSET_STEP):
    """按能量从小到大顺序自动分配 y 偏移，使光谱曲线叠开显示。"""
    return {idx: rank * step for rank, idx in enumerate(selected_indices)}


def spectral_indices_sorted_by_energy(spectra_data, config):
    """返回勾选的光谱索引，按入射能量从小到大排序。"""
    selected = selected_spectral_indices_from_config(spectra_data, config)
    if not selected:
        return selected
    pump_fluences = spectra_data['pump_fluences']
    return sorted(selected, key=lambda idx: pump_fluences[idx])


def spectrum_y_display(spec_matrix, idx, selected_indices, step=SPEC_OFFSET_STEP):
    """每条光谱先 (0,1) 归一化（已在 spec_matrix），再叠加自动 y offset。"""
    return spec_matrix[:, idx] + auto_spec_offsets(selected_indices, step).get(idx, 0.0)


def resolve_spec_y_label(config, *, for_mpl=False):
    custom = (config.get("text_params") or {}).get("y_label_spec", "")
    if custom and str(custom).strip():
        label = str(custom).strip()
        return plotly_note_to_matplotlib(label) if for_mpl else label
    return SPEC_Y_LABEL


def parse_fluence_column_name(name):
    """从 Raw Spec 列名解析能量；兼容 Excel 重复列名后缀（如 1.92 → 1.92.1）。"""
    s = str(name).strip()
    try:
        return float(s)
    except ValueError:
        pass
    m = re.match(r"^(-?\d+\.\d{2})", s)
    if m:
        return float(m.group(1))
    m = re.match(r"^(-?\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1))
    raise ValueError(f"无法从列名解析能量: {name!r}")


def load_spectra_from_analysed(analysed_file):
    """读取 Raw Spec 与 Metrics；能量以 Metrics 为准（与导出脚本列顺序一致）。"""
    df_raw = read_workbook(analysed_file, sheet='Raw Spec (nm)', index_col=0)
    wavelengths = df_raw.index.values.astype(float)

    df_metrics = read_workbook(analysed_file, sheet='Metrics')
    if FLUENCE_COL in df_metrics.columns:
        pump_fluences = df_metrics[FLUENCE_COL].values.astype(float)
    else:
        pump_fluences = None

    n_cols = len(df_raw.columns)
    if pump_fluences is None or len(pump_fluences) != n_cols:
        if pump_fluences is not None and len(pump_fluences) != n_cols:
            print(
                f"⚠️  Metrics 能量点数 ({len(pump_fluences)}) 与光谱列数 ({n_cols}) 不一致，"
                "改从列名解析（含重复列名修正）。"
            )
        pump_fluences = np.array([parse_fluence_column_name(c) for c in df_raw.columns])
    elif any(re.match(r"^-?\d+\.\d+\.\d+$", str(c)) for c in df_raw.columns):
        print("⚠️  Raw Spec 列名含 Excel 重复后缀（如 1.92.1），已用 Metrics 表中的能量值对齐。")

    spec_cols = []
    for col in df_raw.columns:
        raw_y = df_raw[col].values.astype(float)
        max_y = np.max(raw_y)
        spec_cols.append(raw_y / max_y if max_y > 0 else raw_y)
    spec_matrix = np.column_stack(spec_cols) if spec_cols else np.empty((len(wavelengths), 0))

    return {
        'wavelengths': wavelengths,
        'pump_fluences': pump_fluences,
        'spec_matrix': spec_matrix,
    }


def load_laser_data(file_paths):
    manual_fit_file = next((f for f in file_paths if "ManualFit_Result" in f), None)
    analysed_file = next((f for f in file_paths if "DFB_Analysed" in f), None)

    if not manual_fit_file or not analysed_file:
        print("❌ 未同时找到 'ManualFit_Result' 和 'DFB_Analysed' 文件！")
        sys.exit()

    df_plot_data, df_fit = identify_and_load_data([manual_fit_file])
    if df_plot_data is None:
        print("❌ 在 ManualFit_Result 中找不到 'Plot_data' 页。")
        sys.exit()

    df_params = read_workbook(manual_fit_file, sheet='Parameters')
    th_val, th_err = _read_threshold_from_dataframe(df_params)
    if th_val is None and 'Threshold' in df_params.columns:
        try:
            th_val = float(df_params['Threshold'].iloc[0])
        except Exception:
            pass

    spectra_data = load_spectra_from_analysed(analysed_file)

    max_intensity = df_plot_data['Intensity'].max()
    exponent = 0
    if max_intensity > 0:
        exponent = int(math.floor(math.log10(max_intensity)))

    if exponent >= 2:
        scale_factor = 10**exponent
        df_plot_data['Intensity'] = df_plot_data['Intensity'] / scale_factor
        if df_fit is not None and not df_fit.empty:
            df_fit['y'] = df_fit['y'] / scale_factor

    return df_plot_data, df_fit, exponent, spectra_data, th_val, th_err


def _spec_axis_style_plotly():
    """图2：显示 top/right 边框，不在 top/right 显示刻度。"""
    return dict(
        showline=True, linewidth=1.5, linecolor='black', mirror=True,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
    )


def _apply_spec_axis_ticks(fig):
    fig.update_xaxes(ticklabelposition='outside bottom')
    fig.update_yaxes(ticklabelposition='outside left')


def _apply_spec_spines_mpl(ax):
    ax.spines['top'].set_visible(True)
    ax.spines['right'].set_visible(True)
    ax.tick_params(top=False, right=False, labeltop=False, labelright=False)


# =====================================================================
# 1. Plotly — 图1：Threshold
# =====================================================================
def build_plotly_threshold(data_list, config):
    df_plot_data, df_fit, exponent = data_list[:3]
    fig = go.Figure()

    c_int = config["colors"].get("intensity", "#000000")
    c_fit = config["colors"].get("fit", "#FF0000")
    c_fwhm = config["colors"].get("fwhm", "#0000FF")
    w_int = config["widths"].get("intensity", 1.0)
    w_fit = config["widths"].get("fit", 2.0)
    w_fwhm = config["widths"].get("fwhm", 1.0)

    fig.add_trace(go.Scatter(
        x=df_plot_data['Fluence'], y=df_plot_data['Intensity'],
        mode='markers', name='Counts',
        marker=dict(color=c_int, size=10, line=dict(width=w_int, color=c_int)),
        yaxis='y',
    ))

    if df_fit is not None and not df_fit.empty:
        fig.add_trace(go.Scatter(
            x=df_fit['x'], y=df_fit['y'],
            mode='lines', name='Fit',
            line=dict(color=c_fit, width=w_fit),
            yaxis='y',
        ))

    fig.add_trace(go.Scatter(
        x=df_plot_data['Fluence'], y=df_plot_data['FWHM'],
        mode='lines+markers', name='FWHM',
        marker=dict(color=c_fwhm, size=10, symbol='circle', line=dict(width=w_fwhm, color=c_fwhm)),
        line=dict(width=w_fwhm, color=c_fwhm),
        yaxis='y2',
    ))

    y_label_str = resolve_left_y_label(config, exponent)

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
        overlaying='y', side='right',
        showline=True, linewidth=1.5, linecolor='black', mirror=False,
        ticks='inside', tickwidth=1.5, tickcolor='black', ticklen=8,
        rangemode='tozero',
    )
    if config.get("yrange2") and config["yrange2"][0] is not None:
        yaxis2_dict["range"] = config["yrange2"]

    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=xaxis_dict,
        yaxis=yaxis_dict,
        yaxis2=yaxis2_dict,
        plot_bgcolor='white',
        paper_bgcolor='white',
        uirevision="stable",
        margin=dict(l=90, r=90, t=40, b=90),
        legend=dict(
            x=config["legend_pos"].get("x", 0.97),
            y=config["legend_pos"].get("y", 0.97),
            xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.8)", bordercolor="gray", borderwidth=0,
        ),
    )

    text_val = config["text_params"].get("note", "")
    if text_val:
        fig.add_annotation(
            x=config["text_pos"].get("x", 0.05),
            y=config["text_pos"].get("y", 0.95),
            xref="paper", yref="paper",
            text=text_val, showarrow=False,
            font=dict(size=GLOBAL_FONT_SIZE, color="black"),
            xanchor="left", yanchor="top",
        )

    return fig


# =====================================================================
# 1b. Plotly — 图2：Spectra（勾选 + 配色系列，与 Threshold_graph 一致）
# =====================================================================
def build_plotly_spectra(data_list, config):
    spectra_data = data_list[3]
    if spectra_data is None or spectra_data["spec_matrix"].size == 0:
        fig = go.Figure()
        fig.add_annotation(
            text="未找到光谱数据", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False, font=dict(size=16, color="red"),
        )
        return fig

    wavelengths = spectra_data['wavelengths']
    pump_fluences = spectra_data['pump_fluences']
    spec_matrix = spectra_data['spec_matrix']

    selected = spectral_indices_sorted_by_energy(spectra_data, config)
    if not selected:
        fig = go.Figure()
        fig.add_annotation(
            text="请在左侧勾选要显示的光谱数据",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=16, color="gray"),
        )
        fig.update_layout(
            font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
            plot_bgcolor='white', paper_bgcolor='white',
            margin=dict(l=90, r=90, t=40, b=90),
        )
        return fig

    palette_id = config.get("spectra_palette", "viridis")
    colors_rgba = sample_spectra_palette(palette_id, len(selected), as_rgba=True)
    w_spec = config["widths"].get("spectra", 1.8)
    spec_y_label = resolve_spec_y_label(config)

    fig = go.Figure()
    for c_idx, idx in enumerate(selected):
        energy = pump_fluences[idx]
        fig.add_trace(go.Scatter(
            x=wavelengths, y=spectrum_y_display(spec_matrix, idx, selected),
            mode='lines',
            name=f"{energy:.2f} μJ/cm²",
            line=dict(color=colors_rgba[c_idx], width=w_spec, dash='solid'),
        ))

    axis_kw = _spec_axis_style_plotly()
    xaxis_dict = {**axis_kw, "title": "Wavelength (nm)"}
    yaxis_dict = {**axis_kw, "title": spec_y_label}
    if config.get("xrange_spec") and config["xrange_spec"][0] is not None:
        xaxis_dict["range"] = config["xrange_spec"]
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
        legend=dict(
            title="E<sub>in</sub>",
            x=config.get("legend_pos_spec", config["legend_pos"]).get("x", 0.97),
            y=config.get("legend_pos_spec", config["legend_pos"]).get("y", 0.97),
            xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.8)", borderwidth=0,
        ),
    )
    _apply_spec_axis_ticks(fig)
    return fig


# =====================================================================
# 2. Matplotlib 静态导出
# =====================================================================
def plot_matplotlib_static(data_list, config, save_dir):
    df_plot_data, df_fit, exponent, spectra_data = data_list[:4]
    setup_matplotlib_style()

    # —— 图1：Threshold ——
    fig1, ax1 = create_matched_fig_ax(width_px=800, height_px=600, dpi=300)
    fig1.subplots_adjust(right=1.0 - (90 / 800))

    c_int = config["colors"].get("intensity", "#000000")
    c_fit = config["colors"].get("fit", "#FF0000")
    c_fwhm = config["colors"].get("fwhm", "#0000FF")
    w_fit = config["widths"].get("fit", 2.0)
    w_fwhm = config["widths"].get("fwhm", 1.0)

    ax1.scatter(
        df_plot_data['Fluence'], df_plot_data['Intensity'],
        color=c_int, s=80, marker='o', label='Counts', edgecolors='none', zorder=3,
    )
    if df_fit is not None and not df_fit.empty:
        ax1.plot(df_fit['x'], df_fit['y'], color=c_fit, linewidth=w_fit, label='Fit', zorder=2)

    y_label_str = resolve_left_y_label(config, exponent, for_mpl=True)
    ax1.set_xlabel(r"Incident Pump Fluence ($\mu$J/cm$^2$)")
    ax1.set_ylabel(y_label_str)

    if config.get("xrange") and config["xrange"][0] is not None:
        ax1.set_xlim(*config["xrange"])
    if config.get("yrange") and config["yrange"][0] is not None:
        ax1.set_ylim(*config["yrange"])

    ax1.spines['top'].set_visible(True)
    ax1.tick_params(top=False)

    ax2 = ax1.twinx()
    ax2.plot(
        df_plot_data['Fluence'], df_plot_data['FWHM'],
        color=c_fwhm, marker='o', markersize=9, linewidth=w_fwhm, label='FWHM', zorder=4,
    )
    ax2.set_ylabel("FWHM (nm)")
    ax2.tick_params(axis='y', right=True, direction='in')
    ax2.set_ylim(bottom=0)
    if config.get("yrange2") and config["yrange2"][0] is not None:
        ax2.set_ylim(*config["yrange2"])

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    leg_x = config["legend_pos"].get("x", 0.97)
    leg_y = config["legend_pos"].get("y", 0.97)
    ax1.legend(
        lines1 + lines2, labels1 + labels2, loc='upper right',
        bbox_to_anchor=(leg_x, leg_y), frameon=False,
    )

    text_val = config["text_params"].get("note", "")
    if text_val:
        ax1.text(
            config["text_pos"].get("x", 0.05), config["text_pos"].get("y", 0.95),
            plotly_note_to_matplotlib(text_val),
            transform=ax1.transAxes, ha='left', va='top',
            fontsize=GLOBAL_FONT_SIZE,
        )

    fig1.savefig(os.path.join(save_dir, "Laser_Threshold.pdf"), format="pdf", transparent=True)
    fig1.savefig(os.path.join(save_dir, "Laser_Threshold.svg"), format="svg", transparent=True)
    plt.close(fig1)

    # —— 图2：Spectra ——
    selected = spectral_indices_sorted_by_energy(spectra_data, config)
    if not selected:
        print("⚠️  未选择光谱数据，跳过光谱图导出。")
        return

    wavelengths = spectra_data['wavelengths']
    pump_fluences = spectra_data['pump_fluences']
    spec_matrix = spectra_data['spec_matrix']
    palette_id = config.get("spectra_palette", "viridis")
    colors = sample_spectra_palette(palette_id, len(selected))
    w_spec = config["widths"].get("spectra", 1.8)
    spec_y_label = resolve_spec_y_label(config, for_mpl=True)

    fig2, ax_s = create_matched_fig_ax(width_px=800, height_px=600, dpi=300)
    for c_idx, idx in enumerate(selected):
        energy = pump_fluences[idx]
        ax_s.plot(
            wavelengths, spectrum_y_display(spec_matrix, idx, selected),
            color=colors[c_idx], linewidth=w_spec, linestyle='-',
            label=f"{energy:.2f} $\\mu$J/cm$^2$",
        )

    ax_s.set_xlabel("Wavelength (nm)")
    ax_s.set_ylabel(spec_y_label)
    _apply_spec_spines_mpl(ax_s)
    ax_s.legend(
        title=r"$E_{\mathrm{in}}$", loc='upper right', frameon=False,
        fontsize=GLOBAL_FONT_SIZE,
    )

    fig2.savefig(os.path.join(save_dir, "Laser_Spectra.pdf"), format="pdf", transparent=True)
    fig2.savefig(os.path.join(save_dir, "Laser_Spectra.svg"), format="svg", transparent=True)
    plt.close(fig2)

    print("✅ 已保存静态图: Laser_Threshold.pdf / svg")
    print("✅ 已保存静态图: Laser_Spectra.pdf / svg")


# =====================================================================
# 3. 主程序
# =====================================================================
def main():
    print("启动 Laser 交互式绘图...")
    folder_path = select_folder("请选择包含 ManualFit_Result 和 DFB_Analysed 的文件夹")
    file_paths = find_laser_xlsx_files(folder_path)

    df_plot_data, df_fit, exponent, spectra_data, th_val, th_err = load_laser_data(file_paths)
    data_list = [df_plot_data, df_fit, exponent, spectra_data, th_val, th_err]

    selected_dir = folder_path
    config_path = os.path.join(selected_dir, "laser_plot_config.json")

    default_note = format_threshold_note(th_val, th_err)
    default_y_label = default_left_y_label(exponent)

    explorer = DynamicPlotExplorer(
        data_list=data_list,
        config_path=config_path,
        build_plotly_func=build_plotly_threshold,
        build_plotly_func2=build_plotly_spectra,
        plot_mpl_func=plot_matplotlib_static,
        graph_height=600,
        graph2_height=600,
        show_offset=False,
    )

    explorer.add_curve_style("Counts (Scatter)", "intensity", "#000000", default_width=1.0)
    if df_fit is not None and not df_fit.empty:
        explorer.add_curve_style("Fit Line", "fit", "#FF0000", default_width=2.0)
    explorer.add_curve_style("FWHM", "fwhm", "#0000FF", default_width=1.0)
    explorer.add_curve_style("Spectra Line", "spectra", "#000000", default_width=1.8)

    explorer.add_spectra_palette_selector(SPECTRA_COLOR_PALETTES, default="viridis")
    default_spectra_indices, _ = select_spectral_indices(spectra_data['pump_fluences'], th_val)
    spectra_options, default_spectra_indices = build_spectra_selection_options(
        spectra_data, default_spectra_indices,
    )
    explorer.add_spectra_selection(spectra_options, default_spectra_indices)

    explorer.add_text_param("文本注释 (E<sub>th</sub>)", "note", default_note)
    explorer.add_text_param("左 Y 轴标题", "y_label_left", default_y_label)
    explorer.add_text_param("图2 Y 轴标题", "y_label_spec", SPEC_Y_LABEL)
    if th_val is not None:
        explorer.config["text_params"]["note"] = default_note

    final_config = explorer.run()
    plot_matplotlib_static(data_list, final_config, selected_dir)
    print("✨ 程序执行结束！")


if __name__ == "__main__":
    main()
