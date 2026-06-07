from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pandas as pd
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


DEFAULT_DATA_FILE = Path(
    r"C:\My files\Google drive sync\KIT\21_L21_Exp2_00_combined_split.xlsx"
)

AVERAGE_WINDOWS = {
    "10 ps": 10e-12,
    "1 ns": 1e-9,
    "2 ns": 2e-9,
}

WAVELENGTH_WINDOWS = {
    "2 nm": 2.0,
    "3 nm": 3.0,
}

PLOT_MODES = (
    "Time -> spectrum",
    "Wavelength -> trace",
)


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
        self, requested_time_ns: float, half_window_s: float
    ) -> tuple[np.ndarray, np.ndarray, float, int, bool]:
        requested_time_s = requested_time_ns * 1e-9
        center_idx = int(np.nanargmin(np.abs(self.times_s - requested_time_s)))
        nearest_time_s = float(self.times_s[center_idx])

        selected = np.where(np.abs(self.times_s - requested_time_s) <= half_window_s)[0]
        used_nearest_only = selected.size == 0
        if used_nearest_only:
            selected = np.array([center_idx])

        spectrum = np.nanmean(self.signal[:, selected], axis=1)
        return (
            self.wavelengths_nm,
            spectrum,
            nearest_time_s * 1e9,
            int(selected.size),
            used_nearest_only,
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


class TAViewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("KIT TA Data Reading")
        self._configure_display()

        self.data: TAData | None = None
        self.curve_count = 0
        self.cursor_line = None
        self.cursor_marker = None
        self.cursor_annotation = None

        self.file_var = tk.StringVar(value=str(DEFAULT_DATA_FILE))
        self.mode_var = tk.StringVar(value=PLOT_MODES[0])
        self.time_var = tk.StringVar(value="0")
        self.window_var = tk.StringVar(value="1 ns")
        self.wavelength_var = tk.StringVar(value="500")
        self.wavelength_window_var = tk.StringVar(value="2 nm")
        self.status_var = tk.StringVar(value="Load a data file to begin.")

        self._build_ui()
        if DEFAULT_DATA_FILE.exists():
            self._load_file(DEFAULT_DATA_FILE)

    def _configure_display(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        scale = max(1.0, min(2.0, screen_width / 1920))
        self.tk.call("tk", "scaling", scale)

        style = ttk.Style(self)
        base_font = max(10, int(10 * scale))
        heading_font = max(11, int(11 * scale))
        style.configure(".", font=("Segoe UI", base_font))
        style.configure("TButton", padding=(8, 5))
        style.configure("TLabelframe.Label", font=("Segoe UI", heading_font, "bold"))
        self.ui_scale = scale
        self.plot_font_size = max(10, int(10 * scale))

        width = min(max(1200, int(screen_width * 0.72)), 2200)
        height = min(max(820, int(screen_height * 0.76)), 1400)
        self.geometry(f"{width}x{height}")

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=14)
        main.pack(fill=tk.BOTH, expand=True)

        controls = ttk.LabelFrame(main, text="Controls", padding=10)
        controls.pack(fill=tk.X)

        ttk.Label(controls, text="Data file").grid(row=0, column=0, sticky=tk.W)
        file_entry = ttk.Entry(controls, textvariable=self.file_var)
        file_entry.grid(row=0, column=1, columnspan=5, sticky=tk.EW, padx=8)
        ttk.Button(controls, text="Browse", command=self._browse_file).grid(
            row=0, column=6, padx=4
        )
        ttk.Button(controls, text="Load", command=self._load_file_from_entry).grid(
            row=0, column=7, padx=4
        )

        ttk.Label(controls, text="Plot mode").grid(row=1, column=0, sticky=tk.W, pady=10)
        ttk.Combobox(
            controls,
            textvariable=self.mode_var,
            values=PLOT_MODES,
            state="readonly",
            width=22,
        ).grid(row=1, column=1, sticky=tk.W, padx=8, pady=10)
        self.mode_var.trace_add("write", lambda *_args: self._mode_changed())

        ttk.Button(controls, text="Add Curve", command=self.add_curve).grid(
            row=1, column=6, padx=4, pady=10
        )
        ttk.Button(controls, text="Clear Curves", command=self.clear_curves).grid(
            row=1, column=7, padx=4, pady=10
        )

        ttk.Label(controls, text="Time (ns)").grid(row=2, column=0, sticky=tk.W, pady=6)
        time_entry = ttk.Entry(controls, textvariable=self.time_var, width=14)
        time_entry.grid(row=2, column=1, sticky=tk.W, padx=8, pady=6)
        time_entry.bind("<Return>", lambda _event: self.add_curve())
        ttk.Label(controls, text="Time average (+/-)").grid(
            row=2, column=2, sticky=tk.W, padx=(12, 4), pady=6
        )
        ttk.Combobox(
            controls,
            textvariable=self.window_var,
            values=list(AVERAGE_WINDOWS),
            state="readonly",
            width=10,
        ).grid(row=2, column=3, sticky=tk.W, padx=8, pady=6)

        ttk.Label(controls, text="Wavelength (nm)").grid(
            row=3, column=0, sticky=tk.W, pady=6
        )
        wavelength_entry = ttk.Entry(
            controls, textvariable=self.wavelength_var, width=14
        )
        wavelength_entry.grid(row=3, column=1, sticky=tk.W, padx=8, pady=6)
        wavelength_entry.bind("<Return>", lambda _event: self.add_curve())
        ttk.Label(controls, text="Wavelength average (+/-)").grid(
            row=3, column=2, sticky=tk.W, padx=(12, 4), pady=6
        )
        ttk.Combobox(
            controls,
            textvariable=self.wavelength_window_var,
            values=list(WAVELENGTH_WINDOWS),
            state="readonly",
            width=10,
        ).grid(row=3, column=3, sticky=tk.W, padx=8, pady=6)
        controls.columnconfigure(5, weight=1)

        self.time_scale = tk.Scale(
            controls,
            from_=0,
            to=1,
            orient=tk.HORIZONTAL,
            resolution=0.01,
            showvalue=False,
            command=self._scale_changed,
        )
        self.time_scale.grid(
            row=4, column=0, columnspan=8, sticky=tk.EW, pady=(4, 2)
        )

        self.wavelength_scale = tk.Scale(
            controls,
            from_=0,
            to=1,
            orient=tk.HORIZONTAL,
            resolution=0.1,
            showvalue=False,
            command=self._wavelength_scale_changed,
        )
        self.wavelength_scale.grid(
            row=5, column=0, columnspan=8, sticky=tk.EW, pady=(2, 4)
        )

        ttk.Label(main, textvariable=self.status_var, wraplength=1800).pack(
            fill=tk.X, pady=(8, 0)
        )

        plot_frame = ttk.Frame(main)
        plot_frame.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.figure = Figure(figsize=(10, 6), dpi=110)
        self.ax = self.figure.add_subplot(111)
        self._reset_axes()

        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("axes_leave_event", self._hide_cursor)

        toolbar = NavigationToolbar2Tk(self.canvas, plot_frame)
        toolbar.update()

    def _browse_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose split TA data file",
            initialdir=str(DEFAULT_DATA_FILE.parent),
            filetypes=[("Excel files", "*.xlsx"), ("All files", "*.*")],
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
            resolution=0.01,
        )
        self.wavelength_scale.configure(
            from_=self.data.min_wavelength_nm,
            to=self.data.max_wavelength_nm,
            resolution=0.1,
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

    def _mode_changed(self) -> None:
        self.clear_curves()
        self.status_var.set(f"Switched to {self.mode_var.get()} mode.")

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
        half_window_s = AVERAGE_WINDOWS[window_label]

        (
            wavelengths,
            spectrum,
            nearest_time_ns,
            n_columns,
            used_nearest_only,
        ) = self.data.averaged_spectrum(requested_time_ns, half_window_s)

        self.curve_count += 1
        label = (
            f"{self.curve_count}: {nearest_time_ns:.6g} ns, "
            f"{window_label}, {n_columns} cols"
        )
        self.ax.plot(wavelengths, spectrum, label=label)
        self.ax.legend(loc="best", fontsize=max(9, int(9 * self.ui_scale)))
        self._autoscale_to_data_lines()
        self._init_cursor_artists()
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
        self.ax.plot(times_ns, trace, marker="o", markersize=3, linewidth=1.3, label=label)
        self.ax.legend(loc="best", fontsize=max(9, int(9 * self.ui_scale)))
        self._autoscale_to_data_lines()
        self._init_cursor_artists()
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
        self.canvas.draw_idle()

    def _reset_axes(self) -> None:
        if self.mode_var.get() == PLOT_MODES[1]:
            self.ax.set_xlabel("Time (ns)", fontsize=self.plot_font_size)
            self.ax.set_ylabel("Averaged signal", fontsize=self.plot_font_size)
            self.ax.set_title(
                "Averaged TA trace", fontsize=self.plot_font_size + 2
            )
        else:
            self.ax.set_xlabel("Wavelength (nm)", fontsize=self.plot_font_size)
            self.ax.set_ylabel("Averaged signal", fontsize=self.plot_font_size)
            self.ax.set_title(
                "Averaged TA spectrum", fontsize=self.plot_font_size + 2
            )
        self.ax.tick_params(labelsize=max(9, int(9 * self.ui_scale)))
        self.ax.grid(True, alpha=0.25)

    def _init_cursor_artists(self) -> None:
        if self.cursor_line is not None:
            return

        self.cursor_line = self.ax.axvline(
            color="0.35",
            linestyle="--",
            linewidth=0.9,
            alpha=0.7,
            visible=False,
            label="_cursor_line",
        )
        (self.cursor_marker,) = self.ax.plot(
            [],
            [],
            marker="o",
            markersize=max(5, int(5 * self.ui_scale)),
            color="black",
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
            fontsize=max(9, int(9 * self.ui_scale)),
            bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "0.3", "alpha": 0.9},
            arrowprops={"arrowstyle": "->", "color": "0.3"},
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
        x_values = []
        y_values = []
        for line in self._data_lines():
            x_data = np.asarray(line.get_xdata(), dtype=float)
            y_data = np.asarray(line.get_ydata(), dtype=float)
            finite = np.isfinite(x_data) & np.isfinite(y_data)
            if finite.any():
                x_values.append(x_data[finite])
                y_values.append(y_data[finite])

        if not x_values or not y_values:
            return

        x_all = np.concatenate(x_values)
        y_all = np.concatenate(y_values)
        self.ax.set_xlim(*self._limits_with_margin(x_all))
        self.ax.set_ylim(*self._limits_with_margin(y_all))

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
