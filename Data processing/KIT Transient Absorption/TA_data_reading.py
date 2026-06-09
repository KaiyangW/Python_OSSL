import json
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

DARK_BG = "#1a1a1a"
PANEL_BG = "#2b2b2b"
ACCENT = "#2CC985"
ACCENT_RED = "#C92C45"


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

PLOT_MODES = (
    "Spectra",
    "Kinetics",
)

DEFAULT_SPECTRUM_X_MIN_NM = 460.0
DEFAULT_SPECTRUM_X_MAX_NM = 1000.0


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


class TAData:
    def __init__(self, path: Path):
        self.path = path
        self.times_s: np.ndarray
        self.wavelengths_nm: np.ndarray
        self.signal: np.ndarray
        self.load(path)

    def load(self, path: Path) -> None:
        # Matches the grid written by mat_to_CSV.py: corner label
        # "Wavelength (nm) \ Time (s)", first row = times (s), first column =
        # wavelengths (nm), body = dT/T matrix [wavelengths x times].
        if path.suffix.lower() == ".csv":
            raw = pd.read_csv(path, header=None)
        else:
            raw = pd.read_excel(path, header=None, engine="openpyxl")
        numeric = raw.apply(pd.to_numeric, errors="coerce")

        times = numeric.iloc[0, 1:].to_numpy(dtype=float)
        wavelengths = numeric.iloc[1:, 0].to_numpy(dtype=float)
        signal = numeric.iloc[1:, 1:].to_numpy(dtype=float)

        valid_time = np.isfinite(times)
        valid_wavelength = np.isfinite(wavelengths)

        if valid_time.sum() == 0 or valid_wavelength.sum() == 0:
            raise ValueError("Could not find numeric time or wavelength data.")

        self.path = path
        self.times_s = times[valid_time]
        self.wavelengths_nm = wavelengths[valid_wavelength]
        self.signal = signal[np.ix_(valid_wavelength, valid_time)]

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
    ) -> tuple[np.ndarray, np.ndarray, float, int, bool]:
        requested_time_s = requested_time_ns * 1e-9
        center_idx = int(np.nanargmin(np.abs(self.times_s - requested_time_s)))
        nearest_time_s = float(self.times_s[center_idx])

        low = max(0, center_idx - int(half_columns))
        high = min(self.times_s.size, center_idx + int(half_columns) + 1)
        selected = np.arange(low, high)

        spectrum = np.nanmean(self.signal[:, selected], axis=1)
        return (
            self.wavelengths_nm,
            spectrum,
            nearest_time_s * 1e9,
            int(selected.size),
            False,
        )

    def averaged_trace(
        self, requested_wavelength_nm: float, half_window_nm: float
    ) -> tuple[np.ndarray, np.ndarray, float, int, bool]:
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

        trace = np.nanmean(self.signal[selected, :], axis=0)
        return (
            self.times_s * 1e9,
            trace,
            nearest_wavelength_nm,
            int(selected.size),
            used_nearest_only,
        )


class TAViewer(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KIT TA Data Reading")
        ctk.set_appearance_mode("Dark")
        self._configure_display()

        self.data: TAData | None = None
        self.curve_count = 0
        self.curve_params: dict = {}
        # Net transform applied via Flip Y / baseline shift, so curves can be
        # recomputed (e.g. after an averaging change) without losing them.
        self.y_sign = 1.0
        self.y_offset = 0.0
        self.cursor_line = None
        self.cursor_marker = None
        self.cursor_annotation = None

        self.file_var = tk.StringVar(value=str(DEFAULT_DATA_FILE))
        self.mode_var = tk.StringVar(value=PLOT_MODES[0])
        self.time_var = tk.StringVar(value="0")
        self.window_var = tk.StringVar(value="+/-1 col (3 total)")
        self.wavelength_var = tk.StringVar(value="500")
        self.wavelength_window_var = tk.StringVar(value="2 nm")
        self.x_min_var = tk.StringVar(value=f"{DEFAULT_SPECTRUM_X_MIN_NM:g}")
        self.x_max_var = tk.StringVar(value=f"{DEFAULT_SPECTRUM_X_MAX_NM:g}")
        self.y_min_var = tk.StringVar(value="")
        self.y_max_var = tk.StringVar(value="")
        self.baseline_step_var = tk.StringVar(value="1e-4")
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
            width=340,
            label_text="Controls",
            scrollbar_button_color=ACCENT,
            scrollbar_button_hover_color="#249b6b",
        )
        sidebar.pack(side="left", fill="y", padx=10, pady=10)

        # --- Data file ---
        ctk.CTkLabel(sidebar, text="Data file", anchor="w").pack(
            fill="x", padx=10, pady=(10, 2)
        )
        self.file_entry = ctk.CTkEntry(sidebar, textvariable=self.file_var)
        self.file_entry.pack(fill="x", padx=10, pady=2)
        ctk.CTkButton(sidebar, text="Browse", command=self._browse_file).pack(
            fill="x", padx=10, pady=4
        )
        ctk.CTkButton(sidebar, text="Load", command=self._load_file_from_entry).pack(
            fill="x", padx=10, pady=(0, 12)
        )

        # --- Plot mode ---
        ctk.CTkLabel(sidebar, text="Plot mode", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        ctk.CTkOptionMenu(
            sidebar,
            variable=self.mode_var,
            values=list(PLOT_MODES),
            command=lambda _value: self._mode_changed(),
        ).pack(fill="x", padx=10, pady=2)

        ctk.CTkButton(
            sidebar, text="Add Curve", command=self.add_curve, fg_color=ACCENT,
            hover_color="#249b6b", text_color="#0d0d0d",
        ).pack(fill="x", padx=10, pady=(12, 4))
        self.curve_select_menu = ctk.CTkOptionMenu(
            sidebar,
            variable=self.curve_select_var,
            values=["No curves"],
        )
        self.curve_select_menu.pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(
            sidebar,
            text="Delete Selected Curve",
            command=self.delete_selected_curve,
            fg_color=ACCENT_RED,
            hover_color="#9b2436",
        ).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(
            sidebar, text="Clear Curves", command=self.clear_curves,
            fg_color="gray30", hover_color="gray25",
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Time controls ---
        ctk.CTkLabel(sidebar, text="Time (ns)", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        time_entry = ctk.CTkEntry(sidebar, textvariable=self.time_var)
        time_entry.pack(fill="x", padx=10, pady=2)
        time_entry.bind("<Return>", lambda _event: self.add_curve())
        self.time_scale = ctk.CTkSlider(
            sidebar, from_=0, to=1, command=self._scale_changed
        )
        self.time_scale.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(sidebar, text="Time average (columns)", anchor="w").pack(
            fill="x", padx=10, pady=(2, 2)
        )
        ctk.CTkOptionMenu(
            sidebar,
            variable=self.window_var,
            values=list(AVERAGE_WINDOWS),
            command=lambda _value: self._reaverage_curves(),
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Wavelength controls ---
        ctk.CTkLabel(sidebar, text="Wavelength (nm)", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        wavelength_entry = ctk.CTkEntry(sidebar, textvariable=self.wavelength_var)
        wavelength_entry.pack(fill="x", padx=10, pady=2)
        wavelength_entry.bind("<Return>", lambda _event: self.add_curve())
        self.wavelength_scale = ctk.CTkSlider(
            sidebar, from_=0, to=1, command=self._wavelength_scale_changed
        )
        self.wavelength_scale.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(sidebar, text="Wavelength average (+/-)", anchor="w").pack(
            fill="x", padx=10, pady=(2, 2)
        )
        ctk.CTkOptionMenu(
            sidebar,
            variable=self.wavelength_window_var,
            values=list(WAVELENGTH_WINDOWS),
            command=lambda _value: self._reaverage_curves(),
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Axis controls ---
        ctk.CTkLabel(sidebar, text="Axis range (blank = auto)", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        axis_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        axis_frame.pack(fill="x", padx=10, pady=2)
        axis_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(axis_frame, text="X min", anchor="w").grid(
            row=0, column=0, sticky="ew", padx=(0, 4), pady=(0, 2)
        )
        ctk.CTkLabel(axis_frame, text="X max", anchor="w").grid(
            row=0, column=1, sticky="ew", padx=(4, 0), pady=(0, 2)
        )
        x_min_entry = ctk.CTkEntry(axis_frame, textvariable=self.x_min_var)
        x_max_entry = ctk.CTkEntry(axis_frame, textvariable=self.x_max_var)
        x_min_entry.grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        x_max_entry.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))

        ctk.CTkLabel(axis_frame, text="Y min", anchor="w").grid(
            row=2, column=0, sticky="ew", padx=(0, 4), pady=(0, 2)
        )
        ctk.CTkLabel(axis_frame, text="Y max", anchor="w").grid(
            row=2, column=1, sticky="ew", padx=(4, 0), pady=(0, 2)
        )
        y_min_entry = ctk.CTkEntry(axis_frame, textvariable=self.y_min_var)
        y_max_entry = ctk.CTkEntry(axis_frame, textvariable=self.y_max_var)
        y_min_entry.grid(row=3, column=0, sticky="ew", padx=(0, 4), pady=(0, 6))
        y_max_entry.grid(row=3, column=1, sticky="ew", padx=(4, 0), pady=(0, 6))

        for entry in (x_min_entry, x_max_entry, y_min_entry, y_max_entry):
            entry.bind("<Return>", lambda _event: self.apply_axis_limits())

        ctk.CTkButton(
            sidebar, text="Apply Axis Range", command=self.apply_axis_limits
        ).pack(fill="x", padx=10, pady=(4, 4))
        ctk.CTkButton(
            sidebar,
            text="Auto Y Axis",
            command=self.auto_y_axis_limits,
            fg_color="gray30",
            hover_color="gray25",
        ).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(
            sidebar,
            text="Auto Axis Range",
            command=self.auto_axis_limits,
            fg_color="gray30",
            hover_color="gray25",
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Data transform ---
        ctk.CTkLabel(sidebar, text="Data transform", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        ctk.CTkButton(
            sidebar, text="Flip Y (x -1)", command=self.flip_y_axis
        ).pack(fill="x", padx=10, pady=(0, 6))

        ctk.CTkLabel(sidebar, text="Baseline shift step", anchor="w").pack(
            fill="x", padx=10, pady=(2, 2)
        )
        baseline_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        baseline_frame.pack(fill="x", padx=10, pady=(0, 12))
        baseline_frame.grid_columnconfigure(0, weight=1)

        baseline_entry = ctk.CTkEntry(
            baseline_frame, textvariable=self.baseline_step_var
        )
        baseline_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(
            baseline_frame,
            text="\u25b2",
            width=44,
            command=lambda: self.shift_baseline(1),
        ).grid(row=0, column=1, padx=2)
        ctk.CTkButton(
            baseline_frame,
            text="\u25bc",
            width=44,
            command=lambda: self.shift_baseline(-1),
        ).grid(row=0, column=2, padx=(2, 0))

        # --- Settings ---
        ctk.CTkLabel(sidebar, text="Settings", anchor="w").pack(
            fill="x", padx=10, pady=(6, 2)
        )
        ctk.CTkButton(
            sidebar, text="Save JSON Settings", command=self.save_settings
        ).pack(fill="x", padx=10, pady=(0, 4))
        ctk.CTkButton(
            sidebar,
            text="Read JSON Settings",
            command=self.read_settings,
            fg_color="gray30",
            hover_color="gray25",
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Save ---
        ctk.CTkButton(
            sidebar,
            text="Save PNG + XLSX",
            command=self.save_outputs,
            fg_color=ACCENT,
            hover_color="#249b6b",
            text_color="#0d0d0d",
        ).pack(fill="x", padx=10, pady=(0, 12))

        # --- Status ---
        self.status_label = ctk.CTkLabel(
            sidebar,
            textvariable=self.status_var,
            justify="left",
            anchor="w",
            wraplength=280,
            text_color=ACCENT,
        )
        self.status_label.pack(fill="x", padx=10, pady=(8, 12))

        # --- Plot area ---
        plot_frame = ctk.CTkFrame(self)
        plot_frame.pack(side="right", fill="both", expand=True, padx=(0, 10), pady=10)

        plt.style.use("dark_background")
        self.figure = Figure(figsize=(10, 6), dpi=110)
        self.figure.patch.set_facecolor(DARK_BG)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(DARK_BG)
        self._reset_axes()

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("axes_leave_event", self._hide_cursor)

        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame, pack_toolbar=False)
        toolbar.update()
        try:
            toolbar.config(background=PANEL_BG)
            for child in toolbar.winfo_children():
                try:
                    child.config(background=PANEL_BG, foreground="white")
                except tk.TclError:
                    try:
                        child.config(background=PANEL_BG)
                    except tk.TclError:
                        pass
        except tk.TclError:
            pass
        toolbar.pack(fill="x", padx=10, pady=(4, 10))

    def _browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose TA data file",
            initialdir=str(DEFAULT_DATA_FILE.parent),
            filetypes=[
                ("CSV files", "*.csv"),
                ("Excel files", "*.xlsx"),
                ("All files", "*.*"),
            ],
        )
        if selected:
            self.file_var.set(selected)
            self._load_file(Path(selected))

    def _load_file_from_entry(self) -> None:
        self._load_file(Path(self.file_var.get()))

    def _load_file(self, path: Path) -> None:
        try:
            self.data = TAData(path)
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))
            return

        self.file_var.set(str(path))
        self.clear_curves()
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

    def apply_axis_limits(self) -> None:
        if not self._apply_axis_limits_to_axes():
            return
        self.canvas.draw_idle()
        self.status_var.set("Applied axis range. Leave a field blank to autoscale it.")

    def auto_axis_limits(self) -> None:
        self.x_min_var.set("")
        self.x_max_var.set("")
        self.y_min_var.set("")
        self.y_max_var.set("")
        self._autoscale_to_data_lines()
        self.canvas.draw_idle()
        self.status_var.set("Restored automatic axis range.")

    def auto_y_axis_limits(self) -> None:
        data_limits = self._data_limits(x_limits=self.ax.get_xlim())
        if data_limits is None:
            messagebox.showinfo(
                "Auto Y Axis",
                "No plotted data points are inside the current x-axis range.",
            )
            return

        self.y_min_var.set("")
        self.y_max_var.set("")
        _auto_xlim, auto_ylim = data_limits
        if not self._apply_axis_limits_to_axes(auto_ylim=auto_ylim):
            return
        self.canvas.draw_idle()
        self.status_var.set("Restored automatic y-axis range.")

    def flip_y_axis(self) -> None:
        lines = self._data_lines()
        if not lines:
            messagebox.showwarning("No data", "Add at least one curve first.")
            return
        for line in lines:
            line.set_ydata(-np.asarray(line.get_ydata(), dtype=float))
        self.y_sign *= -1.0
        self.y_offset = -self.y_offset
        self._hide_cursor()
        self.canvas.draw_idle()
        self.status_var.set("Flipped data along the y-axis (x -1).")

    def shift_baseline(self, direction: int) -> None:
        lines = self._data_lines()
        if not lines:
            messagebox.showwarning("No data", "Add at least one curve first.")
            return
        try:
            step = float(self.baseline_step_var.get())
        except ValueError:
            messagebox.showerror(
                "Invalid step", "Baseline shift step must be numeric."
            )
            return

        offset = direction * step
        for line in lines:
            line.set_ydata(np.asarray(line.get_ydata(), dtype=float) + offset)
        self.y_offset += offset
        self._hide_cursor()
        self.canvas.draw_idle()
        self.status_var.set(f"Shifted baseline by {offset:+.6g}.")

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
            "plot_mode": self.mode_var.get(),
            "time_ns": self.time_var.get(),
            "time_average": self.window_var.get(),
            "wavelength_nm": self.wavelength_var.get(),
            "wavelength_average": self.wavelength_window_var.get(),
            "x_min": self.x_min_var.get(),
            "x_max": self.x_max_var.get(),
            "y_min": self.y_min_var.get(),
            "y_max": self.y_max_var.get(),
            "baseline_step": self.baseline_step_var.get(),
            "y_sign": self.y_sign,
            "y_offset": self.y_offset,
            "curves": self._current_curves(),
        }

    def _current_curves(self) -> list[dict]:
        curves: list[dict] = []
        for line in self._data_lines():
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
        data_file = str(settings.get("data_file", "")).strip()
        if data_file:
            self.file_var.set(data_file)
            data_path = Path(data_file)
            if data_path.exists():
                self._load_file(data_path)

        plot_mode = str(settings.get("plot_mode", self.mode_var.get()))
        mode_changed = plot_mode in PLOT_MODES and plot_mode != self.mode_var.get()
        if plot_mode in PLOT_MODES:
            self.mode_var.set(plot_mode)
            if mode_changed:
                self.clear_curves()

        self._set_if_valid_option(self.window_var, settings, "time_average", AVERAGE_WINDOWS)
        self._set_if_valid_option(
            self.wavelength_window_var,
            settings,
            "wavelength_average",
            WAVELENGTH_WINDOWS,
        )

        if "curves" in settings:
            self._restore_curves(settings)
        else:
            self._reaverage_curves()

        # Set after restoring curves: restoring mutates time/wavelength vars per
        # curve, so apply the saved "current" selection last.
        for key, variable in (
            ("time_ns", self.time_var),
            ("wavelength_nm", self.wavelength_var),
            ("x_min", self.x_min_var),
            ("x_max", self.x_max_var),
            ("y_min", self.y_min_var),
            ("y_max", self.y_max_var),
            ("baseline_step", self.baseline_step_var),
        ):
            if key in settings:
                variable.set(str(settings[key]))

        self._sync_sliders_to_entries()
        self._reset_axes()
        self.apply_axis_limits()

    def _restore_curves(self, settings: dict) -> None:
        curves = settings.get("curves")
        if not isinstance(curves, list):
            return

        self.clear_curves()
        if self.data is None:
            return

        # clear_curves resets the transform, so restore flip/baseline before
        # plotting so each curve renders with the saved transform applied.
        try:
            self.y_sign = -1.0 if float(settings.get("y_sign", 1.0)) < 0 else 1.0
        except (TypeError, ValueError):
            self.y_sign = 1.0
        try:
            self.y_offset = float(settings.get("y_offset", 0.0))
        except (TypeError, ValueError):
            self.y_offset = 0.0

        for curve in curves:
            if not isinstance(curve, dict):
                continue
            mode = str(curve.get("mode", self.mode_var.get()))
            if mode == PLOT_MODES[1] and "requested_wavelength_nm" in curve:
                self.wavelength_var.set(str(curve["requested_wavelength_nm"]))
                self._add_wavelength_trace()
            elif "requested_time_ns" in curve:
                self.time_var.set(str(curve["requested_time_ns"]))
                self._add_time_spectrum()

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

    def save_outputs(self) -> None:
        lines = self._data_lines()
        if not lines:
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
            self._save_data_workbook(xlsx_path, lines)
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return

        self.status_var.set(
            f"Saved {png_path.name} (400 dpi) and {xlsx_path.name}."
        )

    def _save_data_workbook(self, xlsx_path: Path, lines) -> None:
        x_label = self.ax.get_xlabel() or "X"
        y_label = self.ax.get_ylabel() or "Y"
        x_min, x_max = self.ax.get_xlim()
        y_min, y_max = self.ax.get_ylim()

        info_df = pd.DataFrame(
            {
                "Property": [
                    "Plot title",
                    "X axis title",
                    "Y axis title",
                    "X min",
                    "X max",
                    "Y min",
                    "Y max",
                ],
                "Value": [
                    self.ax.get_title(),
                    x_label,
                    y_label,
                    x_min,
                    x_max,
                    y_min,
                    y_max,
                ],
            }
        )

        x_low, x_high = sorted((x_min, x_max))
        clipped_lines = []
        for line in lines:
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
            data_df = pd.DataFrame(data)
        else:
            frames = []
            for line, x_data, y_data in clipped_lines:
                frames.append(
                    pd.DataFrame(
                        {
                            f"{line.get_label()} | {x_label}": x_data,
                            f"{line.get_label()} | {y_label}": y_data,
                        }
                    )
                )
            data_df = pd.concat(frames, axis=1)

        with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
            info_df.to_excel(writer, sheet_name="Plot info", index=False)
            data_df.to_excel(writer, sheet_name="Data", index=False)

    def _mode_changed(self) -> None:
        self._set_default_axis_limits_for_mode()
        self.clear_curves()
        self.status_var.set(f"Switched to {self.mode_var.get()} mode.")

    def _set_default_axis_limits_for_mode(self) -> None:
        if self.mode_var.get() == PLOT_MODES[0]:
            self.x_min_var.set(f"{DEFAULT_SPECTRUM_X_MIN_NM:g}")
            self.x_max_var.set(f"{DEFAULT_SPECTRUM_X_MAX_NM:g}")
        else:
            self.x_min_var.set("")
            self.x_max_var.set("")
        self.y_min_var.set("")
        self.y_max_var.set("")

    def add_curve(self) -> None:
        if self.data is None:
            messagebox.showwarning("No data", "Please load a data file first.")
            return

        if self.mode_var.get() == PLOT_MODES[1]:
            self._add_wavelength_trace()
        else:
            self._add_time_spectrum()

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
        ) = self.data.averaged_spectrum(requested_time_ns, half_columns)

        self.curve_count += 1
        label = (
            f"{self.curve_count}: {nearest_time_ns:.6g} ns, "
            f"{window_label}, {n_columns} cols"
        )
        (line,) = self.ax.plot(
            wavelengths, self.y_sign * spectrum + self.y_offset, label=label
        )
        self.curve_params[line] = {
            "mode": PLOT_MODES[0],
            "number": self.curve_count,
            "requested_time_ns": requested_time_ns,
        }
        self.ax.legend(
            loc="best",
            fontsize=self.secondary_font_size,
            facecolor=DARK_BG,
            edgecolor="white",
            labelcolor="white",
        )
        self._autoscale_to_data_lines()
        self._init_cursor_artists()
        self._refresh_curve_selector(label)
        self.canvas.draw_idle()

        fallback_note = " No time point fell inside the window; used nearest column." if used_nearest_only else ""
        self.status_var.set(
            f"Added curve at nearest time {nearest_time_ns:.6g} ns "
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
        ) = self.data.averaged_trace(requested_wavelength_nm, half_window_nm)

        self.curve_count += 1
        label = (
            f"{self.curve_count}: {nearest_wavelength_nm:.6g} nm, "
            f"{window_label}, {n_rows} rows"
        )
        (line,) = self.ax.plot(
            times_ns,
            self.y_sign * trace + self.y_offset,
            marker="o",
            markersize=3,
            linewidth=1.3,
            label=label,
        )
        self.curve_params[line] = {
            "mode": PLOT_MODES[1],
            "number": self.curve_count,
            "requested_wavelength_nm": requested_wavelength_nm,
        }
        self.ax.legend(
            loc="best",
            fontsize=self.secondary_font_size,
            facecolor=DARK_BG,
            edgecolor="white",
            labelcolor="white",
        )
        self._autoscale_to_data_lines()
        self._init_cursor_artists()
        self._refresh_curve_selector(label)
        self.canvas.draw_idle()

        fallback_note = (
            " No wavelength fell inside the window; used nearest row."
            if used_nearest_only
            else ""
        )
        self.status_var.set(
            f"Added trace at nearest wavelength {nearest_wavelength_nm:.6g} nm "
            f"(requested {requested_wavelength_nm:.6g} nm, averaged {n_rows} rows)."
            f"{fallback_note}"
        )

    def clear_curves(self) -> None:
        self.ax.clear()
        self.cursor_line = None
        self.cursor_marker = None
        self.cursor_annotation = None
        self._reset_axes()
        self.curve_count = 0
        self.curve_params.clear()
        self.y_sign = 1.0
        self.y_offset = 0.0
        self._refresh_curve_selector()
        self.canvas.draw_idle()

    def delete_selected_curve(self) -> None:
        selected_label = self.curve_select_var.get()
        for line in self._data_lines():
            if line.get_label() == selected_label:
                line.remove()
                self.curve_params.pop(line, None)
                break
        else:
            messagebox.showwarning(
                "No curve selected", "Please select a curve to delete first."
            )
            return

        self._hide_cursor()
        self._refresh_legend()
        if self._data_lines():
            self._autoscale_to_data_lines()
        else:
            self._reset_axes()
        self._refresh_curve_selector()
        self.canvas.draw_idle()
        self.status_var.set(f"Deleted curve: {selected_label}")

    def _reaverage_curves(self) -> None:
        if self.data is None:
            return
        lines = [line for line in self._data_lines() if line in self.curve_params]
        if not lines:
            return

        spectrum_mode = self.mode_var.get() != PLOT_MODES[1]
        if spectrum_mode:
            window_label = self.window_var.get()
            half_columns = AVERAGE_WINDOWS[window_label]
        else:
            window_label = self.wavelength_window_var.get()
            half_window_nm = WAVELENGTH_WINDOWS[window_label]

        selected_before = self.curve_select_var.get()
        selected_after = None
        for line in lines:
            params = self.curve_params[line]
            if spectrum_mode:
                if params["mode"] != PLOT_MODES[0]:
                    continue
                wavelengths, spectrum, nearest_time_ns, n_columns, _ = (
                    self.data.averaged_spectrum(
                        params["requested_time_ns"], half_columns
                    )
                )
                line.set_data(wavelengths, self.y_sign * spectrum + self.y_offset)
                new_label = (
                    f"{params['number']}: {nearest_time_ns:.6g} ns, "
                    f"{window_label}, {n_columns} cols"
                )
            else:
                if params["mode"] != PLOT_MODES[1]:
                    continue
                times_ns, trace, nearest_wavelength_nm, n_rows, _ = (
                    self.data.averaged_trace(
                        params["requested_wavelength_nm"], half_window_nm
                    )
                )
                line.set_data(times_ns, self.y_sign * trace + self.y_offset)
                new_label = (
                    f"{params['number']}: {nearest_wavelength_nm:.6g} nm, "
                    f"{window_label}, {n_rows} rows"
                )

            if line.get_label() == selected_before:
                selected_after = new_label
            line.set_label(new_label)

        self._hide_cursor()
        self._refresh_legend()
        self._refresh_curve_selector(selected_after)
        self._autoscale_to_data_lines()
        self.canvas.draw_idle()
        self.status_var.set(
            f"Re-averaged {len(lines)} existing curve(s) with {window_label}."
        )

    def _refresh_curve_selector(self, selected_label: str | None = None) -> None:
        labels = [line.get_label() for line in self._data_lines()]
        values = labels if labels else ["No curves"]
        self.curve_select_menu.configure(values=values)
        if selected_label in labels:
            self.curve_select_var.set(selected_label)
        elif labels:
            self.curve_select_var.set(labels[-1])
        else:
            self.curve_select_var.set("No curves")

    def _refresh_legend(self) -> None:
        lines = self._data_lines()
        legend = self.ax.get_legend()
        if not lines:
            if legend is not None:
                legend.remove()
            return

        self.ax.legend(
            loc="best",
            fontsize=self.secondary_font_size,
            facecolor=DARK_BG,
            edgecolor="white",
            labelcolor="white",
        )

    def _reset_axes(self) -> None:
        self.ax.set_facecolor(DARK_BG)
        if self.mode_var.get() == PLOT_MODES[1]:
            self.ax.set_xlabel("Time (ns)", fontsize=self.plot_font_size, color="white")
            self.ax.set_ylabel(
                "Ave Delta T/T", fontsize=self.plot_font_size, color="white"
            )
            self.ax.set_title(
                "Averaged TA trace", fontsize=self.plot_font_size + 2, color="white"
            )
        else:
            self.ax.set_xlabel(
                "Wavelength (nm)", fontsize=self.plot_font_size, color="white"
            )
            self.ax.set_ylabel(
                "Ave Delta T/T", fontsize=self.plot_font_size, color="white"
            )
            self.ax.set_title(
                "Averaged TA spectrum", fontsize=self.plot_font_size + 2, color="white"
            )
        self.ax.tick_params(labelsize=self.secondary_font_size, colors="white")
        self.ax.grid(True, linestyle=":", alpha=0.3)

    def _init_cursor_artists(self) -> None:
        if self.cursor_line is not None:
            return

        self.cursor_line = self.ax.axvline(
            color="white",
            linestyle="--",
            linewidth=0.9,
            alpha=0.5,
            visible=False,
            label="_cursor_line",
        )
        (self.cursor_marker,) = self.ax.plot(
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
        self.cursor_annotation = self.ax.annotate(
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

    def _on_mouse_move(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None:
            self._hide_cursor()
            return

        lines = self._data_lines()
        if not lines:
            self._hide_cursor()
            return

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

            x_pixel, y_pixel = self.ax.transData.transform((x_val, y_val))
            distance_px = ((x_pixel - event.x) ** 2 + (y_pixel - event.y) ** 2) ** 0.5
            if nearest is None or distance_px < nearest[0]:
                nearest = (distance_px, x_val, y_val, line.get_label())

        if nearest is None:
            self._hide_cursor()
            return

        max_distance_px = 80 * self.ui_scale
        if nearest[0] > max_distance_px:
            self._hide_cursor()
            return

        _, x_val, y_val, label = nearest
        self._init_cursor_artists()
        assert self.cursor_line is not None
        assert self.cursor_marker is not None
        assert self.cursor_annotation is not None

        self.cursor_line.set_xdata([x_val, x_val])
        self.cursor_line.set_visible(True)
        self.cursor_marker.set_data([x_val], [y_val])
        self.cursor_marker.set_visible(True)
        self.cursor_annotation.xy = (x_val, y_val)
        self.cursor_annotation.set_text(self._cursor_text(x_val, y_val, label))
        self.cursor_annotation.set_visible(True)
        self.canvas.draw_idle()

    def _cursor_text(self, x_val: float, y_val: float, label: str) -> str:
        if self.mode_var.get() == PLOT_MODES[1]:
            return f"{label}\nTime: {x_val:.6g} ns\nSignal: {y_val:.6g}"
        return f"{label}\nWavelength: {x_val:.6g} nm\nSignal: {y_val:.6g}"

    def _hide_cursor(self, _event=None) -> None:
        changed = False
        for artist in (
            self.cursor_line,
            self.cursor_marker,
            self.cursor_annotation,
        ):
            if artist is not None and artist.get_visible():
                artist.set_visible(False)
                changed = True
        if changed:
            self.canvas.draw_idle()

    def _data_lines(self):
        return [
            line
            for line in self.ax.get_lines()
            if not line.get_label().startswith("_")
        ]

    def _autoscale_to_data_lines(self) -> None:
        data_limits = self._data_limits()
        if data_limits is None:
            return

        auto_xlim, auto_ylim = data_limits
        if not self._apply_axis_limits_to_axes(auto_xlim=auto_xlim, auto_ylim=auto_ylim):
            return

        # Refine the y-axis to fit only the data inside the now-current x window
        # (respects manual x range), so adding a curve does not require a manual
        # "Auto Y Axis" click afterwards.
        within = self._data_limits(x_limits=self.ax.get_xlim())
        if within is not None:
            self._apply_axis_limits_to_axes(auto_ylim=within[1])

    def _data_limits(
        self, x_limits: tuple[float, float] | None = None
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        x_values = []
        y_values = []
        x_low, x_high = (None, None)
        if x_limits is not None:
            x_low, x_high = sorted(x_limits)

        for line in self._data_lines():
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

    def _apply_axis_limits_to_axes(
        self,
        auto_xlim: tuple[float, float] | None = None,
        auto_ylim: tuple[float, float] | None = None,
    ) -> bool:
        try:
            x_min = self._axis_bound(self.x_min_var, "X min")
            x_max = self._axis_bound(self.x_max_var, "X max")
            y_min = self._axis_bound(self.y_min_var, "Y min")
            y_max = self._axis_bound(self.y_max_var, "Y max")
        except ValueError as exc:
            messagebox.showerror("Invalid axis range", str(exc))
            return False

        current_xlim = self.ax.get_xlim()
        current_ylim = self.ax.get_ylim()
        x_low = auto_xlim[0] if x_min is None and auto_xlim is not None else x_min
        x_high = auto_xlim[1] if x_max is None and auto_xlim is not None else x_max
        y_low = auto_ylim[0] if y_min is None and auto_ylim is not None else y_min
        y_high = auto_ylim[1] if y_max is None and auto_ylim is not None else y_max

        x_low = current_xlim[0] if x_low is None else x_low
        x_high = current_xlim[1] if x_high is None else x_high
        y_low = current_ylim[0] if y_low is None else y_low
        y_high = current_ylim[1] if y_high is None else y_high

        if x_low >= x_high:
            messagebox.showerror("Invalid axis range", "X min must be smaller than X max.")
            return False
        if y_low >= y_high:
            messagebox.showerror("Invalid axis range", "Y min must be smaller than Y max.")
            return False

        self.ax.set_xlim(x_low, x_high)
        self.ax.set_ylim(y_low, y_high)
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


if __name__ == "__main__":
    enable_high_dpi_awareness()
    app = TAViewer()
    app.mainloop()
