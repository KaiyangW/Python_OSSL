import os
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import matplotlib.ticker as ticker
import matplotlib
matplotlib.use("Agg")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_workbook

import PlotUtils as pu

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_COLOR_CONFIG = os.path.join(_SCRIPT_DIR, "_decay_color_config.json")

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

TIME_COLS = {"Full_Time (ns)", "Fit_Time (ns)"}
TIME_UNIT_CHOICES = ["auto", "ns", "μs", "ms", "s"]
TIME_UNIT_DIVISORS = {"ns": 1, "μs": 1e3, "ms": 1e6, "s": 1e9}
EXTRA_COLORS = ["#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"]

# ══════════════════════════════════════════════════════════════════════════════
# 1. 数据读取
# ══════════════════════════════════════════════════════════════════════════════
def _find_col(prefix, columns):
    return next((c for c in columns if str(c).startswith(prefix)), None)

def _numeric_y_columns(df):
    cols = []
    for c in df.columns:
        if c in TIME_COLS:
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols

def _pick_default(options, preferred):
    for name in preferred:
        if name in options:
            return name
    return options[0] if options else None

def _resolve_time_unit(max_ns, unit_choice):
    if unit_choice and unit_choice != "auto":
        return TIME_UNIT_DIVISORS[unit_choice], unit_choice
    if max_ns >= 1e9:
        return 1e9, "s"
    if max_ns >= 1e6:
        return 1e6, "ms"
    if max_ns >= 1e3:
        return 1e3, "μs"
    return 1, "ns"

def _series_from_df(df, time_col, y_col, t_max_raw=None):
    if time_col not in df.columns or y_col not in df.columns:
        return None
    out = df[[time_col, y_col]].dropna()
    if out.empty:
        return None
    if t_max_raw is not None:
        out = out[out[time_col] <= t_max_raw]
    return out if not out.empty else None

def _resolve_plot_config(config, data_list):
    tp = config.get("text_params", {})
    y_data = tp.get("y_data") or data_list[0].get("default_y_data")
    y_irf = tp.get("y_irf") or data_list[0].get("default_y_irf")
    y_fit = tp.get("y_fit") or data_list[0].get("default_y_fit")
    time_unit = tp.get("time_unit", "auto")
    extra_cols = config.get("checklists", {}).get("extra_columns", [])
    visible = config.get("visible", {})
    return y_data, y_irf, y_fit, time_unit, extra_cols, visible

def load_decay_data(filepath):
    file_basename = os.path.basename(filepath)
    try:
        df = read_workbook(filepath, sheet="Fit_Curve")
    except Exception:
        print(f"❌ 无法找到 'Fit_Curve' Sheet: {file_basename}")
        return None

    columns = list(df.columns)
    col_full_time = "Full_Time (ns)"
    col_fit_time = "Fit_Time (ns)"
    if col_full_time not in columns:
        print(f"❌ {file_basename} 缺失必选列 Full_Time (ns)。")
        return None

    y_cols = _numeric_y_columns(df)
    if not y_cols:
        print(f"❌ {file_basename} 没有可绘制的数值列。")
        return None

    data_options = [c for c in y_cols if "IRF" not in str(c) and not str(c).startswith("Fit_")]
    irf_options = [c for c in y_cols if "IRF" in str(c)]
    fit_options = [c for c in y_cols if str(c).startswith("Fit_") and str(c) != col_fit_time]
    used = set(data_options) | set(irf_options) | set(fit_options)
    extra_options = [c for c in y_cols if c not in used]

    default_y_data = _pick_default(data_options, ["Plot_Counts", "Raw_Counts"])
    default_y_irf = _pick_default(
        irf_options,
        ["Plot_IRF_non_shifted", "Plot_IRF", "Raw_IRF"],
    )
    default_y_fit = _pick_default(fit_options, ["Fit_Plot_Fitted Data", "Fit_Fitted Data"])

    if not default_y_data and not default_y_fit and not irf_options and not extra_options:
        print(f"❌ {file_basename} 缺失可绘制列。")
        return None

    t_raw = df[col_full_time].dropna()
    if t_raw.empty:
        print(f"❌ {file_basename} Full_Time (ns) 无有效数据。")
        return None
    t_max_raw = float(t_raw.max())

    print(f"👉 {file_basename} → Full_Time 最大 {t_max_raw:.3g} ns")
    return dict(
        filepath=filepath,
        basename=file_basename,
        df=df,
        col_full_time=col_full_time,
        col_fit_time=col_fit_time if col_fit_time in columns else None,
        t_max_raw=t_max_raw,
        data_options=data_options,
        irf_options=irf_options,
        fit_options=fit_options,
        extra_options=extra_options,
        default_y_data=default_y_data,
        default_y_irf=default_y_irf,
        default_y_fit=default_y_fit,
    )

def _build_series_list(data_list, config):
    y_data, y_irf, y_fit, time_unit, extra_cols, visible = _resolve_plot_config(config, data_list)
    divisor, unit = _resolve_time_unit(
        max(d["t_max_raw"] for d in data_list),
        time_unit,
    )

    series_list = []
    multi = len(data_list) > 1
    for d in data_list:
        suffix = f" — {os.path.splitext(d['basename'])[0]}" if multi else ""
        df = d["df"]
        t_max_raw = d["t_max_raw"]
        fit_time_col = d["col_fit_time"] if d["col_fit_time"] in df.columns else d["col_full_time"]

        if visible.get("irf", True) and y_irf and y_irf in df.columns:
            s = _series_from_df(df, d["col_full_time"], y_irf, t_max_raw)
            if s is not None:
                series_list.append(dict(
                    key="irf", label=f"IRF{suffix}", style_key="irf",
                    time_col=d["col_full_time"], y_col=y_irf, df=s,
                ))

        if visible.get("raw", True) and y_data and y_data in df.columns:
            s = _series_from_df(df, d["col_full_time"], y_data, t_max_raw)
            if s is not None:
                series_list.append(dict(
                    key="raw", label=f"Data{suffix}", style_key="raw",
                    time_col=d["col_full_time"], y_col=y_data, df=s,
                ))

        if visible.get("fit", True) and y_fit and y_fit in df.columns:
            s = _series_from_df(df, fit_time_col, y_fit, t_max_raw)
            if s is not None:
                series_list.append(dict(
                    key="fit", label=f"Fit{suffix}", style_key="fit",
                    time_col=fit_time_col, y_col=y_fit, df=s,
                ))

        for i, col in enumerate(extra_cols):
            if col not in df.columns:
                continue
            time_col = fit_time_col if str(col).startswith("Fit_") else d["col_full_time"]
            s = _series_from_df(df, time_col, col, t_max_raw)
            if s is None:
                continue
            style_key = f"extra_{col}"
            series_list.append(dict(
                key=style_key,
                label=f"{col}{suffix}",
                style_key=style_key,
                time_col=time_col,
                y_col=col,
                df=s,
                default_color=EXTRA_COLORS[i % len(EXTRA_COLORS)],
            ))

    default_xmax = max(d["t_max_raw"] for d in data_list) / divisor
    return series_list, divisor, unit, default_xmax

# ══════════════════════════════════════════════════════════════════════════════
# 1.5 文本框生成辅助函数
# ══════════════════════════════════════════════════════════════════════════════
def _format_val_stderr(val, stderr, pm=r"\pm"):
    if not val:
        return ""
    text = str(val)
    if stderr:
        text += f" {pm} {stderr}"
    return text

def _tau_sub_suffix(sub, use_latex=False):
    sub = str(sub or "").strip()
    if not sub:
        return ""
    if use_latex:
        return r"_{\mathrm{" + sub + r"}}"
    return f"<sub>{sub}</sub>"

def _tau_unit_suffix(unit, use_latex=False):
    unit = str(unit or "").strip()
    if not unit:
        return ""
    if use_latex:
        latex_units = {"ns": r"\ \mathrm{ns}", "μs": r"\ \mu\mathrm{s}", "ms": r"\ \mathrm{ms}", "s": r"\ \mathrm{s}"}
        return latex_units.get(unit, rf"\ \mathrm{{{unit}}}")
    return f" {unit}"

def get_annotation_text(params, use_latex=False):
    ex = params.get("ex", "")
    emi = params.get("emi", "")
    tau = params.get("tau", "")
    tau_sub = params.get("tau_sub", "")
    tau_stderr = params.get("tau_stderr", "")
    tau_unit = params.get("tau_unit", "")
    beta = params.get("beta", "")
    beta_stderr = params.get("beta_stderr", "")
    chi2 = params.get("chi2", "")
    
    lines = []
    if use_latex:
        if ex: lines.append(r"$\lambda_{\mathrm{ex}} = " + str(ex) + r"\ \mathrm{nm}$")
        if emi: lines.append(r"$\lambda_{\mathrm{emi}} = " + str(emi) + r"\ \mathrm{nm}$")
        tau_text = _format_val_stderr(tau, tau_stderr)
        if tau_text:
            lines.append(
                r"$\tau"
                + _tau_sub_suffix(tau_sub, use_latex=True)
                + " = "
                + tau_text
                + _tau_unit_suffix(tau_unit, use_latex=True)
                + r"$"
            )
        beta_text = _format_val_stderr(beta, beta_stderr)
        if beta_text: lines.append(r"$\beta = " + beta_text + r"$")
        if chi2: lines.append(r"$\chi^2 = " + str(chi2) + r"$")
        return "\n".join(lines)
    else:
        if ex: lines.append(f"λ<sub>ex</sub> = {ex} nm")
        if emi: lines.append(f"λ<sub>emi</sub> = {emi} nm")
        tau_text = _format_val_stderr(tau, tau_stderr, pm="±")
        if tau_text:
            lines.append(
                f"τ{_tau_sub_suffix(tau_sub)} = {tau_text}{_tau_unit_suffix(tau_unit)}"
            )
        beta_text = _format_val_stderr(beta, beta_stderr, pm="±")
        if beta_text: lines.append(f"β = {beta_text}")
        if chi2: lines.append(f"χ<sup>2</sup> = {chi2}")
        return "<br>".join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# 2. Plotly 图构建
# ══════════════════════════════════════════════════════════════════════════════
def build_plotly_figure(data_list, config):
    colors  = config.get("colors", {})
    widths  = config.get("widths", {})
    offsets = config.get("offsets", {})
    xrange  = config.get("xrange")
    yrange  = config.get("yrange")

    series_list, div, unit, default_xmax = _build_series_list(data_list, config)
    fig = go.Figure()

    def _shift(series, key):
        off = float(offsets.get(key, 0.0) or 0.0)
        return series + off if off != 0.0 else series

    for s in series_list:
        style_key = s["style_key"]
        fig.add_trace(go.Scatter(
            x=s["df"][s["time_col"]] / div,
            y=_shift(s["df"][s["y_col"]], style_key),
            mode="lines",
            line=dict(
                color=colors.get(style_key, s.get("default_color", "#444444")),
                width=widths.get(style_key, 1.8),
            ),
            name=(
                f"<span style='color:{colors.get(style_key, s.get('default_color', '#444444'))}'>"
                f"{s['label']}</span>"
            ),
        ))

    axis_base = dict(
        ticks="inside", tickwidth=1.0, ticklen=8, tickcolor="black",
        showline=True, linewidth=1.0, linecolor="black",
        mirror=True, showgrid=False,
    )
    leg_pos = config.get("legend_pos", {"x": 0.97, "y": 0.97})
    fig.update_layout(
        width=800, height=600, autosize=False,
        font=dict(family="Arial", size=pu.GLOBAL_FONT_SIZE, color="black"),
        xaxis_title=f"Time ({unit})",
        yaxis_title="Counts",
        yaxis_type="log",
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(x=leg_pos["x"], y=leg_pos["y"], xanchor="right", yanchor="top",
                    bgcolor="rgba(0,0,0,0)",
                    font=dict(size=pu.GLOBAL_FONT_SIZE)),
        margin=dict(l=90, r=40, t=40, b=90),
        uirevision="stable",
    )
    
    text_html = get_annotation_text(config.get("text_params", {}), use_latex=False)
    if text_html:
        pos = config.get("text_pos", {"x": 0.05, "y": 0.05})
        fig.add_annotation(
            x=pos["x"], y=pos["y"],
            xref="paper", yref="paper",
            text=text_html,
            showarrow=False,
            align="left",
            xanchor="left", yanchor="top",
            font=dict(size=pu.GLOBAL_FONT_SIZE, color="black"),
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="rgba(0,0,0,0)",
            borderwidth=0,
        )

    xaxis_kwargs = {"tickformat": ".1f"}
    if xrange and xrange[1] <= default_xmax * 1.5:
        xaxis_kwargs["range"] = xrange
    else:
        xaxis_kwargs["range"] = [0, default_xmax * 1.02]
    fig.update_xaxes(**axis_base, **xaxis_kwargs)
    fig.update_yaxes(**axis_base, exponentformat="power", **({"range": yrange} if yrange else {}))
    return fig

# ══════════════════════════════════════════════════════════════════════════════
# 3. Matplotlib 静态矢量图
# ══════════════════════════════════════════════════════════════════════════════
def plot_matplotlib_static(data_list, config):
    pu.setup_matplotlib_style()

    colors  = config.get("colors", {})
    widths  = config.get("widths", {})
    offsets = config.get("offsets", {})
    xrange  = config.get("xrange")
    yrange  = config.get("yrange")

    series_list, div, unit, default_xmax = _build_series_list(data_list, config)

    def _shift(series, key):
        off = float(offsets.get(key, 0.0) or 0.0)
        return series + off if off != 0.0 else series

    fig, ax = pu.create_matched_fig_ax()

    for s in series_list:
        style_key = s["style_key"]
        ax.plot(
            s["df"][s["time_col"]] / div,
            _shift(s["df"][s["y_col"]], style_key),
            color=colors.get(style_key, s.get("default_color", "#444444")),
            linewidth=widths.get(style_key, 1.8),
            label=s["label"],
        )

    ax.set_yscale("log")
    ax.set_xlabel(f"Time ({unit})", fontsize=pu.GLOBAL_FONT_SIZE)
    ax.set_ylabel("Counts", fontsize=pu.GLOBAL_FONT_SIZE)

    ax.xaxis.set_major_formatter(ticker.FormatStrFormatter("%.1f"))
    ax.yaxis.set_major_formatter(ticker.LogFormatterMathtext())

    if xrange and xrange[0] is not None and xrange[1] is not None and xrange[1] <= default_xmax * 1.5:
        ax.set_xlim(xrange[0], xrange[1])
    else:
        ax.set_xlim(0, default_xmax * 1.02)
    if yrange and yrange[0] is not None and yrange[1] is not None:
        ax.set_ylim(10**yrange[0], 10**yrange[1])

    leg_pos = config.get("legend_pos", {"x": 0.97, "y": 0.97})
    leg = ax.legend(loc="upper right", bbox_to_anchor=(leg_pos["x"], leg_pos["y"]),
                    handlelength=1.8, handletextpad=0.5)
    for text, line in zip(leg.get_texts(), leg.get_lines()):
        text.set_color(line.get_color())
        
    text_latex = get_annotation_text(config.get("text_params", {}), use_latex=True)
    if text_latex:
        pos = config.get("text_pos", {"x": 0.05, "y": 0.05})
        ax.text(pos["x"], pos["y"], text_latex, transform=ax.transAxes,
                fontsize=pu.GLOBAL_FONT_SIZE,
                verticalalignment="top",
                horizontalalignment="left",
                bbox=dict(boxstyle="square,pad=0.3", facecolor="white", edgecolor="none", alpha=0.8))

    if len(data_list) == 1:
        d        = data_list[0]
        base_dir = os.path.dirname(d["filepath"])
        stem     = os.path.splitext(d["basename"])[0]
    else:
        base_dir = os.path.dirname(data_list[0]["filepath"])
        stem     = "DecayGraph_combined"

    svg_path = os.path.join(base_dir, f"{stem}_Decay.svg")
    pdf_path = os.path.join(base_dir, f"{stem}_Decay.pdf")
    fig.savefig(svg_path, format="svg", bbox_inches="tight")
    fig.savefig(pdf_path, format="pdf", bbox_inches="tight")
    print(f"✅ SVG: {svg_path}")
    print(f"✅ PDF: {pdf_path}")
    import matplotlib.pyplot as plt
    plt.close(fig)
    print("🎉 Matplotlib 矢量图导出完成！")

def _union_options(data_list, key):
    out = []
    seen = set()
    for d in data_list:
        for col in d.get(key, []):
            if col not in seen:
                seen.add(col)
                out.append(col)
    return out

# ══════════════════════════════════════════════════════════════════════════════
# 4. 主程序
# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🚀 TCSPC Decay 两阶段绘图工作流")
    files = pu.select_files("选择 TCSPC Excel 文件（可多选）")

    print(f"\n📚 共 {len(files)} 个文件，读取数据...\n")
    data_list = [d for f in files for d in [load_decay_data(f)] if d]

    if not data_list:
        print("❌ 无可用数据，退出。")
        sys.exit(1)

    data_options = _union_options(data_list, "data_options")
    irf_options = _union_options(data_list, "irf_options")
    fit_options = _union_options(data_list, "fit_options")
    extra_options = _union_options(data_list, "extra_options")

    print(f"\n{'='*55}\n  Phase 1 · Plotly 交互探索\n{'='*55}")
    explorer = pu.DynamicPlotExplorer(
        data_list,
        _COLOR_CONFIG,
        build_plotly_figure,
        plot_matplotlib_static,
        export_on_confirm=True,
        keep_open_after_confirm=True,
    )
    
    explorer.add_curve_style("IRF (dashed)", "irf", "#808080", 1.5, default_visible=bool(irf_options))
    explorer.add_curve_style("Data", "raw", "#003399", 2.0, default_visible=True)
    explorer.add_curve_style("Fit", "fit", "#CC0000", 2.5, default_visible=True)

    if data_options:
        explorer.add_choice_param(
            "Data 列:",
            "y_data",
            data_options,
            data_list[0].get("default_y_data") or data_options[0],
        )
    if irf_options:
        explorer.add_choice_param(
            "IRF 列:",
            "y_irf",
            irf_options,
            data_list[0].get("default_y_irf") or irf_options[0],
        )
    if fit_options:
        explorer.add_choice_param(
            "Fit 列:",
            "y_fit",
            fit_options,
            data_list[0].get("default_y_fit") or fit_options[0],
        )
    explorer.add_choice_param("时间单位:", "time_unit", TIME_UNIT_CHOICES, "auto")
    if extra_options:
        explorer.add_checklist_param(
            "其它列",
            "extra_columns",
            [{"label": col, "value": col} for col in extra_options],
            default_values=[],
        )
    
    explorer.add_text_param("λ_ex:", "ex")
    explorer.add_text_param("λ_emi:", "emi")
    explorer.add_choice_param("τ 下标:", "tau_sub", ["p", "d", "pho"], "p")
    explorer.add_text_param("τ:", "tau")
    explorer.add_text_param("τ StdErr:", "tau_stderr")
    explorer.add_choice_param("τ 单位:", "tau_unit", ["ns", "μs", "ms", "s"], "ns")
    explorer.add_text_param("β:", "beta")
    explorer.add_text_param("β StdErr:", "beta_stderr")
    explorer.add_text_param("χ²:", "chi2")
    
    explorer.run()
    print("\n✨ 完毕。")
