"""KIT Transient Absorption data viewer and baseline correction.

Physical approximations
-----------------------
Time axis (chirp / group-velocity dispersion)
    All wavelengths share one detector time axis (``times_s``). The code does
    not apply chirp or wavelength-dependent t0 correction: pump-probe delay is
    treated as t(lambda) = t_detector for every probe wavelength.

    In white-light probe setups, different wavelengths can reach the sample or
    detector at slightly different times (e.g. blue before red under normal
    dispersion). Here that effect is ignored.

    This is appropriate for nanosecond pump excitation and nanosecond-scale
    delays, where the time step is usually much larger than the chirp spread
    across the probed band. It is not rigorous for femtosecond experiments
    with sub-picosecond chirp: those need per-wavelength t0(lambda) calibration
    and resampling onto a common delay axis before spectra or kinetics analysis.

Baseline correction
    The pre-pump time window used for baseline is chosen once on the global
    time axis. Negative-time columns are preferred whenever present; only files
    without negative times fall back to onset detection that aggregates signal
    activity across wavelengths. The value subtracted at each wavelength is
    that wavelength's median over those columns. With ns timing, any t0(lambda)
    variation inside the narrow pre-pump window is typically negligible compared
    with the time resolution.
"""

import json
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import numpy as np
import pandas as pd
import customtkinter as ctk
import matplotlib
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

matplotlib.use("TkAgg")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from Read_data_unified import read_grid

DARK_BG = "#1a1a1a"
PANEL_BG = "#2b2b2b"
ACCENT = "#2CC985"
ACCENT_RED = "#C92C45"
BASELINE_NOTICE_COLOR = "#FF9F00"


DEFAULT_DATA_FILE = Path(
    r"C:\My files\Google drive sync\KIT\21_L21_Exp2_00_combined_split.csv"
)

AVERAGE_WINDOWS = {
    "+/-1 col (3 total)": 1,
    "+/-2 col (5 total)": 2,
}

WAVELENGTH_WINDOWS = {
    "2 nm": 2.0,
    "3 nm": 3.0,
}

# Curve type tags. Spectrum curves live on the spectra axes (x = wavelength),
# kinetics curves on the kinetics axes (x = time). Both are shown at once.
MODE_SPECTRUM = "Spectra"
MODE_KINETICS = "Kinetics"

DEFAULT_SPECTRUM_X_MIN_NM = 460.0
DEFAULT_SPECTRUM_X_MAX_NM = 1000.0

Y_LABEL = "Ave Delta T/T"


def enable_high_dpi_awareness() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class _Tooltip:
    """Lightweight hover tooltip so the compact icon buttons stay discoverable."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _show(self, _event=None) -> None:
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 6
        y = self.widget.winfo_rooty()
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        try:
            self._tip.wm_attributes("-topmost", True)
        except tk.TclError:
            pass
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip,
            text=self.text,
            background="#000000",
            foreground="#ffffff",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=3,
            font=("Segoe UI", 9),
        ).pack()

    def _hide(self, _event=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class TAData:
    def __init__(self, path: Path):
        self.path = path
        self.times_s: np.ndarray
        self.base_times_s: np.ndarray
        self.time_shift_ns = 0.0
        self.wavelengths_nm: np.ndarray
        self.raw_signal: np.ndarray
        self.signal: np.ndarray
        self.baseline_corrected = False
        self.baseline_vector: np.ndarray | None = None
        self.baseline_mask: np.ndarray | None = None
        self.baseline_t0_ns: float | None = None
        self.load(path)

    def load(self, path: Path) -> None:
        # Unified reader handles encoding, delimiter, and CSV/Excel text variants.
        grid = read_grid(path, layout="ta_grid")
        times = np.asarray(grid.col_values, dtype=float)
        wavelengths = np.asarray(grid.row_values, dtype=float)
        signal = np.asarray(grid.data, dtype=float)

        valid_time = np.isfinite(times)
        valid_wavelength = np.isfinite(wavelengths)

        if valid_time.sum() == 0 or valid_wavelength.sum() == 0:
            raise ValueError("Could not find numeric time or wavelength data.")
        if signal.shape != (wavelengths.size, times.size):
            raise ValueError(
                "TA grid shape does not match the detected time/wavelength axes."
            )

        self.path = path
        self.base_times_s = times[valid_time]
        self.time_shift_ns = 0.0
        self.times_s = self.base_times_s.copy()
        self.wavelengths_nm = wavelengths[valid_wavelength]
        self.raw_signal = signal[np.ix_(valid_wavelength, valid_time)].copy()
        self.signal = self.raw_signal.copy()
        self.baseline_corrected = False
        self.baseline_vector = None
        self.baseline_mask = None
        self.baseline_t0_ns = None

    def apply_baseline_correction(self) -> dict:
        baseline_mask, estimated_t0_ns, method = self._baseline_column_mask()
        if not baseline_mask.any():
            raise ValueError("Could not find baseline columns.")

        baseline = np.nanmedian(self.raw_signal[:, baseline_mask], axis=1)
        fallback = np.nanmedian(self.raw_signal, axis=1)
        baseline = np.where(
            np.isfinite(baseline),
            baseline,
            np.where(np.isfinite(fallback), fallback, 0.0),
        )

        self.signal = self.raw_signal - baseline[:, np.newaxis]
        self.baseline_corrected = True
        self.baseline_vector = baseline
        self.baseline_mask = baseline_mask
        self.baseline_t0_ns = estimated_t0_ns

        baseline_times_ns = self.times_s[baseline_mask] * 1e9
        return {
            "columns": int(baseline_mask.sum()),
            "time_low_ns": float(np.nanmin(baseline_times_ns)),
            "time_high_ns": float(np.nanmax(baseline_times_ns)),
            "estimated_t0_ns": estimated_t0_ns,
            "method": method,
        }

    def clear_baseline_correction(self) -> None:
        self.signal = self.raw_signal.copy()
        self.baseline_corrected = False
        self.baseline_vector = None
        self.baseline_mask = None
        self.baseline_t0_ns = None

    def set_time_shift(self, shift_ns: float) -> None:
        # Rebuild the time axis from the untouched base times so repeated shifts
        # do not accumulate floating-point drift.
        self.time_shift_ns = float(shift_ns)
        self.times_s = self.base_times_s + self.time_shift_ns * 1e-9

    def _baseline_column_mask(self) -> tuple[np.ndarray, float | None, str]:
        finite_time = np.isfinite(self.times_s)
        finite_signal_column = np.isfinite(self.raw_signal).any(axis=0)
        usable = finite_time & finite_signal_column
        if not usable.any():
            raise ValueError("Could not find finite time/signal columns.")

        usable_cols = np.where(usable)[0]
        sorted_cols = usable_cols[np.argsort(self.times_s[usable_cols])]
        sorted_times_ns = self.times_s[sorted_cols] * 1e9

        # Be conservative for pump-probe data: if negative delays are available,
        # use only those known pre-pump columns instead of inferring a later t0.
        negative_cols = usable_cols[self.times_s[usable_cols] < 0]
        if negative_cols.size:
            mask = np.zeros_like(self.times_s, dtype=bool)
            mask[negative_cols] = True
            return mask, None, "negative-time"

        onset_pos = self._estimate_onset_position(sorted_cols)

        if onset_pos is not None:
            # Leave one column before the detected onset as a guard when enough
            # pre-pump points exist; otherwise prefer the detected pre-onset
            # points over a wider negative-time range that may contain signal.
            guard_pos = onset_pos - 1 if onset_pos >= 3 else onset_pos
            if guard_pos > 0:
                mask = np.zeros_like(self.times_s, dtype=bool)
                mask[sorted_cols[:guard_pos]] = True
                return mask, float(sorted_times_ns[onset_pos]), "estimated pre-pump"

        fallback_count = max(1, min(5, int(np.ceil(sorted_cols.size * 0.1))))
        mask = np.zeros_like(self.times_s, dtype=bool)
        mask[sorted_cols[:fallback_count]] = True
        return mask, None, "earliest columns"

    def _estimate_onset_position(self, sorted_cols: np.ndarray) -> int | None:
        n_cols = sorted_cols.size
        if n_cols < 4:
            return None

        reference_count = max(3, min(max(5, n_cols // 10), n_cols // 4))
        sorted_signal = self.raw_signal[:, sorted_cols]
        reference = np.nanmedian(sorted_signal[:, :reference_count], axis=1)
        residual = sorted_signal - reference[:, np.newaxis]
        activity = np.nanmedian(np.abs(residual), axis=0)
        finite_activity = np.isfinite(activity)
        if finite_activity.sum() < 4:
            return None

        activity = np.where(finite_activity, activity, np.nanmedian(activity[finite_activity]))
        early_activity = activity[:reference_count]
        noise_center = float(np.nanmedian(early_activity))
        noise_mad = float(np.nanmedian(np.abs(early_activity - noise_center))) * 1.4826
        peak = float(np.nanmax(activity))
        if not np.isfinite(peak) or peak <= noise_center:
            return None

        threshold = noise_center + max(6.0 * noise_mad, 0.1 * (peak - noise_center))
        if threshold >= peak:
            return None

        consecutive = 2 if n_cols >= 8 else 1
        search_start = max(1, reference_count // 2)
        above_threshold = activity > threshold
        for idx in range(search_start, n_cols - consecutive + 1):
            if np.all(above_threshold[idx : idx + consecutive]):
                return idx
        return None

    @property
    def min_time_ns(self) -> float:
        return float(np.nanmin(self.times_s) * 1e9)

    @property
    def max_time_ns(self) -> float:
        return float(np.nanmax(self.times_s) * 1e9)

    @property
    def min_wavelength_nm(self) -> float:
        return float(np.nanmin(self.wavelengths_nm))

    @property
    def max_wavelength_nm(self) -> float:
        return float(np.nanmax(self.wavelengths_nm))

    @property
    def time_step_summary_ns(self) -> tuple[float, float, float]:
        sorted_times = np.sort(self.times_s[np.isfinite(self.times_s)])
        steps = np.diff(sorted_times)
        steps = steps[steps > 0]
        if steps.size == 0:
            return 0.0, 0.0, 0.0
        return (
            float(np.nanmin(steps) * 1e9),
            float(np.nanmedian(steps) * 1e9),
            float(np.nanmax(steps) * 1e9),
        )

    @property
    def wavelength_step_summary_nm(self) -> tuple[float, float, float]:
        sorted_wavelengths = np.sort(self.wavelengths_nm[np.isfinite(self.wavelengths_nm)])
        steps = np.diff(sorted_wavelengths)
        steps = steps[steps > 0]
        if steps.size == 0:
            return 0.0, 0.0, 0.0
        return (
            float(np.nanmin(steps)),
            float(np.nanmedian(steps)),
            float(np.nanmax(steps)),
        )

    def averaged_spectrum(
        self, requested_time_ns: float, half_columns: int
    ) -> tuple[np.ndarray, np.ndarray, float, int, bool, float, float]:
        requested_time_s = requested_time_ns * 1e-9
        center_idx = int(np.nanargmin(np.abs(self.times_s - requested_time_s)))
        nearest_time_s = float(self.times_s[center_idx])

        low = max(0, center_idx - int(half_columns))
        high = min(self.times_s.size, center_idx + int(half_columns) + 1)
        selected = np.arange(low, high)

        selected_times_ns = self.times_s[selected] * 1e9
        spectrum = np.nanmean(self.signal[:, selected], axis=1)
        return (
            self.wavelengths_nm,
            spectrum,
            nearest_time_s * 1e9,
            int(selected.size),
            False,
            float(np.nanmin(selected_times_ns)),
            float(np.nanmax(selected_times_ns)),
        )

    def averaged_trace(
        self, requested_wavelength_nm: float, half_window_nm: float
    ) -> tuple[np.ndarray, np.ndarray, float, int, bool, float, float]:
        center_idx = int(
            np.nanargmin(np.abs(self.wavelengths_nm - requested_wavelength_nm))
        )
        nearest_wavelength_nm = float(self.wavelengths_nm[center_idx])

        selected = np.where(
            np.abs(self.wavelengths_nm - requested_wavelength_nm) <= half_window_nm
        )[0]
        used_nearest_only = selected.size == 0
        if used_nearest_only:
            selected = np.array([center_idx])

        selected_wavelengths_nm = self.wavelengths_nm[selected]
        trace = np.nanmean(self.signal[selected, :], axis=0)
        return (
            self.times_s * 1e9,
            trace,
            nearest_wavelength_nm,
            int(selected.size),
            used_nearest_only,
            float(np.nanmin(selected_wavelengths_nm)),
            float(np.nanmax(selected_wavelengths_nm)),
        )


class TAViewer(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KIT TA Data Reading")
        ctk.set_appearance_mode("Dark")
        self._configure_display()

        self.data: TAData | None = None
        self.spec_count = 0
        self.kin_count = 0
        self.curve_params: dict = {}
        # Net sign transform applied via Flip Y to both subplots.
        self.y_sign = 1.0
        # One cursor (line/marker/annotation) per subplot, created lazily.
        self.cursor_artists: dict = {}
        self.baseline_notice_artists = []
        self.plot_selected_curve = None

        self.file_var = tk.StringVar(value=str(DEFAULT_DATA_FILE))
        self.time_var = tk.StringVar(value="0")
        self.window_var = tk.StringVar(value="+/-1 col (3 total)")
        self.wavelength_var = tk.StringVar(value="500")
        self.wavelength_window_var = tk.StringVar(value="2 nm")
        self.time_shift_step_var = tk.StringVar(value="0.1")

        # Independent axis ranges per subplot (blank = autoscale).
        self.spec_x_min_var = tk.StringVar(value=f"{DEFAULT_SPECTRUM_X_MIN_NM:g}")
        self.spec_x_max_var = tk.StringVar(value=f"{DEFAULT_SPECTRUM_X_MAX_NM:g}")
        self.spec_y_min_var = tk.StringVar(value="")
        self.spec_y_max_var = tk.StringVar(value="")
        self.kin_x_min_var = tk.StringVar(value="")
        self.kin_x_max_var = tk.StringVar(value="")
        self.kin_y_min_var = tk.StringVar(value="")
        self.kin_y_max_var = tk.StringVar(value="")

        self.curve_select_var = tk.StringVar(value="No curves")
        self.status_var = tk.StringVar(value="Load a data file to begin.")

        self._build_ui()
        if DEFAULT_DATA_FILE.exists():
            self._load_file(DEFAULT_DATA_FILE)

    def _configure_display(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        scale = max(1.0, min(2.0, screen_width / 1920))
        self.ui_scale = scale
        self.plot_font_size = max(5, int(5 * scale))
        self.secondary_font_size = max(4, int(4 * scale))

        width = min(max(1200, int(screen_width * 0.72)), 2200)
        height = min(max(820, int(screen_height * 0.76)), 1400)
        self.geometry(f"{width}x{height}")

    def _build_ui(self) -> None:
        sidebar = ctk.CTkScrollableFrame(
            self,
            width=300,
            label_text="Controls",
            scrollbar_button_color=ACCENT,
            scrollbar_button_hover_color="#249b6b",
        )
        sidebar.pack(side="left", fill="y", padx=8, pady=8)

        pad = 8
        btn_h = 28
        wrap = 250
        icon_font = ctk.CTkFont(size=16)

        def section(text: str, top: int = 10) -> None:
            ctk.CTkLabel(
                sidebar,
                text=text,
                anchor="w",
                font=ctk.CTkFont(size=12, weight="bold"),
            ).pack(fill="x", padx=pad, pady=(top, 2))

        def grid3() -> ctk.CTkFrame:
            frame = ctk.CTkFrame(sidebar, fg_color="transparent")
            frame.pack(fill="x", padx=pad, pady=2)
            frame.grid_columnconfigure((0, 1, 2), weight=1, uniform="icons")
            return frame

        def icon_btn(parent, glyph: str, command, tip: str, **kwargs):
            button = ctk.CTkButton(
                parent,
                text=glyph,
                command=command,
                width=40,
                height=34,
                font=icon_font,
                **kwargs,
            )
            button._tooltip = self._add_tooltip(button, tip)
            return button

        # --- Data file ---
        section("Data file", top=4)
        file_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        file_row.pack(fill="x", padx=pad, pady=2)
        file_row.grid_columnconfigure(0, weight=1)
        self.file_entry = ctk.CTkEntry(
            file_row, textvariable=self.file_var, height=btn_h
        )
        self.file_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        icon_btn(
            file_row, "\U0001F4C2", self._browse_file, "Browse for data file"
        ).grid(row=0, column=1)

        # --- Curve management ---
        section("Curves")
        self.curve_select_menu = ctk.CTkOptionMenu(
            sidebar,
            variable=self.curve_select_var,
            values=["No curves"],
            height=btn_h,
        )
        self.curve_select_menu.pack(fill="x", padx=pad, pady=2)
        row = grid3()
        icon_btn(
            row, "\U0001F5D1", self.delete_selected_curve, "Delete selected curve",
            fg_color=ACCENT_RED, hover_color="#9b2436",
        ).grid(row=0, column=0)
        icon_btn(
            row, "\U0001F9F9", self.clear_curves, "Clear all curves",
            fg_color="gray30", hover_color="gray25",
        ).grid(row=0, column=1)

        # --- Time controls (spectrum curves) ---
        section("Time (ns) \u2192 Spectra")
        time_entry = ctk.CTkEntry(sidebar, textvariable=self.time_var, height=btn_h)
        time_entry.pack(fill="x", padx=pad, pady=2)
        time_entry.bind("<Return>", lambda _event: self.add_spectrum())
        self.time_scale = ctk.CTkSlider(
            sidebar, from_=0, to=1, command=self._scale_changed
        )
        self.time_scale.pack(fill="x", padx=pad, pady=4)
        spec_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        spec_row.pack(fill="x", padx=pad, pady=2)
        spec_row.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(
            spec_row, variable=self.window_var, values=list(AVERAGE_WINDOWS),
            command=lambda _value: self._reaverage(MODE_SPECTRUM), height=btn_h,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        icon_btn(
            spec_row, "\u2795", self.add_spectrum, "Add spectrum at this time",
            fg_color=ACCENT, hover_color="#249b6b", text_color="#0d0d0d",
        ).grid(row=0, column=1)

        # --- Wavelength controls (kinetics curves) ---
        section("Wavelength (nm) \u2192 Kinetics")
        wavelength_entry = ctk.CTkEntry(
            sidebar, textvariable=self.wavelength_var, height=btn_h
        )
        wavelength_entry.pack(fill="x", padx=pad, pady=2)
        wavelength_entry.bind("<Return>", lambda _event: self.add_kinetics())
        self.wavelength_scale = ctk.CTkSlider(
            sidebar, from_=0, to=1, command=self._wavelength_scale_changed
        )
        self.wavelength_scale.pack(fill="x", padx=pad, pady=4)
        kin_row = ctk.CTkFrame(sidebar, fg_color="transparent")
        kin_row.pack(fill="x", padx=pad, pady=2)
        kin_row.grid_columnconfigure(0, weight=1)
        ctk.CTkOptionMenu(
            kin_row, variable=self.wavelength_window_var, values=list(WAVELENGTH_WINDOWS),
            command=lambda _value: self._reaverage(MODE_KINETICS), height=btn_h,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        icon_btn(
            kin_row, "\u2795", self.add_kinetics, "Add kinetics at this wavelength",
            fg_color=ACCENT, hover_color="#249b6b", text_color="#0d0d0d",
        ).grid(row=0, column=1)

        # --- Axis controls ---
        section("Axis range (blank = auto)")
        self._build_axis_group(
            sidebar,
            "Spectrum axis",
            self.spec_x_min_var,
            self.spec_x_max_var,
            self.spec_y_min_var,
            self.spec_y_max_var,
            "nm",
        )
        self._build_axis_group(
            sidebar,
            "Kinetics axis",
            self.kin_x_min_var,
            self.kin_x_max_var,
            self.kin_y_min_var,
            self.kin_y_max_var,
            "ns",
        )
        row = grid3()
        icon_btn(
            row, "\u2714", self.apply_axis_limits, "Apply axis range"
        ).grid(row=0, column=0)
        icon_btn(
            row, "\u2195", self.auto_y_axis_limits, "Auto Y axis",
            fg_color="gray30", hover_color="gray25",
        ).grid(row=0, column=1)
        icon_btn(
            row, "\u2922", self.auto_axis_limits, "Auto X + Y range",
            fg_color="gray30", hover_color="gray25",
        ).grid(row=0, column=2)

        # --- Data transform (applies to both plots) ---
        section("Data transform (both plots)")
        row = grid3()
        icon_btn(
            row, "\U0001F503", self.flip_y_axis, "Flip Y (\u00d7 -1)"
        ).grid(row=0, column=0)
        self.baseline_button = icon_btn(
            row,
            "\U0001F4C9",
            self.toggle_baseline_correction,
            "Apply baseline correction",
            fg_color=ACCENT,
            hover_color="#249b6b",
            text_color="#0d0d0d",
        )
        self.baseline_button.grid(row=0, column=1)

        # --- Time shift (non-baseline data only) ---
        section("Time shift (non-baseline only)")
        self.time_shift_label = ctk.CTkLabel(
            sidebar, text="Shift: 0 ns", anchor="w", text_color=ACCENT
        )
        self.time_shift_label.pack(fill="x", padx=pad, pady=(0, 2))

        shift_step_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        shift_step_frame.pack(fill="x", padx=pad, pady=2)
        shift_step_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(shift_step_frame, text="Step (ns)", anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        ctk.CTkEntry(
            shift_step_frame, textvariable=self.time_shift_step_var, height=btn_h
        ).grid(row=0, column=1, sticky="ew")

        row = grid3()
        self.time_shift_left_button = icon_btn(
            row, "\u25c0", lambda: self._shift_time(-1), "Shift earlier (\u2212 step)"
        )
        self.time_shift_left_button.grid(row=0, column=0)
        self.time_shift_reset_button = icon_btn(
            row, "\u21bb", self.reset_time_shift, "Reset time shift",
            fg_color="gray30", hover_color="gray25",
        )
        self.time_shift_reset_button.grid(row=0, column=1)
        self.time_shift_right_button = icon_btn(
            row, "\u25b6", lambda: self._shift_time(1), "Shift later (+ step)"
        )
        self.time_shift_right_button.grid(row=0, column=2)
        ctk.CTkLabel(
            sidebar,
            text="Tip: click the plot, then use Left/Right arrows.",
            anchor="w",
            justify="left",
            wraplength=wrap,
            text_color="gray60",
        ).pack(fill="x", padx=pad, pady=(0, 4))

        # --- Settings + output ---
        section("Settings & output")
        row = grid3()
        icon_btn(
            row, "\U0001F4BE", self.save_settings, "Save JSON settings"
        ).grid(row=0, column=0)
        icon_btn(
            row, "\U0001F4E5", self.read_settings, "Read JSON settings",
            fg_color="gray30", hover_color="gray25",
        ).grid(row=0, column=1)
        icon_btn(
            row, "\U0001F5BC", self.save_outputs, "Save PNG + XLSX",
            fg_color=ACCENT, hover_color="#249b6b", text_color="#0d0d0d",
        ).grid(row=0, column=2)

        # --- Status ---
        self.status_label = ctk.CTkLabel(
            sidebar,
            textvariable=self.status_var,
            justify="left",
            anchor="w",
            wraplength=wrap,
            text_color=ACCENT,
        )
        self.status_label.pack(fill="x", padx=pad, pady=(8, 8))

        # --- Plot area: three stacked subplots. Spectra on top, kinetics in the
        # middle, and the per-wavelength subtracted baseline on the bottom (only
        # populated while baseline correction is active). ---
        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        plt.style.use("dark_background")
        self.figure = Figure(figsize=(10, 9), dpi=110)
        self.figure.patch.set_facecolor(DARK_BG)
        self.ax_spec, self.ax_kin, self.ax_base = self.figure.subplots(
            3, 1, gridspec_kw={"height_ratios": [1.0, 1.0, 0.6]}
        )
        # Baseline panel shares the wavelength axis with the spectrum panel so
        # its x-range (manual limits, autoscale, and toolbar zoom) always tracks
        # the spectrum for easy side-by-side comparison.
        self.ax_base.sharex(self.ax_spec)
        self.figure.subplots_adjust(
            left=0.10, right=0.97, top=0.96, bottom=0.06, hspace=0.45
        )
        self.axis_vars = {
            self.ax_spec: (
                self.spec_x_min_var,
                self.spec_x_max_var,
                self.spec_y_min_var,
                self.spec_y_max_var,
            ),
            self.ax_kin: (
                self.kin_x_min_var,
                self.kin_x_max_var,
                self.kin_y_min_var,
                self.kin_y_max_var,
            ),
        }
        self._reset_axes()

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        plot_widget = self.canvas.get_tk_widget()
        plot_widget.configure(takefocus=True)
        plot_widget.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("axes_leave_event", self._hide_cursor)
        self.canvas.mpl_connect("button_press_event", self._on_plot_click)
        self.canvas.mpl_connect("key_press_event", self._on_canvas_key_press)

        self.toolbar = NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=False)
        self.toolbar.update()
        try:
            self.toolbar.config(background=PANEL_BG)
            for child in self.toolbar.winfo_children():
                try:
                    child.config(background=PANEL_BG, foreground="white")
                except tk.TclError:
                    try:
                        child.config(background=PANEL_BG)
                    except tk.TclError:
                        pass
        except tk.TclError:
            pass
        self.toolbar.pack(fill="x", padx=10, pady=(4, 10))

        self._set_time_shift_controls_enabled(self.data is not None)

    def _add_tooltip(self, widget, text: str) -> _Tooltip:
        return _Tooltip(widget, text)

    def _build_axis_group(
        self,
        parent,
        title: str,
        x_min_var: tk.StringVar,
        x_max_var: tk.StringVar,
        y_min_var: tk.StringVar,
        y_max_var: tk.StringVar,
        x_unit: str,
    ) -> None:
        ctk.CTkLabel(
            parent, text=title, anchor="w", text_color="gray70"
        ).pack(fill="x", padx=8, pady=(2, 0))
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="x", padx=8, pady=(0, 2))
        frame.grid_columnconfigure((0, 1), weight=1, uniform="axis")

        ctk.CTkLabel(frame, text=f"X min ({x_unit})", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 3)
        )
        ctk.CTkLabel(frame, text=f"X max ({x_unit})", anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(3, 0)
        )
        x_min_entry = ctk.CTkEntry(frame, textvariable=x_min_var, height=26)
        x_max_entry = ctk.CTkEntry(frame, textvariable=x_max_var, height=26)
        x_min_entry.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(0, 3))
        x_max_entry.grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=(0, 3))

        ctk.CTkLabel(frame, text="Y min", anchor="w").grid(
            row=2, column=0, sticky="ew", padx=(0, 3)
        )
        ctk.CTkLabel(frame, text="Y max", anchor="w").grid(
            row=2, column=1, sticky="ew", padx=(3, 0)
        )
        y_min_entry = ctk.CTkEntry(frame, textvariable=y_min_var, height=26)
        y_max_entry = ctk.CTkEntry(frame, textvariable=y_max_var, height=26)
        y_min_entry.grid(row=3, column=0, sticky="ew", padx=(0, 3))
        y_max_entry.grid(row=3, column=1, sticky="ew", padx=(3, 0))

        for entry in (x_min_entry, x_max_entry, y_min_entry, y_max_entry):
            entry.bind("<Return>", lambda _event: self.apply_axis_limits())

    # ------------------------------------------------------------------
    # Axes helpers
    # ------------------------------------------------------------------
    def _all_axes(self):
        return (self.ax_spec, self.ax_kin)

    def _ax_for_mode(self, mode: str):
        return self.ax_kin if mode == MODE_KINETICS else self.ax_spec

    def _data_lines(self, ax):
        return [
            line for line in ax.get_lines() if not line.get_label().startswith("_")
        ]

    def _all_data_lines(self):
        return self._data_lines(self.ax_spec) + self._data_lines(self.ax_kin)

    def _ordered_data_lines(self, ax):
        return sorted(
            self._data_lines(ax),
            key=lambda line: self.curve_params.get(line, {}).get(
                "number", float("inf")
            ),
        )

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------
    def _browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose TA data file",
            initialdir=str(DEFAULT_DATA_FILE.parent),
            filetypes=[
                ("TA grid files", "*.csv *.txt *.dat *.xlsx *.xls *.xlsm"),
                ("CSV files", "*.csv"),
                ("Text files", "*.txt *.dat"),
                ("Excel files", "*.xlsx *.xls *.xlsm"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.file_var.set(selected)
            self._load_file(Path(selected))

    def _load_file(self, path: Path) -> None:
        try:
            self.data = TAData(path)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return

        self.file_var.set(str(path))
        self.clear_curves()
        self._update_baseline_state_ui()
        self.time_scale.configure(
            from_=self.data.min_time_ns,
            to=self.data.max_time_ns,
            number_of_steps=1000,
        )
        self.wavelength_scale.configure(
            from_=self.data.min_wavelength_nm,
            to=self.data.max_wavelength_nm,
            number_of_steps=1000,
        )
        start_ns = 0.0
        if not self.data.min_time_ns <= start_ns <= self.data.max_time_ns:
            start_ns = self.data.min_time_ns
        self.time_var.set(f"{start_ns:.6g}")
        self.time_scale.set(start_ns)
        start_wavelength_nm = self.data.min_wavelength_nm
        self.wavelength_var.set(f"{start_wavelength_nm:.6g}")
        self.wavelength_scale.set(start_wavelength_nm)
        self.status_var.set(
            f"Loaded {path.name}: {self.data.signal.shape[0]} wavelengths, "
            f"{self.data.signal.shape[1]} time points "
            f"({self.data.min_time_ns:.4g} to {self.data.max_time_ns:.4g} ns). "
            f"Time step min/median/max: "
            f"{self.data.time_step_summary_ns[0]:.4g}/"
            f"{self.data.time_step_summary_ns[1]:.4g}/"
            f"{self.data.time_step_summary_ns[2]:.4g} ns. "
            f"Wavelength step min/median/max: "
            f"{self.data.wavelength_step_summary_nm[0]:.4g}/"
            f"{self.data.wavelength_step_summary_nm[1]:.4g}/"
            f"{self.data.wavelength_step_summary_nm[2]:.4g} nm."
        )

    def _scale_changed(self, value: str) -> None:
        self.time_var.set(f"{float(value):.6g}")

    def _wavelength_scale_changed(self, value: str) -> None:
        self.wavelength_var.set(f"{float(value):.6g}")

    # ------------------------------------------------------------------
    # Axis range controls
    # ------------------------------------------------------------------
    def apply_axis_limits(self) -> None:
        for ax in self._all_axes():
            if not self._apply_axis_to_ax(ax):
                return
        self._update_baseline_plot()
        self.canvas.draw_idle()
        self.status_var.set("Applied axis range. Leave a field blank to autoscale it.")

    def auto_axis_limits(self) -> None:
        for variable in (
            self.spec_x_min_var,
            self.spec_x_max_var,
            self.spec_y_min_var,
            self.spec_y_max_var,
            self.kin_x_min_var,
            self.kin_x_max_var,
            self.kin_y_min_var,
            self.kin_y_max_var,
        ):
            variable.set("")
        for ax in self._all_axes():
            self._autoscale_ax(ax)
        self._update_baseline_plot()
        self.canvas.draw_idle()
        self.status_var.set("Restored automatic axis range.")

    def auto_y_axis_limits(self) -> None:
        any_done = False
        for ax in self._all_axes():
            data_limits = self._data_limits(ax, x_limits=ax.get_xlim())
            if data_limits is None:
                continue
            _x_min_var, _x_max_var, y_min_var, y_max_var = self.axis_vars[ax]
            y_min_var.set("")
            y_max_var.set("")
            if self._apply_axis_to_ax(ax, auto_ylim=data_limits[1]):
                any_done = True

        if not any_done:
            messagebox.showinfo(
                "Auto Y Axis",
                "No plotted data points are inside the current x-axis range.",
            )
            return
        self.canvas.draw_idle()
        self.status_var.set("Restored automatic y-axis range.")

    def _autoscale_ax(self, ax) -> None:
        data_limits = self._data_limits(ax)
        if data_limits is None:
            return
        auto_xlim, auto_ylim = data_limits
        if not self._apply_axis_to_ax(ax, auto_xlim=auto_xlim, auto_ylim=auto_ylim):
            return
        # Refine y to fit only data inside the now-current x window (respects a
        # manual x range), so adding a curve does not require a manual click.
        within = self._data_limits(ax, x_limits=ax.get_xlim())
        if within is not None:
            self._apply_axis_to_ax(ax, auto_ylim=within[1])

    def _data_limits(
        self, ax, x_limits: tuple[float, float] | None = None
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        x_values = []
        y_values = []
        x_low, x_high = (None, None)
        if x_limits is not None:
            x_low, x_high = sorted(x_limits)

        for line in self._data_lines(ax):
            x_data = np.asarray(line.get_xdata(), dtype=float)
            y_data = np.asarray(line.get_ydata(), dtype=float)
            finite = np.isfinite(x_data) & np.isfinite(y_data)
            if x_low is not None and x_high is not None:
                finite &= (x_data >= x_low) & (x_data <= x_high)
            if finite.any():
                x_values.append(x_data[finite])
                y_values.append(y_data[finite])

        if not x_values or not y_values:
            return None

        x_all = np.concatenate(x_values)
        y_all = np.concatenate(y_values)
        return self._limits_with_margin(x_all), self._limits_with_margin(y_all)

    def _apply_axis_to_ax(
        self,
        ax,
        auto_xlim: tuple[float, float] | None = None,
        auto_ylim: tuple[float, float] | None = None,
    ) -> bool:
        x_min_var, x_max_var, y_min_var, y_max_var = self.axis_vars[ax]
        title = "Spectrum" if ax is self.ax_spec else "Kinetics"
        try:
            x_min = self._axis_bound(x_min_var, f"{title} X min")
            x_max = self._axis_bound(x_max_var, f"{title} X max")
            y_min = self._axis_bound(y_min_var, f"{title} Y min")
            y_max = self._axis_bound(y_max_var, f"{title} Y max")
        except ValueError as exc:
            messagebox.showerror("Invalid axis range", str(exc))
            return False

        current_xlim = ax.get_xlim()
        current_ylim = ax.get_ylim()
        x_low = auto_xlim[0] if x_min is None and auto_xlim is not None else x_min
        x_high = auto_xlim[1] if x_max is None and auto_xlim is not None else x_max
        y_low = auto_ylim[0] if y_min is None and auto_ylim is not None else y_min
        y_high = auto_ylim[1] if y_max is None and auto_ylim is not None else y_max

        x_low = current_xlim[0] if x_low is None else x_low
        x_high = current_xlim[1] if x_high is None else x_high
        y_low = current_ylim[0] if y_low is None else y_low
        y_high = current_ylim[1] if y_high is None else y_high

        if x_low >= x_high:
            messagebox.showerror(
                "Invalid axis range", f"{title} X min must be smaller than X max."
            )
            return False
        if y_low >= y_high:
            messagebox.showerror(
                "Invalid axis range", f"{title} Y min must be smaller than Y max."
            )
            return False

        ax.set_xlim(x_low, x_high)
        ax.set_ylim(y_low, y_high)
        return True

    @staticmethod
    def _axis_bound(variable: tk.StringVar, label: str) -> float | None:
        raw_value = variable.get().strip()
        if not raw_value:
            return None
        try:
            return float(raw_value)
        except ValueError as exc:
            raise ValueError(f"{label} must be numeric or blank.") from exc

    @staticmethod
    def _limits_with_margin(values: np.ndarray, margin: float = 0.05) -> tuple[float, float]:
        low = float(np.nanmin(values))
        high = float(np.nanmax(values))
        if low == high:
            pad = abs(low) * margin if low != 0 else 1.0
        else:
            pad = (high - low) * margin
        return low - pad, high + pad

    # ------------------------------------------------------------------
    # Data transforms (applied to both subplots)
    # ------------------------------------------------------------------
    def flip_y_axis(self) -> None:
        lines = self._all_data_lines()
        if not lines:
            messagebox.showwarning("No data", "Add at least one curve first.")
            return
        for line in lines:
            line.set_ydata(-np.asarray(line.get_ydata(), dtype=float))
        self.y_sign *= -1.0
        self._hide_cursor()
        for ax in self._all_axes():
            if self._data_lines(ax):
                self._autoscale_ax(ax)
        self._update_baseline_plot()
        self.canvas.draw_idle()
        self.status_var.set("Flipped both plots along the y-axis (x -1).")

    def _time_shift_step_ns(self) -> float | None:
        raw_value = self.time_shift_step_var.get().strip()
        try:
            step = float(raw_value)
        except ValueError:
            messagebox.showerror(
                "Invalid step", "Time shift step must be a positive number (ns)."
            )
            return None
        if step <= 0:
            messagebox.showerror(
                "Invalid step", "Time shift step must be greater than zero."
            )
            return None
        return step

    def _shift_time(self, direction: int) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return
        # Keep time-zero adjustments away from baseline-corrected data so the
        # baseline column detection (which depends on the time axis) stays valid.
        if self.data.baseline_corrected:
            self.status_var.set(
                "Time shift is disabled while baseline correction is applied. "
                "Remove baseline correction first."
            )
            return
        step = self._time_shift_step_ns()
        if step is None:
            return
        self._apply_time_shift(self.data.time_shift_ns + direction * step)

    def reset_time_shift(self) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return
        if self.data.baseline_corrected:
            self.status_var.set(
                "Time shift is disabled while baseline correction is applied. "
                "Remove baseline correction first."
            )
            return
        if self.data.time_shift_ns == 0.0:
            self.status_var.set("Time shift is already 0 ns.")
            return
        self._apply_time_shift(0.0)

    def _apply_time_shift(self, new_shift_ns: float) -> None:
        if self.data is None:
            return
        # Preserve the current zoom so fine time alignment is easy to eyeball.
        saved_limits = {
            ax: (ax.get_xlim(), ax.get_ylim()) for ax in self._all_axes()
        }
        self.data.set_time_shift(new_shift_ns)
        self.time_scale.configure(
            from_=self.data.min_time_ns, to=self.data.max_time_ns
        )
        self._refresh_all_curves()
        for ax, (xlim, ylim) in saved_limits.items():
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
        self.canvas.draw_idle()
        self._update_time_shift_label()
        self.status_var.set(
            f"Applied time shift of {self.data.time_shift_ns:.6g} ns "
            "(data time 0 moved to this physical time)."
        )

    def _update_time_shift_label(self) -> None:
        if not hasattr(self, "time_shift_label"):
            return
        shift = self.data.time_shift_ns if self.data is not None else 0.0
        self.time_shift_label.configure(text=f"Shift: {shift:.6g} ns")

    def _set_time_shift_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for attr in (
            "time_shift_left_button",
            "time_shift_right_button",
            "time_shift_reset_button",
        ):
            button = getattr(self, attr, None)
            if button is not None:
                button.configure(state=state)

    def toggle_baseline_correction(self) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return

        if self.data.baseline_corrected:
            self.data.clear_baseline_correction()
            refreshed = self._refresh_all_curves()
            self._update_baseline_state_ui()
            self.status_var.set(
                f"Baseline correction removed. Updated {refreshed} plotted curve(s)."
            )
            return

        try:
            info = self.data.apply_baseline_correction()
        except Exception as exc:
            messagebox.showerror("Baseline correction failed", str(exc))
            return

        refreshed = self._refresh_all_curves()
        self._update_baseline_state_ui()
        t0_text = ""
        if info["estimated_t0_ns"] is not None:
            t0_text = f" Estimated t0: {info['estimated_t0_ns']:.6g} ns."
        self.status_var.set(
            "Baseline corrected per wavelength using "
            f"{info['columns']} {info['method']} column(s) "
            f"({info['time_low_ns']:.6g} to {info['time_high_ns']:.6g} ns)."
            f"{t0_text} Updated {refreshed} plotted curve(s)."
        )

    def _refresh_all_curves(self) -> int:
        refreshed = 0
        refreshed += self._reaverage(MODE_SPECTRUM, update_status=False)
        refreshed += self._reaverage(MODE_KINETICS, update_status=False)
        self._hide_cursor()
        if refreshed == 0:
            self.canvas.draw_idle()
        return refreshed

    def _update_baseline_state_ui(self) -> None:
        corrected = self.data.baseline_corrected if self.data is not None else False
        self._set_time_shift_controls_enabled(self.data is not None and not corrected)
        self._update_time_shift_label()
        if hasattr(self, "baseline_button"):
            if corrected:
                self.baseline_button.configure(
                    fg_color=BASELINE_NOTICE_COLOR,
                    hover_color="#cc7f00",
                    text_color="#0d0d0d",
                )
                tooltip = getattr(self.baseline_button, "_tooltip", None)
                if tooltip is not None:
                    tooltip.set_text("Remove baseline correction")
            else:
                self.baseline_button.configure(
                    fg_color=ACCENT,
                    hover_color="#249b6b",
                    text_color="#0d0d0d",
                )
                tooltip = getattr(self.baseline_button, "_tooltip", None)
                if tooltip is not None:
                    tooltip.set_text("Apply baseline correction")
        self._update_baseline_plot()
        self._update_baseline_notice()
        if hasattr(self, "canvas"):
            self.canvas.draw_idle()

    def _update_baseline_plot(self) -> None:
        # Bottom panel: the per-wavelength baseline that was subtracted. Plotted
        # against wavelength because baseline_vector holds one value per
        # wavelength. The same y_sign as the main plots is applied so the curve
        # matches what the spectra/kinetics show after a Flip Y.
        ax = self.ax_base
        ax.clear()
        ax.set_facecolor(DARK_BG)
        ax.set_xlabel("Wavelength (nm)", fontsize=self.plot_font_size, color="white")
        ax.set_ylabel(Y_LABEL, fontsize=self.plot_font_size, color="white")
        ax.set_title(
            "Subtracted baseline (per wavelength)",
            fontsize=self.plot_font_size + 2,
            color="white",
        )
        ax.tick_params(labelsize=self.secondary_font_size, colors="white")
        ax.grid(True, linestyle=":", alpha=0.3)

        corrected = (
            self.data is not None
            and self.data.baseline_corrected
            and self.data.baseline_vector is not None
        )
        if not corrected:
            ax.text(
                0.5,
                0.5,
                "Apply baseline correction to view the subtracted baseline",
                transform=ax.transAxes,
                ha="center",
                va="center",
                color="0.6",
                fontsize=self.secondary_font_size,
            )
            return

        wavelengths = np.asarray(self.data.wavelengths_nm, dtype=float)
        baseline = self.y_sign * np.asarray(self.data.baseline_vector, dtype=float)
        ax.plot(
            wavelengths,
            baseline,
            color=BASELINE_NOTICE_COLOR,
            linewidth=1.5,
            label="Subtracted baseline",
        )
        # Auto y-scale to the baseline values inside the current wavelength
        # window (shared with the spectrum), so small variations are visible
        # instead of being flattened by edge outliers or the zero reference.
        x_low, x_high = sorted(self.ax_spec.get_xlim())
        in_window = (
            np.isfinite(wavelengths)
            & np.isfinite(baseline)
            & (wavelengths >= x_low)
            & (wavelengths <= x_high)
        )
        if not in_window.any():
            in_window = np.isfinite(wavelengths) & np.isfinite(baseline)
        if in_window.any():
            y_low, y_high = self._limits_with_margin(baseline[in_window])
            ax.set_ylim(y_low, y_high)
        # Zero reference drawn without expanding the y-range (only shown when 0
        # is already inside the data-driven limits).
        if in_window.any() and y_low <= 0.0 <= y_high:
            ax.axhline(0.0, color="white", linestyle="--", linewidth=0.8, alpha=0.4)
        if self.data.baseline_t0_ns is not None:
            ax.text(
                0.98,
                0.04,
                f"pre-pump cutoff ~ {self.data.baseline_t0_ns:.6g} ns",
                transform=ax.transAxes,
                ha="right",
                va="bottom",
                color="0.7",
                fontsize=self.secondary_font_size,
            )

    def _update_baseline_notice(self) -> None:
        for artist in self.baseline_notice_artists:
            try:
                artist.remove()
            except (ValueError, RuntimeError):
                pass
        self.baseline_notice_artists.clear()

        if self.data is None or not self.data.baseline_corrected:
            return

        for ax in self._all_axes():
            artist = ax.text(
                0.98,
                0.90,
                "BASELINE CORRECTED",
                transform=ax.transAxes,
                ha="right",
                va="top",
                color=BASELINE_NOTICE_COLOR,
                fontsize=max(10, self.plot_font_size + 4),
                fontweight="bold",
                alpha=0.85,
                zorder=1.1,
                bbox={
                    "boxstyle": "round,pad=0.35",
                    "fc": BASELINE_NOTICE_COLOR,
                    "ec": BASELINE_NOTICE_COLOR,
                    "alpha": 0.14,
                },
            )
            self.baseline_notice_artists.append(artist)

    # ------------------------------------------------------------------
    # JSON settings
    # ------------------------------------------------------------------
    def save_settings(self) -> None:
        current_file = self.file_var.get().strip()
        initial_dir = str(Path(current_file).parent) if current_file else None
        settings_path = filedialog.asksaveasfilename(
            title="Save JSON settings",
            initialdir=initial_dir,
            defaultextension=".json",
            filetypes=[("JSON settings", "*.json"), ("All files", "*.*")],
        )
        if not settings_path:
            return

        settings = self._current_settings()
        try:
            with Path(settings_path).open("w", encoding="utf-8") as file:
                json.dump(settings, file, indent=2)
        except Exception as exc:
            messagebox.showerror("Save settings failed", str(exc))
            return

        self.status_var.set(f"Saved settings to {Path(settings_path).name}.")

    def read_settings(self) -> None:
        current_file = self.file_var.get().strip()
        initial_dir = str(Path(current_file).parent) if current_file else None
        settings_path = filedialog.askopenfilename(
            title="Read JSON settings",
            initialdir=initial_dir,
            filetypes=[("JSON settings", "*.json"), ("All files", "*.*")],
        )
        if not settings_path:
            return

        try:
            with Path(settings_path).open("r", encoding="utf-8") as file:
                settings = json.load(file)
            if not isinstance(settings, dict):
                raise ValueError("Settings file must contain a JSON object.")
            self._apply_settings(settings)
        except Exception as exc:
            messagebox.showerror("Read settings failed", str(exc))
            return

        self.status_var.set(f"Read settings from {Path(settings_path).name}.")

    def _current_settings(self) -> dict:
        return {
            "data_file": self.file_var.get(),
            "time_ns": self.time_var.get(),
            "time_average": self.window_var.get(),
            "wavelength_nm": self.wavelength_var.get(),
            "wavelength_average": self.wavelength_window_var.get(),
            "spec_x_min": self.spec_x_min_var.get(),
            "spec_x_max": self.spec_x_max_var.get(),
            "spec_y_min": self.spec_y_min_var.get(),
            "spec_y_max": self.spec_y_max_var.get(),
            "kin_x_min": self.kin_x_min_var.get(),
            "kin_x_max": self.kin_x_max_var.get(),
            "kin_y_min": self.kin_y_min_var.get(),
            "kin_y_max": self.kin_y_max_var.get(),
            "baseline_corrected": (
                self.data.baseline_corrected if self.data is not None else False
            ),
            "time_shift_ns": (
                self.data.time_shift_ns if self.data is not None else 0.0
            ),
            "time_shift_step_ns": self.time_shift_step_var.get(),
            "y_sign": self.y_sign,
            "curves": self._current_curves(),
        }

    def _current_curves(self) -> list[dict]:
        curves: list[dict] = []
        for ax in self._all_axes():
            for line in self._ordered_data_lines(ax):
                params = self.curve_params.get(line)
                if params is None:
                    continue
                curve = {
                    "mode": params.get("mode"),
                    "number": params.get("number"),
                }
                if "requested_time_ns" in params:
                    curve["requested_time_ns"] = params["requested_time_ns"]
                if "requested_wavelength_nm" in params:
                    curve["requested_wavelength_nm"] = params["requested_wavelength_nm"]
                curves.append(curve)
        return curves

    def _apply_settings(self, settings: dict) -> None:
        settings = self._migrate_legacy_settings(settings)

        data_file = str(settings.get("data_file", "")).strip()
        if data_file:
            self.file_var.set(data_file)
            data_path = Path(data_file)
            if data_path.exists():
                self._load_file(data_path)

        self._set_if_valid_option(self.window_var, settings, "time_average", AVERAGE_WINDOWS)
        self._set_if_valid_option(
            self.wavelength_window_var,
            settings,
            "wavelength_average",
            WAVELENGTH_WINDOWS,
        )

        if "time_shift_step_ns" in settings:
            self.time_shift_step_var.set(str(settings["time_shift_step_ns"]))
        # Apply the shift before baseline correction: a saved baseline was
        # computed on the already-shifted time axis, so restore that order.
        if self.data is not None:
            try:
                saved_shift = float(settings.get("time_shift_ns", 0.0))
            except (TypeError, ValueError):
                saved_shift = 0.0
            self.data.set_time_shift(saved_shift)
            self.time_scale.configure(
                from_=self.data.min_time_ns, to=self.data.max_time_ns
            )

        if settings.get("baseline_corrected") and self.data is not None:
            try:
                self.data.apply_baseline_correction()
            except Exception as exc:
                messagebox.showwarning(
                    "Baseline correction skipped",
                    f"Could not restore baseline correction: {exc}",
                )

        if "curves" in settings:
            self._restore_curves(settings)
        else:
            self._reaverage(MODE_SPECTRUM)
            self._reaverage(MODE_KINETICS)

        # Set after restoring curves: restoring mutates time/wavelength vars per
        # curve, so apply the saved "current" selection last.
        for key, variable in (
            ("time_ns", self.time_var),
            ("wavelength_nm", self.wavelength_var),
            ("spec_x_min", self.spec_x_min_var),
            ("spec_x_max", self.spec_x_max_var),
            ("spec_y_min", self.spec_y_min_var),
            ("spec_y_max", self.spec_y_max_var),
            ("kin_x_min", self.kin_x_min_var),
            ("kin_x_max", self.kin_x_max_var),
            ("kin_y_min", self.kin_y_min_var),
            ("kin_y_max", self.kin_y_max_var),
        ):
            if key in settings:
                variable.set(str(settings[key]))

        self._sync_sliders_to_entries()
        self.apply_axis_limits()
        self._update_baseline_state_ui()

    @staticmethod
    def _migrate_legacy_settings(settings: dict) -> dict:
        # Older settings files used a single plot mode with shared x_min/x_max/
        # y_min/y_max. Route those onto whichever subplot the old mode targeted.
        if "spec_x_min" in settings or "kin_x_min" in settings:
            return settings
        if not any(k in settings for k in ("x_min", "x_max", "y_min", "y_max")):
            return settings

        migrated = dict(settings)
        prefix = "kin" if str(settings.get("plot_mode", "")) == MODE_KINETICS else "spec"
        for axis in ("x_min", "x_max", "y_min", "y_max"):
            if axis in settings:
                migrated[f"{prefix}_{axis}"] = settings[axis]
        return migrated

    def _restore_curves(self, settings: dict) -> None:
        curves = settings.get("curves")
        if not isinstance(curves, list):
            return

        self.clear_curves()
        if self.data is None:
            return

        # clear_curves resets the transform, so restore Flip Y before plotting.
        try:
            self.y_sign = -1.0 if float(settings.get("y_sign", 1.0)) < 0 else 1.0
        except (TypeError, ValueError):
            self.y_sign = 1.0

        for curve in curves:
            if not isinstance(curve, dict):
                continue
            mode = str(curve.get("mode", MODE_SPECTRUM))
            if mode == MODE_KINETICS and "requested_wavelength_nm" in curve:
                self.wavelength_var.set(str(curve["requested_wavelength_nm"]))
                self._add_wavelength_trace()
            elif "requested_time_ns" in curve:
                self.time_var.set(str(curve["requested_time_ns"]))
                self._add_time_spectrum()

    @staticmethod
    def _format_average_range(low: float, high: float, unit: str) -> str:
        if np.isclose(low, high):
            return f"{low:.6g} {unit}"
        lo, hi = (low, high) if low <= high else (high, low)
        return f"{lo:.6g} {unit} - {hi:.6g} {unit}"

    @staticmethod
    def _set_if_valid_option(
        variable: tk.StringVar, settings: dict, key: str, valid_options: dict
    ) -> None:
        value = settings.get(key)
        if value in valid_options:
            variable.set(str(value))

    def _sync_sliders_to_entries(self) -> None:
        if self.data is None:
            return
        try:
            time_ns = float(self.time_var.get())
            if self.data.min_time_ns <= time_ns <= self.data.max_time_ns:
                self.time_scale.set(time_ns)
        except ValueError:
            pass
        try:
            wavelength_nm = float(self.wavelength_var.get())
            if self.data.min_wavelength_nm <= wavelength_nm <= self.data.max_wavelength_nm:
                self.wavelength_scale.set(wavelength_nm)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # Output saving
    # ------------------------------------------------------------------
    def save_outputs(self) -> None:
        if not self._all_data_lines():
            messagebox.showwarning(
                "Nothing to save", "Add at least one curve before saving."
            )
            return

        current_file = self.file_var.get().strip()
        initial_dir = str(Path(current_file).parent) if current_file else None
        base_path = filedialog.asksaveasfilename(
            title="Save graph (PNG) and data (XLSX)",
            initialdir=initial_dir,
            defaultextension=".png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not base_path:
            return

        stem = Path(base_path).with_suffix("")
        png_path = stem.with_suffix(".png")
        xlsx_path = stem.with_suffix(".xlsx")

        try:
            self.figure.savefig(
                png_path, dpi=400, facecolor=self.figure.get_facecolor()
            )
            self._save_data_workbook(xlsx_path)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return

        self.status_var.set(
            f"Saved {png_path.name} (400 dpi) and {xlsx_path.name}."
        )

    def _save_data_workbook(self, xlsx_path: Path) -> None:
        properties: list[str] = []
        values: list = []
        for ax, name in ((self.ax_spec, "Spectrum"), (self.ax_kin, "Kinetics")):
            x_min, x_max = ax.get_xlim()
            y_min, y_max = ax.get_ylim()
            properties += [
                f"{name} title",
                f"{name} X axis",
                f"{name} Y axis",
                f"{name} X min",
                f"{name} X max",
                f"{name} Y min",
                f"{name} Y max",
            ]
            values += [
                ax.get_title(),
                ax.get_xlabel(),
                ax.get_ylabel(),
                x_min,
                x_max,
                y_min,
                y_max,
            ]
        info_df = pd.DataFrame({"Property": properties, "Value": values})

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            info_df.to_excel(writer, sheet_name="Plot info", index=False)
            for ax, sheet, x_label in (
                (self.ax_spec, "Spectra", "Wavelength (nm)"),
                (self.ax_kin, "Kinetics", "Time (ns)"),
            ):
                if not self._data_lines(ax):
                    continue
                self._build_line_dataframe(ax, x_label).to_excel(
                    writer, sheet_name=sheet, index=False
                )

    def _build_line_dataframe(self, ax, x_label: str) -> pd.DataFrame:
        x_min, x_max = ax.get_xlim()
        x_low, x_high = sorted((x_min, x_max))

        clipped_lines = []
        for line in self._ordered_data_lines(ax):
            x_data = np.asarray(line.get_xdata(), dtype=float)
            y_data = np.asarray(line.get_ydata(), dtype=float)
            in_range = np.isfinite(x_data) & (x_data >= x_low) & (x_data <= x_high)
            clipped_lines.append((line, x_data[in_range], y_data[in_range]))

        shared_x = None
        same_x = True
        for _line, x_data, _y_data in clipped_lines:
            if shared_x is None:
                shared_x = x_data
            elif shared_x.shape != x_data.shape or not np.allclose(
                shared_x, x_data, equal_nan=True
            ):
                same_x = False
                break

        if same_x and shared_x is not None:
            data = {x_label: shared_x}
            for line, _x_data, y_data in clipped_lines:
                data[line.get_label()] = y_data
            return pd.DataFrame(data)

        frames = []
        for line, x_data, y_data in clipped_lines:
            frames.append(
                pd.DataFrame(
                    {
                        f"{line.get_label()} | {x_label}": x_data,
                        f"{line.get_label()} | {Y_LABEL}": y_data,
                    }
                )
            )
        return pd.concat(frames, axis=1)

    # ------------------------------------------------------------------
    # Curve creation / management
    # ------------------------------------------------------------------
    def add_spectrum(self) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return
        self._add_time_spectrum()

    def add_kinetics(self) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return
        self._add_wavelength_trace()

    def _add_time_spectrum(self) -> None:
        if self.data is None:
            return
        try:
            requested_time_ns = float(self.time_var.get())
        except ValueError:
            messagebox.showerror("Invalid time", "Please enter a numeric time in ns.")
            return

        window_label = self.window_var.get()
        half_columns = AVERAGE_WINDOWS[window_label]

        (
            wavelengths,
            spectrum,
            nearest_time_ns,
            n_columns,
            used_nearest_only,
            time_low_ns,
            time_high_ns,
        ) = self.data.averaged_spectrum(requested_time_ns, half_columns)

        self.spec_count += 1
        label = (
            f"S{self.spec_count}: "
            f"{self._format_average_range(time_low_ns, time_high_ns, 'ns')}"
        )
        (line,) = self.ax_spec.plot(
            wavelengths, self.y_sign * spectrum, label=label
        )
        self.curve_params[line] = {
            "mode": MODE_SPECTRUM,
            "number": self.spec_count,
            "requested_time_ns": requested_time_ns,
        }
        self._reorder_curves(MODE_SPECTRUM)
        self._refresh_legend()
        self._autoscale_ax(self.ax_spec)
        self._refresh_curve_selector(line.get_label())
        self.canvas.draw_idle()

        fallback_note = " No time point fell inside the window; used nearest column." if used_nearest_only else ""
        self.status_var.set(
            f"Added spectrum at nearest time {nearest_time_ns:.6g} ns "
            f"(requested {requested_time_ns:.6g} ns, averaged {n_columns} columns)."
            f"{fallback_note}"
        )

    def _add_wavelength_trace(self) -> None:
        if self.data is None:
            return
        try:
            requested_wavelength_nm = float(self.wavelength_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid wavelength", "Please enter a numeric wavelength in nm."
            )
            return

        window_label = self.wavelength_window_var.get()
        half_window_nm = WAVELENGTH_WINDOWS[window_label]

        (
            times_ns,
            trace,
            nearest_wavelength_nm,
            n_rows,
            used_nearest_only,
            wavelength_low_nm,
            wavelength_high_nm,
        ) = self.data.averaged_trace(requested_wavelength_nm, half_window_nm)

        self.kin_count += 1
        label = (
            f"K{self.kin_count}: "
            f"{self._format_average_range(wavelength_low_nm, wavelength_high_nm, 'nm')}"
        )
        (line,) = self.ax_kin.plot(
            times_ns,
            self.y_sign * trace,
            marker="o",
            markersize=3,
            linewidth=1.3,
            label=label,
        )
        self.curve_params[line] = {
            "mode": MODE_KINETICS,
            "number": self.kin_count,
            "requested_wavelength_nm": requested_wavelength_nm,
        }
        self._reorder_curves(MODE_KINETICS)
        self._refresh_legend()
        self._autoscale_ax(self.ax_kin)
        self._refresh_curve_selector(line.get_label())
        self.canvas.draw_idle()

        fallback_note = (
            " No wavelength fell inside the window; used nearest row."
            if used_nearest_only
            else ""
        )
        self.status_var.set(
            f"Added kinetics at nearest wavelength {nearest_wavelength_nm:.6g} nm "
            f"(requested {requested_wavelength_nm:.6g} nm, averaged {n_rows} rows)."
            f"{fallback_note}"
        )

    def clear_curves(self) -> None:
        for ax in self._all_axes():
            ax.clear()
        self.cursor_artists.clear()
        self.plot_selected_curve = None
        self.spec_count = 0
        self.kin_count = 0
        self.curve_params.clear()
        self.y_sign = 1.0
        self._reset_axes()
        self._refresh_curve_selector()
        self.canvas.draw_idle()

    def delete_selected_curve(self) -> None:
        selected_label = self.curve_select_var.get()
        target = None
        target_ax = None
        for ax in self._all_axes():
            for line in self._data_lines(ax):
                if line.get_label() == selected_label:
                    target = line
                    target_ax = ax
                    break
            if target is not None:
                break

        if target is None:
            messagebox.showwarning(
                "No curve selected", "Please select a curve to delete first."
            )
            return

        self._delete_curve(target, target_ax)

    def _delete_curve(self, target, target_ax) -> None:
        deleted_label = target.get_label()
        mode = self.curve_params.get(target, {}).get("mode", MODE_SPECTRUM)
        target.remove()
        self.curve_params.pop(target, None)
        if self.plot_selected_curve is target:
            self.plot_selected_curve = None

        self._hide_cursor()
        self._reorder_curves(mode)
        self._refresh_legend()
        if self._data_lines(target_ax):
            self._autoscale_ax(target_ax)
        else:
            self._reset_axes_single(target_ax)
            self._update_baseline_notice()
        self._refresh_curve_selector()
        self.canvas.draw_idle()
        self.status_var.set(f"Deleted curve: {deleted_label}")

    def _reaverage(self, mode: str, update_status: bool = True) -> int:
        if self.data is None:
            return 0
        ax = self._ax_for_mode(mode)
        lines = [
            line
            for line in self._data_lines(ax)
            if self.curve_params.get(line, {}).get("mode") == mode
        ]
        if not lines:
            return 0

        if mode == MODE_SPECTRUM:
            window_label = self.window_var.get()
            half_columns = AVERAGE_WINDOWS[window_label]
        else:
            window_label = self.wavelength_window_var.get()
            half_window_nm = WAVELENGTH_WINDOWS[window_label]

        selected_before = self.curve_select_var.get()
        selected_after = None
        for line in lines:
            params = self.curve_params[line]
            if mode == MODE_SPECTRUM:
                wavelengths, spectrum, _nearest, _n, _, low_ns, high_ns = (
                    self.data.averaged_spectrum(
                        params["requested_time_ns"], half_columns
                    )
                )
                line.set_data(wavelengths, self.y_sign * spectrum)
                new_label = (
                    f"S{params['number']}: "
                    f"{self._format_average_range(low_ns, high_ns, 'ns')}"
                )
            else:
                times_ns, trace, _nearest, _n, _, low_nm, high_nm = (
                    self.data.averaged_trace(
                        params["requested_wavelength_nm"], half_window_nm
                    )
                )
                line.set_data(times_ns, self.y_sign * trace)
                new_label = (
                    f"K{params['number']}: "
                    f"{self._format_average_range(low_nm, high_nm, 'nm')}"
                )

            if line.get_label() == selected_before:
                selected_after = new_label
            line.set_label(new_label)

        self._hide_cursor()
        self._refresh_legend()
        self._refresh_curve_selector(selected_after)
        self._autoscale_ax(ax)
        self.canvas.draw_idle()
        if update_status:
            self.status_var.set(
                f"Re-averaged {len(lines)} {mode} curve(s) with {window_label}."
            )
        return len(lines)

    def _reorder_curves(self, mode: str) -> None:
        ax = self._ax_for_mode(mode)
        prefix = "S" if mode == MODE_SPECTRUM else "K"
        sort_key = (
            "requested_time_ns" if mode == MODE_SPECTRUM else "requested_wavelength_nm"
        )
        lines = [
            line
            for line in self._data_lines(ax)
            if self.curve_params.get(line, {}).get("mode") == mode
        ]
        if not lines:
            if mode == MODE_SPECTRUM:
                self.spec_count = 0
            else:
                self.kin_count = 0
            return

        lines.sort(key=lambda line: self.curve_params[line][sort_key])
        for index, line in enumerate(lines, start=1):
            self.curve_params[line]["number"] = index
            current_label = line.get_label()
            range_text = (
                current_label.split(": ", 1)[1]
                if ": " in current_label
                else current_label
            )
            line.set_label(f"{prefix}{index}: {range_text}")

        if mode == MODE_SPECTRUM:
            self.spec_count = len(lines)
        else:
            self.kin_count = len(lines)

    def _refresh_curve_selector(self, selected_label: str | None = None) -> None:
        labels = [
            line.get_label() for line in self._ordered_data_lines(self.ax_spec)
        ] + [line.get_label() for line in self._ordered_data_lines(self.ax_kin)]
        values = labels if labels else ["No curves"]
        self.curve_select_menu.configure(values=values)
        if selected_label in labels:
            self.curve_select_var.set(selected_label)
        elif labels:
            self.curve_select_var.set(labels[-1])
        else:
            self.curve_select_var.set("No curves")

    def _refresh_legend(self) -> None:
        for ax in self._all_axes():
            lines = self._ordered_data_lines(ax)
            legend = ax.get_legend()
            if not lines:
                if legend is not None:
                    legend.remove()
                continue
            ax.legend(
                lines,
                [line.get_label() for line in lines],
                loc="best",
                fontsize=self.secondary_font_size,
                facecolor=DARK_BG,
                edgecolor="white",
                labelcolor="white",
            )

    # ------------------------------------------------------------------
    # Axes appearance
    # ------------------------------------------------------------------
    def _reset_axes(self) -> None:
        self._reset_axes_single(self.ax_spec)
        self._reset_axes_single(self.ax_kin)
        self._update_baseline_plot()
        self._update_baseline_notice()

    def _reset_axes_single(self, ax) -> None:
        ax.set_facecolor(DARK_BG)
        if ax is self.ax_kin:
            ax.set_xlabel("Time (ns)", fontsize=self.plot_font_size, color="white")
            ax.set_ylabel(Y_LABEL, fontsize=self.plot_font_size, color="white")
            ax.set_title(
                "Averaged TA kinetics", fontsize=self.plot_font_size + 2, color="white"
            )
        else:
            ax.set_xlabel(
                "Wavelength (nm)", fontsize=self.plot_font_size, color="white"
            )
            ax.set_ylabel(Y_LABEL, fontsize=self.plot_font_size, color="white")
            ax.set_title(
                "Averaged TA spectrum", fontsize=self.plot_font_size + 2, color="white"
            )
        ax.tick_params(labelsize=self.secondary_font_size, colors="white")
        ax.grid(True, linestyle=":", alpha=0.3)
        ax.axhline(
            0.0,
            color="red",
            linestyle=(0, (4, 2)),
            linewidth=2.5,
            alpha=0.9,
            zorder=1,
            label="_zero_line",
        )

    # ------------------------------------------------------------------
    # Hover cursor (per subplot)
    # ------------------------------------------------------------------
    def _init_cursor_artists(self, ax) -> None:
        if ax in self.cursor_artists:
            return

        cursor_line = ax.axvline(
            color="white",
            linestyle="--",
            linewidth=0.9,
            alpha=0.5,
            visible=False,
            label="_cursor_line",
        )
        (cursor_marker,) = ax.plot(
            [],
            [],
            marker="o",
            markersize=max(5, int(5 * self.ui_scale)),
            color="white",
            markerfacecolor="yellow",
            visible=False,
            zorder=10,
            label="_cursor_marker",
        )
        cursor_annotation = ax.annotate(
            "",
            xy=(0, 0),
            xytext=(15, 15),
            textcoords="offset points",
            fontsize=self.secondary_font_size,
            color="white",
            bbox={"boxstyle": "round,pad=0.35", "fc": PANEL_BG, "ec": "white", "alpha": 0.9},
            arrowprops={"arrowstyle": "->", "color": "white"},
            visible=False,
            zorder=11,
        )
        self.cursor_artists[ax] = {
            "line": cursor_line,
            "marker": cursor_marker,
            "annotation": cursor_annotation,
        }

    def _toolbar_is_active(self) -> bool:
        toolbar = getattr(self, "toolbar", None)
        mode = getattr(toolbar, "mode", "")
        return bool(getattr(mode, "value", mode))

    def _ax_containing_line(self, target_line):
        for ax in self._all_axes():
            if target_line in self._data_lines(ax):
                return ax
        return None

    def _nearest_curve_at_event(self, event, max_distance_px: float | None = None):
        ax = event.inaxes
        if ax not in self._all_axes() or event.xdata is None:
            return None

        lines = self._data_lines(ax)
        if not lines:
            return None

        nearest = None
        for line in lines:
            x_data = np.asarray(line.get_xdata(), dtype=float)
            y_data = np.asarray(line.get_ydata(), dtype=float)
            finite = np.isfinite(x_data) & np.isfinite(y_data)
            if not finite.any():
                continue

            x_valid = x_data[finite]
            y_valid = y_data[finite]
            idx = int(np.nanargmin(np.abs(x_valid - event.xdata)))
            x_val = float(x_valid[idx])
            y_val = float(y_valid[idx])

            x_pixel, y_pixel = ax.transData.transform((x_val, y_val))
            distance_px = ((x_pixel - event.x) ** 2 + (y_pixel - event.y) ** 2) ** 0.5
            if nearest is None or distance_px < nearest[0]:
                nearest = (distance_px, line, x_val, y_val)

        if nearest is None:
            return None

        if max_distance_px is None:
            max_distance_px = 80 * self.ui_scale
        if nearest[0] > max_distance_px:
            return None

        _distance_px, line, x_val, y_val = nearest
        return ax, line, x_val, y_val

    def _on_plot_click(self, event) -> None:
        if event.button != 1:
            return
        self.canvas.get_tk_widget().focus_set()
        if self._toolbar_is_active():
            return

        nearest = self._nearest_curve_at_event(event, max_distance_px=20 * self.ui_scale)
        if nearest is None:
            self.plot_selected_curve = None
            self.status_var.set("Click close to a curve to select it before pressing D.")
            return

        _ax, line, _x_val, _y_val = nearest
        self.plot_selected_curve = line
        self.curve_select_var.set(line.get_label())
        self.status_var.set(
            f"Selected curve: {line.get_label()}. Press D while the plot is focused to delete it."
        )

    def _on_canvas_key_press(self, event) -> None:
        key = (event.key or "").lower()
        if self._toolbar_is_active():
            return

        if key == "left":
            self._shift_time(-1)
            return
        if key == "right":
            self._shift_time(1)
            return

        if key != "d":
            return

        if self.plot_selected_curve is None:
            self.status_var.set("Click a curve on the plot first, then press D to delete it.")
            return

        target_ax = self._ax_containing_line(self.plot_selected_curve)
        if target_ax is None:
            self.plot_selected_curve = None
            self.status_var.set("Selected curve no longer exists. Click another curve first.")
            return

        self._delete_curve(self.plot_selected_curve, target_ax)

    def _on_mouse_move(self, event) -> None:
        nearest = self._nearest_curve_at_event(event)
        if nearest is None:
            self._hide_cursor()
            return

        ax, line, x_val, y_val = nearest
        label = line.get_label()
        self._init_cursor_artists(ax)
        # Hide cursors on the other subplot so only one shows at a time.
        for other_ax, artists in self.cursor_artists.items():
            if other_ax is ax:
                continue
            for artist in artists.values():
                artist.set_visible(False)

        artists = self.cursor_artists[ax]
        artists["line"].set_xdata([x_val, x_val])
        artists["line"].set_visible(True)
        artists["marker"].set_data([x_val], [y_val])
        artists["marker"].set_visible(True)
        artists["annotation"].xy = (x_val, y_val)
        artists["annotation"].set_text(self._cursor_text(ax, x_val, y_val, label))
        artists["annotation"].set_visible(True)
        self.canvas.draw_idle()

    def _cursor_text(self, ax, x_val: float, y_val: float, label: str) -> str:
        if ax is self.ax_kin:
            return f"{label}\nTime: {x_val:.6g} ns\nSignal: {y_val:.6g}"
        return f"{label}\nWavelength: {x_val:.6g} nm\nSignal: {y_val:.6g}"

    def _hide_cursor(self, _event=None) -> None:
        changed = False
        for artists in self.cursor_artists.values():
            for artist in artists.values():
                if artist.get_visible():
                    artist.set_visible(False)
                    changed = True
        if changed:
            self.canvas.draw_idle()


if __name__ == "__main__":
    enable_high_dpi_awareness()
    app = TAViewer()
    app.mainloop()
