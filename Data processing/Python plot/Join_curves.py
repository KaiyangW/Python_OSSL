from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import json
import sys
import threading
import time
import webbrowser
from pathlib import Path

import math

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_workbook

from dash import Dash, Input, Output, State, callback_context, dcc, html, no_update

import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

from PlotUtils import DEFAULT_SPECTRA_PALETTE, GLOBAL_FONT_SIZE, SPECTRA_COLOR_PALETTES

from Manifest_index import (
    MANIFEST_NAME as MANIFEST_INDEX_NAME,
    generate_manifest_interactive,
)

from Join_curves_nonDash_core import (
    DEFAULT_X_NAME,
    DEFAULT_X_UNIT,
    DEFAULT_Y_NAME,
    DEFAULT_Y_UNIT,
    GRAPH_HEIGHT,
    _apply_relayout_to_pos,
    build_plotly_figure,
    collect_series,
    compose_display_axis_titles,
    infer_linear_y_scale_exponent,
    normalize_overlay_params,
    overlay_params_has_content,
    parse_manifest,
    plot_matplotlib_static,
    sanitize_export_basename,
    series_with_legend_lines,
)

MANIFEST_NAME = MANIFEST_INDEX_NAME
GRAPH_CONFIGS_DIRNAME = "Graph_configs"
DASH_PORT = 8052


def run_app(entries: list[dict], by_rel: dict[str, dict], manifest_path: Path) -> None:
    save_dir = str(manifest_path.parent.resolve())
    graph_configs_dir = manifest_path.parent / GRAPH_CONFIGS_DIRNAME
    graph_configs_dir.mkdir(parents=True, exist_ok=True)

    config: dict = {
        "line_width": 1.8,
        "xrange": None,
        "yrange": None,
        "yrange_plotly": None,
        "x_name": DEFAULT_X_NAME,
        "x_unit": DEFAULT_X_UNIT,
        "y_name": DEFAULT_Y_NAME,
        "y_unit": DEFAULT_Y_UNIT,
        "short_dash_rel_paths": [],
        "baseline_y_offsets": {},
        "overlay_params": normalize_overlay_params({}),
        "text_pos": {"x": 0.05, "y": 0.95},
        "direct_labeling": False,
        "direct_labels": {},
    }

    def _graph_config_options() -> list[dict[str, str]]:
        files = sorted(graph_configs_dir.glob("*.json"), key=lambda p: p.name.lower())
        return [{"label": p.name, "value": p.name} for p in files]

    try:
        _lw0 = float(config.get("line_width", 1.8))
    except (TypeError, ValueError):
        _lw0 = 1.8
    initial_ui = {
        "x_name": str(config.get("x_name", DEFAULT_X_NAME)),
        "x_unit": str(config.get("x_unit", DEFAULT_X_UNIT)),
        "y_name": str(config.get("y_name", DEFAULT_Y_NAME)),
        "y_unit": str(config.get("y_unit", DEFAULT_Y_UNIT)),
        "spectra_palette": DEFAULT_SPECTRA_PALETTE,
        "line_width": max(0.1, min(10.0, _lw0)),
        "short_dash_rel_paths": list(config.get("short_dash_rel_paths") or []),
        "baseline_y_offsets": dict(config.get("baseline_y_offsets") or {}),
        "export_basename": "Join_curves",
        "overlay_params": normalize_overlay_params(config.get("overlay_params")),
        "direct_labeling": bool(config.get("direct_labeling")),
    }
    _overlay0 = initial_ui["overlay_params"]

    _init_xt, _init_yt = compose_display_axis_titles(
        str(config.get("x_name", DEFAULT_X_NAME)),
        str(config.get("x_unit", DEFAULT_X_UNIT)),
        str(config.get("y_name", DEFAULT_Y_NAME)),
        str(config.get("y_unit", DEFAULT_Y_UNIT)),
        time_auto=False,
        time_unit=None,
        y_pow_exp=0,
    )

    rel_options = [e["rel_path"] for e in entries]
    checklist_opts = [{"label": rp, "value": rp} for rp in rel_options]

    app = Dash(__name__, suppress_callback_exceptions=True)

    app.layout = html.Div(
        style={
            "fontFamily": "Arial, sans-serif",
            "display": "flex",
            "height": "100vh",
            "overflow": "hidden",
            "background": "#f0f2f5",
        },
        children=[
            dcc.Store(
                id="pos-store",
                data={
                    "legend": {"x": 0.97, "y": 0.97},
                    "text": dict(config.get("text_pos") or {"x": 0.05, "y": 0.95}),
                    "direct_labels": dict(config.get("direct_labels") or {}),
                    "direct_label_series": [],
                    "xrange": config.get("xrange"),
                    "yrange": config.get("yrange"),
                    "yrange_plotly": config.get("yrange_plotly"),
                },
            ),
            dcc.Store(
                id="line-dash-store",
                data={"short_dash_rel_paths": initial_ui["short_dash_rel_paths"]},
            ),
            dcc.Store(
                id="baseline-offset-store",
                data=dict(initial_ui["baseline_y_offsets"]),
            ),
            dcc.Store(id="baseline-select-store", data={"rel_path": None}),
            html.Div(
                style={
                    "width": "310px",
                    "minWidth": "310px",
                    "height": "100vh",
                    "padding": "14px 12px",
                    "background": "linear-gradient(170deg, #1a1a2e 0%, #16213e 100%)",
                    "color": "white",
                    "display": "flex",
                    "flexDirection": "column",
                    "boxShadow": "4px 0 24px rgba(0,0,0,0.35)",
                    "overflowY": "auto",
                    "overflowX": "hidden",
                    "boxSizing": "border-box",
                    "gap": "8px",
                },
                children=[
                    html.H3(
                        "Join curves",
                        style={
                            "margin": "0 0 6px",
                            "fontSize": "16px",
                            "borderBottom": "1px solid rgba(255,255,255,0.18)",
                            "paddingBottom": "8px",
                            "flexShrink": "0",
                        },
                    ),
                    html.Div(
                        style={
                            "flexShrink": "0",
                            "background": "white",
                            "borderRadius": "10px",
                            "padding": "8px 10px",
                            "color": "#333",
                            "minHeight": "clamp(280px, 52vh, 720px)",
                        },
                        children=[
                            html.P(
                                "清单条目（Rel_Path）",
                                style={
                                    "margin": "0 0 6px",
                                    "fontSize": "10px",
                                    "color": "#888",
                                    "textTransform": "uppercase",
                                },
                            ),
                            dcc.Checklist(
                                id="file-checklist",
                                options=checklist_opts,
                                value=[],
                                persistence=False,
                                inputStyle={"marginRight": "6px"},
                                labelStyle={
                                    "display": "block",
                                    "fontSize": "11px",
                                    "lineHeight": "1.35",
                                    "marginBottom": "5px",
                                    "wordBreak": "break-all",
                                },
                                style={
                                    "border": "1px solid #ddd",
                                    "borderRadius": "6px",
                                    "padding": "8px",
                                    "background": "#fafafa",
                                },
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "background": "white",
                            "borderRadius": "8px",
                            "padding": "6px 10px",
                            "color": "#222",
                            "flexShrink": "0",
                        },
                        children=[
                            html.P(
                                "显示选项",
                                style={
                                    "margin": "0 0 4px",
                                    "fontSize": "10px",
                                    "color": "#999",
                                    "textTransform": "uppercase",
                                },
                            ),
                            dcc.Checklist(
                                id="opt-normalize",
                                options=[{"label": " Y 各系列 (0,1) 归一化", "value": "on"}],
                                value=[],
                                inputStyle={"marginRight": "5px"},
                                labelStyle={"fontSize": "10px", "lineHeight": "1.25"},
                            ),
                            dcc.Checklist(
                                id="opt-semilogy",
                                options=[{"label": " Semilogy (对数 Y)", "value": "on"}],
                                value=[],
                                inputStyle={"marginRight": "5px"},
                                labelStyle={"fontSize": "10px", "lineHeight": "1.25"},
                            ),
                            html.Div(
                                style={"marginTop": "8px", "paddingTop": "8px", "borderTop": "1px solid #eee"},
                                children=[
                                    html.P(
                                        "Semilogy baseline 微调",
                                        style={
                                            "margin": "0 0 4px",
                                            "fontSize": "10px",
                                            "color": "#999",
                                            "textTransform": "uppercase",
                                        },
                                    ),
                                    html.P(
                                        "点击曲线选中；↑↓ 按步长平移 Y。",
                                        style={"fontSize": "9px", "color": "#888", "margin": "0 0 6px", "lineHeight": "1.35"},
                                    ),
                                    html.Div(
                                        style={"display": "flex", "alignItems": "center", "gap": "6px", "flexWrap": "wrap"},
                                        children=[
                                            html.Span("步长", style={"fontSize": "11px"}),
                                            dcc.Input(
                                                id="baseline-step",
                                                type="number",
                                                value=10,
                                                min=0.001,
                                                step=1,
                                                debounce=True,
                                                style={
                                                    "width": "70px",
                                                    "padding": "4px 6px",
                                                    "fontSize": "12px",
                                                    "border": "1px solid #ccc",
                                                    "borderRadius": "4px",
                                                    "color": "black",
                                                },
                                            ),
                                            html.Button(
                                                "↑",
                                                id="nudge-up-btn",
                                                n_clicks=0,
                                                title="选中曲线上移（+步长）",
                                                style={
                                                    "padding": "4px 12px",
                                                    "fontSize": "14px",
                                                    "lineHeight": "1",
                                                    "border": "1px solid #ccc",
                                                    "borderRadius": "4px",
                                                    "background": "#f0f0f0",
                                                    "color": "black",
                                                    "cursor": "pointer",
                                                },
                                            ),
                                            html.Button(
                                                "↓",
                                                id="nudge-down-btn",
                                                n_clicks=0,
                                                title="选中曲线下移（−步长）",
                                                style={
                                                    "padding": "4px 12px",
                                                    "fontSize": "14px",
                                                    "lineHeight": "1",
                                                    "border": "1px solid #ccc",
                                                    "borderRadius": "4px",
                                                    "background": "#f0f0f0",
                                                    "color": "black",
                                                    "cursor": "pointer",
                                                },
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "background": "white",
                            "borderRadius": "10px",
                            "padding": "12px",
                            "color": "black",
                            "flexShrink": "0",
                        },
                        children=[
                            html.P(
                                "坐标轴标题",
                                style={
                                    "margin": "0 0 8px",
                                    "fontSize": "11px",
                                    "color": "#999",
                                    "textTransform": "uppercase",
                                },
                            ),
                            html.P("X 轴 — 名称", style={"fontSize": "11px", "margin": "0 0 4px", "color": "#555"}),
                            dcc.Input(
                                id="axis-x-name",
                                type="text",
                                value=str(config.get("x_name", DEFAULT_X_NAME)),
                                debounce=True,
                                style={
                                    "width": "100%",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                    "marginBottom": "6px",
                                },
                            ),
                            html.P("X 轴 — 单位", style={"fontSize": "11px", "margin": "0 0 4px", "color": "#555"}),
                            dcc.Input(
                                id="axis-x-unit",
                                type="text",
                                value=str(config.get("x_unit", DEFAULT_X_UNIT)),
                                debounce=True,
                                style={
                                    "width": "100%",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                    "marginBottom": "6px",
                                },
                            ),
                            html.P(
                                "若数据第 2 行第 2 列为 Time Scan，则 X 单位按 ns→μs/ms/s 自动换算（与 Decay_graph_origin 一致），此处单位字段会被忽略。",
                                style={"fontSize": "10px", "color": "#888", "margin": "0 0 8px", "lineHeight": "1.35"},
                            ),
                            html.P("Y 轴 — 名称", style={"fontSize": "11px", "margin": "0 0 4px", "color": "#555"}),
                            dcc.Input(
                                id="axis-y-name",
                                type="text",
                                value=str(config.get("y_name", DEFAULT_Y_NAME)),
                                debounce=True,
                                style={
                                    "width": "100%",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                    "marginBottom": "6px",
                                },
                            ),
                            html.P("Y 轴 — 单位", style={"fontSize": "11px", "margin": "0 0 4px", "color": "#555"}),
                            dcc.Input(
                                id="axis-y-unit",
                                type="text",
                                value=str(config.get("y_unit", DEFAULT_Y_UNIT)),
                                debounce=True,
                                style={
                                    "width": "100%",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                },
                            ),
                            html.P(
                                "与图内一致，字号 " + str(GLOBAL_FONT_SIZE),
                                style={"fontSize": "10px", "color": "#888", "margin": "8px 0 0"},
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "background": "white",
                            "borderRadius": "10px",
                            "padding": "12px",
                            "color": "black",
                            "flexShrink": "0",
                            "marginBottom": "6px",
                        },
                        children=[
                            html.P(
                                "坐标轴范围",
                                style={
                                    "margin": "0 0 8px",
                                    "fontSize": "11px",
                                    "color": "#999",
                                    "textTransform": "uppercase",
                                },
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "marginBottom": "6px", "gap": "4px"},
                                children=[
                                    html.Span("X 轴", style={"fontSize": "11px", "width": "32px", "color": "#555"}),
                                    dcc.Input(
                                        id="x-min-input",
                                        type="number",
                                        step="any",
                                        debounce=True,
                                        placeholder="Min",
                                        style={
                                            "width": "60px",
                                            "padding": "4px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                        },
                                    ),
                                    html.Span("-", style={"fontSize": "11px", "color": "#555"}),
                                    dcc.Input(
                                        id="x-max-input",
                                        type="number",
                                        step="any",
                                        debounce=True,
                                        placeholder="Max",
                                        style={
                                            "width": "60px",
                                            "padding": "4px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                        },
                                    ),
                                ],
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "4px"},
                                children=[
                                    html.Span("Y 轴", style={"fontSize": "11px", "width": "32px", "color": "#555"}),
                                    dcc.Input(
                                        id="y-min-input",
                                        type="number",
                                        step="any",
                                        debounce=True,
                                        placeholder="Min",
                                        style={
                                            "width": "60px",
                                            "padding": "4px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                        },
                                    ),
                                    html.Span("-", style={"fontSize": "11px", "color": "#555"}),
                                    dcc.Input(
                                        id="y-max-input",
                                        type="number",
                                        step="any",
                                        debounce=True,
                                        placeholder="Max",
                                        style={
                                            "width": "60px",
                                            "padding": "4px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                        },
                                    ),
                                ],
                            ),
                            html.P(
                                "拖动右侧图表时自动更新；亦可手动输入后回车生效。",
                                style={"fontSize": "9px", "color": "#999", "margin": "8px 0 0"},
                            ),
                        ],
                    ),
                    html.Div(
                        style={
                            "background": "white",
                            "borderRadius": "10px",
                            "padding": "12px",
                            "color": "black",
                            "flexShrink": "0",
                        },
                        children=[
                            html.P(
                                "曲线样式",
                                style={
                                    "margin": "0 0 8px",
                                    "fontSize": "11px",
                                    "color": "#999",
                                    "textTransform": "uppercase",
                                },
                            ),
                            html.P(
                                "配色系列（按勾选顺序取色）",
                                style={"fontSize": "11px", "color": "#666", "margin": "0 0 6px"},
                            ),
                            dcc.Dropdown(
                                id="spectra-palette",
                                options=[
                                    {"label": meta["label"], "value": key}
                                    for key, meta in SPECTRA_COLOR_PALETTES.items()
                                ],
                                value=initial_ui["spectra_palette"],
                                clearable=False,
                                style={"fontSize": "12px", "color": "black", "marginBottom": "10px"},
                            ),
                            html.Div(
                                style={"display": "flex", "alignItems": "center", "gap": "8px"},
                                children=[
                                    html.Span("线宽", style={"fontSize": "12px"}),
                                    dcc.Input(
                                        id="line-width",
                                        type="number",
                                        value=config.get("line_width", 1.8),
                                        min=0.1,
                                        max=10,
                                        step=0.1,
                                        debounce=True,
                                        style={
                                            "width": "70px",
                                            "padding": "4px 6px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                            "color": "black",
                                        },
                                    ),
                                    html.Span("Enter / 失焦 / 短暂停顿后更新", style={"fontSize": "10px", "color": "#888"}),
                                ],
                            ),
                            dcc.Checklist(
                                id="opt-click-short-dash",
                                options=[{"label": " 点击曲线切换 short dash line", "value": "on"}],
                                value=[],
                                inputStyle={"marginRight": "5px"},
                                labelStyle={"fontSize": "10px", "lineHeight": "1.25"},
                                style={"marginTop": "10px"},
                            ),
                            html.P(
                                "勾选后在右侧图中点击某条曲线，可在实线 / short dash 间切换；导出会保留该线型。",
                                style={"fontSize": "9px", "color": "#999", "margin": "4px 0 0"},
                            ),
                            html.P(
                                "图例文字（每行对应一条勾选曲线，顺序与勾选一致；空行保留清单默认名）",
                                style={"fontSize": "11px", "color": "#666", "margin": "12px 0 6px"},
                            ),
                            dcc.Textarea(
                                id="legend-lines",
                                value="",
                                placeholder="例如第一行写曲线 A 的图例…",
                                style={
                                    "width": "100%",
                                    "minHeight": "72px",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "11px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                    "resize": "vertical",
                                },
                            ),
                            html.P(
                                "失焦后更新右侧图例；点「确认」保存时按框内当前文字导出，无需失焦。",
                                style={"fontSize": "9px", "color": "#999", "margin": "4px 0 0"},
                            ),
                            dcc.Checklist(
                                id="opt-direct-label",
                                options=[
                                    {
                                        "label": " Direct Labeling（曲线末端标注，替代图例）",
                                        "value": "on",
                                    }
                                ],
                                value=["on"] if initial_ui["direct_labeling"] else [],
                                inputStyle={"marginRight": "5px"},
                                labelStyle={"fontSize": "10px", "lineHeight": "1.25"},
                                style={"marginTop": "10px"},
                            ),
                            html.P(
                                "开启后标签置于各曲线最右端，颜色与曲线一致、字号与图例相同；"
                                "可在右侧图中直接拖拽调整位置，确认保存后写入导出图。",
                                style={"fontSize": "9px", "color": "#999", "margin": "4px 0 0"},
                            ),
                            html.P(
                                "图内附加文字（留空不显示，可只填其中一项）",
                                style={"fontSize": "11px", "color": "#666", "margin": "12px 0 6px"},
                            ),
                            html.Div(
                                style={
                                    "display": "flex",
                                    "alignItems": "center",
                                    "gap": "6px",
                                    "marginBottom": "6px",
                                    "flexWrap": "wrap",
                                },
                                children=[
                                    html.Span(
                                        ["λ", html.Sub("ex"), " ="],
                                        style={"fontSize": "12px", "color": "#333", "whiteSpace": "nowrap"},
                                    ),
                                    dcc.Input(
                                        id="overlay-lambda-ex",
                                        type="text",
                                        value=_overlay0["lambda_ex"],
                                        debounce=True,
                                        placeholder="488",
                                        style={
                                            "flex": "1 1 72px",
                                            "minWidth": "72px",
                                            "boxSizing": "border-box",
                                            "padding": "5px 6px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                            "color": "black",
                                        },
                                    ),
                                    html.Span("nm", style={"fontSize": "12px", "color": "#555"}),
                                ],
                            ),
                            html.Div(
                                style={
                                    "display": "flex",
                                    "alignItems": "center",
                                    "gap": "6px",
                                    "marginBottom": "6px",
                                    "flexWrap": "wrap",
                                },
                                children=[
                                    html.Span(
                                        ["λ", html.Sub("emi"), " ="],
                                        style={"fontSize": "12px", "color": "#333", "whiteSpace": "nowrap"},
                                    ),
                                    dcc.Input(
                                        id="overlay-lambda-emi",
                                        type="text",
                                        value=_overlay0["lambda_emi"],
                                        debounce=True,
                                        placeholder="520",
                                        style={
                                            "flex": "1 1 72px",
                                            "minWidth": "72px",
                                            "boxSizing": "border-box",
                                            "padding": "5px 6px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                            "color": "black",
                                        },
                                    ),
                                    html.Span("nm", style={"fontSize": "12px", "color": "#555"}),
                                ],
                            ),
                            html.Div(
                                style={
                                    "display": "flex",
                                    "alignItems": "center",
                                    "gap": "6px",
                                    "marginBottom": "4px",
                                    "flexWrap": "wrap",
                                },
                                children=[
                                    html.Span(
                                        "rep rate =",
                                        style={"fontSize": "12px", "color": "#333", "whiteSpace": "nowrap"},
                                    ),
                                    dcc.Input(
                                        id="overlay-rep-rate",
                                        type="text",
                                        value=_overlay0["rep_rate"],
                                        debounce=True,
                                        placeholder="80",
                                        style={
                                            "flex": "1 1 72px",
                                            "minWidth": "72px",
                                            "boxSizing": "border-box",
                                            "padding": "5px 6px",
                                            "fontSize": "12px",
                                            "border": "1px solid #ccc",
                                            "borderRadius": "4px",
                                            "color": "black",
                                        },
                                    ),
                                    dcc.Dropdown(
                                        id="overlay-rep-rate-unit",
                                        options=[
                                            {"label": "Hz", "value": "Hz"},
                                            {"label": "MHz", "value": "MHz"},
                                            {"label": "无单位", "value": ""},
                                        ],
                                        value=_overlay0["rep_rate_unit"],
                                        clearable=False,
                                        style={
                                            "flex": "0 0 88px",
                                            "minWidth": "88px",
                                            "fontSize": "12px",
                                            "color": "black",
                                        },
                                    ),
                                ],
                            ),
                            html.P(
                                "每项独立可选；有值才显示。字号 "
                                + str(GLOBAL_FONT_SIZE)
                                + "，黑色；可在右侧图中拖动位置。",
                                style={"fontSize": "9px", "color": "#999", "margin": "4px 0 0"},
                            ),
                        ],
                    ),
                    html.P(
                        "💡 右侧图中缩放 / 平移后，轴范围会同步到导出图；可拖动图例、Direct Label 与附加文字。左侧多行图例与勾选顺序一致。",
                        style={
                            "fontSize": "11px",
                            "color": "rgba(255,255,255,0.75)",
                            "margin": "0",
                            "lineHeight": "1.4",
                            "flexShrink": "0",
                        },
                    ),
                    html.Div(
                        style={
                            "background": "white",
                            "borderRadius": "8px",
                            "padding": "8px 10px",
                            "color": "#222",
                            "flexShrink": "0",
                            "marginBottom": "6px",
                        },
                        children=[
                            html.P(
                                "导出文件名（不含扩展名）",
                                style={"margin": "0 0 4px", "fontSize": "11px", "color": "#555"},
                            ),
                            dcc.Input(
                                id="export-basename",
                                type="text",
                                value=initial_ui["export_basename"],
                                debounce=True,
                                style={
                                    "width": "100%",
                                    "boxSizing": "border-box",
                                    "padding": "6px 8px",
                                    "fontSize": "12px",
                                    "border": "1px solid #ccc",
                                    "borderRadius": "4px",
                                    "color": "black",
                                },
                            ),
                            html.P(
                                "读取已保存配置（Graph_configs）",
                                style={"margin": "10px 0 4px", "fontSize": "11px", "color": "#555"},
                            ),
                            dcc.Dropdown(
                                id="config-load-dropdown",
                                options=_graph_config_options(),
                                value=None,
                                clearable=True,
                                placeholder="选择 Graph_configs 里的 json",
                                style={"fontSize": "12px", "color": "black", "marginBottom": "8px"},
                            ),
                            html.Button(
                                "📂 读取 JSON 配置",
                                id="btn-load-config",
                                n_clicks=0,
                                style={
                                    "width": "100%",
                                    "padding": "8px",
                                    "background": "#f3f4f6",
                                    "color": "#222",
                                    "border": "1px solid #d1d5db",
                                    "borderRadius": "6px",
                                    "cursor": "pointer",
                                    "fontWeight": "600",
                                    "fontSize": "12px",
                                },
                            ),
                        ],
                    ),
                    html.Button(
                        "↺ 重置为初始状态",
                        id="btn-refresh",
                        n_clicks=0,
                        style={
                            "width": "100%",
                            "padding": "10px",
                            "background": "rgba(255,255,255,0.12)",
                            "color": "white",
                            "border": "1px solid rgba(255,255,255,0.35)",
                            "borderRadius": "8px",
                            "cursor": "pointer",
                            "fontWeight": "600",
                            "fontSize": "12px",
                            "marginBottom": "8px",
                            "flexShrink": "0",
                        },
                    ),
                    html.Button(
                        "✅ 确认并生成 PDF / SVG",
                        id="btn-confirm",
                        n_clicks=0,
                        style={
                            "width": "100%",
                            "padding": "12px",
                            "background": "linear-gradient(90deg,#00b09b,#96c93d)",
                            "color": "white",
                            "border": "none",
                            "borderRadius": "10px",
                            "cursor": "pointer",
                            "fontWeight": "700",
                            "fontSize": "13px",
                            "marginTop": "4px",
                            "flexShrink": "0",
                        },
                    ),
                    html.Div(
                        id="status-msg",
                        style={
                            "fontSize": "11px",
                            "color": "rgba(255,255,255,0.65)",
                            "whiteSpace": "pre-wrap",
                            "flexShrink": "0",
                        },
                    ),
                ],
            ),
            html.Div(
                style={
                    "flex": "1",
                    "padding": "18px",
                    "overflow": "auto",
                    "display": "flex",
                    "justifyContent": "center",
                    "alignItems": "flex-start",
                },
                children=[
                    html.Div(
                        style={
                            "width": "800px",
                            "height": f"{GRAPH_HEIGHT}px",
                            "minWidth": "800px",
                            "minHeight": f"{GRAPH_HEIGHT}px",
                            "boxShadow": "0 4px 12px rgba(0,0,0,0.1)",
                            "backgroundColor": "white",
                        },
                        children=[
                            dcc.Graph(
                                id="main-graph",
                                figure=build_plotly_figure(
                                    [],
                                    config={**config, "xrange": None, "yrange": None},
                                    semilogy=False,
                                    normalize_01=False,
                                    x_title=_init_xt,
                                    y_title=_init_yt,
                                    y_pow_exp=0,
                                ),
                                style={"width": "800px", "height": f"{GRAPH_HEIGHT}px"},
                                config={
                                    "scrollZoom": True,
                                    "displayModeBar": True,
                                    "editable": True,
                                    "edits": {
                                        "annotationPosition": True,
                                        "legendPosition": True,
                                        "titleText": False,
                                        "axisTitleText": False,
                                        "annotationText": False,
                                    },
                                    "toImageButtonOptions": {
                                        "format": "svg",
                                        "width": 800,
                                        "height": GRAPH_HEIGHT,
                                    },
                                },
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )

    def _rel_from_click(click_data, selected_paths: list[str]) -> str | None:
        points = (click_data or {}).get("points") or []
        if not points:
            return None
        pt = points[0]
        rel = pt.get("customdata")
        if isinstance(rel, (list, tuple)):
            rel = rel[0] if rel else None
        if rel:
            return str(rel)
        cn = pt.get("curveNumber")
        if cn is not None:
            idx = int(cn) // 2
            if 0 <= idx < len(selected_paths):
                return selected_paths[idx]
        return None

    def _baseline_step_value(raw) -> float:
        try:
            step = float(raw)
        except (TypeError, ValueError):
            step = 10.0
        return max(0.001, step)

    @app.callback(
        [
            Output("main-graph", "figure"),
            Output("pos-store", "data"),
            Output("line-dash-store", "data"),
            Output("baseline-offset-store", "data"),
            Output("baseline-select-store", "data"),
            Output("status-msg", "children"),
            Output("file-checklist", "value"),
            Output("opt-normalize", "value"),
            Output("opt-semilogy", "value"),
            Output("axis-x-name", "value"),
            Output("axis-x-unit", "value"),
            Output("axis-y-name", "value"),
            Output("axis-y-unit", "value"),
            Output("spectra-palette", "value"),
            Output("line-width", "value"),
            Output("legend-lines", "value"),
            Output("opt-direct-label", "value"),
            Output("overlay-lambda-ex", "value"),
            Output("overlay-lambda-emi", "value"),
            Output("overlay-rep-rate", "value"),
            Output("overlay-rep-rate-unit", "value"),
            Output("export-basename", "value"),
            Output("baseline-step", "value"),
            Output("config-load-dropdown", "value"),
            Output("config-load-dropdown", "options"),
            Output("x-min-input", "value"),
            Output("x-max-input", "value"),
            Output("y-min-input", "value"),
            Output("y-max-input", "value"),
        ],
        [
            Input("file-checklist", "value"),
            Input("opt-normalize", "value"),
            Input("opt-semilogy", "value"),
            Input("opt-click-short-dash", "value"),
            Input("spectra-palette", "value"),
            Input("line-width", "value"),
            Input("line-width", "n_submit"),
            Input("axis-x-name", "value"),
            Input("axis-x-unit", "value"),
            Input("axis-y-name", "value"),
            Input("axis-y-unit", "value"),
            Input("legend-lines", "n_blur"),
            Input("opt-direct-label", "value"),
            Input("overlay-lambda-ex", "value"),
            Input("overlay-lambda-emi", "value"),
            Input("overlay-rep-rate", "value"),
            Input("overlay-rep-rate-unit", "value"),
            Input("export-basename", "value"),
            Input("main-graph", "relayoutData"),
            Input("main-graph", "clickData"),
            Input("nudge-up-btn", "n_clicks"),
            Input("nudge-down-btn", "n_clicks"),
            Input("btn-confirm", "n_clicks"),
            Input("btn-refresh", "n_clicks"),
            Input("btn-load-config", "n_clicks"),
            Input("x-min-input", "value"),
            Input("x-max-input", "value"),
            Input("y-min-input", "value"),
            Input("y-max-input", "value"),
        ],
        [
            State("pos-store", "data"),
            State("line-dash-store", "data"),
            State("baseline-offset-store", "data"),
            State("baseline-select-store", "data"),
            State("legend-lines", "value"),
            State("baseline-step", "value"),
            State("config-load-dropdown", "value"),
        ],
    )
    def on_update(
        selected,
        norm_vals,
        semi_vals,
        click_short_dash_vals,
        palette,
        lw_state,
        _lw_n_submit,
        x_name_in,
        x_unit_in,
        y_name_in,
        y_unit_in,
        _legend_n_blur,
        direct_label_vals,
        overlay_lambda_ex_in,
        overlay_lambda_emi_in,
        overlay_rep_rate_in,
        overlay_rep_rate_unit_in,
        export_basename_in,
        relayout,
        click_data,
        _nudge_up,
        _nudge_down,
        n_confirm,
        n_refresh,
        n_load_config,
        x_min_in,
        x_max_in,
        y_min_in,
        y_max_in,
        pos_data,
        line_dash_data,
        baseline_offsets_in,
        baseline_select_in,
        legend_lines_val,
        baseline_step_in,
        config_load_value_in,
    ):
        def _overlay_params_from_inputs(
            lambda_ex,
            lambda_emi,
            rep_rate,
            rep_rate_unit,
        ) -> dict:
            return normalize_overlay_params(
                {
                    "lambda_ex": lambda_ex,
                    "lambda_emi": lambda_emi,
                    "rep_rate": rep_rate,
                    "rep_rate_unit": rep_rate_unit,
                }
            )

        ctx = callback_context
        triggered = [t["prop_id"] for t in ctx.triggered] if ctx.triggered else []

        refresh = bool(n_refresh) and "btn-refresh.n_clicks" in triggered
        load_config = bool(n_load_config) and "btn-load-config.n_clicks" in triggered
        status = ""

        if refresh:
            pos = {
                "legend": {"x": 0.97, "y": 0.97},
                "text": {"x": 0.05, "y": 0.95},
                "direct_labels": {},
                "direct_label_series": [],
                "xrange": None,
                "yrange": None,
                "yrange_plotly": None,
            }
            normalize_01 = False
            semilogy = False
            selected = []
            lw = float(initial_ui["line_width"])
            x_name = initial_ui["x_name"]
            x_unit = initial_ui["x_unit"]
            y_name = initial_ui["y_name"]
            y_unit = initial_ui["y_unit"]
            palette = initial_ui["spectra_palette"]
            legend_lines_text = ""
            direct_labeling = False
            overlay_params = normalize_overlay_params({})
            export_bn = initial_ui["export_basename"]
            short_dash_rel_paths = set(initial_ui["short_dash_rel_paths"])
            line_dash_data_out = {"short_dash_rel_paths": sorted(short_dash_rel_paths)}
            baseline_offsets = {}
            baseline_select = {"rel_path": None}
            baseline_step = 10.0
            config_load_value = None
        elif load_config:
            selected = list(selected or [])
            cfg_value = str(config_load_value_in or "").strip()
            if not cfg_value:
                status = "⚠️ 请先在下拉框选择要读取的配置。"
                cfg_path = None
            else:
                cfg_path = graph_configs_dir / cfg_value
            loaded_cfg = {}
            if cfg_path and cfg_path.is_file():
                try:
                    loaded_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                except Exception as e:
                    status = f"❌ 读取配置失败: {e}"
            elif cfg_path:
                status = f"❌ 未找到配置文件: {cfg_path.name}"

            if loaded_cfg:
                pos = {
                    "legend": {"x": 0.97, "y": 0.97},
                    "text": dict(loaded_cfg.get("text_pos") or {"x": 0.05, "y": 0.95}),
                    "direct_labels": dict(loaded_cfg.get("direct_labels") or {}),
                    "direct_label_series": [],
                    "xrange": loaded_cfg.get("xrange"),
                    "yrange": loaded_cfg.get("yrange"),
                    "yrange_plotly": loaded_cfg.get("yrange_plotly"),
                }
                normalize_01 = bool(loaded_cfg.get("normalize_01"))
                semilogy = bool(loaded_cfg.get("semilogy"))
                selected = list(loaded_cfg.get("selected_rel_paths") or [])
                try:
                    lw = float(loaded_cfg.get("line_width", 1.8))
                except (TypeError, ValueError):
                    lw = 1.8
                lw = max(0.1, min(10.0, lw))
                x_name = str((loaded_cfg.get("x_name") or DEFAULT_X_NAME)).strip() or DEFAULT_X_NAME
                x_unit = str((loaded_cfg.get("x_unit") or DEFAULT_X_UNIT)).strip() or DEFAULT_X_UNIT
                y_name = str((loaded_cfg.get("y_name") or DEFAULT_Y_NAME)).strip() or DEFAULT_Y_NAME
                y_unit = str((loaded_cfg.get("y_unit") or DEFAULT_Y_UNIT)).strip() or DEFAULT_Y_UNIT
                legend_lines_text = str(loaded_cfg.get("legend_lines") or "")
                export_bn = str(
                    loaded_cfg.get("export_basename")
                    or (cfg_path.stem if cfg_path is not None else initial_ui["export_basename"])
                )
                palette = str(loaded_cfg.get("spectra_palette") or initial_ui["spectra_palette"])
                direct_labeling = bool(loaded_cfg.get("direct_labeling"))
                overlay_params = normalize_overlay_params(loaded_cfg.get("overlay_params"))
                short_dash_rel_paths = set(loaded_cfg.get("short_dash_rel_paths") or [])
                line_dash_data_out = {"short_dash_rel_paths": sorted(short_dash_rel_paths)}
                baseline_offsets = dict(loaded_cfg.get("baseline_y_offsets") or {})
                baseline_select = {"rel_path": None}
                baseline_step = _baseline_step_value(loaded_cfg.get("baseline_step", 10))
                config_load_value = cfg_path.name if cfg_path is not None else cfg_value
                status = f"✅ 已读取配置: {config_load_value}"
            else:
                pos = pos_data or {
                    "legend": {"x": 0.97, "y": 0.97},
                    "text": {"x": 0.05, "y": 0.95},
                    "direct_labels": {},
                    "direct_label_series": [],
                    "xrange": None,
                    "yrange": None,
                    "yrange_plotly": None,
                }
                normalize_01 = bool(norm_vals and "on" in norm_vals)
                semilogy = bool(semi_vals and "on" in semi_vals)
                try:
                    lw = float(lw_state) if lw_state not in (None, "") else 1.8
                except (TypeError, ValueError):
                    lw = 1.8
                lw = max(0.1, min(10.0, lw))
                x_name = (x_name_in if x_name_in is not None else "").strip() or DEFAULT_X_NAME
                x_unit = (x_unit_in if x_unit_in is not None else "").strip() or DEFAULT_X_UNIT
                y_name = (y_name_in if y_name_in is not None else "").strip() or DEFAULT_Y_NAME
                y_unit = (y_unit_in if y_unit_in is not None else "").strip() or DEFAULT_Y_UNIT
                legend_lines_text = legend_lines_val if legend_lines_val is not None else ""
                export_bn = (
                    export_basename_in
                    if export_basename_in is not None
                    else initial_ui["export_basename"]
                )
                palette = palette or initial_ui["spectra_palette"]
                direct_labeling = bool(direct_label_vals and "on" in direct_label_vals)
                overlay_params = _overlay_params_from_inputs(
                    overlay_lambda_ex_in,
                    overlay_lambda_emi_in,
                    overlay_rep_rate_in,
                    overlay_rep_rate_unit_in,
                )
                short_dash_rel_paths = set((line_dash_data or {}).get("short_dash_rel_paths") or [])
                line_dash_data_out = {"short_dash_rel_paths": sorted(short_dash_rel_paths)}
                baseline_offsets = dict(baseline_offsets_in or {})
                baseline_select = dict(baseline_select_in or {"rel_path": None})
                baseline_step = _baseline_step_value(baseline_step_in)
                config_load_value = cfg_value
        else:
            pos = pos_data or {
                "legend": {"x": 0.97, "y": 0.97},
                "text": {"x": 0.05, "y": 0.95},
                "direct_labels": {},
                "direct_label_series": [],
                "xrange": None,
                "yrange": None,
                "yrange_plotly": None,
            }
            pos.setdefault("legend", {"x": 0.97, "y": 0.97})
            pos.setdefault("text", {"x": 0.05, "y": 0.95})
            pos.setdefault("direct_labels", {})
            pos.setdefault("direct_label_series", [])
            pos.setdefault("xrange", None)
            pos.setdefault("yrange", None)
            pos.setdefault("yrange_plotly", None)

            direct_labeling = bool(direct_label_vals and "on" in direct_label_vals)
            overlay_params = _overlay_params_from_inputs(
                overlay_lambda_ex_in,
                overlay_lambda_emi_in,
                overlay_rep_rate_in,
                overlay_rep_rate_unit_in,
            )
            has_overlay = overlay_params_has_content(overlay_params)

            # 勾选曲线 / 对数 Y / 归一化 会改变轴语义；沿用旧的 yrange（尤其曾在 semilogy 下缩放）会导致刻度畸形或曲线不可见。
            if "file-checklist.value" in triggered:
                pos["xrange"] = None
                pos["yrange"] = None
                pos["yrange_plotly"] = None
                pos["direct_labels"] = {
                    k: v
                    for k, v in (pos.get("direct_labels") or {}).items()
                    if k in (selected or [])
                }
            elif "opt-semilogy.value" in triggered or "opt-normalize.value" in triggered:
                pos["yrange"] = None
                pos["yrange_plotly"] = None
                pos["direct_labels"] = {}

            normalize_01 = bool(norm_vals and "on" in norm_vals)
            semilogy = bool(semi_vals and "on" in semi_vals)
            selected = list(selected or [])
            click_short_dash = bool(click_short_dash_vals and "on" in click_short_dash_vals)
            baseline_step = _baseline_step_value(baseline_step_in)

            baseline_offsets = dict(baseline_offsets_in or {})
            baseline_select = dict(baseline_select_in or {"rel_path": None})
            baseline_select.setdefault("rel_path", None)

            short_dash_rel_paths = set((line_dash_data or {}).get("short_dash_rel_paths") or [])
            if "main-graph.clickData" in triggered and click_data:
                rel_clicked = _rel_from_click(click_data, selected)
                if rel_clicked and click_short_dash:
                    if rel_clicked in short_dash_rel_paths:
                        short_dash_rel_paths.remove(rel_clicked)
                        status = f"已取消 short dash: {rel_clicked}"
                    else:
                        short_dash_rel_paths.add(rel_clicked)
                        status = f"已设置 short dash: {rel_clicked}"
                elif rel_clicked and semilogy and not click_short_dash:
                    baseline_select = {"rel_path": rel_clicked}
                    off = float(baseline_offsets.get(rel_clicked, 0) or 0)
                    status = (
                        f"Baseline 微调: {rel_clicked}（↑ +{baseline_step:g} / ↓ -{baseline_step:g}，"
                        f"当前偏移 {off:+.4g}）"
                    )
            line_dash_data_out = {"short_dash_rel_paths": sorted(short_dash_rel_paths)}

            if semilogy and baseline_select.get("rel_path"):
                rel_sel = baseline_select["rel_path"]
                if rel_sel not in selected:
                    baseline_select = {"rel_path": None}
                elif "nudge-up-btn.n_clicks" in triggered or "nudge-down-btn.n_clicks" in triggered:
                    delta = baseline_step if "nudge-up-btn.n_clicks" in triggered else -baseline_step
                    baseline_offsets[rel_sel] = float(baseline_offsets.get(rel_sel, 0) or 0) + delta
                    status = (
                        f"Baseline 微调: {rel_sel} 偏移 {baseline_offsets[rel_sel]:+.4g} "
                        f"（步长 {baseline_step:g}）"
                    )

            if "file-checklist.value" in triggered:
                baseline_offsets = {k: v for k, v in baseline_offsets.items() if k in selected}
                if baseline_select.get("rel_path") not in selected:
                    baseline_select = {"rel_path": None}

            if "opt-semilogy.value" in triggered and not semilogy:
                baseline_select = {"rel_path": None}

            if not semilogy:
                baseline_select = {"rel_path": None}

            try:
                lw = float(lw_state) if lw_state not in (None, "") else 1.8
            except (TypeError, ValueError):
                lw = 1.8
            lw = max(0.1, min(10.0, lw))

            x_name = (x_name_in if x_name_in is not None else "").strip() or DEFAULT_X_NAME
            x_unit = (x_unit_in if x_unit_in is not None else "").strip() or DEFAULT_X_UNIT
            y_name = (y_name_in if y_name_in is not None else "").strip() or DEFAULT_Y_NAME
            y_unit = (y_unit_in if y_unit_in is not None else "").strip() or DEFAULT_Y_UNIT
            legend_lines_text = legend_lines_val if legend_lines_val is not None else ""
            export_bn = (
                export_basename_in
                if export_basename_in is not None
                else initial_ui["export_basename"]
            )
            config_load_value = config_load_value_in

        series, tmeta = collect_series(by_rel, selected, normalize_01=normalize_01)
        y_pow = 0
        if series and (not normalize_01) and (not semilogy):
            y_pow = infer_linear_y_scale_exponent([s["y"] for s in series])
        y_scale = 10.0 ** y_pow if y_pow != 0 else 1.0

        if not refresh and not load_config:
            if "main-graph.relayoutData" in triggered and relayout:
                _apply_relayout_to_pos(
                    pos,
                    relayout,
                    semilogy=semilogy,
                    direct_labeling=direct_labeling,
                    has_overlay=has_overlay,
                    y_scale=y_scale,
                )
            if "x-min-input.value" in triggered or "x-max-input.value" in triggered:
                if x_min_in is not None and x_max_in is not None:
                    pos["xrange"] = [x_min_in, x_max_in]
                else:
                    pos["xrange"] = None
            if "y-min-input.value" in triggered or "y-max-input.value" in triggered:
                if y_min_in is not None and y_max_in is not None:
                    pos["yrange"] = [y_min_in * y_scale, y_max_in * y_scale]
                    if semilogy:
                        try:
                            pos["yrange_plotly"] = [float(math.log10(y_min_in)), float(math.log10(y_max_in))]
                        except Exception:
                            pass
                else:
                    pos["yrange"] = None
                    pos["yrange_plotly"] = None

        plot_cfg = {
            "line_width": lw,
            "spectra_palette": palette or DEFAULT_SPECTRA_PALETTE,
            "legend_pos": pos["legend"],
            "text_pos": pos["text"],
            "overlay_params": overlay_params,
            "direct_labeling": direct_labeling if not refresh else False,
            "direct_labels": dict(pos.get("direct_labels") or {}),
            "xrange": pos["xrange"],
            "yrange": pos["yrange"],
            "yrange_plotly": pos.get("yrange_plotly"),
            "x_name": x_name,
            "x_unit": x_unit,
            "y_name": y_name,
            "y_unit": y_unit,
            "short_dash_rel_paths": sorted(short_dash_rel_paths),
            "baseline_y_offsets": dict(baseline_offsets),
            "baseline_selected_rel": baseline_select.get("rel_path"),
        }

        x_title, y_title = compose_display_axis_titles(
            x_name,
            x_unit,
            y_name,
            y_unit,
            time_auto=bool(tmeta.get("time_auto")),
            time_unit=tmeta.get("time_unit"),
            y_pow_exp=y_pow,
        )

        series_plot = series_with_legend_lines(series, legend_lines_text)
        pos["direct_label_series"] = [
            str(s.get("rel_path") or i) for i, s in enumerate(series_plot)
        ]

        fig = build_plotly_figure(
            series_plot,
            config=plot_cfg,
            semilogy=semilogy,
            normalize_01=normalize_01,
            x_title=x_title,
            y_title=y_title,
            y_pow_exp=y_pow,
        )

        if not refresh and "btn-confirm.n_clicks" in triggered and n_confirm:
            base_safe = sanitize_export_basename(export_bn)
            out_cfg = {
                "line_width": lw,
                "xrange": pos["xrange"],
                "yrange": pos["yrange"],
                "yrange_plotly": pos.get("yrange_plotly"),
                "x_name": x_name,
                "x_unit": x_unit,
                "y_name": y_name,
                "y_unit": y_unit,
                "short_dash_rel_paths": sorted(short_dash_rel_paths),
                "baseline_y_offsets": dict(baseline_offsets),
                "overlay_params": overlay_params,
                "text_pos": pos["text"],
                "direct_labeling": direct_labeling,
                "direct_labels": dict(pos.get("direct_labels") or {}),
                "selected_rel_paths": list(selected),
                "normalize_01": normalize_01,
                "semilogy": semilogy,
                "spectra_palette": palette or DEFAULT_SPECTRA_PALETTE,
                "legend_lines": legend_lines_text,
                "export_basename": base_safe,
                "baseline_step": baseline_step,
            }
            graph_cfg_path = graph_configs_dir / f"{base_safe}.json"
            graph_cfg_path.write_text(
                json.dumps(out_cfg, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            config_load_value = graph_cfg_path.name
            plot_matplotlib_static(
                series_plot,
                config=plot_cfg,
                semilogy=semilogy,
                save_dir=save_dir,
                x_title=x_title,
                y_title=y_title,
                y_pow_exp=y_pow,
                export_basename=base_safe,
            )
            status = (
                f"✅ 配置已保存；已写入 {base_safe}.pdf / {base_safe}.svg；"
                f"配置: {GRAPH_CONFIGS_DIRNAME}/{base_safe}.json"
            )

        if refresh:
            status = "↺ 已重置：勾选清空、轴范围与图例文字、显示选项恢复为初始值。"

        def _round_if_linear(val, is_log):
            if val is None or not math.isfinite(val): return None
            if is_log:
                return float(f"{val:.2e}") if val < 1 else round(val)
            return round(val)

        x_min_out = _round_if_linear(pos["xrange"][0], False) if pos["xrange"] else None
        x_max_out = _round_if_linear(pos["xrange"][1], False) if pos["xrange"] else None
        y_min_out = _round_if_linear(pos["yrange"][0] / y_scale, semilogy) if pos["yrange"] else None
        y_max_out = _round_if_linear(pos["yrange"][1] / y_scale, semilogy) if pos["yrange"] else None

        if refresh:
            ui_tail = (
                [],
                [],
                [],
                initial_ui["x_name"],
                initial_ui["x_unit"],
                initial_ui["y_name"],
                initial_ui["y_unit"],
                initial_ui["spectra_palette"],
                initial_ui["line_width"],
                "",
                [],
                "",
                "",
                "",
                "MHz",
                initial_ui["export_basename"],
                10,
                None,
                _graph_config_options(),
                None,
                None,
                None,
                None,
            )
        elif load_config:
            ui_tail = (
                list(selected or []),
                ["on"] if normalize_01 else [],
                ["on"] if semilogy else [],
                x_name,
                x_unit,
                y_name,
                y_unit,
                palette or initial_ui["spectra_palette"],
                lw,
                legend_lines_text,
                ["on"] if direct_labeling else [],
                overlay_params.get("lambda_ex", ""),
                overlay_params.get("lambda_emi", ""),
                overlay_params.get("rep_rate", ""),
                overlay_params.get("rep_rate_unit", "MHz"),
                export_bn,
                baseline_step,
                config_load_value,
                _graph_config_options(),
                x_min_out,
                x_max_out,
                y_min_out,
                y_max_out,
            )
        else:
            if not refresh and "btn-confirm.n_clicks" in triggered and n_confirm:
                ui_tail = (no_update,) * 17 + (config_load_value, _graph_config_options(), x_min_out, x_max_out, y_min_out, y_max_out)
            else:
                ui_tail = (no_update,) * 18 + (_graph_config_options(), x_min_out, x_max_out, y_min_out, y_max_out)

        return (
            fig,
            pos,
            line_dash_data_out,
            baseline_offsets,
            baseline_select,
            status,
            *ui_tail,
        )

    url = f"http://127.0.0.1:{DASH_PORT}/"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"控制台: {url}")
    server = threading.Thread(
        target=app.run,
        kwargs=dict(debug=False, port=DASH_PORT, use_reloader=False),
        daemon=True,
    )
    server.start()
    print("进程将保持运行，可多次保存或刷新；在本终端按 Ctrl+C 结束。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


def main():
    print("Join curves — 请选择数据根目录（将生成/覆盖 plot_manifest.xlsx）…")
    mp = generate_manifest_interactive()
    if mp is None:
        print("已取消。")
        sys.exit(0)
    try:
        df = read_workbook(mp, sheet=0)
    except Exception as e:
        print(f"❌ 无法读取清单: {e}")
        sys.exit(1)
    try:
        entries, by_rel = parse_manifest(df)
    except ValueError as e:
        print(f"❌ {e}")
        sys.exit(1)
    if not entries:
        print("❌ 清单中无有效数据行。")
        sys.exit(1)
    print(f"已载入 {len(entries)} 条路径，清单目录: {mp.parent}")
    run_app(entries, by_rel, mp)
    print("结束。")


if __name__ == "__main__":
    main()
