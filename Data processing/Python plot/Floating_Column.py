import os

import numpy as np
import plotly.graph_objects as go
import matplotlib.pyplot as plt

from PlotUtils import (
    setup_matplotlib_style,
    DynamicPlotExplorer,
    GLOBAL_FONT_SIZE,
    sample_spectra_palette,
    DEFAULT_SPECTRA_PALETTE,
    SPECTRA_COLOR_PALETTES,
)

# ══════════════════════════════════════════════════════════════════════════════
# 数据 —— HOMO / LUMO（eV），有机半导体能级。可直接在此处增删/修改。
# 每个浮动柱从 HOMO（柱底，较负）跨到 LUMO（柱顶，较不负）。
# ══════════════════════════════════════════════════════════════════════════════
MATERIALS = [
    {"name": "136",  "homo": -6.11, "lumo": -3.55},
    {"name": "CBP",  "homo": -6.00, "lumo": -2.90},
    {"name": "CzSi", "homo": -6.00, "lumo": -2.50},
    {"name": "TCTA", "homo": -5.83, "lumo": -2.43},
    {"name": "tCP",  "homo": -5.80, "lumo": -2.30},
    {"name": "PPT",  "homo": -6.70, "lumo": -3.00},
]

# 默认每个材料的柱体颜色（可在 Plotly 界面里实时修改）。
DEFAULT_PALETTE = "nature"

# 导出图尺寸：20 × 15 cm。
FIG_W_CM = 20.0
FIG_H_CM = 15.0
CM_PER_INCH = 2.54

BAR_WIDTH = 0.6
DEFAULT_Y_LABEL = "Energy (eV)"
DEFAULT_X_LABEL = ""

# 图片保存目录。
SAVE_DIR = r"C:\My files\Google drive sync\St Andrews\Data\Strasbourg materials"
# 配置文件仍存放在脚本旁边。
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "floating_column_config.json")


# ──────────────────────────────────────────────────────────────────────────────
# 小工具
# ──────────────────────────────────────────────────────────────────────────────
def _value_labels_on(config):
    return (config.get("text_params") or {}).get("show_values", "显示") == "显示"


def _resolve_label(config, key, default):
    custom = (config.get("text_params") or {}).get(key, "")
    return str(custom).strip() if custom and str(custom).strip() else default


def _y_padding(materials):
    """根据数据范围给文本标签留出空隙。"""
    lows = [m["homo"] for m in materials]
    highs = [m["lumo"] for m in materials]
    span = max(highs) - min(lows)
    return 0.04 * span if span > 0 else 0.1


def _hex_from_palette_value(value):
    """PlotUtils palettes may return either '#RRGGBB' strings or RGBA tuples."""
    if isinstance(value, str):
        return value
    r, g, b, *_ = value
    return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"


def _bar_colors(materials, config, *, as_rgba=False):
    palette_id = config.get("spectra_palette")
    if palette_id:
        return sample_spectra_palette(palette_id, len(materials), as_rgba=as_rgba)
    return [
        config["colors"].get(m["name"], "#3C5488") for m in materials
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 1. Plotly —— 交互预览
# ══════════════════════════════════════════════════════════════════════════════
def build_plotly_floating_column(data_list, config):
    materials = data_list[0]
    fig = go.Figure()

    names = [m["name"] for m in materials]
    homos = [m["homo"] for m in materials]
    lumos = [m["lumo"] for m in materials]
    heights = [lu - ho for ho, lu in zip(homos, lumos)]

    colors = _bar_colors(materials, config, as_rgba=True)
    edge_w = config["widths"].get(materials[0]["name"], 1.0) if materials else 1.0

    fig.add_trace(go.Bar(
        x=names,
        y=heights,
        base=homos,
        marker=dict(color=colors, line=dict(color="black", width=edge_w)),
        width=BAR_WIDTH,
        showlegend=False,
        hovertemplate="%{x}<br>HOMO: %{base:.2f} eV<br>LUMO: %{customdata:.2f} eV<extra></extra>",
        customdata=lumos,
    ))

    if _value_labels_on(config):
        pad = _y_padding(materials)
        for name, ho, lu in zip(names, homos, lumos):
            fig.add_annotation(x=name, y=lu + pad, text=f"{lu:.2f}",
                               showarrow=False, yanchor="bottom",
                               font=dict(size=GLOBAL_FONT_SIZE, color="black"))
            fig.add_annotation(x=name, y=ho - pad, text=f"{ho:.2f}",
                               showarrow=False, yanchor="top",
                               font=dict(size=GLOBAL_FONT_SIZE, color="black"))

    y_label = _resolve_label(config, "y_label", DEFAULT_Y_LABEL)
    x_label = _resolve_label(config, "x_label", DEFAULT_X_LABEL)

    xaxis_dict = dict(
        title=x_label,
        type="category",
        categoryorder="array",
        categoryarray=names,
        showline=True, linewidth=1.5, linecolor="black", mirror=True,
        ticks="inside", tickwidth=1.5, tickcolor="black", ticklen=8,
    )
    if config.get("xrange") and config["xrange"][0] is not None:
        xaxis_dict["range"] = config["xrange"]
    else:
        # Pad category axis so the first/last tick labels and value labels are not clipped.
        xaxis_dict["range"] = [-0.7, len(names) - 0.3]

    yaxis_dict = dict(
        title=y_label,
        showline=True, linewidth=1.5, linecolor="black", mirror=True,
        ticks="inside", tickwidth=1.5, tickcolor="black", ticklen=8,
    )
    if config.get("yrange") and config["yrange"][0] is not None:
        yaxis_dict["range"] = config["yrange"]

    fig.update_layout(
        font=dict(family="Arial", size=GLOBAL_FONT_SIZE, color="black"),
        xaxis=xaxis_dict,
        yaxis=yaxis_dict,
        plot_bgcolor="white",
        paper_bgcolor="white",
        bargap=0.0,
        uirevision="stable",
        margin=dict(l=90, r=40, t=40, b=90),
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# 2. Matplotlib —— 静态矢量导出（20×15 cm, PDF + SVG, 四边内向刻度）
# ══════════════════════════════════════════════════════════════════════════════
def plot_matplotlib_static(data_list, config, save_dir=SAVE_DIR):
    materials = data_list[0]
    setup_matplotlib_style()

    fig, ax = plt.subplots(
        figsize=(FIG_W_CM / CM_PER_INCH, FIG_H_CM / CM_PER_INCH), dpi=300,
    )
    fig.subplots_adjust(left=0.14, right=0.96, bottom=0.12, top=0.96)

    x_pos = np.arange(len(materials))
    homos = np.array([m["homo"] for m in materials])
    lumos = np.array([m["lumo"] for m in materials])
    heights = lumos - homos
    colors = _bar_colors(materials, config)
    edge_w = config["widths"].get(materials[0]["name"], 1.0) if materials else 1.0

    ax.bar(
        x_pos, heights, bottom=homos, width=BAR_WIDTH,
        color=colors, edgecolor="black", linewidth=edge_w, zorder=3,
    )

    if _value_labels_on(config):
        pad = _y_padding(materials)
        for x, ho, lu in zip(x_pos, homos, lumos):
            ax.text(x, lu + pad, f"{lu:.2f}", ha="center", va="bottom",
                    fontsize=GLOBAL_FONT_SIZE, clip_on=False)
            ax.text(x, ho - pad, f"{ho:.2f}", ha="center", va="top",
                    fontsize=GLOBAL_FONT_SIZE, clip_on=False)

    ax.set_xticks(x_pos)
    ax.set_xticklabels([m["name"] for m in materials])

    y_label = _resolve_label(config, "y_label", DEFAULT_Y_LABEL)
    x_label = _resolve_label(config, "x_label", DEFAULT_X_LABEL)
    ax.set_ylabel(y_label)
    if x_label:
        ax.set_xlabel(x_label)

    if config.get("xrange") and config["xrange"][0] is not None:
        ax.set_xlim(*config["xrange"])
    else:
        ax.set_xlim(-0.7, len(materials) - 0.3)
    if config.get("yrange") and config["yrange"][0] is not None:
        ax.set_ylim(*config["yrange"])
    else:
        pad = _y_padding(materials)
        ax.set_ylim(homos.min() - 4 * pad, lumos.max() + 4 * pad)

    # 四边边框；刻度向内，仅保留底部 x 和左侧 y（去掉顶部 x 和右侧 y 刻度）。
    for spine in ax.spines.values():
        spine.set_visible(True)
    ax.tick_params(axis="both", which="both", direction="in",
                   top=False, right=False)

    os.makedirs(save_dir, exist_ok=True)
    pdf_path = os.path.join(save_dir, "Floating_Column.pdf")
    svg_path = os.path.join(save_dir, "Floating_Column.svg")
    fig.savefig(pdf_path, format="pdf", transparent=True)
    fig.savefig(svg_path, format="svg", transparent=True)
    plt.close(fig)
    print(f"✅ 已保存: {pdf_path}")
    print(f"✅ 已保存: {svg_path}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. 主程序
# ══════════════════════════════════════════════════════════════════════════════
def main():
    print("启动 Floating Column 交互式绘图...")
    data_list = [MATERIALS]

    default_colors = sample_spectra_palette(DEFAULT_PALETTE, len(MATERIALS)) \
        if DEFAULT_PALETTE not in (None, "") else []

    explorer = DynamicPlotExplorer(
        data_list=data_list,
        config_path=CONFIG_PATH,
        build_plotly_func=build_plotly_floating_column,
        plot_mpl_func=plot_matplotlib_static,
        show_offset=False,
        export_on_confirm=True,
        keep_open_after_confirm=True,
    )

    fallback = ["#E64B35", "#4DBBD5", "#00A087", "#3C5488", "#F39B7F", "#8491B4", "#91D1C2"]
    for i, m in enumerate(MATERIALS):
        if default_colors:
            color = _hex_from_palette_value(default_colors[i])
        else:
            color = fallback[i % len(fallback)]
        explorer.add_curve_style(m["name"], m["name"], color, default_width=1.0)

    explorer.add_spectra_palette_selector(SPECTRA_COLOR_PALETTES, default=DEFAULT_PALETTE)
    explorer.add_choice_param("数值标签", "show_values", ["显示", "隐藏"], "显示")
    explorer.add_text_param("Y 轴标题", "y_label", DEFAULT_Y_LABEL)
    explorer.add_text_param("X 轴标题", "x_label", DEFAULT_X_LABEL)

    final_config = explorer.run()
    plot_matplotlib_static(data_list, final_config, SAVE_DIR)
    print("✨ 程序执行结束！")


if __name__ == "__main__":
    main()
