"""Join curves：清单解析、读数、组序列、Plotly/Matplotlib 出图（无 Dash）。"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import os
import re
import sys
from html import escape as html_escape
from pathlib import Path

import matplotlib.lines as mlines
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_text_lines, read_workbook, read_xy, sniff_delimiter

from PlotUtils import (
    GLOBAL_FONT_SIZE,
    create_matched_fig_ax,
    sample_spectra_palette,
    setup_matplotlib_style,
)

# 与 Join_curves Dash 入口共用画布高度（像素）
GRAPH_HEIGHT = 600
# 坐标轴名称与单位分拆（兼容旧配置里的整串 x_label / y_label）
DEFAULT_X_NAME = "Wavelength"
DEFAULT_X_UNIT = "nm"
DEFAULT_Y_NAME = "Normalized PL. & Abs."
DEFAULT_Y_UNIT = "a.u."


def _strip_outer_parens(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 2 and s.startswith("(") and s.endswith(")"):
        return s[1:-1].strip()
    return s


def split_legacy_axis_label(full: str) -> tuple[str, str]:
    """将类似 \"Wavelength (nm)\" 拆成名称与单位；否则整体作为名称。"""
    full = (full or "").strip()
    if not full:
        return "", ""
    if "(" in full and full.endswith(")"):
        i = full.rfind("(")
        name = full[:i].strip()
        unit = full[i + 1 : -1].strip()
        return name, unit
    return full, ""


_SUP_SCRIPT_MAP = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")


def _unicode_superscript_int(exp: int) -> str:
    neg = exp < 0
    body = str(abs(exp)).translate(_SUP_SCRIPT_MAP)
    return ("⁻" if neg else "") + body


def format_times10_unicode(exp: int) -> str:
    """返回 \"×10³\" 样式的后缀；exp==0 返回空串。"""
    if exp == 0:
        return ""
    return "×10" + _unicode_superscript_int(exp)


def time_divisor_and_unit_from_max_ns(max_ns: float) -> tuple[float, str]:
    """与 Decay_graph_origin.py 一致：原始 X 认为单位为 ns，按最大值选择 s/ms/μs/ns。"""
    try:
        m = float(max_ns)
    except (TypeError, ValueError):
        return 1.0, "ns"
    if not np.isfinite(m) or m < 0:
        return 1.0, "ns"
    if m >= 1e9:
        return 1e9, "s"
    if m >= 1e6:
        return 1e6, "ms"
    if m >= 1e3:
        return 1e3, "μs"
    return 1.0, "ns"


_Y_MAG_REF_PERCENTILE = 99.5
_Y_SCALE_EXP_CLAMP = 15
_LOG10_FLOAT_MAX = float(np.log10(np.finfo(float).max))
_LOG10_FLOAT_TINY = float(np.log10(np.finfo(float).tiny))


def plotly_uirevision_join(
    *, semilogy: bool, y_pow_exp: int, normalize_01: bool, n_series: int = 0
) -> str:
    """Plotly uirevision：轴类型 / Y 数量级 / 归一化 / 有无曲线变化时必须变更，否则会沿用旧视口（尤其先开 semilogy 再勾选数据时轴范围仍为空图状态，曲线不可见）。"""
    return f"join_sl{int(semilogy)}_yp{y_pow_exp}_n{int(normalize_01)}_s{n_series}"


def infer_linear_y_scale_exponent(y_arrays: list[np.ndarray]) -> int:
    """线性 Y 轴 ×10^k：按典型量级取 floor(log10)，思路对齐 Decay 类脚本的数量级分档（稳健、抗单列尖峰）。"""
    parts = []
    for a in y_arrays:
        v = np.asarray(a, dtype=float).ravel()
        parts.append(v[np.isfinite(v)])
    if not parts:
        return 0
    vals = np.concatenate(parts)
    if vals.size == 0:
        return 0
    av = np.abs(vals)
    vmax = float(np.nanmax(av))
    if vmax <= 0 or not np.isfinite(vmax):
        return 0
    ref = float(np.percentile(av, _Y_MAG_REF_PERCENTILE))
    if not np.isfinite(ref) or ref <= 0:
        ref = vmax
    ref = min(ref, vmax)
    exp = int(np.floor(np.log10(ref)))
    return max(-_Y_SCALE_EXP_CLAMP, min(_Y_SCALE_EXP_CLAMP, exp))


def compose_display_axis_titles(
    x_name: str,
    x_unit: str,
    y_name: str,
    y_unit: str,
    *,
    time_auto: bool,
    time_unit: str | None,
    y_pow_exp: int,
) -> tuple[str, str]:
    """合成用于绘图的完整 X/Y 轴标题字符串。"""
    xn = (x_name or "").strip() or DEFAULT_X_NAME
    yn = (y_name or "").strip() or DEFAULT_Y_NAME
    yun = _strip_outer_parens((y_unit or "").strip() or DEFAULT_Y_UNIT)
    if time_auto and time_unit:
        xt = f"{xn} ({time_unit})"
    else:
        xu = _strip_outer_parens((x_unit or "").strip() or DEFAULT_X_UNIT)
        xt = f"{xn} ({xu})" if xu else xn
    if y_pow_exp != 0:
        y_inner = f"{yun} {format_times10_unicode(y_pow_exp)}".strip()
    else:
        y_inner = yun
    yt = f"{yn} ({y_inner})"
    return xt, yt


def file_has_time_scan_keyword(file_path: str) -> bool:
    """数据文件第 2 列第 2 行（Excel B2 / CSV 第 2 行第 2 列）含 \"Time Scan\"。"""
    p = Path(file_path)
    if not p.is_file():
        return False
    try:
        suf = p.suffix.lower()
        if suf in (".xlsx", ".xls", ".xlsm"):
            df = read_workbook(p, sheet=0, header=None, nrows=4)
            if df is None or df.shape[0] < 2 or df.shape[1] < 2:
                return False
            cell = df.iloc[1, 1]
        else:
            lines, _encoding = read_text_lines(p, max_lines=4)
            if len(lines) < 2:
                return False
            delimiter = sniff_delimiter(lines)
            row = _split_text_row(lines[1], delimiter)
            if len(row) < 2:
                return False
            cell = row[1]
    except Exception:
        return False
    return "Time Scan" in str(cell)


def _split_text_row(line: str, delimiter: str) -> list[str]:
    if delimiter == "\\s+":
        return line.split()
    if delimiter == ",":
        return [cell.strip() for cell in line.replace("\t", ",").split(",")]
    return [cell.strip() for cell in line.split(delimiter)]



def parse_manifest(df: pd.DataFrame) -> tuple[list[dict], dict[str, dict]]:
    """返回 entries 列表与 rel_path -> entry 映射。"""
    cols = {str(c).strip(): c for c in df.columns}
    col_rp = cols.get("Rel_Path")
    col_fp = cols.get("File_Path")
    col_lb = cols.get("Label")
    if col_rp is None or col_fp is None:
        raise ValueError("清单需包含列 Rel_Path 与 File_Path")

    entries: list[dict] = []
    by_rel: dict[str, dict] = {}
    for _, row in df.iterrows():
        rp = row[col_rp]
        fp = row[col_fp]
        if pd.isna(rp) or pd.isna(fp):
            continue
        rel_s = str(rp).strip()
        fp_s = str(fp).strip()
        if not rel_s or not fp_s:
            continue
        lb = row[col_lb] if col_lb is not None else None
        label = str(lb).strip() if lb is not None and not pd.isna(lb) else Path(fp_s).stem
        ent = {"rel_path": rel_s, "file_path": fp_s, "label": label}
        entries.append(ent)
        by_rel[rel_s] = ent
    return entries, by_rel


def load_xy_from_file(file_path: str) -> tuple[np.ndarray, np.ndarray] | None:
    """读取前两列为 x, y（表头自动识别）。CSV 依次尝试 utf-8 / cp1252 等以支持仪器导出的 µ 等字符。"""
    p = Path(file_path)
    if not p.is_file():
        return None
    try:
        suf = p.suffix.lower()
        if suf in (".xlsx", ".xls", ".xlsm"):
            df = read_workbook(p, sheet=0)
            if df is None or df.shape[1] < 2:
                return None
            x = pd.to_numeric(df.iloc[:, 0], errors="coerce")
            y = pd.to_numeric(df.iloc[:, 1], errors="coerce")
            m = x.notna() & y.notna()
            x = x[m].to_numpy(dtype=float)
            y = y[m].to_numpy(dtype=float)
        else:
            spectrum = read_xy(p)
            x = spectrum.x
            y = spectrum.y
    except Exception:
        return None
    if len(x) < 2:
        return None
    return x, y


def _normalize_01(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return y
    return (y - lo) / (hi - lo)


def collect_series(
    by_rel: dict[str, dict],
    selected: list[str],
    *,
    normalize_01: bool,
) -> tuple[list[dict], dict]:
    """返回曲线列表与时间轴自动换算信息（与 Decay_graph_origin 一致：原始 X 为 ns）。"""
    rows: list[tuple[dict, bool]] = []
    for rel in selected:
        ent = by_rel.get(rel)
        if not ent:
            continue
        fp = ent["file_path"]
        xy = load_xy_from_file(fp)
        if xy is None:
            continue
        x, y = xy
        is_time = file_has_time_scan_keyword(fp)
        if normalize_01:
            y = _normalize_01(y)
        rows.append(
            (
                {
                    "rel_path": rel,
                    "label": ent["label"],
                    "file_path": fp,
                    "x": np.asarray(x, dtype=float),
                    "y": np.asarray(y, dtype=float),
                    "time_scan": is_time,
                },
                is_time,
            )
        )

    time_auto = any(t for _, t in rows)
    time_unit: str | None = None
    divisor = 1.0
    if time_auto:
        xmax = 0.0
        for item, is_t in rows:
            if is_t:
                xmax = max(xmax, float(np.nanmax(np.abs(item["x"]))))
        divisor, time_unit = time_divisor_and_unit_from_max_ns(xmax)

    out: list[dict] = []
    for item, is_t in rows:
        x = item["x"]
        if is_t:
            x = x / divisor
        out.append(
            {
                "rel_path": item["rel_path"],
                "label": item["label"],
                "x": x,
                "y": item["y"],
            }
        )

    meta = {"time_auto": time_auto, "time_unit": time_unit}
    return out, meta


def _y_for_log(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(y) & (y > 0), y, np.nan)


def _semilogy_y_with_baseline(y: np.ndarray, offset: float = 0.0) -> np.ndarray:
    """Semilogy 显示用 Y：取正值后叠加 baseline 平移（简单加减）。"""
    yv = _y_for_log(y)
    if not offset:
        return yv
    yv = yv + float(offset)
    with np.errstate(invalid="ignore"):
        return np.where(np.isfinite(yv) & (yv > 0), yv, np.nan)


def _relayout_axis_range_pair(relayout: dict, axis: str) -> tuple[float, float] | None:
    """读取 Plotly relayout 中的轴范围；支持 xaxis/yaxis 的 range[0]/range[1] 或整段 range 数组（框选、拖轴端等）。"""
    k0, k1 = f"{axis}.range[0]", f"{axis}.range[1]"
    if k0 in relayout and k1 in relayout:
        try:
            a, b = float(relayout[k0]), float(relayout[k1])
            if np.isfinite(a) and np.isfinite(b):
                return (a, b)
        except (TypeError, ValueError):
            pass
    rk = f"{axis}.range"
    if rk in relayout:
        r = relayout[rk]
        if isinstance(r, (list, tuple)) and len(r) >= 2:
            try:
                a, b = float(r[0]), float(r[1])
                if np.isfinite(a) and np.isfinite(b):
                    return (a, b)
            except (TypeError, ValueError):
                pass
    return None


_ANN_RE = re.compile(r"annotations\[(\d+)\]\.(x|y)$")


def _right_end_finite_xy(x, y) -> tuple[float | None, float | None]:
    """曲线最右端（最大 X）的有限点，供 Direct Labeling 默认位置。"""
    xv = np.asarray(x, dtype=float).ravel()
    yv = np.asarray(y, dtype=float).ravel()
    m = np.isfinite(xv) & np.isfinite(yv)
    xi, yi = xv[m], yv[m]
    if xi.size == 0:
        return None, None
    j = int(np.argmax(xi))
    return float(xi[j]), float(yi[j])


def _series_y_for_plot(
    s: dict,
    *,
    semilogy: bool,
    y_scale: float,
    baseline_offsets: dict,
) -> np.ndarray:
    y_raw = s["y"]
    rel_path = s.get("rel_path")
    y_offset = float(baseline_offsets.get(rel_path, 0) or 0) if semilogy else 0.0
    if semilogy:
        y_lin = _semilogy_y_with_baseline(y_raw, y_offset)
    elif y_scale != 1.0:
        y_lin = y_raw / y_scale
    else:
        y_lin = y_raw
    return np.asarray(y_lin, dtype=float)


def _direct_label_base_index(config: dict) -> int:
    return 1 if _overlay_annotation_spec(config) else 0


def _apply_relayout_to_pos(
    pos: dict,
    relayout: dict | None,
    *,
    semilogy: bool = False,
    direct_labeling: bool = False,
    has_overlay: bool = False,
    series_rel_paths: list[str] | None = None,
    y_scale: float = 1.0,
) -> None:
    if not isinstance(relayout, dict):
        return
    if relayout.get("xaxis.autorange") is True:
        pos["xrange"] = None
    else:
        xp = _relayout_axis_range_pair(relayout, "xaxis")
        if xp is not None:
            pos["xrange"] = [xp[0], xp[1]]
    if relayout.get("yaxis.autorange") is True:
        pos["yrange"] = None
        pos["yrange_plotly"] = None
    else:
        yp = _relayout_axis_range_pair(relayout, "yaxis")
        if yp is not None:
            if semilogy:
                # Plotly log-axis relayout range is in log10 space, while Matplotlib
                # expects real data coordinates for set_ylim on a log-scaled axis.
                e0 = min(max(float(yp[0]), _LOG10_FLOAT_TINY), _LOG10_FLOAT_MAX)
                e1 = min(max(float(yp[1]), _LOG10_FLOAT_TINY), _LOG10_FLOAT_MAX)
                yr0 = float(np.exp(e0 * np.log(10.0)))
                yr1 = float(np.exp(e1 * np.log(10.0)))
                if (
                    np.isfinite(yr0)
                    and np.isfinite(yr1)
                    and yr0 > 0
                    and yr1 > 0
                ):
                    pos["yrange"] = [yr0, yr1]
                    pos["yrange_plotly"] = [e0, e1]
            else:
                pos["yrange"] = [yp[0] * y_scale, yp[1] * y_scale]
                pos["yrange_plotly"] = None
    if "legend.x" in relayout:
        pos["legend"]["x"] = relayout["legend.x"]
    if "legend.y" in relayout:
        pos["legend"]["y"] = relayout["legend.y"]
    pos.setdefault("text", {"x": 0.05, "y": 0.95})
    pos.setdefault("direct_labels", {})
    dl_base = 1 if has_overlay else 0
    rel_paths = list(series_rel_paths or pos.get("direct_label_series") or [])
    for k, v in relayout.items():
        m = _ANN_RE.match(k)
        if not m:
            continue
        idx = int(m.group(1))
        axis = m.group(2)
        if has_overlay and idx == 0:
            pos["text"][axis] = v
        elif direct_labeling and idx >= dl_base:
            si = idx - dl_base
            if 0 <= si < len(rel_paths):
                rp = str(rel_paths[si])
                pos["direct_labels"].setdefault(rp, {})
                pos["direct_labels"][rp][axis] = v


def series_with_legend_lines(series: list[dict], legend_lines_text: str | None) -> list[dict]:
    """按勾选顺序：每行覆盖一条曲线的图例文字；空行或缺行保留清单默认名。"""
    if not series:
        return []
    lines = (legend_lines_text or "").splitlines()
    out: list[dict] = []
    for i, s in enumerate(series):
        d = dict(s)
        if i < len(lines):
            t = lines[i].strip()
            if t:
                d["label"] = t
        out.append(d)
    return out


def sanitize_export_basename(raw: str | None) -> str:
    s = (raw or "").strip()
    if not s:
        return "Join_curves"
    s = os.path.basename(s.replace("\\", "/"))
    for ext in (".pdf", ".svg", ".png", ".eps"):
        low = s.lower()
        if low.endswith(ext):
            s = s[: -len(ext)]
            break
    bad = '<>:"/\\|?*\x00'
    out = "".join(c for c in s if c not in bad).strip()
    return out or "Join_curves"


def _first_two_finite_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """取前两组有限点，供 legendonly 图例短线（对数轴上 y 可能含 nan）。"""
    xv = np.asarray(x, dtype=float).ravel()
    yv = np.asarray(y, dtype=float).ravel()
    m = np.isfinite(xv) & np.isfinite(yv)
    xi, yi = xv[m], yv[m]
    if xi.size >= 2:
        return xi[:2], yi[:2]
    if xi.size == 1:
        return np.array([xi[0], xi[0]]), np.array([yi[0], yi[0]])
    return np.array([0.0, 1.0]), np.array([0.0, 0.0])


def normalize_overlay_params(raw: dict | None) -> dict:
    p = raw if isinstance(raw, dict) else {}
    unit = str(p.get("rep_rate_unit") or "MHz").strip()
    return {
        "lambda_ex": str(p.get("lambda_ex") or "").strip(),
        "lambda_emi": str(p.get("lambda_emi") or "").strip(),
        "rep_rate": str(p.get("rep_rate") or "").strip(),
        "rep_rate_unit": unit,
    }


def overlay_params_has_content(params: dict | None) -> bool:
    p = normalize_overlay_params(params)
    return bool(p["lambda_ex"] or p["lambda_emi"] or p["rep_rate"])


def build_overlay_text_plotly(params: dict) -> str:
    p = normalize_overlay_params(params)
    lines: list[str] = []
    if p["lambda_ex"]:
        lines.append(f"λ<sub>ex</sub> = {html_escape(p['lambda_ex'])} nm")
    if p["lambda_emi"]:
        lines.append(f"λ<sub>emi</sub> = {html_escape(p['lambda_emi'])} nm")
    if p["rep_rate"]:
        unit = p["rep_rate_unit"]
        unit_suffix = f" {html_escape(unit)}" if unit else ""
        lines.append(f"rep rate = {html_escape(p['rep_rate'])}{unit_suffix}")
    return "<br>".join(lines)


def build_overlay_text_matplotlib(params: dict) -> str:
    p = normalize_overlay_params(params)
    lines: list[str] = []
    if p["lambda_ex"]:
        lines.append(r"$\lambda_{\mathrm{ex}} = " + p["lambda_ex"] + r"\ \mathrm{nm}$")
    if p["lambda_emi"]:
        lines.append(r"$\lambda_{\mathrm{emi}} = " + p["lambda_emi"] + r"\ \mathrm{nm}$")
    if p["rep_rate"]:
        unit = p["rep_rate_unit"]
        if unit:
            lines.append(r"rep rate $= " + p["rep_rate"] + r"\ \mathrm{" + unit + r"}$")
        else:
            lines.append(f"rep rate = {p['rep_rate']}")
    return "\n".join(lines)


def _overlay_annotation_spec(config: dict) -> tuple[dict, dict] | None:
    params = normalize_overlay_params(config.get("overlay_params"))
    if not overlay_params_has_content(params):
        return None
    pos = config.get("text_pos") or {"x": 0.05, "y": 0.95}
    return params, pos


def _add_direct_label_annotations(
    fig: go.Figure,
    series: list[dict],
    colors_rgba: list[str],
    *,
    config: dict,
    semilogy: bool,
    y_scale: float,
    baseline_offsets: dict,
) -> None:
    saved = config.get("direct_labels") or {}
    for i, s in enumerate(series):
        xs = np.asarray(s["x"], dtype=float)
        yv = _series_y_for_plot(
            s,
            semilogy=semilogy,
            y_scale=y_scale,
            baseline_offsets=baseline_offsets,
        )
        rel_path = str(s.get("rel_path") or i)
        override = saved.get(rel_path)
        if override and override.get("x") is not None and override.get("y") is not None:
            ax, ay = float(override["x"]), float(override["y"])
        else:
            ax, ay = _right_end_finite_xy(xs, yv)
            if ax is None or ay is None:
                continue
        fig.add_annotation(
            x=ax,
            y=ay,
            xref="x",
            yref="y",
            text=str(s["label"]),
            showarrow=False,
            xanchor="left",
            yanchor="middle",
            font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color=colors_rgba[i]),
        )


def _add_overlay_annotation(fig: go.Figure, config: dict) -> None:
    spec = _overlay_annotation_spec(config)
    if not spec:
        return
    params, pos = spec
    fig.add_annotation(
        x=pos.get("x", 0.05),
        y=pos.get("y", 0.95),
        xref="paper",
        yref="paper",
        text=build_overlay_text_plotly(params),
        showarrow=False,
        align="left",
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xanchor="left",
        yanchor="top",
    )


def build_plotly_figure(
    series: list[dict],
    *,
    config: dict,
    semilogy: bool,
    normalize_01: bool,
    x_title: str,
    y_title: str,
    y_pow_exp: int,
) -> go.Figure:
    fig = go.Figure()
    title_font = dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black")

    if not series:
        if not _overlay_annotation_spec(config):
            fig.add_annotation(
                text="请在左侧勾选至少一个有效数据文件",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=16, color="gray"),
            )
        fig.update_layout(
            font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
            xaxis=dict(
                title=dict(text=x_title, font=title_font),
                showline=True,
                linewidth=1.5,
                linecolor="black",
                mirror=True,
                ticks="inside",
                tickwidth=1.5,
                tickcolor="black",
                ticklen=8,
                showgrid=False,
                exponentformat="none",
                showexponent="none",
            ),
            yaxis=dict(
                title=dict(text=y_title, font=title_font),
                showline=True,
                linewidth=1.5,
                linecolor="black",
                mirror=True,
                ticks="inside",
                tickwidth=1.5,
                tickcolor="black",
                ticklen=8,
                type="log" if semilogy else "linear",
                showgrid=False,
                **(
                    {"exponentformat": "power"}
                    if semilogy
                    else {"exponentformat": "none", "showexponent": "none"}
                ),
            ),
            plot_bgcolor="white",
            paper_bgcolor="white",
            margin=dict(l=90, r=140, t=40, b=90),
            uirevision=plotly_uirevision_join(
                semilogy=semilogy,
                y_pow_exp=y_pow_exp,
                normalize_01=normalize_01,
                n_series=0,
            ),
        )
        _add_overlay_annotation(fig, config)
        return fig

    palette_id = config.get("spectra_palette") or "viridis"
    n = len(series)
    colors_rgba = sample_spectra_palette(palette_id, n, as_rgba=True)
    lw = float(config.get("line_width") or 1.5)
    short_dash_rel_paths = set(config.get("short_dash_rel_paths") or [])
    baseline_offsets = config.get("baseline_y_offsets") or {}
    baseline_selected_rel = config.get("baseline_selected_rel")
    direct_labeling = bool(config.get("direct_labeling"))

    y_scale = 10.0 ** y_pow_exp if y_pow_exp != 0 else 1.0

    for i, s in enumerate(series):
        rel_path = s.get("rel_path")
        xs = np.asarray(s["x"], dtype=float)
        yv = _series_y_for_plot(
            s,
            semilogy=semilogy,
            y_scale=y_scale,
            baseline_offsets=baseline_offsets,
        )
        is_short_dash = rel_path in short_dash_rel_paths
        dash_style = "4px,2px" if is_short_dash else "solid"
        trace_lw = lw * 1.55 if rel_path and rel_path == baseline_selected_rel else lw

        fig.add_trace(
            go.Scatter(
                x=xs,
                y=yv,
                mode="lines",
                showlegend=False,
                line=dict(color=colors_rgba[i], width=trace_lw, dash=dash_style),
                name=str(s["label"]),
                customdata=[rel_path] * len(xs),
            )
        )
        if not direct_labeling:
            lx, ly = _first_two_finite_xy(xs, yv)
            leg_name = (
                f"<span style=\"color:{colors_rgba[i]}\">"
                f"{html_escape(str(s['label']))}</span>"
            )
            fig.add_trace(
                go.Scatter(
                    x=lx,
                    y=ly,
                    mode="lines",
                    visible="legendonly",
                    showlegend=True,
                    name=leg_name,
                    line=dict(
                        color=colors_rgba[i],
                        width=max(0.25, lw * 0.5),
                        dash=dash_style,
                    ),
                    legendwidth=36,
                    hoverinfo="skip",
                    customdata=[rel_path, rel_path],
                )
            )

    xaxis_dict = dict(
        title=dict(text=x_title, font=title_font),
        showline=True,
        linewidth=1.5,
        linecolor="black",
        mirror=True,
        ticks="inside",
        tickwidth=1.5,
        tickcolor="black",
        ticklen=8,
        showgrid=False,
        exponentformat="none",
        showexponent="none",
    )
    if config.get("xrange") and config["xrange"][0] is not None:
        xaxis_dict["range"] = config["xrange"]

    yaxis_dict = dict(
        title=dict(text=y_title, font=title_font),
        showline=True,
        linewidth=1.5,
        linecolor="black",
        mirror=True,
        ticks="inside",
        tickwidth=1.5,
        tickcolor="black",
        ticklen=8,
        showgrid=False,
        type="log" if semilogy else "linear",
    )
    if semilogy:
        # 与 Decay_graph.py 中 Plotly 预览一致：对数轴用 power 指数样式
        yaxis_dict["exponentformat"] = "power"
    else:
        # 线性轴禁用 Plotly 自带 ×10ⁿ 刻度，避免与手册缩放 / 标题上标冲突出现畸形刻度
        yaxis_dict["exponentformat"] = "none"
        yaxis_dict["showexponent"] = "none"
    if semilogy:
        yrp = config.get("yrange_plotly")
        if yrp and yrp[0] is not None:
            yaxis_dict["range"] = yrp
        elif config.get("yrange") and config["yrange"][0] is not None:
            # Backward compatibility: if only real-value range is available, map it
            # back to Plotly's log10-axis range.
            yr = config["yrange"]
            try:
                y0, y1 = float(yr[0]), float(yr[1])
                if np.isfinite(y0) and np.isfinite(y1) and y0 > 0 and y1 > 0:
                    yaxis_dict["range"] = [float(np.log10(y0)), float(np.log10(y1))]
            except (TypeError, ValueError):
                pass
    elif config.get("yrange") and config["yrange"][0] is not None:
        yr = config["yrange"]
        if y_scale != 1.0:
            yaxis_dict["range"] = [yr[0] / y_scale, yr[1] / y_scale]
        else:
            yaxis_dict["range"] = yr

    layout_kw: dict = dict(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=xaxis_dict,
        yaxis=yaxis_dict,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=90, r=140, t=40, b=90),
        uirevision=plotly_uirevision_join(
            semilogy=semilogy,
            y_pow_exp=y_pow_exp,
            normalize_01=normalize_01,
            n_series=len(series),
        ),
    )
    if direct_labeling:
        layout_kw["showlegend"] = False
    else:
        leg = config.get("legend_pos") or {"x": 0.97, "y": 0.97}
        layout_kw["legend"] = dict(
            x=leg.get("x", 0.97),
            y=leg.get("y", 0.97),
            xanchor="right",
            yanchor="top",
            orientation="v",
            itemsizing="constant",
            itemwidth=36,
            tracegroupgap=0,
            bgcolor="rgba(255,255,255,0.92)",
            borderwidth=0,
            traceorder="normal",
            font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        )
    fig.update_layout(**layout_kw)
    _add_overlay_annotation(fig, config)
    if direct_labeling:
        _add_direct_label_annotations(
            fig,
            series,
            colors_rgba,
            config=config,
            semilogy=semilogy,
            y_scale=y_scale,
            baseline_offsets=baseline_offsets,
        )
    return fig


def plot_matplotlib_static(
    series: list[dict],
    *,
    config: dict,
    semilogy: bool,
    save_dir: str,
    x_title: str,
    y_title: str,
    y_pow_exp: int,
    export_basename: str = "Join_curves",
) -> None:
    setup_matplotlib_style()
    fig, ax = create_matched_fig_ax(width_px=800, height_px=GRAPH_HEIGHT, dpi=300)
    y_scale = 10.0 ** y_pow_exp if y_pow_exp != 0 else 1.0
    base = sanitize_export_basename(export_basename)

    if not series:
        ax.text(0.5, 0.5, "无选中曲线", transform=ax.transAxes, ha="center", va="center")
    else:
        palette_id = config.get("spectra_palette") or "viridis"
        colors = sample_spectra_palette(palette_id, len(series))
        lw = float(config.get("line_width") or 1.5)
        leg_lw = max(0.25, lw * 0.5)
        _handlelen = float(plt.rcParams.get("legend.handlelength", 2.0)) * 0.5
        short_dash_rel_paths = set(config.get("short_dash_rel_paths") or [])
        baseline_offsets = config.get("baseline_y_offsets") or {}
        handles: list = []
        for i, s in enumerate(series):
            y_raw = s["y"]
            rel_path = s.get("rel_path")
            y_offset = float(baseline_offsets.get(rel_path, 0) or 0) if semilogy else 0.0
            if semilogy:
                yp = _semilogy_y_with_baseline(y_raw, y_offset)
            elif y_scale != 1.0:
                yp = y_raw / y_scale
            else:
                yp = y_raw
            linestyle = (0, (3, 2)) if rel_path in short_dash_rel_paths else "-"
            ax.plot(s["x"], yp, color=colors[i], linewidth=lw, linestyle=linestyle)
            handles.append(
                mlines.Line2D(
                    [],
                    [],
                    color=colors[i],
                    linestyle=linestyle,
                    linewidth=leg_lw,
                )
            )
        if config.get("direct_labeling"):
            saved = config.get("direct_labels") or {}
            for i, s in enumerate(series):
                yp = _series_y_for_plot(
                    s,
                    semilogy=semilogy,
                    y_scale=y_scale,
                    baseline_offsets=baseline_offsets,
                )
                rel_path = str(s.get("rel_path") or i)
                override = saved.get(rel_path)
                if override and override.get("x") is not None and override.get("y") is not None:
                    tx, ty = float(override["x"]), float(override["y"])
                else:
                    tx, ty = _right_end_finite_xy(s["x"], yp)
                    if tx is None or ty is None:
                        continue
                ax.text(
                    tx,
                    ty,
                    s["label"],
                    color=colors[i],
                    fontsize=GLOBAL_FONT_SIZE,
                    ha="left",
                    va="center",
                )
        else:
            ax.legend(
                handles,
                [s["label"] for s in series],
                loc="upper right",
                bbox_to_anchor=(
                    config.get("legend_pos", {}).get("x", 0.97),
                    config.get("legend_pos", {}).get("y", 0.97),
                ),
                frameon=False,
                fontsize=GLOBAL_FONT_SIZE,
                labelcolor="linecolor",
                handlelength=_handlelen,
            )

    if semilogy:
        ax.set_yscale("log")
    else:
        ax.ticklabel_format(style="plain", axis="y", useOffset=False)
    ax.set_xlabel(x_title, fontsize=GLOBAL_FONT_SIZE)
    ax.set_ylabel(y_title, fontsize=GLOBAL_FONT_SIZE)
    ax.spines["top"].set_visible(True)
    ax.spines["right"].set_visible(True)
    ax.tick_params(top=False, right=False)

    if config.get("xrange") and config["xrange"][0] is not None:
        ax.set_xlim(*config["xrange"])
    if config.get("yrange") and config["yrange"][0] is not None:
        yr = config["yrange"]
        if not semilogy and y_scale != 1.0:
            ax.set_ylim(yr[0] / y_scale, yr[1] / y_scale)
        else:
            ax.set_ylim(*yr)

    overlay = _overlay_annotation_spec(config)
    if overlay:
        params, pos = overlay
        ax.text(
            pos.get("x", 0.05),
            pos.get("y", 0.95),
            build_overlay_text_matplotlib(params),
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=GLOBAL_FONT_SIZE,
            color="black",
        )

    pdf_path = os.path.join(save_dir, f"{base}.pdf")
    svg_path = os.path.join(save_dir, f"{base}.svg")
    fig.savefig(pdf_path, format="pdf", transparent=True)
    fig.savefig(svg_path, format="svg", transparent=True)
    plt.close(fig)
    print(f"✅ 已保存: {pdf_path}")
    print(f"✅ 已保存: {svg_path}")