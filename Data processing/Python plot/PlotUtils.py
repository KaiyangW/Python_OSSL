import json
import os
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog
from dash import Dash, Input, Output, State, callback_context, dcc, html
import plotly.graph_objects as go
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import matplotlib.colors as mcolors
import matplotlib.cm as cm
import numpy as np
from matplotlib import rcParams
import cmcrameri  # noqa: F401 — register colormaps with matplotlib
import cmcrameri.cm as cmc

'''
注意：此py是所有画图的通用模板，尽量不要修改其中涉及图像的格式，plotly更新逻辑等设置

'''

GLOBAL_FONT_SIZE = 22

# Unified Matplotlib export format. Keep final PDF/SVG export settings here so
# journal formatting changes do not need to be repeated in each plotting script.
MATPLOTLIB_EXPORT_WIDTH_PX = 800
MATPLOTLIB_EXPORT_HEIGHT_PX = 600
MATPLOTLIB_EXPORT_DPI = 600
MATPLOTLIB_EXPORT_AXES_LINEWIDTH = 1.0
MATPLOTLIB_EXPORT_TICK_WIDTH = 1.0
MATPLOTLIB_EXPORT_MAJOR_TICK_SIZE = 8
MATPLOTLIB_EXPORT_MINOR_TICK_SIZE = 4
MATPLOTLIB_EXPORT_MARGIN_LEFT_PX = 90
MATPLOTLIB_EXPORT_MARGIN_RIGHT_PX = 40
MATPLOTLIB_EXPORT_MARGIN_BOTTOM_PX = 90
MATPLOTLIB_EXPORT_MARGIN_TOP_PX = 40
MATPLOTLIB_EXPORT_TWINY_RIGHT_MARGIN_PX = 90

# ══════════════════════════════════════════════════════════════════════════════
# 光谱 / 多曲线配色（Join_curves、Threshold_graph 等共用）
# ══════════════════════════════════════════════════════════════════════════════


# 渐变配色取样时丢弃最浅端的比例（10%~20% 取中值）
CMAP_LIGHT_TRIM = 0.15


def create_truncated_cmap(base_cmap, minval: float = 0.0, maxval: float = 1.0, n: int = 256):
    """对色板进行截取，丢弃两头或单侧太浅的颜色。"""
    if isinstance(base_cmap, str):
        base_cmap = plt.get_cmap(base_cmap)
    name = getattr(base_cmap, "name", "cmap")
    return mcolors.LinearSegmentedColormap.from_list(
        f"truncated_{name}",
        base_cmap(np.linspace(minval, maxval, n)),
    )


_BLACK_BODY_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "black_body",
    ["#000000", "#4A0000", "#A30000", "#E64D00", "#FF7700"],
    N=256,
)

_TRUNCATED_COOLWARM_CMAP = mcolors.LinearSegmentedColormap.from_list(
    "sub_coolwarm",
    np.vstack(
        (
            plt.get_cmap("coolwarm")(np.linspace(0.1, 0.43, 128)),
            plt.get_cmap("coolwarm")(np.linspace(0.57, 0.9, 128)),
        )
    ),
)

_DISCRETE_SPECTRA_COLOR_PALETTES: dict = {
    "nature": {
        "label": "Nature 常用",
        "colors": ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4", "#91D1C2"],
    },
    "science": {
        "label": "Science（无浅绿/浅紫）",
        "colors": ["#3B4992", "#EE0000", "#008B45", "#631879", "#008280", "#BB0021"],
    },
    "black_body_radiation": {
        "label": "Black-Body Radiation（黑体辐射，无晃眼浅黄）",
        "colors": [
            "#000000",
            "#330000",
            "#660000",
            "#990000",
            "#CC3300",
            "#FF5500",
            "#FF7700",
        ],
        "cmap": _BLACK_BODY_CMAP,
    },
    "truncated_coolwarm": {
        "label": "Truncated Coolwarm（截取冷暖，无褪色浅区）",
        "colors": ["#1A468A", "#437BB7", "#76A2CE", "#D2776D", "#B34347", "#831426"],
        "cmap": _TRUNCATED_COOLWARM_CMAP,
    },
}


CMCRAMERI_SPECTRA_PALETTE_NAMES = (
    "navia",
    "devon",
    "lajolla",
    "davos",
    "lapaz",
    "imola",
    "lipari",
)

MATPLOTLIB_SPECTRA_PALETTE_NAMES = (
    "viridis",
    "plasma",
    "magma",
    "inferno",
)

# Matplotlib 经典渐变：砍掉最浅端 20%
MATPLOTLIB_CMAP_LIGHT_TRIM = 0.20


def _cmcrameri_sequential_palettes() -> dict:
    """Fabio Crameri Scientific Colour Maps — sequential gradients (cmcrameri)."""
    palettes = {}
    for name in CMCRAMERI_SPECTRA_PALETTE_NAMES:
        base = getattr(cmc, name)
        palettes[name] = {
            "label": f"Crameri {name}",
            "cmap": create_truncated_cmap(base, maxval=1.0 - CMAP_LIGHT_TRIM),
        }
    return palettes


def _matplotlib_spectra_palettes() -> dict:
    """Matplotlib perceptually uniform colormaps, light end trimmed."""
    palettes = {}
    for name in MATPLOTLIB_SPECTRA_PALETTE_NAMES:
        palettes[name] = {
            "label": name.capitalize(),
            "cmap": create_truncated_cmap(name, maxval=1.0 - MATPLOTLIB_CMAP_LIGHT_TRIM),
        }
    return palettes


SPECTRA_COLOR_PALETTES: dict = {
    **_DISCRETE_SPECTRA_COLOR_PALETTES,
    **_cmcrameri_sequential_palettes(),
    **_matplotlib_spectra_palettes(),
}

DEFAULT_SPECTRA_PALETTE = "nature"


def sample_spectra_palette(palette_id, n, *, as_rgba=False):
    """按顺序从配色系列中取样 n 个颜色。"""
    if n <= 0:
        return []

    palette_id = palette_id or DEFAULT_SPECTRA_PALETTE
    spec = SPECTRA_COLOR_PALETTES.get(palette_id, SPECTRA_COLOR_PALETTES[DEFAULT_SPECTRA_PALETTE])
    if "colors" in spec:
        colors = spec["colors"]
        return [colors[i % len(colors)] for i in range(n)]

    cmap_key = spec["cmap"]
    cmap = cm.get_cmap(cmap_key) if isinstance(cmap_key, str) else cmap_key
    samples = np.linspace(0, 1, n) if n > 1 else np.array([0.5])
    rgba = cmap(samples)
    if as_rgba:
        return [
            f"rgba({int(r * 255)},{int(g * 255)},{int(b * 255)},1)"
            for r, g, b, _ in rgba
        ]
    return [tuple(channel) for channel in rgba]


# ══════════════════════════════════════════════════════════════════════════════
# 0. 通用文件选择
# ══════════════════════════════════════════════════════════════════════════════
def select_files(prompt_text, filetypes=None):
    if filetypes is None:
        filetypes = [("Excel files", "*.xlsx;*.xls;*.csv"), ("All files", "*.*")]
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    file_paths = filedialog.askopenfilenames(
        title=prompt_text,
        filetypes=filetypes,
    )
    root.destroy()
    if not file_paths:
        print("❌ 未选择任何文件，操作取消！")
        sys.exit()
    return list(file_paths)

# ══════════════════════════════════════════════════════════════════════════════
# 1. Matplotlib 设置
# ══════════════════════════════════════════════════════════════════════════════
def setup_matplotlib_style(font_size=GLOBAL_FONT_SIZE):
    """设置全局的 Matplotlib 字体和刻度样式。"""
    rcParams.update({
        "font.family":       "Arial",
        "font.size":         font_size,
        "figure.dpi":        MATPLOTLIB_EXPORT_DPI,
        "savefig.dpi":       MATPLOTLIB_EXPORT_DPI,
        "axes.labelsize":    font_size,
        "xtick.labelsize":   font_size,
        "ytick.labelsize":   font_size,
        "legend.fontsize":   font_size,
        "axes.linewidth":    MATPLOTLIB_EXPORT_AXES_LINEWIDTH,
        "xtick.direction":   "in",
        "ytick.direction":   "in",
        "xtick.major.width": MATPLOTLIB_EXPORT_TICK_WIDTH,
        "ytick.major.width": MATPLOTLIB_EXPORT_TICK_WIDTH,
        "xtick.minor.width": MATPLOTLIB_EXPORT_TICK_WIDTH,
        "ytick.minor.width": MATPLOTLIB_EXPORT_TICK_WIDTH,
        "xtick.major.size":  MATPLOTLIB_EXPORT_MAJOR_TICK_SIZE,
        "ytick.major.size":  MATPLOTLIB_EXPORT_MAJOR_TICK_SIZE,
        "xtick.minor.size":  MATPLOTLIB_EXPORT_MINOR_TICK_SIZE,
        "ytick.minor.size":  MATPLOTLIB_EXPORT_MINOR_TICK_SIZE,
        "xtick.top":         False,
        "ytick.right":       False,
        "legend.frameon":    False,
        "pdf.fonttype":      42,
        "svg.fonttype":      "none",
    })

def apply_matplotlib_export_axes_style(*axes):
    """Apply the unified export spine/tick format to existing Matplotlib axes."""
    for ax in axes:
        if ax is None:
            continue
        for spine in ax.spines.values():
            spine.set_linewidth(MATPLOTLIB_EXPORT_AXES_LINEWIDTH)
        ax.tick_params(
            axis="both",
            which="both",
            direction="in",
            width=MATPLOTLIB_EXPORT_TICK_WIDTH,
        )

def set_matched_right_margin(fig, right_margin_px=MATPLOTLIB_EXPORT_MARGIN_RIGHT_PX, width_px=None):
    """Set right margin using the same pixel-space convention as create_matched_fig_ax."""
    width_px = MATPLOTLIB_EXPORT_WIDTH_PX if width_px is None else width_px
    fig.subplots_adjust(right=1.0 - (right_margin_px / width_px))

def set_matched_twin_y_right_margin(fig, width_px=None):
    """Use the shared wider right margin for Matplotlib figures with a second y-axis."""
    set_matched_right_margin(fig, MATPLOTLIB_EXPORT_TWINY_RIGHT_MARGIN_PX, width_px=width_px)

def create_matched_fig_ax(width_px=None, height_px=None, dpi=None):
    """
    创建与 Plotly 预览尺寸严格匹配的 Matplotlib 画布。
    手动设置 Margin，与 Plotly 的默认边界完全对齐，保证拖拽坐标和相对字体大小一致。
    """
    width_px = MATPLOTLIB_EXPORT_WIDTH_PX if width_px is None else width_px
    height_px = MATPLOTLIB_EXPORT_HEIGHT_PX if height_px is None else height_px
    dpi = MATPLOTLIB_EXPORT_DPI if dpi is None else dpi

    # Plotly 默认 margin: l=90, r=40, t=40, b=90
    fig, ax = plt.subplots(figsize=(width_px/72, height_px/72), dpi=dpi)
    left_frac = MATPLOTLIB_EXPORT_MARGIN_LEFT_PX / width_px
    right_frac = 1.0 - (MATPLOTLIB_EXPORT_MARGIN_RIGHT_PX / width_px)
    bottom_frac = MATPLOTLIB_EXPORT_MARGIN_BOTTOM_PX / height_px
    top_frac = 1.0 - (MATPLOTLIB_EXPORT_MARGIN_TOP_PX / height_px)
    
    fig.subplots_adjust(left=left_frac, right=right_frac, bottom=bottom_frac, top=top_frac)
    apply_matplotlib_export_axes_style(ax)
    return fig, ax

# ══════════════════════════════════════════════════════════════════════════════
# 2. 通用交互式探索器框架 (Plotly + Dash)
# ══════════════════════════════════════════════════════════════════════════════
class DynamicPlotExplorer:
    def __init__(self, data_list, config_path, build_plotly_func, plot_mpl_func, graph_height=600, show_offset=True, build_plotly_func2=None, graph2_height=600, export_on_confirm=False, keep_open_after_confirm=False):
        self.data_list = data_list
        self.config_path = config_path
        self.build_plotly_func = build_plotly_func
        self.build_plotly_func2 = build_plotly_func2
        self.plot_mpl_func = plot_mpl_func
        self.graph_height = graph_height
        self.graph2_height = graph2_height
        self.show_offset = show_offset
        self.export_on_confirm = export_on_confirm
        self.keep_open_after_confirm = keep_open_after_confirm
        self.curves = []  # list of dicts: label, key, default_color, default_width, default_offset
        self.texts = []   # list of dicts: label, key, default_val, control, options
        self.spectra_palettes = {}
        self.spectra_selection_options = []
        self.font_size = GLOBAL_FONT_SIZE
        
        # 默认配置数据结构
        self.config = {
            "colors": {}, "widths": {}, "offsets": {}, "visible": {}, "text_params": {},
            "checklists": {},
            "legend_pos": {"x": 0.97, "y": 0.97}, "text_pos": {"x": 0.05, "y": 0.05},
            "xrange": None, "yrange": None, "yrange2": None,
            "xrange_spec": None, "yrange_spec": None,
            "spectra_palette": None,
            "spectra_selected_indices": [],
        }
        self.checklists = []
        
        if os.path.exists(self.config_path):
            try:
                loaded = json.load(open(self.config_path))
                if "colors" in loaded and "widths" in loaded:
                    self.config.update(loaded)
                else:
                    self.config["colors"].update(loaded)
                print(f"📂 已读取配置: {self.config_path}")
            except Exception:
                pass
                
        self._confirmed = threading.Event()

    @staticmethod
    def _dash_id(key: str) -> str:
        """Dash component IDs cannot contain '.' or '{'. Config keys may."""
        return key.replace(".", "_p_").replace("{", "_lb_").replace("}", "_rb_")

    def add_curve_style(self, label, key, default_color, default_width=1.5, default_offset=0.0, style_controls=True, default_visible=True):
        self.curves.append({"label": label, "key": key, "style_controls": style_controls})
        if key not in self.config["colors"]:
            self.config["colors"][key] = default_color
        if key not in self.config["widths"]:
            self.config["widths"][key] = default_width
        if key not in self.config["offsets"]:
            self.config["offsets"][key] = default_offset
        if key not in self.config["visible"]:
            self.config["visible"][key] = default_visible

    def add_text_param(self, label, key, default_val=""):
        self.texts.append({"label": label, "key": key, "control": "text"})
        if key not in self.config["text_params"]:
            self.config["text_params"][key] = default_val

    def add_choice_param(self, label, key, options, default_val=None):
        options = list(options or [])
        if default_val is None:
            default_val = options[0] if options else ""
        self.texts.append({"label": label, "key": key, "control": "dropdown", "options": options})
        if key not in self.config["text_params"]:
            self.config["text_params"][key] = default_val

    def add_spectra_palette_selector(self, palettes, default=DEFAULT_SPECTRA_PALETTE):
        self.spectra_palettes = palettes or {}
        if self.config.get("spectra_palette") not in self.spectra_palettes:
            self.config["spectra_palette"] = default if default in self.spectra_palettes else next(iter(self.spectra_palettes), default)

    def add_spectra_selection(self, options, default_values=None):
        """Add a checklist for selecting spectra traces by their data index."""
        self.spectra_selection_options = options or []
        valid_values = {opt["value"] for opt in self.spectra_selection_options}
        saved_values = self.config.get("spectra_selected_indices", [])
        if saved_values:
            self.config["spectra_selected_indices"] = [
                v for v in saved_values if v in valid_values
            ]
        else:
            self.config["spectra_selected_indices"] = [
                v for v in (default_values or []) if v in valid_values
            ]

    def add_checklist_param(self, label, key, options, default_values=None):
        """Add a generic multi-select checklist stored in config['checklists'][key]."""
        options = list(options or [])
        self.checklists.append({"label": label, "key": key, "options": options})
        valid_values = {opt["value"] for opt in options}
        saved_values = self.config.get("checklists", {}).get(key, [])
        if saved_values:
            self.config["checklists"][key] = [v for v in saved_values if v in valid_values]
        else:
            self.config["checklists"][key] = [v for v in (default_values or []) if v in valid_values]

    @staticmethod
    def _apply_uirevision(fig):
        """确保每张图都携带 uirevision="stable"，使 Dash 在 callback 重绘时
        保留用户的缩放/平移状态，而不是每次都重置视图。"""
        fig.update_layout(uirevision="stable")
        return fig

    def _color_picker_row(self, label, key):
        did = self._dash_id(key)
        return html.Div(style={
            "display": "flex", "alignItems": "center",
            "marginBottom": "14px", "gap": "10px",
        }, children=[
            dcc.Input(
                type="color",
                id=f"color-{did}",
                value=self.config["colors"][key],
                debounce=True,
                style={
                    "width": "32px", "height": "32px",
                    "border": "none", "borderRadius": "6px",
                    "cursor": "pointer", "padding": "0",
                    "backgroundColor": "transparent", "flexShrink": "0"
                },
            ),
            html.Div(style={"flex": "1"}, children=[
                html.Div(style={"display": "flex", "justifyContent": "space-between", "marginBottom": "2px"}, children=[
                    html.Span(label, style={"fontSize": "13px", "fontWeight": "700", "color": "#333"}),
                    html.Span(id=f"hex-{did}", children=self.config["colors"][key], style={"fontSize": "11px", "color": "#888", "fontFamily": "monospace"}),
                ]),
                html.Div(style={"display": "flex", "alignItems": "center", "gap": "6px"}, children=[
                    html.Span("Width:", style={"fontSize": "11px", "color": "#666"}),
                    dcc.Input(
                        id=f"width-{did}", type="number", value=self.config["widths"][key],
                        step=0.1, min=0.1, max=10, debounce=True,
                        style={"width": "50px", "padding": "2px 4px", "fontSize": "11px", 
                               "border": "1px solid #ddd", "borderRadius": "4px", "color": "black"}
                    ),
                ])
            ])
        ])

    def _range_input(self, id_, placeholder):
        return dcc.Input(
            id=id_, type="number", placeholder=placeholder,
            style={
                "width": "85px", "padding": "5px 8px",
                "border": "1px solid #d0d0d0", "borderRadius": "6px",
                "fontSize": "13px",
            },
        )

    def run(self):
        app = Dash(__name__, suppress_callback_exceptions=True)
        confirm_label = (
            "✅  生成 PDF/SVG（可继续修改）"
            if self.keep_open_after_confirm
            else "✅  确认配色，生成 PDF/SVG"
        )
        
        # ── 构建前端布局 ──────────────────────────────────────────────────────────
        offset_blocks = []
        if self.show_offset:
            for c in self.curves:
                key = c["key"]
                did = self._dash_id(key)
                label = c["label"]
                offset_blocks.append(
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "8px"}, children=[
                        html.Span(label.split()[0], style={"fontSize": "12px", "fontWeight": "700", "color": "#333", "width": "40px"}),
                        dcc.Input(
                            id=f"offset-{did}", type="number",
                            value=self.config["offsets"].get(key, 0.0),
                            step=0.05, debounce=True,
                            style={"width": "70px", "padding": "3px 6px", "fontSize": "12px", "border": "1px solid #ddd", "borderRadius": "4px", "color": "black"},
                        ),
                        html.Button("↺", id=f"reset-offset-{did}", n_clicks=0, title="重置为 0",
                                    style={"padding": "2px 7px", "fontSize": "13px", "border": "1px solid #ccc", "borderRadius": "4px", "cursor": "pointer", "background": "#f5f5f5", "color": "#555", "lineHeight": "1"}),
                    ])
                )
            
        text_blocks = []
        for t in self.texts:
            key = t["key"]
            did = self._dash_id(key)
            label = t["label"]
            if t.get("control") == "dropdown":
                text_blocks.append(
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "6px"}, children=[
                        html.Span(label, style={"fontSize": "12px", "fontWeight": "700", "color": "#333", "width": "58px"}),
                        dcc.Dropdown(
                            id=f"text-{did}",
                            options=[{"label": opt, "value": opt} for opt in t.get("options", [])],
                            value=self.config["text_params"].get(key, t.get("options", [""])[0]),
                            clearable=False,
                            style={"flex": "1", "fontSize": "12px", "color": "black"},
                        ),
                    ])
                )
            else:
                text_blocks.append(
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px", "marginBottom": "6px"}, children=[
                        html.Span(label, style={"fontSize": "12px", "fontWeight": "700", "color": "#333", "width": "58px"}),
                        dcc.Input(
                            id=f"text-{did}", type="text",
                            value=self.config["text_params"].get(key, ""), debounce=True,
                            style={"flex": "1", "padding": "3px 6px", "fontSize": "12px", "border": "1px solid #ddd", "borderRadius": "4px", "color": "black"},
                        ),
                    ])
                )

        # 当 show_offset=False 时，offset 输入框仍需存在于 layout 中（只是隐藏），
        # 否则 master callback 引用这些 ID 时 Dash 会静默崩溃，导致所有回调失效。
        hidden_offset_inputs = []
        if not self.show_offset:
            hidden_offset_inputs = [
                dcc.Input(
                    id=f"offset-{self._dash_id(c['key'])}", type="number",
                    value=self.config["offsets"].get(c["key"], 0.0),
                    style={"display": "none"}
                )
                for c in self.curves
            ]

        app.layout = html.Div(style={
            "fontFamily": "Arial, sans-serif",
            "display": "flex", "height": "100vh", "overflow": "hidden",
            "background": "#f0f2f5",
        }, children=[
            dcc.Store(id="pos-store", data={
                "legend": self.config["legend_pos"],
                "text":   self.config["text_pos"],
                "xrange": self.config.get("xrange"),
                "yrange": self.config.get("yrange"),
                "yrange2": self.config.get("yrange2"),
                "xrange_spec": self.config.get("xrange_spec"),
                "yrange_spec": self.config.get("yrange_spec"),
            }),
            *hidden_offset_inputs,
            # ── 左侧面板
            html.Div(style={
                "width": "270px", "minWidth": "270px",
                "padding": "22px 18px",
                "background": "linear-gradient(170deg, #1a1a2e 0%, #16213e 100%)",
                "color": "white", "display": "flex", "flexDirection": "column",
                "boxShadow": "4px 0 24px rgba(0,0,0,0.35)",
                "overflowY": "auto",
            }, children=[
                html.H3("🎨 配色控制台", style={
                    "marginTop": "0", "marginBottom": "18px", "fontSize": "17px",
                    "borderBottom": "1px solid rgba(255,255,255,0.18)",
                    "paddingBottom": "12px", "letterSpacing": "0.4px",
                }),

                # Style 控制
                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px",
                }, children=[
                    html.P("Style 控制", style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    *[self._color_picker_row(c["label"], c["key"]) for c in self.curves if c.get("style_controls", True)]
                ]) if self.curves else None,

                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                }, children=[
                    html.P("曲线显示", style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    html.P("勾选要绘制的曲线", style={"fontWeight": "400", "margin": "0 0 10px", "fontSize": "11px", "color": "#777"}),
                    dcc.Checklist(
                        id="curve-visibility",
                        options=[{"label": c["label"], "value": c["key"]} for c in self.curves],
                        value=[c["key"] for c in self.curves if self.config.get("visible", {}).get(c["key"], True)],
                        labelStyle={"display": "block", "marginBottom": "6px", "fontSize": "12px"},
                        inputStyle={"marginRight": "6px"},
                    ),
                ]) if self.curves else None,

                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                }, children=[
                    html.P("光谱配色系列", style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    html.P("按曲线顺序依次取色", style={"fontWeight": "400", "margin": "0 0 10px", "fontSize": "11px", "color": "#777"}),
                    dcc.Dropdown(
                        id="spectra-palette",
                        options=[
                            {"label": meta["label"], "value": key}
                            for key, meta in self.spectra_palettes.items()
                        ],
                        value=self.config.get("spectra_palette"),
                        clearable=False,
                        style={"fontSize": "12px", "color": "black"},
                    ),
                ]) if self.spectra_palettes else None,

                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                }, children=[
                    html.P("光谱数据选择", style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    html.P("勾选要显示和导出的能量数据", style={"fontWeight": "400", "margin": "0 0 10px", "fontSize": "11px", "color": "#777"}),
                    dcc.Checklist(
                        id="spectra-selection",
                        options=self.spectra_selection_options,
                        value=self.config.get("spectra_selected_indices", []),
                        labelStyle={"display": "block", "marginBottom": "6px", "fontSize": "12px"},
                        inputStyle={"marginRight": "6px"},
                        style={"maxHeight": "220px", "overflowY": "auto", "paddingRight": "4px"},
                    ),
                ]) if self.spectra_selection_options else None,

                *[
                    html.Div(style={
                        "background": "white", "borderRadius": "12px",
                        "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                    }, children=[
                        html.P(cl["label"], style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                        dcc.Checklist(
                            id=f"checklist-{self._dash_id(cl['key'])}",
                            options=cl["options"],
                            value=self.config.get("checklists", {}).get(cl["key"], []),
                            labelStyle={"display": "block", "marginBottom": "6px", "fontSize": "12px"},
                            inputStyle={"marginRight": "6px"},
                            style={"maxHeight": "220px", "overflowY": "auto", "paddingRight": "4px"},
                        ),
                    ])
                    for cl in self.checklists
                ],

                # Y轴基线偏移
                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                }, children=[
                    html.P("Y 轴基线偏移 (×10ⁿ)", style={"margin": "0 0 4px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    html.P("输入 n，曲线 y 值 × 10ⁿ（log 图上平移）", style={"fontSize": "10px", "color": "#aaa", "margin": "0 0 10px"}),
                    *offset_blocks
                ]) if offset_blocks else None,

                # 文本参数
                html.Div(style={
                    "background": "white", "borderRadius": "12px",
                    "padding": "14px 16px", "marginBottom": "18px", "color": "black",
                }, children=[
                    html.P("图表参数 (文本框)", style={"margin": "0 0 10px", "fontSize": "11px", "color": "#999", "textTransform": "uppercase", "letterSpacing": "0.6px"}),
                    html.P("输入数值按Enter预览，留空不显示", style={"fontWeight": "400", "margin": "0 0 10px", "fontSize": "11px", "color": "#777"}),
                    *text_blocks,
                    html.P("💡 提示：可以在图表上直接用鼠标拖动 Legend 和此文本框！", style={"marginTop": "10px", "fontSize": "11px", "color": "#00b09b", "fontWeight": "bold"}),
                ]) if text_blocks else None,


                # 确认按钮
                html.Button(confirm_label, id="btn-confirm", n_clicks=0, style={
                    "width": "100%", "padding": "13px", "background": "linear-gradient(90deg,#00b09b,#96c93d)", "color": "white", "border": "none",
                    "borderRadius": "10px", "cursor": "pointer", "fontWeight": "700", "fontSize": "14px", "boxShadow": "0 4px 16px rgba(0,176,155,0.4)", "marginBottom": "14px",
                }),
                html.Div(id="status-msg", style={"fontSize": "12px", "color": "rgba(255,255,255,0.65)", "lineHeight": "1.6", "whiteSpace": "pre-wrap"}),
            ]),

            # ── 右侧图表区
            html.Div(id="graphs-container", style={
                "flex": "1", "display": "flex", "alignItems": "center", "justifyContent": "flex-start", "flexDirection": "column",
                "padding": "20px", "overflow": "auto",
            }, children=[
                html.Div(style={
                    "width": "800px", "height": f"{self.graph_height}px", "minWidth": "800px", "minHeight": f"{self.graph_height}px",
                    "boxShadow": "0 4px 12px rgba(0,0,0,0.1)", "backgroundColor": "white",
                    "marginBottom": "20px" if self.build_plotly_func2 else "0"
                }, children=[
                    dcc.Graph(
                        id="main-graph",
                        figure=self._apply_uirevision(self.build_plotly_func(self.data_list, self.config)),
                        style={"width": "800px", "height": f"{self.graph_height}px"},
                        config={
                            "scrollZoom": True, "displayModeBar": True,
                            "editable": True,
                            "edits": {
                                "annotationPosition": True, "legendPosition": True,
                                "titleText": False, "axisTitleText": False, "annotationText": False,
                            },
                            "toImageButtonOptions": {"format": "svg", "width": 800, "height": self.graph_height},
                        },
                    ),
                ]),
                html.Div(style={
                    "width": "800px", "height": f"{self.graph2_height}px", "minWidth": "800px", "minHeight": f"{self.graph2_height}px",
                    "boxShadow": "0 4px 12px rgba(0,0,0,0.1)", "backgroundColor": "white",
                    "display": "block" if self.build_plotly_func2 else "none"
                }, children=[
                    dcc.Graph(
                        id="second-graph",
                        figure=self._apply_uirevision(self.build_plotly_func2(self.data_list, self.config)) if self.build_plotly_func2 else go.Figure(),
                        style={"width": "800px", "height": f"{self.graph2_height}px"},
                        config={
                            "scrollZoom": True, "displayModeBar": True,
                            "editable": True,
                            "edits": {
                                "annotationPosition": True, "legendPosition": True,
                                "titleText": False, "axisTitleText": False, "annotationText": False,
                            },
                            "toImageButtonOptions": {"format": "svg", "width": 800, "height": self.graph2_height},
                        },
                    ),
                ] if self.build_plotly_func2 else [])
            ]),
        ])

        # ── 动态构建回调参数 ────────────────────────────────────────────────────────
        outputs = [
            Output("main-graph", "figure"),
            Output("status-msg", "children"),
            Output("pos-store", "data")
        ]
        
        inputs = []
        for c in self.curves:
            did = self._dash_id(c["key"])
            if c.get("style_controls", True):
                inputs.append(Input(f"color-{did}", "value"))
                inputs.append(Input(f"width-{did}", "value"))
                outputs.append(Output(f"hex-{did}", "children"))
            inputs.append(Input(f"offset-{did}", "value"))
            
        for t in self.texts:
            inputs.append(Input(f"text-{self._dash_id(t['key'])}", "value"))

        if self.spectra_palettes:
            inputs.append(Input("spectra-palette", "value"))

        if self.spectra_selection_options:
            inputs.append(Input("spectra-selection", "value"))

        if self.curves:
            inputs.append(Input("curve-visibility", "value"))

        for cl in self.checklists:
            inputs.append(Input(f"checklist-{self._dash_id(cl['key'])}", "value"))
            
        inputs.extend([
            Input("main-graph", "relayoutData"),
            Input("btn-confirm", "n_clicks")
        ])
        
        if self.build_plotly_func2:
            outputs.insert(1, Output("second-graph", "figure"))
            inputs.insert(-1, Input("second-graph", "relayoutData"))
        
        states = [
            State("pos-store", "data")
        ]
        
        # 定义动态重置回调 (如果需要的话，也可以用模式匹配)
        if offset_blocks:
            @app.callback(
                [Output(f"offset-{self._dash_id(c['key'])}", "value") for c in self.curves],
                [Input(f"reset-offset-{self._dash_id(c['key'])}", "n_clicks") for c in self.curves],
                [State(f"offset-{self._dash_id(c['key'])}", "value") for c in self.curves],
                prevent_initial_call=True
            )
            def reset_offsets(*args):
                n_curves = len(self.curves)
                n_clicks = args[0:n_curves]
                vals = args[n_curves:2*n_curves]
                ctx = callback_context
                triggered = ctx.triggered[0]["prop_id"] if ctx.triggered else ""
                
                new_vals = list(vals)
                for i, c in enumerate(self.curves):
                    if f"reset-offset-{self._dash_id(c['key'])}" in triggered:
                        new_vals[i] = 0.0
                return new_vals

        @app.callback(outputs, inputs, states)
        def master_callback(*args):
            ctx = callback_context
            triggered = [t["prop_id"] for t in ctx.triggered]
            
            # 分解参数
            idx = 0
            n_curves = len(self.curves)
            
            c_colors = {}
            c_widths = {}
            c_offsets = {}
            
            # 1. 颜色、线宽、偏移
            for c in self.curves:
                k = c["key"]
                if c.get("style_controls", True):
                    c_colors[k] = args[idx]
                    try:
                        c_widths[k] = float(args[idx + 1]) if args[idx + 1] not in (None, "") else self.config["widths"].get(k, 1.5)
                    except Exception:
                        c_widths[k] = 1.5
                    try:
                        c_offsets[k] = float(args[idx + 2]) if args[idx + 2] not in (None, "") else 0.0
                    except Exception:
                        c_offsets[k] = 0.0
                    idx += 3
                else:
                    c_colors[k] = self.config["colors"].get(k, "#000000")
                    c_widths[k] = self.config["widths"].get(k, 1.5)
                    try:
                        c_offsets[k] = float(args[idx]) if args[idx] not in (None, "") else 0.0
                    except Exception:
                        c_offsets[k] = 0.0
                    idx += 1
                
            # 2. 文本参数
            n_texts = len(self.texts)
            c_texts = {}
            for t in self.texts:
                c_texts[t["key"]] = args[idx]
                idx += 1

            spectra_palette = self.config.get("spectra_palette")
            if self.spectra_palettes:
                spectra_palette = args[idx]
                idx += 1

            spectra_selected_indices = self.config.get("spectra_selected_indices", [])
            if self.spectra_selection_options:
                spectra_selected_indices = args[idx] or []
                idx += 1

            curve_visible = {
                c["key"]: c["key"] in (args[idx] or [])
                for c in self.curves
            } if self.curves else {}
            if self.curves:
                idx += 1

            checklist_values = dict(self.config.get("checklists", {}))
            for cl in self.checklists:
                checklist_values[cl["key"]] = args[idx] or []
                idx += 1
                
            # 3. relayoutData 和确认按钞
            relayout = args[idx];  idx += 1
            if self.build_plotly_func2:
                relayout2 = args[idx]; idx += 1
            else:
                relayout2 = None
            n_clicks = args[idx];  idx += 1
            
            # 4. 状态：从 store 读取位置和历史范围
            st_pos_data = args[idx]
            pos_data = st_pos_data or {
                "legend": {"x": 0.97, "y": 0.97},
                "text":   {"x": 0.05, "y": 0.05},
                "xrange": None, "yrange": None, "yrange2": None,
                "xrange_spec": None, "yrange_spec": None
            }
            # 如果旧存储中没有 xrange/yrange（升级兼容）就初始化
            pos_data.setdefault("xrange", None)
            pos_data.setdefault("yrange", None)
            pos_data.setdefault("yrange2", None)
            pos_data.setdefault("xrange_spec", None)
            pos_data.setdefault("yrange_spec", None)
            
            if "main-graph.relayoutData" in triggered and relayout:
                # 鼠标拖拽缩放/平移 → 更新范围
                if "xaxis.range[0]" in relayout:
                    pos_data["xrange"] = [relayout["xaxis.range[0]"], relayout["xaxis.range[1]"]]
                if "yaxis.range[0]" in relayout:
                    pos_data["yrange"] = [relayout["yaxis.range[0]"], relayout["yaxis.range[1]"]]
                if "yaxis2.range[0]" in relayout:
                    pos_data["yrange2"] = [relayout["yaxis2.range[0]"], relayout["yaxis2.range[1]"]]
                # 双击 Reset Axes 或 Autoscale → 清除已存范围
                if "xaxis.autorange" in relayout:
                    pos_data["xrange"] = None
                if "yaxis.autorange" in relayout:
                    pos_data["yrange"] = None
                if "yaxis2.autorange" in relayout:
                    pos_data["yrange2"] = None
                # 图例和注释拖动
                if "legend.x" in relayout: pos_data["legend"]["x"] = relayout["legend.x"]
                if "legend.y" in relayout: pos_data["legend"]["y"] = relayout["legend.y"]
                for k, v in relayout.items():
                    if k.startswith("annotations[") and k.endswith("].x"): pos_data["text"]["x"] = v
                    if k.startswith("annotations[") and k.endswith("].y"): pos_data["text"]["y"] = v

            if "second-graph.relayoutData" in triggered and relayout2:
                if "xaxis.range[0]" in relayout2:
                    pos_data["xrange_spec"] = [relayout2["xaxis.range[0]"], relayout2["xaxis.range[1]"]]
                if "yaxis.range[0]" in relayout2:
                    pos_data["yrange_spec"] = [relayout2["yaxis.range[0]"], relayout2["yaxis.range[1]"]]
                if "xaxis.autorange" in relayout2:
                    pos_data["xrange_spec"] = None
                if "yaxis.autorange" in relayout2:
                    pos_data["yrange_spec"] = None

            xrange = pos_data["xrange"]
            yrange = pos_data["yrange"]
            yrange2 = pos_data["yrange2"]
            xrange_spec = pos_data["xrange_spec"]
            yrange_spec = pos_data["yrange_spec"]

            current_config = {
                "colors": c_colors, "widths": c_widths, "offsets": c_offsets,
                "visible": curve_visible,
                "checklists": checklist_values,
                "xrange": xrange, "yrange": yrange, "yrange2": yrange2,
                "xrange_spec": xrange_spec, "yrange_spec": yrange_spec,
                "text_params": c_texts,
                "legend_pos": pos_data["legend"],
                "text_pos": pos_data["text"],
                "spectra_palette": spectra_palette,
                "spectra_selected_indices": spectra_selected_indices,
            }
            
            fig = self._apply_uirevision(self.build_plotly_func(self.data_list, current_config))
            if self.build_plotly_func2:
                fig2 = self._apply_uirevision(self.build_plotly_func2(self.data_list, current_config))
            
            # 处理保存
            status = ""
            if "btn-confirm.n_clicks" in triggered and n_clicks:
                self.config["colors"] = c_colors.copy()
                self.config["widths"] = c_widths.copy()
                self.config["offsets"] = c_offsets.copy()
                self.config["visible"] = curve_visible.copy()
                self.config["checklists"] = {k: list(v) for k, v in checklist_values.items()}
                self.config["text_params"] = c_texts.copy()
                self.config["legend_pos"] = pos_data["legend"].copy()
                self.config["text_pos"] = pos_data["text"].copy()
                self.config["xrange"] = xrange
                self.config["yrange"] = yrange
                self.config["yrange2"] = yrange2
                self.config["xrange_spec"] = xrange_spec
                self.config["yrange_spec"] = yrange_spec
                self.config["spectra_palette"] = spectra_palette
                self.config["spectra_selected_indices"] = spectra_selected_indices
                
                with open(self.config_path, "w") as f:
                    json.dump({
                        "colors": self.config["colors"],
                        "widths": self.config["widths"],
                        "offsets": self.config["offsets"],
                        "visible": self.config["visible"],
                        "checklists": self.config["checklists"],
                        "text_params": self.config["text_params"],
                        "legend_pos": self.config["legend_pos"],
                        "text_pos": self.config["text_pos"],
                        "xrange": self.config["xrange"],
                        "yrange": self.config["yrange"],
                        "yrange2": self.config["yrange2"],
                        "xrange_spec": self.config["xrange_spec"],
                        "yrange_spec": self.config["yrange_spec"],
                        "spectra_palette": self.config["spectra_palette"],
                        "spectra_selected_indices": self.config["spectra_selected_indices"],
                    }, f, indent=2)
                
                offset_strs = [f"{c['label']}:{c_offsets[c['key']]:+.3g}" for c in self.curves]
                status = "✅ 配置已保存，正在生成矢量图…\n\n" + "\n".join(offset_strs)

                if self.export_on_confirm:
                    try:
                        self.plot_mpl_func(self.data_list, self.config)
                        status = "✅ PDF/SVG 已生成。可以继续修改后再次点击生成。\n\n" + "\n".join(offset_strs)
                    except Exception as exc:
                        print(f"❌ 生成 Matplotlib 矢量图失败: {exc}")
                        status = f"❌ 生成 PDF/SVG 失败: {exc}"

                if not self.keep_open_after_confirm:
                    threading.Timer(1.2, self._confirmed.set).start()

            # 构建返回値
            ret = [fig]
            if self.build_plotly_func2:
                ret.append(fig2)
            ret.extend([status, pos_data])
            for c in self.curves:
                if c.get("style_controls", True):
                    ret.append(c_colors[c["key"]])
                
            return ret

        # ── 启动 ──────────────────────────────────────────────────────────────────
        port = 8051
        url  = f"http://127.0.0.1:{port}/"
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
        print(f"\n\U0001f310 控制台: {url}")
        print("  → 点击色块选颜色，按回车或失焦后更新图表")
        print("  → 鼠标拖拽/缩放调节坐标轴范围")
        print("  → 满意后点绳色按鈕生成 Matplotlib 矢量图\n")
        
        server_thread = threading.Thread(target=app.run, kwargs=dict(debug=False, port=port, use_reloader=False))
        server_thread.daemon = True
        server_thread.start()
        
        if self.keep_open_after_confirm:
            print("  → 可多次点击生成 PDF/SVG；修改错误数值后再次点击即可覆盖导出")
            print("  → 需要结束程序时，在本终端按 Ctrl+C\n")
            try:
                while True:
                    time.sleep(3600)
            except KeyboardInterrupt:
                print("\n🛑 已停止交互式绘图。")
        else:
            self._confirmed.wait(timeout=600)
        return self.config
