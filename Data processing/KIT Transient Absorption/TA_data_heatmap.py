"""KIT Transient Absorption 2D heatmap engine.

This module is imported by ``TA_data_reading.py`` and exposes
:class:`TAHeatmapViewer`, a secondary window that draws the parent viewer's
full TA grid as a 2D heatmap (after baseline correction and any time shift
applied in the main window). It cannot be run on its own: file loading,
baseline correction, and time shifting are all owned by the curve viewer.

The heatmap intentionally always shows the entire grid, regardless of which
spectra/kinetics curves are selected in the parent UI.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


DEFAULT_CMAP = "RdBu_r"

# Local copies of styling constants so this module stays standalone-importable
# without forcing a circular import on ``TA_data_reading``.
DARK_BG = "#1a1a1a"
PANEL_BG = "#2b2b2b"
Y_LABEL = "Ave Delta T/T"


def centers_to_edges(values: np.ndarray) -> np.ndarray:
    """Convert monotonic center coordinates to pcolormesh cell edges."""
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        raise ValueError("Cannot build edges for an empty axis.")
    if values.size == 1:
        center = float(values[0])
        width = abs(center) * 0.05 if center != 0 else 0.5
        return np.array([center - width, center + width], dtype=float)

    edges = np.empty(values.size + 1, dtype=float)
    edges[1:-1] = (values[:-1] + values[1:]) / 2.0
    edges[0] = values[0] - (values[1] - values[0]) / 2.0
    edges[-1] = values[-1] + (values[-1] - values[-2]) / 2.0
    return edges


class TAHeatmapViewer(ctk.CTkToplevel):
    """Secondary window that draws the full TA grid as a 2D heatmap.

    The viewer reads the parent application's ``data`` attribute every time it
    refreshes, so any baseline correction or time shift applied by the parent
    is reflected here. The full wavelength x time grid is always drawn; the
    spectra/kinetics curve selection in the parent has no effect on this plot.
    """

    def __init__(self, master) -> None:
        super().__init__(master)
        self._parent_app = master
        self.title("KIT TA 2D Heatmap")
        self.geometry("1200x760")
        self.minsize(980, 620)
        self.configure(fg_color=DARK_BG)

        self.mesh = None
        self.colorbar = None

        self.status_var = tk.StringVar(value="")
        self.cmap_var = tk.StringVar(value=DEFAULT_CMAP)
        self.symmetric_var = tk.BooleanVar(value=True)
        self.vmin_var = tk.StringVar(value="")
        self.vmax_var = tk.StringVar(value="")
        self.time_min_var = tk.StringVar(value="")
        self.time_max_var = tk.StringVar(value="")
        self.wavelength_min_var = tk.StringVar(value="")
        self.wavelength_max_var = tk.StringVar(value="")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh()

    # ------------------------------------------------------------------
    # Public API used by the parent viewer
    # ------------------------------------------------------------------
    @property
    def data(self):
        return getattr(self._parent_app, "data", None)

    def refresh(self) -> None:
        """Re-read the parent's TAData and redraw the heatmap."""
        if self.data is None:
            self.status_var.set("Parent viewer has no data loaded.")
            self.draw_heatmap(preserve_limits=False)
            return
        self._fill_axis_entries()
        self.draw_heatmap(preserve_limits=False)
        self.status_var.set(self._status_text())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        sidebar = ctk.CTkScrollableFrame(self, width=300, fg_color=PANEL_BG)
        sidebar.pack(side="left", fill="y", padx=10, pady=10)
        sidebar.pack_propagate(False)

        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        self.figure = Figure(figsize=(9.0, 6.2), dpi=100)
        self.figure.patch.set_facecolor(DARK_BG)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(DARK_BG)

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.toolbar = NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=False)
        self.toolbar.update()
        self.toolbar.pack(fill="x", padx=10, pady=(4, 10))

        pad = 10
        ctk.CTkLabel(
            sidebar,
            text="TA Heatmap",
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=pad, pady=(10, 4))

        ctk.CTkLabel(
            sidebar,
            text=(
                "Shows the full grid after baseline correction and time shift. "
                "Use the main viewer to change those; this window will refresh "
                "automatically."
            ),
            anchor="w",
            justify="left",
            wraplength=250,
            text_color="gray70",
        ).pack(fill="x", padx=pad, pady=(0, 8))

        ctk.CTkButton(
            sidebar,
            text="Refresh from main viewer",
            command=self.refresh,
        ).pack(fill="x", padx=pad, pady=2)

        self._section(sidebar, "Axes")
        self._range_entry(sidebar, "Time ns", self.time_min_var, self.time_max_var)
        self._range_entry(
            sidebar, "Wavelength nm", self.wavelength_min_var, self.wavelength_max_var
        )
        ctk.CTkButton(
            sidebar, text="Apply axis limits", command=self.apply_axis_limits
        ).pack(fill="x", padx=pad, pady=(4, 2))
        ctk.CTkButton(sidebar, text="Autoscale axes", command=self.autoscale_axes).pack(
            fill="x", padx=pad, pady=2
        )

        self._section(sidebar, "Color")
        ctk.CTkLabel(sidebar, text="Colormap", anchor="w").pack(
            fill="x", padx=pad, pady=(0, 2)
        )
        ctk.CTkOptionMenu(
            sidebar,
            variable=self.cmap_var,
            values=["RdBu_r", "seismic", "coolwarm", "viridis", "plasma", "magma"],
            command=lambda _value: self.draw_heatmap(preserve_limits=True),
        ).pack(fill="x", padx=pad, pady=2)
        ctk.CTkCheckBox(
            sidebar,
            text="Symmetric about zero",
            variable=self.symmetric_var,
            command=lambda: self.draw_heatmap(preserve_limits=True),
        ).pack(fill="x", padx=pad, pady=4)
        self._range_entry(sidebar, "Color", self.vmin_var, self.vmax_var)
        ctk.CTkButton(
            sidebar, text="Apply color scale", command=self._apply_color_scale
        ).pack(fill="x", padx=pad, pady=(4, 2))
        ctk.CTkButton(
            sidebar, text="Auto color scale", command=self.auto_color_scale
        ).pack(fill="x", padx=pad, pady=2)

        ctk.CTkLabel(
            sidebar,
            textvariable=self.status_var,
            anchor="w",
            justify="left",
            wraplength=250,
            text_color="#C7C7C7",
        ).pack(fill="x", padx=pad, pady=(18, 10))

    @staticmethod
    def _section(parent, text: str) -> None:
        ctk.CTkLabel(
            parent,
            text=text,
            font=ctk.CTkFont(size=14, weight="bold"),
            anchor="w",
        ).pack(fill="x", padx=10, pady=(14, 4))

    @staticmethod
    def _range_entry(
        parent,
        label: str,
        low_var: tk.StringVar,
        high_var: tk.StringVar,
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=2)
        row.grid_columnconfigure((1, 2), weight=1)
        ctk.CTkLabel(row, text=label, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        ctk.CTkEntry(row, textvariable=low_var, placeholder_text="min").grid(
            row=0, column=1, sticky="ew", padx=(0, 4)
        )
        ctk.CTkEntry(row, textvariable=high_var, placeholder_text="max").grid(
            row=0, column=2, sticky="ew"
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        notify = getattr(self._parent_app, "_on_heatmap_window_closed", None)
        if callable(notify):
            try:
                notify(self)
            except Exception:
                pass
        self.destroy()

    # ------------------------------------------------------------------
    # Helpers reading from the parent's TAData
    # ------------------------------------------------------------------
    def _status_text(self) -> str:
        data = self.data
        if data is None:
            return "Parent viewer has no data loaded."
        path_name = (
            data.path.name if getattr(data, "path", None) is not None else "current data"
        )
        parts = [
            f"Showing {path_name}",
            f"{data.wavelengths_nm.size} wavelengths x {data.times_s.size} time points",
        ]
        if getattr(data, "baseline_corrected", False):
            parts.append("baseline corrected")
        shift_ns = getattr(data, "time_shift_ns", 0.0)
        if shift_ns:
            parts.append(f"shift {shift_ns:.6g} ns")
        return " | ".join(parts) + "."

    def _fill_axis_entries(self) -> None:
        data = self.data
        if data is None:
            return
        self.time_min_var.set(f"{data.min_time_ns:.6g}")
        self.time_max_var.set(f"{data.max_time_ns:.6g}")
        self.wavelength_min_var.set(f"{data.min_wavelength_nm:.6g}")
        self.wavelength_max_var.set(f"{data.max_wavelength_nm:.6g}")

    def _heatmap_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        data = self.data
        if data is None:
            raise ValueError("No TA data available.")

        times_ns = np.asarray(data.times_s, dtype=float) * 1e9
        wavelengths = np.asarray(data.wavelengths_nm, dtype=float)
        signal = np.asarray(data.signal, dtype=float)

        finite_time = np.isfinite(times_ns)
        finite_wavelength = np.isfinite(wavelengths)
        if not finite_time.any() or not finite_wavelength.any():
            raise ValueError("Could not find finite time or wavelength values.")

        time_order = np.where(finite_time)[0][np.argsort(times_ns[finite_time])]
        wavelength_order = np.where(finite_wavelength)[0][
            np.argsort(wavelengths[finite_wavelength])
        ]
        return (
            times_ns[time_order],
            wavelengths[wavelength_order],
            signal[np.ix_(wavelength_order, time_order)],
        )

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------
    def draw_heatmap(self, preserve_limits: bool = True) -> None:
        if self.data is None:
            self.ax.clear()
            self.ax.set_facecolor(DARK_BG)
            if self.colorbar is not None:
                try:
                    self.colorbar.remove()
                except Exception:
                    pass
                self.colorbar = None
            self.ax.text(
                0.5,
                0.5,
                "Load a TA file in the main viewer to draw a heatmap",
                transform=self.ax.transAxes,
                ha="center",
                va="center",
                color="0.7",
            )
            self.canvas.draw_idle()
            return

        saved_xlim = self.ax.get_xlim()
        saved_ylim = self.ax.get_ylim()

        try:
            times_ns, wavelengths, signal = self._heatmap_arrays()
            vmin, vmax = self._color_limits(signal)
        except Exception as exc:
            messagebox.showerror("Plot failed", str(exc), parent=self)
            return

        self.ax.clear()
        self.ax.set_facecolor(DARK_BG)
        if self.colorbar is not None:
            try:
                self.colorbar.remove()
            except Exception:
                pass
            self.colorbar = None

        self.mesh = self.ax.pcolormesh(
            centers_to_edges(times_ns),
            centers_to_edges(wavelengths),
            signal,
            shading="auto",
            cmap=self.cmap_var.get(),
            vmin=vmin,
            vmax=vmax,
        )
        self.colorbar = self.figure.colorbar(self.mesh, ax=self.ax, pad=0.015)
        self.colorbar.set_label(Y_LABEL, color="white")
        self.colorbar.ax.yaxis.set_tick_params(color="white")
        for label in self.colorbar.ax.get_yticklabels():
            label.set_color("white")

        self.ax.axvline(0, color="white", linewidth=0.8, alpha=0.65, linestyle="--")
        self.ax.set_xlabel("Time (ns)", color="white")
        self.ax.set_ylabel("Wavelength (nm)", color="white")
        self.ax.set_title(self._plot_title(), color="white")
        self.ax.tick_params(colors="white")
        self.ax.grid(False)

        if preserve_limits:
            self.ax.set_xlim(saved_xlim)
            self.ax.set_ylim(saved_ylim)
        else:
            self.ax.set_xlim(float(np.nanmin(times_ns)), float(np.nanmax(times_ns)))
            self.ax.set_ylim(float(np.nanmin(wavelengths)), float(np.nanmax(wavelengths)))

        self.figure.tight_layout()
        self.canvas.draw_idle()

    def _plot_title(self) -> str:
        data = self.data
        if data is None:
            return "KIT TA Heatmap"
        parts = [data.path.name] if getattr(data, "path", None) is not None else ["KIT TA Heatmap"]
        if getattr(data, "baseline_corrected", False):
            parts.append("baseline corrected")
        shift_ns = getattr(data, "time_shift_ns", 0.0)
        if shift_ns:
            parts.append(f"shift {shift_ns:.6g} ns")
        return " | ".join(parts)

    def _color_limits(self, signal: np.ndarray) -> tuple[float | None, float | None]:
        manual_low = self._optional_float(self.vmin_var, "Color min")
        manual_high = self._optional_float(self.vmax_var, "Color max")
        if manual_low is not None or manual_high is not None:
            if manual_low is None or manual_high is None:
                raise ValueError("Color min and max must both be set, or both blank.")
            if manual_low >= manual_high:
                raise ValueError("Color min must be smaller than color max.")
            return manual_low, manual_high

        finite_signal = signal[np.isfinite(signal)]
        if finite_signal.size == 0:
            raise ValueError("Signal contains no finite values.")
        low = float(np.nanmin(finite_signal))
        high = float(np.nanmax(finite_signal))
        if low == high:
            pad = abs(low) * 0.05 if low != 0 else 1.0
            low -= pad
            high += pad
        if self.symmetric_var.get():
            limit = max(abs(low), abs(high))
            low, high = -limit, limit
        return low, high

    @staticmethod
    def _optional_float(variable: tk.StringVar, label: str) -> float | None:
        raw_value = variable.get().strip()
        if not raw_value:
            return None
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric or blank.") from exc

    # ------------------------------------------------------------------
    # Axis / color controls
    # ------------------------------------------------------------------
    def _axis_bounds(self) -> tuple[float, float, float, float]:
        t_low = self._optional_float(self.time_min_var, "Time min")
        t_high = self._optional_float(self.time_max_var, "Time max")
        w_low = self._optional_float(self.wavelength_min_var, "Wavelength min")
        w_high = self._optional_float(self.wavelength_max_var, "Wavelength max")
        if None in (t_low, t_high, w_low, w_high):
            raise ValueError("Axis min and max fields must all be set.")
        if t_low >= t_high:
            raise ValueError("Time min must be smaller than time max.")
        if w_low >= w_high:
            raise ValueError("Wavelength min must be smaller than wavelength max.")
        return t_low, t_high, w_low, w_high

    def apply_axis_limits(self) -> None:
        if self.data is None:
            messagebox.showwarning(
                "No data", "Load a data file in the main viewer first.", parent=self
            )
            return
        try:
            t_low, t_high, w_low, w_high = self._axis_bounds()
        except ValueError as exc:
            messagebox.showerror("Invalid axis range", str(exc), parent=self)
            return
        self.ax.set_xlim(t_low, t_high)
        self.ax.set_ylim(w_low, w_high)
        self.canvas.draw_idle()

    def autoscale_axes(self) -> None:
        if self.data is None:
            messagebox.showwarning(
                "No data", "Load a data file in the main viewer first.", parent=self
            )
            return
        self._fill_axis_entries()
        self.draw_heatmap(preserve_limits=False)

    def _apply_color_scale(self) -> None:
        self.draw_heatmap(preserve_limits=True)

    def auto_color_scale(self) -> None:
        self.vmin_var.set("")
        self.vmax_var.set("")
        self.draw_heatmap(preserve_limits=True)


__all__ = ["TAHeatmapViewer", "centers_to_edges"]


if __name__ == "__main__":
    import sys

    print(
        "TA_data_heatmap is an engine module and cannot be run on its own.\n"
        "Run TA_data_reading.py and click the 'Heatmap' button to open the "
        "heatmap window.",
        file=sys.stderr,
    )
    sys.exit(1)
