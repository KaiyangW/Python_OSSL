from __future__ import annotations

import os
import sys
import math
import re
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go


try:
    import ctypes

    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_grid, read_workbook
from PlotUtils import (
    DynamicPlotExplorer,
    GLOBAL_FONT_SIZE,
    MATPLOTLIB_EXPORT_AXES_LINEWIDTH,
    MATPLOTLIB_EXPORT_DPI,
    MATPLOTLIB_EXPORT_HEIGHT_PX,
    MATPLOTLIB_EXPORT_MARGIN_BOTTOM_PX,
    MATPLOTLIB_EXPORT_MARGIN_LEFT_PX,
    MATPLOTLIB_EXPORT_MARGIN_RIGHT_PX,
    MATPLOTLIB_EXPORT_MARGIN_TOP_PX,
    MATPLOTLIB_EXPORT_WIDTH_PX,
    SPECTRA_COLOR_PALETTES,
    apply_matplotlib_export_axes_style,
    sample_spectra_palette,
    select_files,
    setup_matplotlib_style,
)


Y_LABEL = "ΔT/T"
CONFIG_NAME = "ta_graph_config.json"
EXPORT_STEM = "TA"
STYLE_SOLID = "Solid line"
STYLE_SCATTER = "Scatter line"
LINE_STYLE_OPTIONS = (STYLE_SOLID, STYLE_SCATTER)
SPECTRA_LEGEND_TITLE = "Time (ns)"
KINETICS_LEGEND_TITLE = "Wavelength (nm)"
SPECTRA_LEGEND_FONT_SIZE = GLOBAL_FONT_SIZE - 2
PLOTLY_AXIS_LINEWIDTH = 1.5
DEFAULT_SPECTRA_PALETTE = "viridis"
DEFAULT_KINETICS_PALETTE = "viridis"
KINETICS_PALETTE_OPTIONS = tuple(SPECTRA_COLOR_PALETTES.keys())


@dataclass
class Curve:
    label: str
    x: np.ndarray
    y: np.ndarray
    time_value_ns: float | None = None


@dataclass
class TAPlotData:
    source_path: Path
    spectra: list[Curve]
    kinetics: list[Curve]
    spectra_x_label: str = "Wavelength (nm)"
    spectra_y_label: str = Y_LABEL
    kinetics_x_label: str = "Time (ns)"
    kinetics_y_label: str = Y_LABEL


def _to_numeric(values) -> np.ndarray:
    try:
        import pandas as pd

        return pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
    except Exception:
        out = []
        for value in values:
            try:
                out.append(float(value))
            except (TypeError, ValueError):
                out.append(np.nan)
        return np.asarray(out, dtype=float)


def _clean_label(label) -> str:
    text = str(label).strip()
    if not text or text.lower().startswith("unnamed"):
        return "Curve"
    return text


def _label_prefix(label) -> str:
    text = _clean_label(label)
    if "|" in text:
        return text.split("|", 1)[0].strip()
    return text


def _finite_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _parse_xy_sheet(df, default_x_label: str, default_y_label: str) -> tuple[list[Curve], str, str]:
    """Parse TA_data_reading workbook sheets saved by _build_line_dataframe()."""
    if df is None or df.empty:
        return [], default_x_label, default_y_label

    df = df.dropna(axis=1, how="all")
    columns = list(df.columns)
    if len(columns) < 2:
        return [], default_x_label, default_y_label

    # When curves have different x axes, TA_data_reading saves x/y column pairs:
    # "Curve label | Wavelength (nm)", "Curve label | Ave Delta T/T".
    paired_curves: list[Curve] = []
    if any("|" in str(col) for col in columns):
        used: set[int] = set()
        for i, x_col in enumerate(columns[:-1]):
            if i in used or "|" not in str(x_col):
                continue
            prefix = _label_prefix(x_col)
            for j in range(i + 1, len(columns)):
                if j in used:
                    continue
                y_col = columns[j]
                if _label_prefix(y_col) != prefix:
                    continue
                x = _to_numeric(df[x_col])
                y = _to_numeric(df[y_col])
                x, y = _finite_xy(x, y)
                if x.size:
                    paired_curves.append(Curve(prefix, x, y))
                    used.update({i, j})
                break
        if paired_curves:
            return paired_curves, default_x_label, default_y_label

    x_col = columns[0]
    x = _to_numeric(df[x_col])
    curves: list[Curve] = []
    for y_col in columns[1:]:
        y = _to_numeric(df[y_col])
        xx, yy = _finite_xy(x, y)
        if xx.size:
            curves.append(Curve(_clean_label(y_col), xx, yy))

    x_label = _clean_label(x_col) if not str(x_col).lower().startswith("unnamed") else default_x_label
    return curves, x_label, default_y_label


def _plot_info_value(info_df, property_name: str, default: str) -> str:
    if info_df is None or info_df.empty:
        return default
    columns = list(info_df.columns)
    if len(columns) < 2:
        return default
    props = info_df[columns[0]].astype(str).str.strip()
    matches = info_df.loc[props == property_name, columns[1]]
    if matches.empty:
        return default
    value = matches.iloc[0]
    if value is None or str(value).strip() == "" or str(value).lower() == "nan":
        return default
    return str(value)


def _load_from_workbook(path: Path) -> TAPlotData:
    sheets = read_workbook(path, sheet=None)
    spectra_df = sheets.get("Spectra")
    kinetics_df = sheets.get("Kinetics")
    info_df = sheets.get("Plot info")

    spectra_x_label = _plot_info_value(info_df, "Spectrum X axis", "Wavelength (nm)")
    kinetics_x_label = _plot_info_value(info_df, "Kinetics X axis", "Time (ns)")

    spectra, spectra_x_label, spectra_y_label = _parse_xy_sheet(
        spectra_df, spectra_x_label, Y_LABEL
    )
    for curve in spectra:
        curve.time_value_ns = _parse_time_ns_from_label(curve.label)
    kinetics, kinetics_x_label, kinetics_y_label = _parse_xy_sheet(
        kinetics_df, kinetics_x_label, Y_LABEL
    )

    if not spectra and not kinetics:
        raise ValueError("Workbook does not contain readable Spectra or Kinetics sheets.")

    return TAPlotData(
        source_path=path,
        spectra=spectra,
        kinetics=kinetics,
        spectra_x_label=spectra_x_label,
        spectra_y_label=spectra_y_label,
        kinetics_x_label=kinetics_x_label,
        kinetics_y_label=kinetics_y_label,
    )


def _representative_indices(size: int, limit: int = 5) -> list[int]:
    if size <= 0:
        return []
    if size <= limit:
        return list(range(size))
    return sorted(set(np.linspace(0, size - 1, limit, dtype=int).tolist()))


def _format_number(value: float, unit: str) -> str:
    return f"{value:.6g} {unit}"


def _parse_time_ns_from_label(label: str) -> float | None:
    text = _clean_label(label)
    if len(text) > 2 and text[0].upper() in {"S", "K"} and text[1].isdigit():
        _, sep, rest = text.partition(":")
        if sep and rest.strip():
            text = rest.strip()
    match = re.search(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', text)
    if not match:
        return None
    try:
        value = float(match.group(0))
    except (ValueError, OverflowError):
        return None
    if not np.isfinite(value) or value < 0:
        return None
    after_number = text[match.end():].strip().lower()
    if after_number.startswith('us') or after_number.startswith('\xb5s'):
        value *= 1000
    return value


def _format_time_ns(value_ns: float, unit: str) -> str:
    if unit == "us":
        return f"{value_ns / 1000:.6g}"
    return f"{value_ns:.6g}"


def _load_from_grid(path: Path) -> TAPlotData:
    grid = read_grid(path, layout="ta_grid")
    wavelengths = np.asarray(grid.row_values, dtype=float)
    times_ns = np.asarray(grid.col_values, dtype=float) * 1e9
    signal = np.asarray(grid.data, dtype=float)

    spectra = [
        Curve(f"S: {_format_number(times_ns[idx], 'ns')}", wavelengths, signal[:, idx], time_value_ns=times_ns[idx])
        for idx in _representative_indices(times_ns.size)
    ]
    kinetics = [
        Curve(f"K: {_format_number(wavelengths[idx], 'nm')}", times_ns, signal[idx, :])
        for idx in _representative_indices(wavelengths.size)
    ]
    return TAPlotData(path, spectra, kinetics)


def load_ta_plot_data(path: str | os.PathLike) -> TAPlotData:
    p = Path(path)
    if p.suffix.lower() in {".xlsx", ".xls", ".xlsm"}:
        try:
            return _load_from_workbook(p)
        except Exception as exc:
            print(f"Workbook-style TA output was not detected ({exc}); trying TA grid reader.")
    return _load_from_grid(p)


def _default_indices(curves: list[Curve], limit: int = 8) -> list[int]:
    if len(curves) <= limit:
        return list(range(len(curves)))
    return _representative_indices(len(curves), limit)


def _selection_options(curves: list[Curve]) -> list[dict]:
    return [{"label": curve.label, "value": idx} for idx, curve in enumerate(curves)]


def _selected_indices(values, curves: list[Curve], default_limit: int = 8) -> list[int]:
    selected: list[int] = []
    for value in values or []:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(curves) and idx not in selected:
            selected.append(idx)
    return selected or _default_indices(curves, default_limit)


def _selected_spectra(data: TAPlotData, config) -> list[int]:
    return _selected_indices((config or {}).get("spectra_selected_indices"), data.spectra)


def _selected_kinetics(data: TAPlotData, config) -> list[int]:
    checklist = (config or {}).get("checklists", {}).get("kinetics_selected_indices", [])
    return _selected_indices(checklist, data.kinetics)


def _group_visible(config, group: str) -> bool:
    return (config or {}).get("visible", {}).get(group, True)


def _text_param(config, key: str, default: str = "") -> str:
    value = (config.get("text_params") or {}).get(key, default)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _legend_label(config, group: str, curve_idx: int, default: str,
                  *, time_unit: str = "ns", time_value_ns: float | None = None) -> str:
    if group != "spectra":
        return _default_legend_text(
            _text_param(config, f"kinetics_legend_label_{curve_idx}", _default_legend_text(default))
        )

    if time_value_ns is not None:
        clean = _default_legend_text(default)
        if len(re.findall(r'\d+(?:\.\d+)?', clean)) <= 1:
            return _format_spectra_legend_text(default, time_unit=time_unit, time_value_ns=time_value_ns)

    current_default = _format_spectra_legend_text(default, time_unit=time_unit)
    label = _text_param(config, f"spectra_legend_label_{curve_idx}", current_default)
    if label.strip() == current_default.strip():
        return current_default
    return _format_spectra_legend_text(label, time_unit=time_unit)


def _legend_title(config, group: str, default: str) -> str:
    title = _text_param(config, f"{group}_legend_title", default)
    if group == "spectra" and title == "Spectra":
        return default
    if group == "kinetics" and title == "Kinetics":
        return default
    if group == "spectra":
        time_unit = _text_param(config, "time_unit", "ns")
        if title in (f"Time (ns)", f"Time (us)") and title != f"Time ({time_unit})":
            return default
    return title


def _default_legend_text(label: str) -> str:
    text = str(label).strip()
    if len(text) > 2 and text[0].upper() in {"S", "K"} and text[1].isdigit():
        _, sep, rest = text.partition(":")
        if sep and rest.strip():
            text = rest.strip()
    return re.sub(r"\s*(?:ns|nm|us|µs)\s*$", "", text, flags=re.IGNORECASE).strip()


def _format_spectra_legend_text(label: str, *, time_unit: str = "ns", time_value_ns: float | None = None) -> str:
    if time_unit == "us":
        fmt = ".2f"
    else:
        fmt = ".1f"

    if time_value_ns is not None:
        if time_unit == "us":
            return f"{time_value_ns / 1000:{fmt}}"
        return f"{time_value_ns:{fmt}}"

    text = _default_legend_text(label)
    text = re.sub(r"\b(?:ns|us|µs)\b", "", text, flags=re.IGNORECASE)

    def _fmt_number(match: re.Match) -> str:
        value = float(match.group(0))
        if time_unit == "us":
            value /= 1000
        return f"{value:{fmt}}"

    text = re.sub(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",
        _fmt_number,
        text,
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def _add_legend_title_param(
    explorer, group: str, label: str, default_text: str, old_default_text: str
) -> None:
    key = f"{group}_legend_title"
    existing = explorer.config.get("text_params", {}).get(key)
    explorer.add_text_param(label, key, default_text)
    if existing in (None, "", old_default_text):
        explorer.config["text_params"][key] = default_text


def _add_legend_text_param(explorer, group: str, idx: int, curve: Curve, label_prefix: str) -> None:
    key = f"{group}_legend_label_{idx}"
    time_unit = (explorer.config.get("text_params") or {}).get("time_unit", "ns")
    default_text = (
        _format_spectra_legend_text(curve.label, time_unit=time_unit, time_value_ns=curve.time_value_ns)
        if group == "spectra"
        else _default_legend_text(curve.label)
    )
    existing = explorer.config.get("text_params", {}).get(key)
    explorer.add_text_param(f"{label_prefix}{idx + 1} legend", key, default_text)
    if existing in (None, "", curve.label, _default_legend_text(curve.label)):
        explorer.config["text_params"][key] = default_text


def _auto_y_exponent(curves: list[Curve], selected: list[int]) -> int:
    values = []
    for idx in selected:
        y = np.asarray(curves[idx].y, dtype=float)
        finite = np.abs(y[np.isfinite(y)])
        if finite.size:
            values.append(float(np.nanmax(finite)))
    max_abs = max(values) if values else 0.0
    if not np.isfinite(max_abs) or max_abs <= 0:
        return 0

    exponent = int(math.floor(math.log10(max_abs)))
    return exponent if exponent >= 2 or exponent <= -2 else 0


def _scaled_y(y: np.ndarray, exponent: int) -> np.ndarray:
    if exponent == 0:
        return y
    return y / (10**exponent)


def _y_axis_title(base_label: str, exponent: int, *, for_mpl: bool = False) -> str:
    if exponent == 0:
        return base_label
    if for_mpl:
        return f"{base_label} ($\\times 10^{{{exponent}}}$)"
    return f"{base_label} × 10<sup>{exponent}</sup>"


def _scaled_y_values(curves: list[Curve], selected: list[int], exponent: int) -> np.ndarray:
    values = []
    for idx in selected:
        y = _scaled_y(np.asarray(curves[idx].y, dtype=float), exponent)
        finite = y[np.isfinite(y)]
        if finite.size:
            values.append(finite)
    return np.concatenate(values) if values else np.asarray([], dtype=float)


def _usable_scaled_range(axis_range, curves: list[Curve], selected: list[int], exponent: int):
    """Keep user zoom ranges, but drop stale ranges saved before y rescaling."""
    if not axis_range or axis_range[0] is None:
        return None
    try:
        low, high = float(axis_range[0]), float(axis_range[1])
    except (TypeError, ValueError):
        return None
    if not np.isfinite(low) or not np.isfinite(high) or low == high:
        return None

    scaled = _scaled_y_values(curves, selected, exponent)
    if scaled.size == 0:
        return [low, high]
    data_low = float(np.nanmin(scaled))
    data_high = float(np.nanmax(scaled))
    data_span = max(data_high - data_low, np.finfo(float).eps)
    data_abs = max(abs(data_low), abs(data_high), np.finfo(float).eps)
    range_span = abs(high - low)
    range_abs = max(abs(low), abs(high))

    # Existing config files may contain raw ΔT/T ranges. After converting data to
    # displayed scientific units those ranges can be orders of magnitude too small.
    if (
        (exponent != 0 and range_abs < data_abs * 0.1)
        or range_span < data_span * 0.05
        or range_span > data_span * 1e3
    ):
        return None
    return [low, high]


def _plotly_axis(title: str, axis_range=None) -> dict:
    axis = dict(
        title=title,
        showline=True,
        linewidth=PLOTLY_AXIS_LINEWIDTH,
        linecolor="black",
        mirror=True,
        ticks="inside",
        tickwidth=PLOTLY_AXIS_LINEWIDTH,
        tickcolor="black",
        ticklen=8,
    )
    if axis_range and axis_range[0] is not None:
        axis["range"] = axis_range
    return axis


def _add_plotly_zero_line(fig: go.Figure) -> None:
    fig.add_hline(
        y=0.0,
        line_color="red",
        line_width=PLOTLY_AXIS_LINEWIDTH,
        line_dash="3px,3px",
    )


def _plotly_spectra_axis(title: str, axis_range=None, *, yaxis: bool = False) -> dict:
    axis = _plotly_axis(title, axis_range)
    axis["mirror"] = "all"
    axis["side"] = "left" if yaxis else "bottom"
    return axis


def _trace_mode(style_name: str) -> str:
    return "lines+markers" if style_name == STYLE_SCATTER else "lines"


def _add_plotly_curves(
    fig: go.Figure,
    curves: list[Curve],
    selected: list[int],
    colors: list,
    width: float,
    style_name: str,
    exponent: int,
    config,
    group: str,
    showlegend: bool = True,
) -> None:
    mode = _trace_mode(style_name)
    time_unit = _text_param(config, "time_unit", "ns")
    for color_idx, curve_idx in enumerate(selected):
        curve = curves[curve_idx]
        fig.add_trace(
            go.Scatter(
                x=curve.x,
                y=_scaled_y(curve.y, exponent),
                mode=mode,
                name=_legend_label(config, group, curve_idx, curve.label,
                                   time_unit=time_unit,
                                   time_value_ns=curve.time_value_ns if group == "spectra" else None),
                showlegend=showlegend,
                line=dict(color=colors[color_idx], width=width, dash="solid"),
                marker=dict(color=colors[color_idx], size=7),
            )
        )


def build_plotly_spectra(data_list, config):
    data: TAPlotData = data_list[0]
    selected = _selected_spectra(data, config) if _group_visible(config, "spectra") else []
    exponent = _auto_y_exponent(data.spectra, selected)
    fig = go.Figure()
    if not selected:
        fig.add_annotation(
            text="No spectra curves selected",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
    else:
        colors = sample_spectra_palette(
            config.get("spectra_palette", DEFAULT_SPECTRA_PALETTE), len(selected), as_rgba=True
        )
        _add_plotly_curves(
            fig,
            data.spectra,
            selected,
            colors,
            config["widths"].get("spectra", 1.8),
            config["text_params"].get("spectra_style", STYLE_SOLID),
            exponent,
            config,
            "spectra",
            showlegend=True,
        )
        _add_plotly_zero_line(fig)

    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=_plotly_spectra_axis(data.spectra_x_label, config.get("xrange")),
        yaxis=_plotly_spectra_axis(
            _y_axis_title(data.spectra_y_label, exponent),
            _usable_scaled_range(config.get("yrange"), data.spectra, selected, exponent),
            yaxis=True,
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=True,
        margin=dict(l=90, r=40, t=40, b=90),
        legend=dict(
            title=dict(
                text=_legend_title(config, "spectra", f"Time ({_text_param(config, 'time_unit', 'ns')})"),
                font=dict(size=SPECTRA_LEGEND_FONT_SIZE),
            ),
            font=dict(size=SPECTRA_LEGEND_FONT_SIZE),
            x=config.get("legend_pos", {}).get("x", 0.97),
            y=config.get("legend_pos", {}).get("y", 0.97),
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            borderwidth=0,
        ),
    )
    return fig


def build_plotly_kinetics(data_list, config):
    data: TAPlotData = data_list[0]
    selected = _selected_kinetics(data, config) if _group_visible(config, "kinetics") else []
    exponent = _auto_y_exponent(data.kinetics, selected)
    fig = go.Figure()
    if not selected:
        fig.add_annotation(
            text="No kinetics curves selected",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )
    else:
        kinetics_palette_id = _text_param(
            config, "kinetics_palette", DEFAULT_KINETICS_PALETTE
        )
        colors = sample_spectra_palette(kinetics_palette_id, len(selected), as_rgba=True)
        _add_plotly_curves(
            fig,
            data.kinetics,
            selected,
            colors,
            config["widths"].get("kinetics", 1.5),
            config["text_params"].get("kinetics_style", STYLE_SCATTER),
            exponent,
            config,
            "kinetics",
        )
        _add_plotly_zero_line(fig)

    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=_plotly_axis(data.kinetics_x_label, config.get("xrange_spec")),
        yaxis=_plotly_axis(
            _y_axis_title(data.kinetics_y_label, exponent),
            _usable_scaled_range(config.get("yrange_spec"), data.kinetics, selected, exponent),
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        showlegend=True,
        margin=dict(l=90, r=40, t=40, b=90),
        legend=dict(
            title=dict(text=_legend_title(config, "kinetics", KINETICS_LEGEND_TITLE)),
            x=config.get("legend_pos", {}).get("x", 0.97),
            y=config.get("legend_pos", {}).get("y", 0.97),
            xanchor="right",
            yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            borderwidth=0,
        ),
    )
    return fig


def _plot_mpl_curves(ax, curves, selected, colors, width, style_name, exponent, config, group):
    marker = "o" if style_name == STYLE_SCATTER else None
    markersize = max(3.5, width * 2.5) if marker else 0
    time_unit = _text_param(config, "time_unit", "ns")
    for color_idx, curve_idx in enumerate(selected):
        curve = curves[curve_idx]
        ax.plot(
            curve.x,
            _scaled_y(curve.y, exponent),
            color=colors[color_idx],
            linewidth=width,
            linestyle="-",
            marker=marker,
            markersize=markersize,
            label=_legend_label(config, group, curve_idx, curve.label,
                                time_unit=time_unit,
                                time_value_ns=curve.time_value_ns if group == "spectra" else None),
        )


def _add_mpl_zero_line(ax) -> None:
    ax.axhline(
        y=0.0,
        color="red",
        linewidth=MATPLOTLIB_EXPORT_AXES_LINEWIDTH,
        linestyle=(0, (3, 3)),
    )


def _make_figure() -> tuple[plt.Figure, plt.Axes]:
    width_px = MATPLOTLIB_EXPORT_WIDTH_PX
    height_px = MATPLOTLIB_EXPORT_HEIGHT_PX
    fig, ax = plt.subplots(
        figsize=(width_px / 72, height_px / 72),
        dpi=MATPLOTLIB_EXPORT_DPI,
    )
    fig.subplots_adjust(
        left=MATPLOTLIB_EXPORT_MARGIN_LEFT_PX / width_px,
        right=1.0 - (MATPLOTLIB_EXPORT_MARGIN_RIGHT_PX / width_px),
        bottom=MATPLOTLIB_EXPORT_MARGIN_BOTTOM_PX / height_px,
        top=1.0 - (MATPLOTLIB_EXPORT_MARGIN_TOP_PX / height_px),
    )
    apply_matplotlib_export_axes_style(ax)
    ax.spines["top"].set_visible(True)
    ax.spines["right"].set_visible(True)
    return fig, ax


def _save_figure(fig, filepath: str) -> None:
    fig.savefig(filepath, format="pdf", transparent=True)
    svg_path = os.path.splitext(filepath)[0] + ".svg"
    fig.savefig(svg_path, format="svg", transparent=True)
    plt.close(fig)
    print(f"Saved: {filepath}")
    print(f"Saved: {svg_path}")


def plot_matplotlib_static(data_list, config, save_dir):
    data: TAPlotData = data_list[0]
    spectra_selected = _selected_spectra(data, config) if _group_visible(config, "spectra") else []
    kinetics_selected = _selected_kinetics(data, config) if _group_visible(config, "kinetics") else []
    spectra_exponent = _auto_y_exponent(data.spectra, spectra_selected)
    kinetics_exponent = _auto_y_exponent(data.kinetics, kinetics_selected)

    setup_matplotlib_style()

    palette_id = config.get("spectra_palette", DEFAULT_SPECTRA_PALETTE)
    kinetics_palette_id = _text_param(config, "kinetics_palette", DEFAULT_KINETICS_PALETTE)
    spectra_colors = sample_spectra_palette(palette_id, len(spectra_selected))
    kinetics_colors = sample_spectra_palette(kinetics_palette_id, len(kinetics_selected))

    # --- Spectra figure ---
    fig_spec, ax_spec = _make_figure()
    _plot_mpl_curves(
        ax_spec, data.spectra, spectra_selected, spectra_colors,
        config["widths"].get("spectra", 1.8),
        config["text_params"].get("spectra_style", STYLE_SOLID),
        spectra_exponent, config, "spectra",
    )
    _add_mpl_zero_line(ax_spec)
    ax_spec.tick_params(top=False, right=False, labeltop=False, labelright=False)
    ax_spec.set_xlabel(data.spectra_x_label)
    ax_spec.set_ylabel(_y_axis_title(data.spectra_y_label, spectra_exponent, for_mpl=True))
    if config.get("xrange") and config["xrange"][0] is not None:
        ax_spec.set_xlim(*config["xrange"])
    spec_yrange = _usable_scaled_range(
        config.get("yrange"), data.spectra, spectra_selected, spectra_exponent
    )
    if spec_yrange:
        ax_spec.set_ylim(*spec_yrange)
    if ax_spec.get_legend_handles_labels()[0]:
        ax_spec.legend(
            loc="best",
            frameon=False,
            title=_legend_title(config, "spectra", f"Time ({_text_param(config, 'time_unit', 'ns')})"),
            fontsize=SPECTRA_LEGEND_FONT_SIZE,
            title_fontsize=SPECTRA_LEGEND_FONT_SIZE,
        )
    _save_figure(fig_spec, os.path.join(save_dir, f"{EXPORT_STEM}_Spectra.pdf"))

    # --- Kinetics figure ---
    fig_kin, ax_kin = _make_figure()
    _plot_mpl_curves(
        ax_kin, data.kinetics, kinetics_selected, kinetics_colors,
        config["widths"].get("kinetics", 1.5),
        config["text_params"].get("kinetics_style", STYLE_SCATTER),
        kinetics_exponent, config, "kinetics",
    )
    _add_mpl_zero_line(ax_kin)
    ax_kin.tick_params(top=False, right=False, labeltop=False, labelright=False)
    ax_kin.set_xlabel(data.kinetics_x_label)
    ax_kin.set_ylabel(_y_axis_title(data.kinetics_y_label, kinetics_exponent, for_mpl=True))
    if config.get("xrange_spec") and config["xrange_spec"][0] is not None:
        ax_kin.set_xlim(*config["xrange_spec"])
    kin_yrange = _usable_scaled_range(
        config.get("yrange_spec"), data.kinetics, kinetics_selected, kinetics_exponent
    )
    if kin_yrange:
        ax_kin.set_ylim(*kin_yrange)
    if ax_kin.get_legend_handles_labels()[0]:
        ax_kin.legend(
            loc="best", frameon=False,
            title=_legend_title(config, "kinetics", KINETICS_LEGEND_TITLE),
        )
    _save_figure(fig_kin, os.path.join(save_dir, f"{EXPORT_STEM}_Kinetics.pdf"))


def main():
    print("Starting TA spectra/kinetics graph builder...")
    selected = select_files(
        "Select TA_data_reading output workbook or TA grid",
        filetypes=[
            ("TA outputs", "*.xlsx;*.xls;*.xlsm;*.csv;*.txt;*.dat"),
            ("Excel files", "*.xlsx;*.xls;*.xlsm"),
            ("Text grid files", "*.csv;*.txt;*.dat"),
            ("All files", "*.*"),
        ],
    )
    source_path = Path(selected[0])
    data = load_ta_plot_data(source_path)

    if not data.spectra:
        raise RuntimeError("No spectra curves were found.")
    if not data.kinetics:
        raise RuntimeError("No kinetics curves were found.")

    save_dir = str(source_path.parent)
    config_path = os.path.join(save_dir, CONFIG_NAME)
    data_list = [data]

    explorer = DynamicPlotExplorer(
        data_list=data_list,
        config_path=config_path,
        build_plotly_func=build_plotly_spectra,
        build_plotly_func2=build_plotly_kinetics,
        plot_mpl_func=plot_matplotlib_static,
        graph_height=MATPLOTLIB_EXPORT_HEIGHT_PX,
        graph2_height=MATPLOTLIB_EXPORT_HEIGHT_PX,
        show_offset=False,
    )
    explorer.add_curve_style("Spectra line", "spectra", "#000000", default_width=1.8)
    explorer.add_curve_style("Kinetics line", "kinetics", "#000000", default_width=1.5)
    explorer.add_spectra_palette_selector(SPECTRA_COLOR_PALETTES, default=DEFAULT_SPECTRA_PALETTE)
    explorer.add_choice_param(
        "K palette", "kinetics_palette", KINETICS_PALETTE_OPTIONS, DEFAULT_KINETICS_PALETTE
    )
    explorer.add_spectra_selection(
        _selection_options(data.spectra),
        _default_indices(data.spectra),
    )
    explorer.add_checklist_param(
        "Kinetics data selection",
        "kinetics_selected_indices",
        _selection_options(data.kinetics),
        _default_indices(data.kinetics),
    )
    explorer.add_choice_param("Spectra style", "spectra_style", LINE_STYLE_OPTIONS, STYLE_SOLID)
    explorer.add_choice_param("Kinetics style", "kinetics_style", LINE_STYLE_OPTIONS, STYLE_SCATTER)
    explorer.add_choice_param("Time unit", "time_unit", ("ns", "us"), "ns")
    _add_legend_title_param(
        explorer, "spectra", "S legend title",
        f"Time ({((explorer.config or {}).get('text_params') or {}).get('time_unit', 'ns')})", "Spectra",
    )
    _add_legend_title_param(
        explorer, "kinetics", "K legend title", KINETICS_LEGEND_TITLE, "Kinetics"
    )
    for idx, curve in enumerate(data.spectra):
        _add_legend_text_param(explorer, "spectra", idx, curve, "S")
    for idx, curve in enumerate(data.kinetics):
        _add_legend_text_param(explorer, "kinetics", idx, curve, "K")

    final_config = explorer.run()
    plot_matplotlib_static(data_list, final_config, save_dir)
    print("Done.")


if __name__ == "__main__":
    main()
