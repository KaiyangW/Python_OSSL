"""Validation plots for PL onset detection."""

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.ticker import AutoMinorLocator, FuncFormatter, MaxNLocator, NullLocator

try:
    from onset_calculator import (
        calculate_pl_onset_from_file,
        calculate_pl_onset_from_wavelength,
        calculate_pl_onset_tangent_baseline,
    )
except ImportError:  # Allows package-style imports if this folder is packaged later.
    from .onset_calculator import (
        calculate_pl_onset_from_file,
        calculate_pl_onset_from_wavelength,
        calculate_pl_onset_tangent_baseline,
    )

# hc in eV·nm for wavelength (nm) <-> photon energy (eV)
HC_EV_NM = 1240.0
CM_PER_INCH = 2.54
FIG_WIDTH_CM = 20.0
FIG_HEIGHT_CM = 15.0
# --- Typography: edit these to change plot fonts ---
FONT_FAMILY = "Arial"       # e.g. "Times New Roman", "DejaVu Sans", "Helvetica"
FONT_SIZE = 20              # axis titles (Wavelength, PL intensity)
TICK_FONT_SIZE = 20        # tick numbers on all axes (nm, eV, intensity values)
LEGEND_FONT_SIZE = "16"  # legend entries (or use an integer, e.g. 16)
ONSET_LABEL_FONT_SIZE = 18  # font size for "onset = … nm (… eV)" label only

LABEL_PAD = 16


def wavelength_nm_to_energy_ev(wavelength_nm):
    """Photon energy (eV) from wavelength (nm): E = hc / lambda."""
    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    return HC_EV_NM / wavelength_nm


def energy_ev_to_wavelength_nm(energy_ev):
    """Wavelength (nm) from photon energy (eV)."""
    energy_ev = np.asarray(energy_ev, dtype=float)
    return HC_EV_NM / energy_ev


def _style_tick_labels(axis, tick_font_size=TICK_FONT_SIZE, font_family=FONT_FAMILY):
    """Apply font family and size to numeric tick labels on one axis."""
    for label in axis.get_ticklabels():
        label.set_fontsize(tick_font_size)
        label.set_fontfamily(font_family)


def _apply_publication_style(ax, tick_font_size=TICK_FONT_SIZE):
    """Inward ticks on left y and bottom x only; no grid."""
    ax.tick_params(
        axis="y",
        which="major",
        direction="in",
        left=True,
        right=False,
        labelleft=True,
        labelright=False,
        labelsize=tick_font_size,
        width=1.2,
        length=6,
    )
    ax.tick_params(
        axis="y",
        which="minor",
        direction="in",
        left=True,
        right=False,
        width=0.8,
        length=3,
    )
    ax.tick_params(
        axis="x",
        which="major",
        direction="in",
        top=False,
        bottom=True,
        labelsize=tick_font_size,
        width=1.2,
        length=6,
    )
    ax.tick_params(
        axis="x",
        which="minor",
        direction="in",
        top=False,
        bottom=True,
        width=0.8,
        length=3,
    )
    ax.xaxis.set_minor_locator(AutoMinorLocator())
    ax.yaxis.set_minor_locator(AutoMinorLocator())
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_linewidth(1.2)

    _style_tick_labels(ax.xaxis)
    _style_tick_labels(ax.yaxis)


def _format_yaxis_scientific_in_label(ax, base_ylabel, font_size=FONT_SIZE):
    """
    Scale y tick labels and move the global multiplier into the y-axis label.

    Tick labels show mantissa values (e.g. 8, 10, 12); the axis title carries
    the global multiplier so the corner offset text is not shown.
    """
    y_min, y_max = ax.get_ylim()
    magnitude = max(abs(y_min), abs(y_max))
    if magnitude <= 0:
        exponent = 0
    else:
        exponent = int(np.floor(np.log10(magnitude)))

    scale = 10.0**exponent

    ax.yaxis.set_major_formatter(
        FuncFormatter(lambda value, _pos: f"{value / scale:g}")
    )
    ax.yaxis.get_offset_text().set_visible(False)

    if exponent == 0:
        ylabel = base_ylabel
    else:
        ylabel = rf"{base_ylabel} ($\times 10^{{{exponent}}}$)"

    ax.set_ylabel(ylabel, fontsize=font_size, fontfamily=FONT_FAMILY, labelpad=LABEL_PAD)
    _style_tick_labels(ax.yaxis)


def _set_wavelength_top_axis(secax, e_min, e_max, tick_font_size=TICK_FONT_SIZE):
    """Top x-axis: independent wavelength ticks when the bottom axis is photon energy."""
    wl_low = float(energy_ev_to_wavelength_nm(e_max))
    wl_high = float(energy_ev_to_wavelength_nm(e_min))

    locator = MaxNLocator(nbins=5, steps=[1, 2, 5, 10])
    wavelength_ticks = locator.tick_values(wl_low, wl_high)
    wavelength_ticks = wavelength_ticks[(wavelength_ticks >= wl_low) & (wavelength_ticks <= wl_high)]

    if len(wavelength_ticks) == 0:
        wavelength_ticks = np.array([wl_low, wl_high])

    energy_tick_positions = wavelength_nm_to_energy_ev(wavelength_ticks)
    secax.set_xticks(energy_tick_positions)
    secax.set_xticklabels([f"{wavelength:g}" for wavelength in wavelength_ticks])

    secax.xaxis.set_minor_locator(NullLocator())
    secax.tick_params(
        axis="x",
        which="major",
        direction="in",
        top=True,
        bottom=False,
        labeltop=True,
        labelbottom=False,
        labelsize=tick_font_size,
        pad=8,
        width=1.2,
        length=6,
    )
    _style_tick_labels(secax.xaxis, tick_font_size=tick_font_size)


def _set_energy_top_axis(secax, x_min_nm, x_max_nm, tick_font_size=TICK_FONT_SIZE):
    """Top x-axis: independent photon-energy ticks only (not mirrored wavelength ticks)."""
    e_low = float(wavelength_nm_to_energy_ev(x_max_nm))
    e_high = float(wavelength_nm_to_energy_ev(x_min_nm))

    locator = MaxNLocator(nbins=5, steps=[1, 2, 5, 10])
    energy_ticks = locator.tick_values(e_low, e_high)
    energy_ticks = energy_ticks[(energy_ticks >= e_low) & (energy_ticks <= e_high)]

    if len(energy_ticks) == 0:
        energy_ticks = np.array([e_low, e_high])

    wl_tick_positions = energy_ev_to_wavelength_nm(energy_ticks)
    secax.set_xticks(wl_tick_positions)
    secax.set_xticklabels([f"{energy:g}" for energy in energy_ticks])

    secax.xaxis.set_minor_locator(NullLocator())
    secax.tick_params(
        axis="x",
        which="major",
        direction="in",
        top=True,
        bottom=False,
        labeltop=True,
        labelbottom=False,
        labelsize=tick_font_size,
        pad=8,
        width=1.2,
        length=6,
    )
    _style_tick_labels(secax.xaxis, tick_font_size=tick_font_size)


def _tangent_segment_x(onset_x, edge_x, x_min, x_max, margin_fraction=0.08):
    """
    X-range for drawing the tangent line near the rising edge only.

    The segment spans from the onset intersection to slightly beyond the
    tangent anchor point, clipped to the PL data window.
    """
    x_lo = min(onset_x, edge_x)
    x_hi = max(onset_x, edge_x)
    span = max(x_hi - x_lo, 1e-9)
    margin = margin_fraction * span
    seg_lo = max(x_min, x_lo - margin)
    seg_hi = min(x_max, x_hi + margin)
    return np.linspace(seg_lo, seg_hi, 80)


def _energy_y_to_plot_y(onset_result, energy_y, wavelength_nm):
    """Map energy-domain intensity back to wavelength-domain plot intensity."""
    wavelength_nm = np.asarray(wavelength_nm, dtype=float)
    plot_y = np.asarray(energy_y, dtype=float) * HC_EV_NM / (wavelength_nm**2)
    offset = float(onset_result.get("plot_y_offset", 0.0))
    scale = float(onset_result.get("plot_y_scale", 1.0))
    return (plot_y - offset) / scale


def _baseline_plot_y(onset_result, x_values):
    """Return baseline y values in the current plot domain."""
    if onset_result.get("calculation_domain") == "energy":
        baseline_energy = onset_result.get("baseline_y_energy", onset_result["baseline_y"])
        return _energy_y_to_plot_y(onset_result, baseline_energy, x_values)

    return np.full_like(np.asarray(x_values, dtype=float), onset_result["baseline_y"], dtype=float)


def _tangent_plot_y(onset_result, x_values):
    """Return tangent y values in the current plot domain."""
    x_values = np.asarray(x_values, dtype=float)
    if onset_result.get("calculation_domain") == "energy":
        slope = onset_result.get("tangent_slope_energy", onset_result["tangent_slope"])
        intercept = onset_result.get("tangent_intercept_energy", onset_result["tangent_intercept"])
        energy_x = wavelength_nm_to_energy_ev(x_values)
        tangent_energy_y = slope * energy_x + intercept
        return _energy_y_to_plot_y(onset_result, tangent_energy_y, x_values)

    return onset_result["tangent_slope"] * x_values + onset_result["tangent_intercept"]


def _scaled_energy_y(onset_result, values):
    """Apply optional per-panel scaling used by normalized energy plots."""
    offset = float(onset_result.get("plot_y_offset_energy", 0.0))
    scale = float(onset_result.get("plot_y_scale_energy", 1.0))
    return (np.asarray(values, dtype=float) - offset) / scale


def _baseline_plot_y_energy(onset_result, x_values):
    """Return baseline y values on an energy-domain x axis."""
    x_values = np.asarray(x_values, dtype=float)
    baseline_y = float(onset_result.get("baseline_y_energy", onset_result["baseline_y"]))
    offset = float(onset_result.get("plot_y_offset_energy", 0.0))
    scale = float(onset_result.get("plot_y_scale_energy", 1.0))
    return np.full_like(x_values, (baseline_y - offset) / scale)


def _tangent_plot_y_energy(onset_result, x_values):
    """Return tangent y values on an energy-domain x axis."""
    x_values = np.asarray(x_values, dtype=float)
    slope = onset_result.get("tangent_slope_energy", onset_result["tangent_slope"])
    intercept = onset_result.get("tangent_intercept_energy", onset_result["tangent_intercept"])
    return _scaled_energy_y(onset_result, slope * x_values + intercept)


def _save_figure(fig, save_path, dpi):
    save_kwargs = {"dpi": dpi, "bbox_inches": "tight"}
    suffix = str(save_path).lower()
    if suffix.endswith(".pdf"):
        save_kwargs["format"] = "pdf"
    elif suffix.endswith(".png"):
        save_kwargs["format"] = "png"
    elif suffix.endswith(".svg"):
        save_kwargs["format"] = "svg"
    fig.savefig(save_path, **save_kwargs)


def plot_pl_onset_validation(
    filepath=None,
    x=None,
    intensity=None,
    onset_result=None,
    x_col=0,
    y_col=1,
    baseline_region=None,
    window_length=11,
    polyorder=3,
    derivative_mode="absolute",
    edge_region="pre_peak",
    ax=None,
    title=None,
    save_path=None,
    dpi=600,
    show=True,
):
    """
    Plot a publication-audit trail for tangent-baseline PL onset detection.

    The PL spectrum sets the axis limits. Baseline, tangent (local segment only),
    and onset markers are drawn on top without expanding the view. A secondary
    top axis shows photon energy via E (eV) = 1240 / lambda (nm).
    """
    if onset_result is None:
        if filepath is not None:
            onset_result = calculate_pl_onset_from_file(
                filepath,
                x_col=x_col,
                y_col=y_col,
                baseline_region=baseline_region,
                smooth=True,
                window_length=window_length,
                polyorder=polyorder,
                derivative_mode=derivative_mode,
                edge_region=edge_region,
            )
        elif x is None or intensity is None:
            raise ValueError("x and intensity are required when onset_result is not supplied")
        else:
            onset_result = calculate_pl_onset_from_wavelength(
                x,
                intensity,
                baseline_region=baseline_region,
                smooth=True,
                window_length=window_length,
                polyorder=polyorder,
                derivative_mode=derivative_mode,
                edge_region=edge_region,
            )

    x_data = onset_result["x"]
    y_raw = onset_result["raw_intensity"]
    y_smooth = onset_result["smoothed_intensity"]
    onset_x = onset_result["onset_x"]
    onset_y = onset_result["onset_y"]
    edge_x = onset_result["edge_x"]
    edge_y = onset_result["edge_y"]
    onset_ev = onset_result.get("onset_energy_ev", wavelength_nm_to_energy_ev(onset_x))

    figsize_in = (FIG_WIDTH_CM / CM_PER_INCH, FIG_HEIGHT_CM / CM_PER_INCH)

    if ax is None:
        fig, ax = plt.subplots(figsize=figsize_in)
    else:
        fig = ax.figure
        fig.set_size_inches(figsize_in[0], figsize_in[1])

    # 1) PL data first — axis limits follow the spectrum only
    ax.plot(
        x_data,
        y_raw,
        color="lightgray",
        alpha=0.4,
        linewidth=1.0,
        label="PL data",
        zorder=1,
    )
    ax.plot(
        x_data,
        y_smooth,
        color="black",
        linewidth=2.5,
        label="Smoothed",
        zorder=2,
    )

    x_min, x_max = float(np.min(x_data)), float(np.max(x_data))
    y_min = float(np.min([np.min(y_raw), np.min(y_smooth)]))
    y_max = float(np.max([np.max(y_raw), np.max(y_smooth)]))
    y_pad = 0.04 * (y_max - y_min) if y_max > y_min else 1.0

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min - y_pad, y_max + y_pad)

    # 2) Overlays — clipped to the PL window; tangent is a local segment only
    baseline_x = np.linspace(x_min, x_max, 300)
    ax.plot(
        baseline_x,
        _baseline_plot_y(onset_result, baseline_x),
        color="blue",
        linestyle="--",
        linewidth=2.5,
        label="Baseline",
        zorder=3,
    )

    tangent_x = _tangent_segment_x(onset_x, edge_x, x_min, x_max)
    tangent_y = _tangent_plot_y(onset_result, tangent_x)
    ax.plot(
        tangent_x,
        tangent_y,
        color="red",
        linestyle="-.",
        linewidth=2.5,
        label="Tangent",
        zorder=4,
        clip_on=True,
    )

    ax.plot(
        onset_x,
        onset_y,
        marker="*",
        color="red",
        markersize=18,
        linestyle="None",
        label="Onset",
        zorder=6,
    )
    ax.plot(
        edge_x,
        edge_y,
        marker="o",
        color="red",
        markersize=8,
        linestyle="None",
        label="Tangent point",
        zorder=5,
    )

    if title:
        ax.set_title(title, fontsize=FONT_SIZE, pad=14)

    ax.set_xlabel(
        "Wavelength (nm)",
        fontsize=FONT_SIZE,
        fontfamily=FONT_FAMILY,
        labelpad=LABEL_PAD,
    )
    _apply_publication_style(ax)
    _format_yaxis_scientific_in_label(ax, "PL intensity (counts)", font_size=FONT_SIZE)

    secax = ax.secondary_xaxis("top", functions=(wavelength_nm_to_energy_ev, energy_ev_to_wavelength_nm))
    _set_energy_top_axis(secax, x_min, x_max)

    # Place legend in the typically empty high-wavelength / low-intensity corner
    legend = ax.legend(
        loc="upper right",
        ncol=2,
        fontsize=LEGEND_FONT_SIZE,
        prop={"family": FONT_FAMILY},
        framealpha=0.8,
        frameon=True,
        borderaxespad=0.6,
    )
    legend.get_frame().set_linewidth(1.0)

    annotation = f"onset = {onset_x:.2f} nm\n({onset_ev:.3f} eV)"
    ax.annotate(
        annotation,
        xy=(onset_x, onset_y),
        xytext=(16, 20),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE,
        fontfamily=FONT_FAMILY,
        color="black",
        ha="left",
        va="bottom",
        bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=2),
        arrowprops={"arrowstyle": "->", "color": "red", "lw": 1.2},
        zorder=10,
    )

    fig.tight_layout(pad=1.4)

    if save_path is not None:
        _save_figure(fig, save_path, dpi)

    if show:
        plt.show()

    return fig, ax, onset_result


def plot_pl_onset_validation_energy(
    onset_result,
    *,
    title=None,
    save_path=None,
    dpi=600,
    show=False,
):
    """
    Plot the same onset validation figure on a photon-energy x axis.

    Uses the Jacobian-transformed I(E) arrays stored in the onset result.
    """
    if onset_result.get("calculation_domain") != "energy":
        raise ValueError("energy validation plot requires an energy-domain onset result")

    x_data = np.asarray(onset_result["energy_x"], dtype=float)
    y_raw = np.asarray(onset_result["raw_intensity_energy"], dtype=float)
    y_smooth = np.asarray(onset_result["smoothed_intensity_energy"], dtype=float)
    onset_x = float(onset_result["onset_energy_ev"])
    onset_y = float(onset_result["baseline_y_energy"])
    edge_x = float(onset_result["edge_energy_ev"])
    edge_y = float(
        onset_result["tangent_slope_energy"] * edge_x + onset_result["tangent_intercept_energy"]
    )
    onset_nm = float(onset_result["onset_x"])

    figsize_in = (FIG_WIDTH_CM / CM_PER_INCH, FIG_HEIGHT_CM / CM_PER_INCH)
    fig, ax = plt.subplots(figsize=figsize_in)

    ax.plot(
        x_data,
        y_raw,
        color="lightgray",
        alpha=0.4,
        linewidth=1.0,
        label="PL data",
        zorder=1,
    )
    ax.plot(
        x_data,
        y_smooth,
        color="black",
        linewidth=2.5,
        label="Smoothed",
        zorder=2,
    )

    x_min, x_max = float(np.min(x_data)), float(np.max(x_data))
    y_min = float(np.min([np.min(y_raw), np.min(y_smooth)]))
    y_max = float(np.max([np.max(y_raw), np.max(y_smooth)]))
    y_pad = 0.04 * (y_max - y_min) if y_max > y_min else 1.0

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min - y_pad, y_max + y_pad)

    baseline_x = np.linspace(x_min, x_max, 300)
    ax.plot(
        baseline_x,
        _baseline_plot_y_energy(onset_result, baseline_x),
        color="blue",
        linestyle="--",
        linewidth=2.5,
        label="Baseline",
        zorder=3,
    )

    tangent_x = _tangent_segment_x(onset_x, edge_x, x_min, x_max)
    tangent_y = _tangent_plot_y_energy(onset_result, tangent_x)
    ax.plot(
        tangent_x,
        tangent_y,
        color="red",
        linestyle="-.",
        linewidth=2.5,
        label="Tangent",
        zorder=4,
        clip_on=True,
    )

    ax.plot(
        onset_x,
        onset_y,
        marker="*",
        color="red",
        markersize=18,
        linestyle="None",
        label="Onset",
        zorder=6,
    )
    ax.plot(
        edge_x,
        edge_y,
        marker="o",
        color="red",
        markersize=8,
        linestyle="None",
        label="Tangent point",
        zorder=5,
    )

    if title:
        ax.set_title(title, fontsize=FONT_SIZE, pad=14)

    ax.set_xlabel(
        "Photon energy (eV)",
        fontsize=FONT_SIZE,
        fontfamily=FONT_FAMILY,
        labelpad=LABEL_PAD,
    )
    _apply_publication_style(ax)
    _format_yaxis_scientific_in_label(ax, "PL intensity (counts)", font_size=FONT_SIZE)

    secax = ax.secondary_xaxis("top", functions=(energy_ev_to_wavelength_nm, wavelength_nm_to_energy_ev))
    _set_wavelength_top_axis(secax, x_min, x_max)

    legend = ax.legend(
        loc="upper right",
        ncol=2,
        fontsize=LEGEND_FONT_SIZE,
        prop={"family": FONT_FAMILY},
        framealpha=0.8,
        frameon=True,
        borderaxespad=0.6,
    )
    legend.get_frame().set_linewidth(1.0)

    annotation = f"onset = {onset_x:.3f} eV\n({onset_nm:.2f} nm)"
    ax.annotate(
        annotation,
        xy=(onset_x, onset_y),
        xytext=(16, 20),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE,
        fontfamily=FONT_FAMILY,
        color="black",
        ha="left",
        va="bottom",
        bbox=dict(facecolor="white", alpha=0.8, edgecolor="none", pad=2),
        arrowprops={"arrowstyle": "->", "color": "red", "lw": 1.2},
        zorder=10,
    )

    fig.tight_layout(pad=1.4)

    if save_path is not None:
        _save_figure(fig, save_path, dpi)

    if show:
        plt.show()
    else:
        plt.close(fig)

    return fig, ax


def _plot_onset_trace(
    ax,
    onset_result,
    color,
    label,
    *,
    show_raw=True,
    zorder_base=1,
):
    """Draw one PL spectrum with baseline, tangent segment, and onset marker."""
    x_data = onset_result["x"]
    y_raw = onset_result["raw_intensity"]
    y_smooth = onset_result["smoothed_intensity"]
    onset_x = onset_result["onset_x"]
    onset_y = onset_result["onset_y"]
    edge_x = onset_result["edge_x"]
    edge_y = onset_result["edge_y"]

    x_min, x_max = float(np.min(x_data)), float(np.max(x_data))

    if show_raw:
        ax.plot(
            x_data,
            y_raw,
            color=color,
            alpha=0.25,
            linewidth=1.0,
            label=f"{label} data",
            zorder=zorder_base,
        )

    ax.plot(
        x_data,
        y_smooth,
        color=color,
        linewidth=2.5,
        label=f"{label} smoothed",
        zorder=zorder_base + 1,
    )
    baseline_x = np.linspace(x_min, x_max, 300)
    ax.plot(
        baseline_x,
        _baseline_plot_y(onset_result, baseline_x),
        color=color,
        linestyle="--",
        linewidth=2.0,
        alpha=0.85,
        label=f"{label} baseline",
        zorder=zorder_base + 2,
    )

    tangent_x = _tangent_segment_x(onset_x, edge_x, x_min, x_max)
    tangent_y = _tangent_plot_y(onset_result, tangent_x)
    ax.plot(
        tangent_x,
        tangent_y,
        color=color,
        linestyle="-.",
        linewidth=2.0,
        alpha=0.9,
        label=f"{label} tangent",
        zorder=zorder_base + 3,
        clip_on=True,
    )
    ax.plot(
        onset_x,
        onset_y,
        marker="*",
        color=color,
        markersize=18,
        linestyle="None",
        label=f"{label} onset",
        zorder=zorder_base + 5,
    )
    ax.plot(
        edge_x,
        edge_y,
        marker="o",
        color=color,
        markersize=8,
        linestyle="None",
        zorder=zorder_base + 4,
    )

    return onset_x, onset_y


def _plot_onset_trace_energy(
    ax,
    onset_result,
    color,
    label,
    *,
    show_raw=True,
    zorder_base=1,
):
    """Draw one PL spectrum with energy-domain baseline, tangent, and onset marker."""
    x_data = np.asarray(onset_result["energy_x"], dtype=float)
    y_raw = np.asarray(onset_result["raw_intensity_energy"], dtype=float)
    y_smooth = np.asarray(onset_result["smoothed_intensity_energy"], dtype=float)
    onset_x = float(onset_result["onset_energy_ev"])
    onset_y = float(onset_result["baseline_y_energy"])
    edge_x = float(onset_result["edge_energy_ev"])
    edge_y = float(
        onset_result["tangent_slope_energy"] * edge_x + onset_result["tangent_intercept_energy"]
    )

    x_min, x_max = float(np.min(x_data)), float(np.max(x_data))

    if show_raw:
        ax.plot(
            x_data,
            y_raw,
            color=color,
            alpha=0.25,
            linewidth=1.0,
            label=f"{label} data",
            zorder=zorder_base,
        )

    ax.plot(
        x_data,
        y_smooth,
        color=color,
        linewidth=2.5,
        label=f"{label} smoothed",
        zorder=zorder_base + 1,
    )

    baseline_x = np.linspace(x_min, x_max, 300)
    ax.plot(
        baseline_x,
        _baseline_plot_y_energy(onset_result, baseline_x),
        color=color,
        linestyle="--",
        linewidth=2.0,
        alpha=0.85,
        label=f"{label} baseline",
        zorder=zorder_base + 2,
    )

    tangent_x = _tangent_segment_x(onset_x, edge_x, x_min, x_max)
    tangent_y = _tangent_plot_y_energy(onset_result, tangent_x)
    ax.plot(
        tangent_x,
        tangent_y,
        color=color,
        linestyle="-.",
        linewidth=2.0,
        alpha=0.9,
        label=f"{label} tangent",
        zorder=zorder_base + 3,
        clip_on=True,
    )
    ax.plot(
        onset_x,
        onset_y,
        marker="*",
        color=color,
        markersize=18,
        linestyle="None",
        label=f"{label} onset",
        zorder=zorder_base + 5,
    )
    ax.plot(
        edge_x,
        edge_y,
        marker="o",
        color=color,
        markersize=8,
        linestyle="None",
        zorder=zorder_base + 4,
    )

    return onset_x, onset_y


def _scale_result_to_unit_interval(onset_result):
    """
    Min–max scale one spectrum to [0, 1] (each trace normalized independently).

    Onset wavelength is unchanged; y-related tangent parameters are scaled consistently.
    """
    y_raw = onset_result["raw_intensity"]
    y_smooth = onset_result["smoothed_intensity"]
    y_min = float(np.min(np.concatenate([y_raw, y_smooth])))
    y_max = float(np.max(np.concatenate([y_raw, y_smooth])))
    span = y_max - y_min if y_max > y_min else 1.0

    def scale(y):
        return (np.asarray(y, dtype=float) - y_min) / span

    scaled = dict(onset_result)
    scaled["raw_intensity"] = scale(y_raw)
    scaled["smoothed_intensity"] = scale(y_smooth)
    scaled["derivative"] = onset_result["derivative"] / span
    scaled["baseline_y"] = scale(onset_result["baseline_y"])
    scaled["edge_y"] = scale(onset_result["edge_y"])
    scaled["onset_y"] = scale(onset_result["onset_y"])
    scaled["tangent_slope"] = onset_result["tangent_slope"] / span
    scaled["tangent_intercept"] = (onset_result["tangent_intercept"] - y_min) / span
    scaled["plot_y_offset"] = y_min
    scaled["plot_y_scale"] = span

    if "raw_intensity_energy" in onset_result:
        y_raw_e = onset_result["raw_intensity_energy"]
        y_smooth_e = onset_result["smoothed_intensity_energy"]
        y_min_e = float(np.min(np.concatenate([y_raw_e, y_smooth_e])))
        y_max_e = float(np.max(np.concatenate([y_raw_e, y_smooth_e])))
        span_e = y_max_e - y_min_e if y_max_e > y_min_e else 1.0

        def scale_energy(y):
            return (np.asarray(y, dtype=float) - y_min_e) / span_e

        scaled["raw_intensity_energy"] = scale_energy(y_raw_e)
        scaled["smoothed_intensity_energy"] = scale_energy(y_smooth_e)
        scaled["derivative_energy"] = onset_result["derivative_energy"] / span_e
        scaled["baseline_y_energy"] = scale_energy(onset_result["baseline_y_energy"])
        scaled["tangent_slope_energy"] = onset_result["tangent_slope_energy"] / span_e
        scaled["tangent_intercept_energy"] = (
            onset_result["tangent_intercept_energy"] - y_min_e
        ) / span_e
        scaled["plot_y_offset_energy"] = y_min_e
        scaled["plot_y_scale_energy"] = span_e

    return scaled


def _dual_onset_summary_text(flu_label, phos_label, flu_onset_x, flu_onset_ev, phos_onset_x, phos_onset_ev, delta_nm, delta_ev):
    return (
        f"{flu_label} onset = {flu_onset_x:.2f} nm ({flu_onset_ev:.3f} eV)\n"
        f"{phos_label} onset = {phos_onset_x:.2f} nm ({phos_onset_ev:.3f} eV)\n"
        f"Δλ = {delta_nm:.2f} nm\n"
        f"ΔE = {delta_ev:.3f} eV  (E$_{{flu}}$ − E$_{{phos}}$)"
    )


def _draw_dual_onset_panel(
    ax,
    flu_result,
    phos_result,
    *,
    flu_label,
    phos_label,
    flu_color,
    phos_color,
    flu_onset_ev,
    phos_onset_ev,
    delta_nm,
    delta_ev,
    normalized=False,
    show_xlabel=True,
    show_summary=True,
):
    """Draw one fluorescence/phosphorescence comparison panel (raw or normalized)."""
    flu_onset_x, flu_onset_y = _plot_onset_trace(
        ax, flu_result, flu_color, flu_label, zorder_base=1
    )
    phos_onset_x, phos_onset_y = _plot_onset_trace(
        ax, phos_result, phos_color, phos_label, zorder_base=10
    )

    all_x = np.concatenate([flu_result["x"], phos_result["x"]])
    x_min, x_max = float(np.min(all_x)), float(np.max(all_x))

    if normalized:
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel(
            "Normalized PL intensity",
            fontsize=FONT_SIZE,
            fontfamily=FONT_FAMILY,
            labelpad=LABEL_PAD,
        )
    else:
        all_y = np.concatenate(
            [
                flu_result["raw_intensity"],
                flu_result["smoothed_intensity"],
                phos_result["raw_intensity"],
                phos_result["smoothed_intensity"],
            ]
        )
        y_min, y_max = float(np.min(all_y)), float(np.max(all_y))
        y_pad = 0.06 * (y_max - y_min) if y_max > y_min else 1.0
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        _format_yaxis_scientific_in_label(ax, "PL intensity (counts)", font_size=FONT_SIZE)

    if show_xlabel:
        ax.set_xlabel(
            "Wavelength (nm)",
            fontsize=FONT_SIZE,
            fontfamily=FONT_FAMILY,
            labelpad=LABEL_PAD,
        )

    _apply_publication_style(ax)

    secax = ax.secondary_xaxis("top", functions=(wavelength_nm_to_energy_ev, energy_ev_to_wavelength_nm))
    _set_energy_top_axis(secax, x_min, x_max)

    legend = ax.legend(
        loc="upper right",
        ncol=2,
        fontsize=LEGEND_FONT_SIZE,
        prop={"family": FONT_FAMILY},
        framealpha=0.85,
        frameon=True,
        borderaxespad=0.6,
    )
    legend.get_frame().set_linewidth(1.0)

    if show_summary:
        summary = _dual_onset_summary_text(
            flu_label,
            phos_label,
            flu_onset_x,
            flu_onset_ev,
            phos_onset_x,
            phos_onset_ev,
            delta_nm,
            delta_ev,
        )
        ax.text(
            0.02,
            0.98,
            summary,
            transform=ax.transAxes,
            fontsize=ONSET_LABEL_FONT_SIZE,
            fontfamily=FONT_FAMILY,
            va="top",
            ha="left",
            bbox=dict(facecolor="white", alpha=0.88, edgecolor="0.6", pad=6),
            zorder=20,
        )

    ax.annotate(
        f"{flu_onset_x:.2f} nm",
        xy=(flu_onset_x, flu_onset_y),
        xytext=(12, 18),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        color=flu_color,
        ha="left",
        va="bottom",
        arrowprops={"arrowstyle": "->", "color": flu_color, "lw": 1.2},
        zorder=10,
    )
    ax.annotate(
        f"{phos_onset_x:.2f} nm",
        xy=(phos_onset_x, phos_onset_y),
        xytext=(12, -22),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        color=phos_color,
        ha="left",
        va="top",
        arrowprops={"arrowstyle": "->", "color": phos_color, "lw": 1.2},
        zorder=10,
    )

    panel_title = "Normalized (0–1)" if normalized else "Raw counts"
    ax.text(
        0.98,
        0.98,
        panel_title,
        transform=ax.transAxes,
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        ha="right",
        va="top",
        bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=3),
        zorder=20,
    )

    return flu_onset_x, phos_onset_x


def _draw_dual_onset_panel_energy(
    ax,
    flu_result,
    phos_result,
    *,
    flu_label,
    phos_label,
    flu_color,
    phos_color,
    flu_onset_ev,
    phos_onset_ev,
    flu_onset_nm,
    phos_onset_nm,
    delta_nm,
    delta_ev,
    normalized=False,
    show_xlabel=True,
    show_summary=True,
):
    """Draw one fluorescence/phosphorescence comparison panel on an energy axis."""
    flu_onset_x, flu_onset_y = _plot_onset_trace_energy(
        ax, flu_result, flu_color, flu_label, zorder_base=1
    )
    phos_onset_x, phos_onset_y = _plot_onset_trace_energy(
        ax, phos_result, phos_color, phos_label, zorder_base=10
    )

    all_x = np.concatenate(
        [
            np.asarray(flu_result["energy_x"], dtype=float),
            np.asarray(phos_result["energy_x"], dtype=float),
        ]
    )
    x_min, x_max = float(np.min(all_x)), float(np.max(all_x))

    if normalized:
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(-0.05, 1.05)
        ax.set_ylabel(
            "Normalized PL intensity",
            fontsize=FONT_SIZE,
            fontfamily=FONT_FAMILY,
            labelpad=LABEL_PAD,
        )
    else:
        all_y = np.concatenate(
            [
                flu_result["raw_intensity_energy"],
                flu_result["smoothed_intensity_energy"],
                phos_result["raw_intensity_energy"],
                phos_result["smoothed_intensity_energy"],
            ]
        )
        y_min, y_max = float(np.min(all_y)), float(np.max(all_y))
        y_pad = 0.06 * (y_max - y_min) if y_max > y_min else 1.0
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        _format_yaxis_scientific_in_label(ax, "PL intensity (counts)", font_size=FONT_SIZE)

    if show_xlabel:
        ax.set_xlabel(
            "Photon energy (eV)",
            fontsize=FONT_SIZE,
            fontfamily=FONT_FAMILY,
            labelpad=LABEL_PAD,
        )

    _apply_publication_style(ax)

    secax = ax.secondary_xaxis("top", functions=(energy_ev_to_wavelength_nm, wavelength_nm_to_energy_ev))
    _set_wavelength_top_axis(secax, x_min, x_max)

    legend = ax.legend(
        loc="upper right",
        ncol=2,
        fontsize=LEGEND_FONT_SIZE,
        prop={"family": FONT_FAMILY},
        framealpha=0.85,
        frameon=True,
        borderaxespad=0.6,
    )
    legend.get_frame().set_linewidth(1.0)

    if show_summary:
        summary = _dual_onset_summary_text(
            flu_label,
            phos_label,
            flu_onset_nm,
            flu_onset_ev,
            phos_onset_nm,
            phos_onset_ev,
            delta_nm,
            delta_ev,
        )
        ax.text(
            0.02,
            0.98,
            summary,
            transform=ax.transAxes,
            fontsize=ONSET_LABEL_FONT_SIZE,
            fontfamily=FONT_FAMILY,
            va="top",
            ha="left",
            bbox=dict(facecolor="white", alpha=0.88, edgecolor="0.6", pad=6),
            zorder=20,
        )

    ax.annotate(
        f"{flu_onset_ev:.3f} eV",
        xy=(flu_onset_x, flu_onset_y),
        xytext=(12, 18),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        color=flu_color,
        ha="left",
        va="bottom",
        arrowprops={"arrowstyle": "->", "color": flu_color, "lw": 1.2},
        zorder=10,
    )
    ax.annotate(
        f"{phos_onset_ev:.3f} eV",
        xy=(phos_onset_x, phos_onset_y),
        xytext=(12, -22),
        textcoords="offset points",
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        color=phos_color,
        ha="left",
        va="top",
        arrowprops={"arrowstyle": "->", "color": phos_color, "lw": 1.2},
        zorder=10,
    )

    panel_title = "Normalized (0–1)" if normalized else "Raw counts"
    ax.text(
        0.98,
        0.98,
        panel_title,
        transform=ax.transAxes,
        fontsize=ONSET_LABEL_FONT_SIZE - 2,
        fontfamily=FONT_FAMILY,
        ha="right",
        va="top",
        bbox=dict(facecolor="white", alpha=0.75, edgecolor="none", pad=3),
        zorder=20,
    )

    return flu_onset_x, phos_onset_x


def plot_pl_dual_onset_comparison(
    flu_result,
    phos_result,
    *,
    flu_label="Fluorescence",
    phos_label="Phosphorescence",
    save_path=None,
    dpi=600,
    show=True,
):
    """
    Plot fluorescence and phosphorescence spectra with both onsets and their difference.

    Two stacked panels are shown: raw counts (top) and per-spectrum (0, 1) normalization (bottom).

    Parameters
    ----------
    flu_result, phos_result : dict
        Onset result dictionaries from calculate_pl_onset_from_file.
    """
    figsize_in = (FIG_WIDTH_CM / CM_PER_INCH, 2 * FIG_HEIGHT_CM / CM_PER_INCH)
    fig, (ax_raw, ax_norm) = plt.subplots(2, 1, figsize=figsize_in, sharex=True)

    flu_color = "#1f77b4"
    phos_color = "#d62728"

    flu_onset_x = float(flu_result["onset_x"])
    phos_onset_x = float(phos_result["onset_x"])
    flu_onset_ev = float(flu_result.get("onset_energy_ev", wavelength_nm_to_energy_ev(flu_onset_x)))
    phos_onset_ev = float(phos_result.get("onset_energy_ev", wavelength_nm_to_energy_ev(phos_onset_x)))
    delta_nm = float(flu_onset_x - phos_onset_x)
    delta_ev = float(flu_onset_ev - phos_onset_ev)

    _draw_dual_onset_panel(
        ax_raw,
        flu_result,
        phos_result,
        flu_label=flu_label,
        phos_label=phos_label,
        flu_color=flu_color,
        phos_color=phos_color,
        flu_onset_ev=flu_onset_ev,
        phos_onset_ev=phos_onset_ev,
        delta_nm=delta_nm,
        delta_ev=delta_ev,
        normalized=False,
        show_xlabel=False,
        show_summary=True,
    )

    flu_norm = _scale_result_to_unit_interval(flu_result)
    phos_norm = _scale_result_to_unit_interval(phos_result)
    _draw_dual_onset_panel(
        ax_norm,
        flu_norm,
        phos_norm,
        flu_label=flu_label,
        phos_label=phos_label,
        flu_color=flu_color,
        phos_color=phos_color,
        flu_onset_ev=flu_onset_ev,
        phos_onset_ev=phos_onset_ev,
        delta_nm=delta_nm,
        delta_ev=delta_ev,
        normalized=True,
        show_xlabel=True,
        show_summary=False,
    )

    fig.tight_layout(pad=1.4)

    if save_path is not None:
        _save_figure(fig, save_path, dpi)

    if show:
        plt.show()

    return fig, (ax_raw, ax_norm), {
        "flu_onset_nm": flu_onset_x,
        "flu_onset_ev": flu_onset_ev,
        "phos_onset_nm": phos_onset_x,
        "phos_onset_ev": phos_onset_ev,
        "delta_nm": delta_nm,
        "delta_ev": delta_ev,
    }


def plot_pl_dual_onset_comparison_energy(
    flu_result,
    phos_result,
    *,
    flu_label="Fluorescence",
    phos_label="Phosphorescence",
    save_path=None,
    dpi=600,
    show=False,
):
    """
    Plot the dual fluorescence/phosphorescence comparison on a photon-energy x axis.
    """
    if flu_result.get("calculation_domain") != "energy":
        raise ValueError("energy dual comparison requires energy-domain onset results")
    if phos_result.get("calculation_domain") != "energy":
        raise ValueError("energy dual comparison requires energy-domain onset results")

    figsize_in = (FIG_WIDTH_CM / CM_PER_INCH, 2 * FIG_HEIGHT_CM / CM_PER_INCH)
    fig, (ax_raw, ax_norm) = plt.subplots(2, 1, figsize=figsize_in, sharex=True)

    flu_color = "#1f77b4"
    phos_color = "#d62728"

    flu_onset_nm = float(flu_result["onset_x"])
    phos_onset_nm = float(phos_result["onset_x"])
    flu_onset_ev = float(flu_result["onset_energy_ev"])
    phos_onset_ev = float(phos_result["onset_energy_ev"])
    delta_nm = float(flu_onset_nm - phos_onset_nm)
    delta_ev = float(flu_onset_ev - phos_onset_ev)

    _draw_dual_onset_panel_energy(
        ax_raw,
        flu_result,
        phos_result,
        flu_label=flu_label,
        phos_label=phos_label,
        flu_color=flu_color,
        phos_color=phos_color,
        flu_onset_ev=flu_onset_ev,
        phos_onset_ev=phos_onset_ev,
        flu_onset_nm=flu_onset_nm,
        phos_onset_nm=phos_onset_nm,
        delta_nm=delta_nm,
        delta_ev=delta_ev,
        normalized=False,
        show_xlabel=False,
        show_summary=True,
    )

    flu_norm = _scale_result_to_unit_interval(flu_result)
    phos_norm = _scale_result_to_unit_interval(phos_result)
    _draw_dual_onset_panel_energy(
        ax_norm,
        flu_norm,
        phos_norm,
        flu_label=flu_label,
        phos_label=phos_label,
        flu_color=flu_color,
        phos_color=phos_color,
        flu_onset_ev=flu_onset_ev,
        phos_onset_ev=phos_onset_ev,
        flu_onset_nm=flu_onset_nm,
        phos_onset_nm=phos_onset_nm,
        delta_nm=delta_nm,
        delta_ev=delta_ev,
        normalized=True,
        show_xlabel=True,
        show_summary=False,
    )

    fig.tight_layout(pad=1.4)

    if save_path is not None:
        _save_figure(fig, save_path, dpi)

    if show:
        plt.show()
    else:
        plt.close(fig)

    return fig, (ax_raw, ax_norm), {
        "flu_onset_nm": flu_onset_nm,
        "flu_onset_ev": flu_onset_ev,
        "phos_onset_nm": phos_onset_nm,
        "phos_onset_ev": phos_onset_ev,
        "delta_nm": delta_nm,
        "delta_ev": delta_ev,
    }
