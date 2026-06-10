"""
Stimulated-emission (SE) cross-section calculator.

Implements the Fuchtbauer-Ladenburg relation as written in Nakanotani, H., 
Furukawa, T., Hosokai, T., Hatakeyama, T. & Adachi, C. Light Amplification 
in Molecules Exhibiting Thermally Activated Delayed Fluorescence. 
Advanced Optical Materials 5, 1700051 (2017).:

                       lambda^4 * Ef(lambda)
    sigma_em(lambda) = ---------------------------
                       8 * pi * n^2(lambda) * c * tau

where
    Ef(lambda) : fluorescence spectrum normalised so that its area
                 (integral over wavelength) equals 1,
    n(lambda)  : refractive index of the active gain layer,
    c          : speed of light,
    tau        : RADIATIVE lifetime  tau_rad = tau_f / PLQY.

The user supplies the measured fluorescence lifetime tau_f (in ns) and,
optionally, the photoluminescence quantum yield (PLQY).  If PLQY = 1 the
input lifetime is treated directly as the radiative lifetime.

All internal calculations are done in CGS units (cm, s) so the resulting
cross-section comes out in cm^2.
"""

import os
import sys
import ctypes

import numpy as np

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

import tkinter as tk
from tkinter import filedialog, messagebox
import tkinter.font as tkfont
import customtkinter as ctk

_READER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _READER_ROOT not in sys.path:
    sys.path.insert(0, _READER_ROOT)

from Read_data_unified import read_xy


# --------------------------------------------------------------------------- #
#  Physical constants (CGS)
# --------------------------------------------------------------------------- #
C_CGS = 2.99792458e10        # speed of light [cm / s]
NM_TO_CM = 1.0e-7            # 1 nm = 1e-7 cm
UI_FONT_FAMILY = "Segoe UI"
UI_FONT_SIZE = 12
UI_SMALL_FONT_SIZE = 11
UI_HEADING_FONT_SIZE = 13


# --------------------------------------------------------------------------- #
#  Data parsing
# --------------------------------------------------------------------------- #
def read_pl_file(path):
    """
    Read a fluorimeter PL file.

    The format has a metadata header (Labels, Type, ...), a blank line and
    then two-column data 'wavelength, counts'.  This parser ignores the
    header and simply keeps every line whose first two comma-separated
    fields can be read as numbers, so it is robust to the exact header
    length and to trailing commas.

    Returns
    -------
    wl : ndarray  -- wavelength [nm], sorted ascending
    pl : ndarray  -- intensity / counts (arbitrary units)
    """
    spectrum = read_xy(path)
    order = np.argsort(spectrum.x)
    return spectrum.x[order], spectrum.y[order]


def read_nk_file(path):
    """
    Read a refractive-index file with columns 'wavelength  n  k'.

    The first lines are text headers (e.g. 'Opt. Const. of B-Spline vs. nm'
    and the column-title row).  Any line whose first two whitespace/tab
    separated fields parse as numbers is treated as data.

    Returns
    -------
    wl : ndarray  -- wavelength [nm], sorted ascending
    n  : ndarray  -- refractive index
    """
    spectrum = read_xy(path, format="whitespace_xy")
    order = np.argsort(spectrum.x)
    return spectrum.x[order], spectrum.y[order]


# --------------------------------------------------------------------------- #
#  Core calculation
# --------------------------------------------------------------------------- #
def compute_cross_section(wl_nm, pl, nk_wl_nm, nk_n, tau_rad_s):
    """
    Compute the SE cross-section over the PL wavelength grid.

    Parameters
    ----------
    wl_nm     : PL wavelength grid [nm]
    pl        : measured PL intensity (arbitrary units, photon counts)
    nk_wl_nm  : wavelength grid of the n data [nm]
    nk_n      : refractive index values
    tau_rad_s : radiative lifetime [s]

    Returns
    -------
    wl_nm  : wavelength grid [nm] (same as input)
    Ef     : area-normalised PL (integral over lambda[cm] == 1) [1/cm]
    n_i    : refractive index interpolated onto the PL grid
    sigma  : SE cross-section [cm^2]
    """
    # Interpolate the refractive index onto the PL wavelength grid.
    # np.interp clamps to the end values outside the n data range, but we
    # additionally warn the caller if extrapolation happens (handled in UI).
    n_i = np.interp(wl_nm, nk_wl_nm, nk_n)

    # Work in cm.
    wl_cm = wl_nm * NM_TO_CM

    # Normalise the PL spectrum to unit area in wavelength (cm):
    #   integral Ef(lambda) d(lambda) = 1
    pl = np.clip(pl, 0.0, None)        # discard small negative noise
    area = np.trapezoid(pl, wl_cm)
    if area <= 0:
        raise ValueError("The PL spectrum integrates to zero; cannot "
                         "normalise. Check the PL data.")
    Ef = pl / area                     # [1/cm]

    # Fuchtbauer-Ladenburg:
    #   sigma = lambda^4 * Ef / (8 pi n^2 c tau)
    sigma = (wl_cm ** 4) * Ef / (8.0 * np.pi * n_i ** 2 * C_CGS * tau_rad_s)

    return wl_nm, Ef, n_i, sigma


# --------------------------------------------------------------------------- #
#  High-DPI awareness (Windows)
# --------------------------------------------------------------------------- #
def enable_high_dpi():
    if sys.platform != "win32":
        return 1.0
    try:
        # PER_MONITOR_AWARE_V2
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    # Determine the scaling factor for the primary monitor.
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        return dpi / 96.0
    except Exception:
        return 1.0


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
class CrossSectionApp(ctk.CTk):
    def __init__(self, scaling=1.0):
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("Stimulated-Emission Cross-Section Calculator")

        # Apply Tk scaling so widgets/fonts respect the display DPI.
        self.tk.call("tk", "scaling", scaling * 1.0)
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
        self._scaling = scaling
        self._configure_fonts()

        # State
        self.pl_path = tk.StringVar()
        self.nk_path = tk.StringVar()
        self.lifetime_ns = tk.StringVar(value="1.0")
        self.plqy = tk.StringVar(value="1.0")
        self.status = tk.StringVar(value="Select a PL file and an n,k file to begin.")

        self.result = None   # (wl, Ef, n, sigma)

        self._build_widgets()

        base = 1200, 800
        self.minsize(900, 620)
        self.geometry(f"{base[0]}x{base[1]}")

    # --------------------------------------------------------------------- #
    def _configure_fonts(self):
        """Use a larger Segoe UI type scale throughout the Tk and plot UI."""
        named_fonts = {
            "TkDefaultFont": (UI_FONT_FAMILY, UI_FONT_SIZE),
            "TkTextFont": (UI_FONT_FAMILY, UI_FONT_SIZE),
            "TkFixedFont": ("Consolas", UI_FONT_SIZE),
            "TkMenuFont": (UI_FONT_FAMILY, UI_FONT_SIZE),
            "TkHeadingFont": (UI_FONT_FAMILY, UI_HEADING_FONT_SIZE, "bold"),
            "TkCaptionFont": (UI_FONT_FAMILY, UI_FONT_SIZE),
            "TkSmallCaptionFont": (UI_FONT_FAMILY, UI_SMALL_FONT_SIZE),
            "TkIconFont": (UI_FONT_FAMILY, UI_FONT_SIZE),
            "TkTooltipFont": (UI_FONT_FAMILY, UI_SMALL_FONT_SIZE),
        }
        for name, font_spec in named_fonts.items():
            try:
                tkfont.nametofont(name).configure(
                    family=font_spec[0],
                    size=font_spec[1],
                    weight=font_spec[2] if len(font_spec) > 2 else "normal",
                )
            except tk.TclError:
                continue

        matplotlib.rcParams.update({
            "font.family": UI_FONT_FAMILY,
            "font.size": UI_FONT_SIZE,
            "axes.labelsize": UI_FONT_SIZE,
            "axes.titlesize": UI_HEADING_FONT_SIZE,
            "xtick.labelsize": UI_SMALL_FONT_SIZE,
            "ytick.labelsize": UI_SMALL_FONT_SIZE,
            "figure.facecolor": "#1a1a1a",
            "axes.facecolor": "#1a1a1a",
            "axes.edgecolor": "white",
            "axes.labelcolor": "white",
            "xtick.color": "white",
            "ytick.color": "white",
            "text.color": "white",
        })

    # --------------------------------------------------------------------- #
    def _build_widgets(self):
        self.configure(fg_color="#0f0f0f")

        # ---- Left control panel, matching Bilinear_fit.py ---------------- #
        self.sidebar = ctk.CTkFrame(self, width=320)
        self.sidebar.pack(side="left", fill="y", padx=10, pady=10)
        self.sidebar.pack_propagate(False)

        title_font = ctk.CTkFont(family=UI_FONT_FAMILY, size=18, weight="bold")
        label_font = ctk.CTkFont(family=UI_FONT_FAMILY, size=14)
        small_font = ctk.CTkFont(family=UI_FONT_FAMILY, size=12)
        result_font = ctk.CTkFont(family=UI_FONT_FAMILY, size=14, weight="bold")

        ctk.CTkLabel(
            self.sidebar,
            text="SE Cross-Section",
            font=title_font,
        ).pack(pady=(18, 12), padx=12, fill="x")

        self.btn_load_pl = ctk.CTkButton(
            self.sidebar, text="Import PL Data", command=self._pick_pl,
            font=label_font,
        )
        self.btn_load_pl.pack(pady=(8, 6), padx=12, fill="x")

        self.pl_file_label = ctk.CTkLabel(
            self.sidebar, text="No PL file loaded.", justify="left",
            wraplength=260, font=small_font, text_color="#bdbdbd",
        )
        self.pl_file_label.pack(pady=(0, 10), padx=12, fill="x")

        self.btn_load_n = ctk.CTkButton(
            self.sidebar, text="Import n Data", command=self._pick_nk,
            font=label_font,
        )
        self.btn_load_n.pack(pady=(8, 6), padx=12, fill="x")

        self.n_file_label = ctk.CTkLabel(
            self.sidebar, text="No n file loaded.", justify="left",
            wraplength=260, font=small_font, text_color="#bdbdbd",
        )
        self.n_file_label.pack(pady=(0, 18), padx=12, fill="x")

        ctk.CTkLabel(
            self.sidebar, text="Prompt lifetime tau_f (ns)",
            anchor="w", font=label_font,
        ).pack(pady=(6, 4), padx=12, fill="x")
        self.lifetime_entry = ctk.CTkEntry(
            self.sidebar, textvariable=self.lifetime_ns, font=label_font,
        )
        self.lifetime_entry.pack(pady=(0, 12), padx=12, fill="x")

        ctk.CTkLabel(
            self.sidebar, text="Prompt PLQY, Phi_f (0-1)",
            anchor="w", font=label_font,
        ).pack(pady=(6, 4), padx=12, fill="x")
        self.plqy_entry = ctk.CTkEntry(
            self.sidebar, textvariable=self.plqy, font=label_font,
        )
        self.plqy_entry.pack(pady=(0, 10), padx=12, fill="x")

        ctk.CTkLabel(
            self.sidebar,
            justify="left",
            wraplength=260,
            font=small_font,
            text_color="#bdbdbd",
        ).pack(pady=(0, 18), padx=12, fill="x")

        self.calc_btn = ctk.CTkButton(
            self.sidebar, text="CALCULATE", command=self._calculate,
            font=result_font, fg_color="#2CC985", hover_color="#229e69",
            text_color="#101010",
        )
        self.calc_btn.pack(pady=(8, 8), padx=12, fill="x")

        self.result_label = ctk.CTkLabel(
            self.sidebar,
            text="Result:\n--",
            justify="left",
            wraplength=260,
            text_color="#2CC985",
            font=result_font,
        )
        self.result_label.pack(pady=(12, 12), padx=12, fill="x")

        self.status_label = ctk.CTkLabel(
            self.sidebar, textvariable=self.status, justify="left",
            wraplength=260, font=small_font, text_color="#bdbdbd",
        )
        self.status_label.pack(pady=(8, 12), padx=12, fill="x")

        self.save_btn = ctk.CTkButton(
            self.sidebar, text="SAVE RESULTS", command=self._save,
            fg_color="#C92C45", hover_color="#9f2135", state="disabled",
            font=result_font,
        )
        self.save_btn.pack(side="bottom", pady=20, padx=12, fill="x")

        # ---- Right plot area -------------------------------------------- #
        self.plot_frame = ctk.CTkFrame(self)
        self.plot_frame.pack(side="right", fill="both", expand=True,
                             padx=(0, 10), pady=10)

        self.fig = Figure(figsize=(8, 6), dpi=int(100 * self._scaling))
        self.fig.patch.set_facecolor("#1a1a1a")
        self.ax = self.fig.add_subplot(111)
        self._style_axis()
        self.ax.set_title("Stimulated-Emission Cross-Section")
        self.ax.text(
            0.5, 0.5, "Import PL and n data, then calculate.",
            ha="center", va="center", transform=self.ax.transAxes,
            color="#bdbdbd", fontsize=UI_FONT_SIZE,
        )
        self.fig.tight_layout()

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

    # --------------------------------------------------------------------- #
    def _style_axis(self):
        self.ax.set_facecolor("#1a1a1a")
        self.ax.set_xlabel("Wavelength (nm)", color="white")
        self.ax.set_ylabel(r"$\sigma_{em}$ (cm$^2$)", color="white")
        self.ax.tick_params(colors="white")
        for spine in self.ax.spines.values():
            spine.set_color("white")
        self.ax.grid(True, alpha=0.25, color="#777777")

    # --------------------------------------------------------------------- #
    def _pick_pl(self):
        path = filedialog.askopenfilename(
            title="Select PL data file",
            filetypes=[("PL data", "*.csv *.txt *.dat"), ("All files", "*.*")],
        )
        if path:
            self.pl_path.set(path)
            self.pl_file_label.configure(
                text="PL loaded:\n" + os.path.basename(path),
                text_color="#ffffff",
            )

    def _pick_nk(self):
        path = filedialog.askopenfilename(
            title="Select refractive-index (n,k) file",
            filetypes=[("n,k data", "*.txt *.csv *.dat"), ("All files", "*.*")],
        )
        if path:
            self.nk_path.set(path)
            self.n_file_label.configure(
                text="n loaded:\n" + os.path.basename(path),
                text_color="#ffffff",
            )

    # --------------------------------------------------------------------- #
    def _calculate(self):
        try:
            pl_path = self.pl_path.get().strip()
            nk_path = self.nk_path.get().strip()
            if not pl_path or not os.path.isfile(pl_path):
                raise ValueError("Please choose a valid PL data file.")
            if not nk_path or not os.path.isfile(nk_path):
                raise ValueError("Please choose a valid n,k data file.")

            tau_f_ns = float(self.lifetime_ns.get())
            if tau_f_ns <= 0:
                raise ValueError("Lifetime must be a positive number (ns).")

            plqy = float(self.plqy.get())
            if not (0 < plqy <= 1):
                raise ValueError("PLQY must be in the range (0, 1].")

            tau_rad_s = (tau_f_ns * 1e-9) / plqy

            wl, pl = read_pl_file(pl_path)
            nk_wl, nk_n = read_nk_file(nk_path)

            # Warn if the n data does not cover the PL range (interp clamps).
            if wl.min() < nk_wl.min() or wl.max() > nk_wl.max():
                messagebox.showwarning(
                    "Range warning",
                    "The PL wavelength range ({:.0f}-{:.0f} nm) extends beyond "
                    "the n,k data range ({:.0f}-{:.0f} nm).\n\n"
                    "The refractive index is held constant (clamped) outside "
                    "the measured range for those points.".format(
                        wl.min(), wl.max(), nk_wl.min(), nk_wl.max()),
                )

            wl, Ef, n_i, sigma = compute_cross_section(
                wl, pl, nk_wl, nk_n, tau_rad_s)

            self.result = (wl, Ef, n_i, sigma)
            self._plot(wl, sigma)

            peak_i = int(np.argmax(sigma))
            self.status.set(
                "Done.  Peak sigma = {:.3e} cm^2 at {:.1f} nm   "
                "(tau_rad = {:.3f} ns).".format(
                    sigma[peak_i], wl[peak_i], tau_rad_s * 1e9))
            self.result_label.configure(
                text=(
                    "Result:\n"
                    "Peak sigma = {:.3e} cm^2\n"
                    "Peak wavelength = {:.1f} nm\n"
                    "tau_rad = {:.3f} ns"
                ).format(sigma[peak_i], wl[peak_i], tau_rad_s * 1e9)
            )
            self.save_btn.configure(state="normal")

        except Exception as exc:
            self.status.set("Error: " + str(exc))
            self.result_label.configure(text="Result:\n--")
            messagebox.showerror("Calculation error", str(exc))

    # --------------------------------------------------------------------- #
    def _plot(self, wl, sigma):
        self.ax.clear()
        self._style_axis()
        self.ax.plot(wl, sigma, color="#2CC985", lw=2.0)
        self.ax.set_title("Stimulated-Emission Cross-Section", color="white")
        self.ax.ticklabel_format(axis="y", style="sci", scilimits=(0, 0))
        self.fig.tight_layout()
        self.canvas.draw()

    # --------------------------------------------------------------------- #
    def _save(self):
        if self.result is None:
            return
        wl, Ef, n_i, sigma = self.result

        pl_path = self.pl_path.get().strip()
        base_dir = os.path.dirname(pl_path)
        stem = os.path.splitext(os.path.basename(pl_path))[0]
        default_name = stem + "_SE_cross_section.csv"

        out_path = filedialog.asksaveasfilename(
            title="Save SE cross-section data",
            initialdir=base_dir,
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV file", "*.csv"), ("All files", "*.*")],
        )
        if not out_path:
            return

        try:
            tau_f_ns = float(self.lifetime_ns.get())
            plqy = float(self.plqy.get())
            tau_rad_ns = tau_f_ns / plqy

            header = (
                "Stimulated-emission cross-section (Fuchtbauer-Ladenburg)\n"
                "sigma_em = lambda^4 * Ef / (8*pi*n^2*c*tau_rad)\n"
                "Source PL file: {}\n"
                "tau_f = {:g} ns, PLQY = {:g}, tau_rad = {:g} ns\n"
                "Ef is area-normalised (integral over wavelength[cm] = 1)\n"
                "Wavelength_nm,Ef_per_cm,n,sigma_em_cm2"
            ).format(os.path.basename(pl_path), tau_f_ns, plqy, tau_rad_ns)

            data = np.column_stack([wl, Ef, n_i, sigma])
            np.savetxt(out_path, data, delimiter=",", header=header,
                       comments="", fmt="%.6e")

            self.status.set("Saved: " + out_path)
            messagebox.showinfo("Saved", "Data written to:\n" + out_path)
        except Exception as exc:
            self.status.set("Error saving: " + str(exc))
            messagebox.showerror("Save error", str(exc))


# --------------------------------------------------------------------------- #
def main():
    scaling = enable_high_dpi()
    app = CrossSectionApp(scaling=scaling)
    app.mainloop()


if __name__ == "__main__":
    main()
