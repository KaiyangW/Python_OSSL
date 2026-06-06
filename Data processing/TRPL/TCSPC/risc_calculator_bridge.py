"""
Bridge to push fit results into the `RISC Calculator.xlsx` workbook and read
back the exact-solution rate constants.

The template workbook is always opened read-only. Nothing is ever saved back
to disk, so the original file is never modified.

Workbook layout (sheet "Temp test"):
    Inputs (written into memory only)
        D5  : tau_p  (ns)
        D8  : tau_d  (us)
        D7  : A_p    (cleared -> Phi-based path)
        D10 : A_d    (cleared)
        D11 : Phi_PF (fraction)
        D13 : Phi_PLQY (fraction, TADF-only)
        D79 : k_ISC iterative guess, then formula =D69
    Fixed in template (never written)
        D15 : R^DF_DE  (= 1)

    Outputs (exact)
        D64 : k^S_r
        D65 : k^S_nr
        D66 : k^S
        D69 : k_ISC
        D70 : k_RISC  (exact iterative solution)

    Outputs (approximate k_RISC models)
        D32 : k_RISC  (Masui / Goushi)
        D38 : k_RISC  (Dias)
        D44 : k_RISC  (Wada)
"""

import os
import subprocess
import sys
import textwrap
import time

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec
import xlwings as xw

RISC_XLSX_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "RISC Calculator.xlsx"
)

OUTPUT_CELLS = {
    "k_r_S":  "D64",
    "k_nr_S": "D65",
    "k_S":    "D66",
    "k_ISC":  "D69",
    "k_RISC": "D70",
}

APPROX_OUTPUT_CELLS = {
    "k_RISC_Masui": "D32",
    "k_RISC_Dias":  "D38",
    "k_RISC_Wada":  "D44",
}

TEXT_BOX_PROPS = dict(boxstyle='round', facecolor='black', alpha=0.9, edgecolor='gray')
RIGHT_PANEL_X = 0.58

# Keep the result panel visually separate from the y-axis label/ticks.
INFO_PANEL_WIDTH_IN = 1.85
PLOT_WIDTH_IN = 5.35
FIG_HEIGHT_IN = 8
GS_LEFT, GS_RIGHT = 0.02, 0.98
FIG_SIZE = (
    (INFO_PANEL_WIDTH_IN + PLOT_WIDTH_IN) / (GS_RIGHT - GS_LEFT),
    FIG_HEIGHT_IN,
)
BTN_FIT_RECT = [0.38, 0.03, 0.18, 0.055]
BTN_SAVE_RECT = [0.60, 0.03, 0.18, 0.055]
INFO_PANEL_TEXT_WIDTH = 40
PANEL_WSPACE = 0.18
FIT_PANEL_FONT_FAMILY = 'Segoe UI'
FIT_PANEL_FONT_SIZE = 9
FIT_PANEL_LINE_STEP = 0.036
FIT_PANEL_COLORS = {
    'default': 'white',
    'separator': '#9a9a9a',
    'beta': '#ff77c8',
    'num_ave': '#ffd84d',
    'int_ave': '#b7f7a3',
}

WAIT_LABEL = "[RISC] Waiting for Excel..."
_CALC_DELAY_S = 0.05
INFO_PANEL_BOX_PROPS = dict(boxstyle='round', facecolor='#101010', alpha=1.0, edgecolor='#6a6a6a')


def create_lifetime_fit_figure():
    """Figure with a dedicated left column for fit summary text."""
    fig = plt.figure(figsize=FIG_SIZE)
    gs = GridSpec(
        2, 2, figure=fig,
        width_ratios=[INFO_PANEL_WIDTH_IN, PLOT_WIDTH_IN],
        height_ratios=[4, 1],
        hspace=0.05, wspace=PANEL_WSPACE,
        left=GS_LEFT, right=GS_RIGHT, bottom=0.10, top=0.9,
    )
    ax_info = fig.add_subplot(gs[:, 0])
    ax_info.set_facecolor('#141414')
    ax_info.axis('off')
    ax_info.add_patch(plt.Rectangle(
        (0.01, 0.005), 0.96, 0.99,
        transform=ax_info.transAxes,
        facecolor='#101010', edgecolor='#6a6a6a',
        linewidth=1.0, zorder=0,
    ))

    ax = fig.add_subplot(gs[0, 1])
    ax_res = fig.add_subplot(gs[1, 1], sharex=ax)
    return fig, ax_info, ax, ax_res


def _wrap_info_panel_text(text, width=INFO_PANEL_TEXT_WIDTH):
    """Wrap long summary lines so they stay inside the left info panel."""
    wrapped_lines = []
    for line in text.splitlines():
        if not line.strip() or set(line.strip()) == {'-'}:
            wrapped_lines.append(line)
            continue
        wrapped_lines.extend(textwrap.wrap(
            line,
            width=width,
            subsequent_indent='  ',
            break_long_words=False,
            break_on_hyphens=False,
        ) or [''])
    return "\n".join(wrapped_lines)


def _fit_summary_line_color(line):
    stripped = line.strip()
    lowered = stripped.lower()
    if stripped and set(stripped) == {'-'}:
        return FIT_PANEL_COLORS['separator']
    if lowered.startswith(('ß=', 'β=', 'beta=')):
        return FIT_PANEL_COLORS['beta']
    if lowered.startswith('num_ave='):
        return FIT_PANEL_COLORS['num_ave']
    if lowered.startswith('int_ave='):
        return FIT_PANEL_COLORS['int_ave']
    return FIT_PANEL_COLORS['default']


class FitSummaryPanel:
    """Draw fit summary one line at a time so key metrics can be color-coded."""

    def __init__(self, target, use_info_panel):
        self.target = target
        self.use_info_panel = use_info_panel
        self.artists = []

    def remove(self):
        for artist in self.artists:
            try:
                artist.remove()
            except Exception:
                pass
        self.artists = []

    def set_text(self, text):
        self.remove()
        if self.use_info_panel:
            display_text = _wrap_info_panel_text(text)
            y = 0.98
            for line in display_text.splitlines():
                if line.strip():
                    artist = self.target.text(
                        0.05, y, line,
                        transform=self.target.transAxes,
                        fontsize=FIT_PANEL_FONT_SIZE,
                        verticalalignment='top',
                        horizontalalignment='left',
                        color=_fit_summary_line_color(line),
                        family=FIT_PANEL_FONT_FAMILY,
                        clip_on=True,
                    )
                    self.artists.append(artist)
                y -= FIT_PANEL_LINE_STEP
            return

        artist = self.target.text(
            0.05, 0.98, text,
            transform=self.target.transAxes,
            fontsize=FIT_PANEL_FONT_SIZE,
            verticalalignment='top',
            horizontalalignment='left',
            color=FIT_PANEL_COLORS['default'],
            family=FIT_PANEL_FONT_FAMILY,
            bbox=TEXT_BOX_PROPS.copy(),
        )
        self.artists.append(artist)


def clear_fit_summary_panel(app_state):
    box = app_state.get('text_box')
    if box is not None:
        try:
            box.remove()
        except Exception:
            pass
    app_state['text_box'] = None


def set_fit_summary_panel(app_state, text):
    """Show fit summary in the left info column (falls back to main axes if missing)."""
    clear_fit_summary_panel(app_state)
    target = app_state.get('ax_info') or app_state.get('ax')
    if target is None:
        return None
    use_info_panel = app_state.get('ax_info') is not None
    app_state['text_box'] = FitSummaryPanel(target, use_info_panel)
    app_state['text_box'].set_text(text)
    return app_state['text_box']


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _notify(status_callback, message, verbose=False):
    if status_callback is not None:
        try:
            status_callback(message)
        except Exception:
            pass
    if verbose:
        print(message)


def _excel_calculate(app, delay_s=_CALC_DELAY_S):
    app.calculate()
    if delay_s and delay_s > 0:
        time.sleep(delay_s)


def _close_workbook_no_save(wb):
    """Explicitly discard all in-memory edits and close the workbook."""
    if wb is None:
        return
    try:
        wb.api.Close(SaveChanges=False)
    except Exception:
        try:
            wb.close()
        except Exception:
            pass


def _restore_app_settings(app, saved):
    if app is None or not saved:
        return
    try:
        if saved.get("iteration") is not None:
            app.api.Iteration = saved["iteration"]
        if saved.get("max_iterations") is not None:
            app.api.MaxIterations = saved["max_iterations"]
        if saved.get("max_change") is not None:
            app.api.MaxChange = saved["max_change"]
    except Exception:
        pass


def _quit_excel_app(app, saved_settings=None):
    """Force Excel to exit and avoid leaving zombie processes."""
    if app is None:
        return
    _restore_app_settings(app, saved_settings or {})
    try:
        app.quit()
    except Exception:
        pass
    try:
        app.kill()
    except Exception:
        pass


def cleanup_all_excel_processes(force_taskkill=True, verbose=False):
    """
    Close Excel instances started via xlwings and, on Windows, optionally force-kill
    any remaining EXCEL.EXE processes (e.g. after multiprocessing workers).

    Warning: force_taskkill closes *all* Excel windows on the machine, including
    workbooks the user may have open elsewhere.
    """
    n_xw = 0
    try:
        apps = list(xw.apps)
    except Exception:
        apps = []

    for app in apps:
        n_xw += 1
        try:
            for book in list(app.books):
                try:
                    book.close()
                except Exception:
                    pass
        except Exception:
            pass
        _quit_excel_app(app)

    killed = False
    if force_taskkill and sys.platform == "win32":
        try:
            proc = subprocess.run(
                ["taskkill", "/F", "/IM", "EXCEL.EXE"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            killed = proc.returncode == 0
        except Exception:
            pass

    if verbose:
        msg = f"[Excel cleanup] xlwings apps closed: {n_xw}"
        if force_taskkill and sys.platform == "win32":
            msg += f"; taskkill EXCEL.EXE: {'ok' if killed else 'none / failed'}"
        print(msg)

    return {"xlwings_apps": n_xw, "taskkill": killed}


def flush_plot_status(app_state, message=None):
    """Redraw the matplotlib plot and pump the Tk event loop."""
    fig = app_state.get("fig")
    if fig is None:
        return
    if message is not None:
        text_box = app_state.get("text_box")
        if text_box is not None:
            text_box.set_text(message)
    fig.canvas.draw_idle()
    fig.canvas.flush_events()
    root = app_state.get("tk_root")
    if root is not None:
        try:
            root.update_idletasks()
        except Exception:
            pass


def make_plot_status_callback(app_state, base_text):
    """Return a callback that appends live Excel status below the fit summary."""

    def _callback(message):
        wait_text = format_risc_calc_panel(waiting_message=f"Waiting for Excel...\n  {message}")
        display = f"{base_text.rstrip()}\n\n{wait_text}"
        flush_plot_status(app_state, display)

    return _callback


def _read_risc_result(sht, max_change=1e-6, calc_delay_s=_CALC_DELAY_S):
    """Read output cells and convergence after an iterative solve."""
    d79 = sht.range("D79").value
    d69 = sht.range("D69").value
    residual = abs(d79 - d69) if (_is_number(d79) and _is_number(d69)) else float("inf")
    converged = residual <= max_change * max(1.0, abs(d79 or 1.0))

    if calc_delay_s and calc_delay_s > 0:
        time.sleep(calc_delay_s)

    result = {}
    for name, cell in OUTPUT_CELLS.items():
        val = sht.range(cell).value
        result[name] = float(val) if _is_number(val) else None
    for name, cell in APPROX_OUTPUT_CELLS.items():
        val = sht.range(cell).value
        result[name] = float(val) if _is_number(val) else None
    result["converged"] = bool(converged)
    result["residual"] = float(residual) if residual != float("inf") else None
    return result


def _solve_risc_on_sheet(sht, app, tau_p_ns, tau_d_us, Phi_PF, Phi_PLQY_tadf,
                         write_fixed_inputs=True, max_change=1e-6,
                         extra_passes=3, calc_delay_s=_CALC_DELAY_S,
                         status_callback=None, verbose=False):
    """
    Run one Excel iterative solve on an already-open workbook sheet.

    When write_fixed_inputs is False, only D11 (Phi_PF) is updated; tau and PLQY
    must already be set (batch Monte Carlo path).
    """
    if write_fixed_inputs:
        _notify(status_callback, "Writing fit inputs...", verbose)
        sht.range("D5").value = float(tau_p_ns)
        sht.range("D8").value = float(tau_d_us)
        sht.range("D7").value = None
        sht.range("D10").value = None
        sht.range("D13").value = float(Phi_PLQY_tadf)
        _excel_calculate(app, calc_delay_s)

    sht.range("D11").value = float(Phi_PF)
    _excel_calculate(app, calc_delay_s)

    start_val = sht.range("D80").value
    if not _is_number(start_val) or start_val <= 0:
        k_p = sht.range("D17").value
        start_val = (k_p * 0.1) if _is_number(k_p) and k_p > 0 else 1.0e7
    sht.range("D79").value = float(start_val)
    _excel_calculate(app, calc_delay_s)

    _notify(status_callback, "Excel is calculating (iterative k_ISC)...", verbose)
    sht.range("D79").formula = "=D69"
    for _ in range(max(1, int(extra_passes))):
        _excel_calculate(app, calc_delay_s)

    result = _read_risc_result(sht, max_change=max_change, calc_delay_s=calc_delay_s)

    if verbose:
        d79 = sht.range("D79").value
        d69 = sht.range("D69").value
        residual = result.get("residual")
        print(f"  Phi_PF={Phi_PF:.6f}  k_RISC={result.get('k_RISC')}")
        print(f"  D80 start={start_val:.4e}  D69={d69}  D79={d79}  |res|={residual:.3e}")

    return result


def compute_risc_rates(tau_p_ns, tau_d_ns, Phi_PF, Phi_PLQY_tadf,
                       xlsx_path=None, status_callback=None,
                       max_iterations=5000, max_change=1e-6,
                       extra_passes=3, calc_delay_s=_CALC_DELAY_S,
                       verbose=False):
    """
    Drive the RISC Calculator workbook (read-only) and return D64/D65/D66/D69/D70.

    Excel iterative calculation is used with D79 = D69. The template on disk is
    never modified: read_only=True on open, SaveChanges=False on close.
    """
    path = xlsx_path or RISC_XLSX_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"RISC Calculator workbook not found: {path}")

    tau_d_us = float(tau_d_ns) / 1000.0
    app = None
    wb = None
    saved_settings = {}

    try:
        _notify(status_callback, "Starting Excel (read-only template)...", verbose)

        app = xw.App(visible=False, add_book=False)
        try:
            app.display_alerts = False
            app.screen_updating = False

            _notify(status_callback, "Opening RISC Calculator.xlsx (read-only)...", verbose)
            wb = app.books.open(path, update_links=False, read_only=True)

            try:
                saved_settings = {
                    "iteration": app.api.Iteration,
                    "max_iterations": app.api.MaxIterations,
                    "max_change": app.api.MaxChange,
                }
            except Exception:
                saved_settings = {}

            app.api.Iteration = True
            app.api.MaxIterations = int(max_iterations)
            app.api.MaxChange = float(max_change)

            sht = wb.sheets[0]
            result = _solve_risc_on_sheet(
                sht, app, tau_p_ns, tau_d_us, Phi_PF, Phi_PLQY_tadf,
                write_fixed_inputs=True, max_change=max_change,
                extra_passes=extra_passes, calc_delay_s=calc_delay_s,
                status_callback=status_callback, verbose=verbose,
            )
            _notify(status_callback, "Done.", verbose)
            return result

        except Exception as e:
            raise RuntimeError(f"RISC Calculator Excel bridge failed: {e}") from e

    finally:
        _close_workbook_no_save(wb)
        wb = None
        _quit_excel_app(app, saved_settings)
        app = None


def compute_risc_rates_batch(phi_pf_values, tau_p_ns, tau_d_ns, Phi_PLQY_tadf,
                             xlsx_path=None, status_callback=None,
                             max_iterations=5000, max_change=1e-6,
                             extra_passes=3, calc_delay_s=_CALC_DELAY_S,
                             progress_every=25, progress_callback=None,
                             verbose=False):
    """
    Evaluate k_RISC for many Phi_PF samples with a single Excel session.

    Returns a list of result dicts (same keys as compute_risc_rates). Much faster
    than calling compute_risc_rates in a loop because the workbook is opened once.
    """
    path = xlsx_path or RISC_XLSX_PATH
    if not os.path.exists(path):
        raise FileNotFoundError(f"RISC Calculator workbook not found: {path}")

    phi_list = [float(v) for v in phi_pf_values]
    if not phi_list:
        return []

    tau_d_us = float(tau_d_ns) / 1000.0
    app = None
    wb = None
    saved_settings = {}
    results = []

    try:
        _notify(status_callback, "Starting Excel (batch, read-only)...", verbose)
        app = xw.App(visible=False, add_book=False)
        app.display_alerts = False
        app.screen_updating = False

        wb = app.books.open(path, update_links=False, read_only=True)
        try:
            saved_settings = {
                "iteration": app.api.Iteration,
                "max_iterations": app.api.MaxIterations,
                "max_change": app.api.MaxChange,
            }
        except Exception:
            saved_settings = {}

        app.api.Iteration = True
        app.api.MaxIterations = int(max_iterations)
        app.api.MaxChange = float(max_change)
        sht = wb.sheets[0]

        n = len(phi_list)
        for i, phi_pf in enumerate(phi_list):
            if progress_every and (i == 0 or (i + 1) % progress_every == 0 or i + 1 == n):
                _notify(status_callback, f"Batch solve {i + 1}/{n} (Phi_PF={phi_pf:.6f})...", verbose)

            res = _solve_risc_on_sheet(
                sht, app, tau_p_ns, tau_d_us, phi_pf, Phi_PLQY_tadf,
                write_fixed_inputs=(i == 0),
                max_change=max_change, extra_passes=extra_passes,
                calc_delay_s=calc_delay_s, verbose=verbose and i == 0,
            )
            res["Phi_PF"] = phi_pf
            results.append(res)

            if progress_callback is not None:
                try:
                    progress_callback(i + 1, n, phi_pf)
                except Exception:
                    pass

        _notify(status_callback, f"Batch done ({n} samples).", verbose)
        return results

    except Exception as e:
        raise RuntimeError(f"RISC Calculator batch Excel bridge failed: {e}") from e

    finally:
        _close_workbook_no_save(wb)
        wb = None
        _quit_excel_app(app, saved_settings)
        app = None


def format_rate(k):
    """Pretty-print a rate constant in scientific notation with s^-1 unit."""
    if k is None or not _is_number(k) or k == 0:
        return "N/A"
    return f"{k:.3e} s^-1"


def format_risc_calc_panel(rates=None, waiting_message=None):
    """Build compact UI text for RISC values only."""
    lines = ["----------------------------------------------"]
    if waiting_message:
        lines.append(waiting_message)
        return "\n".join(lines)
    if rates is None:
        return "\n".join(lines)
    lines.extend([
        f"Exact RISC: {format_rate(rates.get('k_RISC'))}",
        f"Masui RISC: {format_rate(rates.get('k_RISC_Masui'))}",
        f"Dias RISC : {format_rate(rates.get('k_RISC_Dias'))}",
        f"Wada RISC : {format_rate(rates.get('k_RISC_Wada'))}",
    ])
    return "\n".join(lines)


def format_risc_approx_panel(rates=None, waiting_message=None):
    """Backward-compatible wrapper for the combined RISC calculation panel."""
    return format_risc_calc_panel(rates, waiting_message=waiting_message)


def clear_risc_text_box(app_state):
    box = app_state.get('risc_text_box')
    if box is not None:
        try:
            box.remove()
        except Exception:
            pass
    app_state['risc_text_box'] = None


def show_risc_approx_panel(app_state, ax, rates=None, waiting_message=None):
    """Draw or update the right-hand RISC calculation panel."""
    clear_risc_text_box(app_state)
    text = format_risc_approx_panel(rates, waiting_message=waiting_message)
    app_state['risc_text_box'] = ax.text(
        RIGHT_PANEL_X, 0.98, text,
        transform=ax.transAxes, fontsize=9,
        verticalalignment='top', horizontalalignment='left',
        color='white', bbox=TEXT_BOX_PROPS.copy(), family='monospace',
    )
    return app_state['risc_text_box']


def clear_result_panels(app_state):
    """Remove both the main fit summary and the RISC side panel."""
    clear_fit_summary_panel(app_state)
    clear_risc_text_box(app_state)


if __name__ == "__main__":
    res = compute_risc_rates(
        tau_p_ns=2.82,
        tau_d_ns=525.46e3,
        Phi_PF=0.54,
        Phi_PLQY_tadf=0.8723,
        verbose=True,
    )
    for k, v in res.items():
        print(f"{k}: {v}")
