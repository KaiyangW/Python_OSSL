import sys
import time
import os
import datetime
import threading
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, Cursor
import tkinter as tk
from tkinter import messagebox, filedialog, simpledialog

import seabreeze
seabreeze.use('pyseabreeze')

from seabreeze.spectrometers import Spectrometer, SeaBreezeError, list_devices

# ==========================================
# CONFIGURATION SETTINGS
# ==========================================
DWELL_TIME_SEC = 0.7  # Integration time in seconds
WAVELENGTH_MIN = 370  # Minimum wavelength to display (nm)
WAVELENGTH_MAX = 800  # Maximum wavelength to display (nm)
BG_FRAMES = 5          # Number of frames to average for background
# ==========================================

class LiveSpectrumAnalyzer:
    def __init__(self, integration_time_s=0.1):
        self.spec = None
        self.integration_time_micros = int(integration_time_s * 1000000)
        # Preserving your specific file path
        self.save_dir = r"C:\My files\Google drive sync\St Andrews\Data\Oceanoptics data"
        
        # Plot setup
        # Using a dark theme for a "premium" look
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(facecolor='black')
        self.ax.set_facecolor('black')
        self.line, = self.ax.plot([], [], color='#00aaff', linewidth=1) # Vibrant blue line
        
        # Cursor text placeholder
        self.cursor_text = None
        self.cursor_widget = None

        # Buttons
        self.btn_save = None
        self.btn_pause = None
        
        # Threading variables
        self.stop_event = threading.Event()
        self.data_lock = threading.Lock()
        
        # Shared data containers (Latest from hardware)
        self.current_wl = None
        self.current_inten = None

        # Display data containers (What is currently on screen)
        # We separate these to allow "Pausing" the display while hardware keeps running
        self.plot_wl = None
        self.plot_inten = None
        
        # State flags
        self.is_paused = False
        self.last_data_time = time.time()
        self.warning_text = None

        # Background subtraction
        self.bg_frames_buffer = []   # Accumulates raw frames during BG collection
        self.background = None       # Averaged background array
        self.bg_collecting = True    # True while still collecting BG frames
        self.bg_sub_enabled = True   # Whether BG subtraction is currently applied
        self.btn_bgsub = None
        self.bg_status_text = None

        # Optimization: Persistent Tkinter root for dialogs
        self._tk_root = None
        
        # Optimization: Cached Y-axis limits to avoid constant recalculation
        self._current_ymax = 1000
        self._current_ymin = 0
        
        # Optimization: Pre-calculate wavelength mask
        self._wl_mask = None
    
    def connect(self):
        print("Searching for spectrometers...")

        try:
            import usb.core
            # Ocean Optics Vendor ID = 0x2457
            print("DEBUG: Attempting raw USB reset...")
            devs = usb.core.find(find_all=True, idVendor=0x2457)
            for dev in devs:
                try:
                    # 这一步相当于拔插 USB
                    dev.reset()
                    print(f"DEBUG: Reset sent to USB device {dev.idProduct:x}")
                except Exception as e:
                    print(f"DEBUG: USB Reset warning: {e}")
            
            # 重置后必须等待设备重启，否则会找不到
            import time
            time.sleep(2.0) 
        except Exception as e:
            print(f"DEBUG: Raw USB reset skipped: {e}")

        try:
            devices = list_devices()
            if not devices:
                print("Error: No devices found. Check Zadig driver (try libusbK).")
                return False
            
            print(f"Device found. Force opening...")
            # 我们直接拿到这个底层设备对象，不让它自动初始化所有属性
            device = devices[0]
            
            try:
                # 尝试标准初始化
                self.spec = Spectrometer(device)
                print("Standard connection successful.")
            except Exception as e:
                print(f"Standard init failed ({e}), trying raw mode...")
                device.open() 
                self.spec = Spectrometer(device) 

            print("Spectrometer link established.")
            
            self.spec.integration_time_micros(self.integration_time_micros)
            return True

        except Exception as e:
            print(f"CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        
    def data_acquisition_loop(self):
        """
        Background thread: Continuously fetches data from hardware.
        Optimization: Cache wavelengths outside the loop.
        """
        print("Data acquisition thread started.")
        
        # 1. Get wavelengths ONCE (they are usually static)
        try:
            cached_wl = self.spec.wavelengths()
        except Exception as e:
            print(f"Error fetching initial wavelengths: {e}")
            return

        error_count = 0 
        
        while not self.stop_event.is_set() and self.spec:
            try:
                # 2. Get intensities
                inten = self.spec.intensities()
                
                with self.data_lock:
                    self.current_wl = cached_wl # Use cached version
                    self.current_inten = inten
                    self.last_data_time = time.time()
                
                error_count = 0 
                time.sleep(0.005) 

            except Exception as e:
                error_count += 1
                print(f"Warning: Read error ({error_count}/10): {e}")
                time.sleep(0.5)
                if error_count > 10:
                    print("Too many errors. Stopping acquisition thread.")
                    break
                
    def init_plot(self):
        self.ax.set_title(f"Real-Time Spectrum - Ctrl+B to Save", color='white', pad=15)
        self.ax.set_xlabel("Wavelength (nm)", color='white')
        self.ax.set_ylabel("Intensity (counts)", color='white')
        self.ax.grid(True, linestyle='--', alpha=0.3, color='gray')
        
        # Style the spines
        for spine in self.ax.spines.values():
            spine.set_color('#444444')
        
        # Style ticks
        self.ax.tick_params(colors='white', which='both')
        
        # REMOVED: self.ax.set_ylim(0, 65535) 
        # Instead, we set a default view, but let the update loop handle the max
        self.ax.set_ylim(0, 1000) 
        # Set wavelength range from config
        self.ax.set_xlim(WAVELENGTH_MIN, WAVELENGTH_MAX) 
        
        # Initialize cursor text (Top-left relative to axes)
        self.cursor_text = self.ax.text(0.02, 0.95, "", transform=self.ax.transAxes, 
                                        color='#ff3366', fontsize=10, verticalalignment='top')
        
        # Connection warning text (Center of plot)
        self.warning_text = self.ax.text(0.5, 0.5, "CONNECTION LOST\n(Data not updating)", 
                                         transform=self.ax.transAxes, 
                                         color='#ff0000', fontsize=20, fontweight='bold',
                                         ha='center', va='center', visible=False)
        
        # Dwell time display (Top-right)
        self.dwell_text = self.ax.text(0.98, 0.95, f"Dwell: {self.integration_time_micros/1000:.0f}ms", 
                                        transform=self.ax.transAxes, 
                                        color='#00ccff', fontsize=10, fontweight='bold',
                                        ha='right', va='top')

        # Background status (bottom-left of axes)
        self.bg_status_text = self.ax.text(0.02, 0.05, "", transform=self.ax.transAxes,
                                           color='#ffaa00', fontsize=9, fontweight='bold',
                                           va='bottom')
        
        return self.line, self.cursor_text, self.warning_text, self.dwell_text, self.bg_status_text
    
    def toggle_bg_sub(self, event=None):
        """Toggle background subtraction on/off."""
        self.bg_sub_enabled = not self.bg_sub_enabled
        label = "BG Sub: ON" if self.bg_sub_enabled else "BG Sub: OFF"
        self.btn_bgsub.label.set_text(label)
        self.fig.canvas.draw_idle()

    def update(self, frame):
        """
        Animation loop. Updates the plot ONLY if not paused.
        Handles background collection (first BG_FRAMES frames) and subtraction.
        """
        # If paused, we do NOT update plot_wl/plot_inten, 
        # so the plot remains frozen on the last frame.
        if self.is_paused:
            return self.line, self.cursor_text, self.warning_text, self.dwell_text, self.bg_status_text

        # Fetch new data from background thread
        wl_new, inten_new = None, None
        with self.data_lock:
            if self.current_wl is not None:
                wl_new = self.current_wl.copy()
                inten_new = self.current_inten.copy()

        if wl_new is not None and inten_new is not None:

            # --- BACKGROUND COLLECTION PHASE ---
            if self.bg_collecting:
                self.bg_frames_buffer.append(inten_new)
                n = len(self.bg_frames_buffer)
                if self.bg_status_text:
                    self.bg_status_text.set_text(f"Collecting BG: {n}/{BG_FRAMES} frames...")

                if n >= BG_FRAMES:
                    # Average all collected frames and store as background
                    self.background = np.mean(self.bg_frames_buffer, axis=0)
                    self.bg_collecting = False
                    print(f"Background captured ({BG_FRAMES} frames averaged).")

                # During collection, show the raw spectrum as a preview
                display_inten = inten_new
            else:
                # --- LIVE ACQUISITION PHASE ---
                if self.bg_sub_enabled and self.background is not None:
                    display_inten = inten_new - self.background
                    if self.bg_status_text:
                        self.bg_status_text.set_text("BG Sub: ACTIVE")
                else:
                    display_inten = inten_new
                    if self.bg_status_text:
                        self.bg_status_text.set_text("BG Sub: OFF")

            self.plot_wl = wl_new
            self.plot_inten = display_inten

            # Update visual artists
            self.line.set_data(self.plot_wl, self.plot_inten)
            
            # --- DYNAMIC Y-AXIS LOGIC (Optimized) ---
            if self._wl_mask is None or len(self._wl_mask) != len(self.plot_wl):
                self._wl_mask = (self.plot_wl >= WAVELENGTH_MIN) & (self.plot_wl <= WAVELENGTH_MAX)
            
            visible_inten = self.plot_inten[self._wl_mask]
            if visible_inten.size > 0:
                peak = np.max(visible_inten)
                ymin_val = np.min(visible_inten)
            else:
                peak, ymin_val = 500, 0

            # Only update axis if change is significant (> 10%) to save CPU
            target_ymax = max(peak * 1.1, 500)
            target_ymin = min(ymin_val * 1.1, 0) if self.bg_sub_enabled and self.background is not None else 0
            
            if abs(target_ymax - self._current_ymax) > self._current_ymax * 0.1 or \
               abs(target_ymin - self._current_ymin) > (abs(self._current_ymin) + 1) * 0.1:
                self._current_ymax = target_ymax
                self._current_ymin = target_ymin
                self.ax.set_ylim(self._current_ymin, self._current_ymax)

        # Check for connection timeout
        time_since_last_data = time.time() - self.last_data_time
        if time_since_last_data > 2.0:
            self.warning_text.set_visible(True)
        else:
            self.warning_text.set_visible(False)
        
        return self.line, self.cursor_text, self.warning_text, self.dwell_text, self.bg_status_text

    def toggle_pause(self, event=None):
        self.is_paused = not self.is_paused
        label = "Resume" if self.is_paused else "Pause"
        self.btn_pause.label.set_text(label)
        
        # --- Visual Feedback: Change button colors based on pause state ---
        if self.is_paused:
            # Paused State: White background, black text
            bg_color = 'white'
            text_color = 'black'
            hover_color = '#eeeeee'
        else:
            # Running State: Dark background, white/orange text
            bg_color = '#333333'
            text_color = 'white'
            hover_color = '#444444'

        for btn in [self.btn_save, self.btn_pause, self.btn_bgsub]:
            if btn:
                btn.color = bg_color
                btn.hovercolor = hover_color
                btn.ax.set_facecolor(bg_color)
                # Keep BG Sub button text orange if running, else black
                if btn == self.btn_bgsub and not self.is_paused:
                    btn.label.set_color('#ffaa00')
                else:
                    btn.label.set_color(text_color)
        
        # Optimization: Actually pause the animation timer to free CPU
        if self.is_paused:
            self.ani.pause()
        else:
            self.ani.resume()
            
        self.fig.canvas.draw_idle()

    def on_mouse_move(self, event):
        """
        Updates the text label when the mouse moves over the plot.
        Works best when Paused or if computer is fast enough.
        """
        if event.inaxes == self.ax:
            self.cursor_text.set_text(f"Cursor: {event.xdata:.2f} nm\nInt: {event.ydata:.0f}")
        else:
            self.cursor_text.set_text("")

    def save_spectrum(self, event=None):
        """
        Saves the CURRENTLY DISPLAYED data.
        If paused, it saves the frozen frame.
        If running, it saves the latest frame shown.
        """
        # We use self.plot_wl instead of self.current_wl to ensure "What You See Is What You Save"
        if self.plot_wl is None:
            print("No data available to save yet.")
            return

        try:
            # Use persistent Tk root to avoid the heavy creation overhead
            if self._tk_root is None:
                self._tk_root = tk.Tk()
                self._tk_root.withdraw()
            
            self._tk_root.attributes("-topmost", True)
            
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            initial_file = f"Spectrum_{timestamp}.csv"
            
            filepath = filedialog.asksaveasfilename(
                initialdir=self.save_dir,
                initialfile=initial_file,
                defaultextension=".csv",
                filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
                title="Save Spectrum Data",
                parent=self._tk_root
            )

            if not filepath:
                print("Save cancelled by user.")
                return

            filename = os.path.basename(filepath)
            
            status_str = "PAUSED" if self.is_paused else "LIVE"
            header = (f"Saved: {timestamp} ({status_str})\n"
                      f"Integration Time: {self.integration_time_micros/1000}ms\n"
                      f"Wavelength(nm),Intensity")
            
            data = np.column_stack((self.plot_wl, self.plot_inten))
            
            np.savetxt(filepath, data, fmt='%.4f', delimiter=',', header=header, comments='# ')
            
            print(f">>> DATA SAVED: {filename}")
            sys.stdout.flush()

            # Optional popup confirm
            # messagebox.showinfo("Data Saved", f"File saved successfully:\n{filename}")
            
        except Exception as e:
            print(f"Error saving file: {e}")

    def on_key_press(self, event):
        if event.key == 'ctrl+b':
            print("Shortcut 'Ctrl+B' detected...")
            self.save_spectrum()
        # Optional: Spacebar to toggle pause
        if event.key == ' ':
            self.toggle_pause()

    def run(self):
        if not self.connect():
            return

        t = threading.Thread(target=self.data_acquisition_loop, daemon=True)
        t.start()

        print("Starting Live View...")
        print("Controls: [Pause] to freeze, [Save] to save csv.")
        
        # Adjust layout to make room for buttons
        plt.subplots_adjust(bottom=0.2)
        
        # Button: Save
        ax_save = plt.axes([0.55, 0.05, 0.2, 0.075], facecolor='#222222') 
        self.btn_save = Button(ax_save, 'Save Data', color='#333333', hovercolor='#444444')
        self.btn_save.label.set_color('white')
        self.btn_save.on_clicked(self.save_spectrum)

        # Button: Pause
        ax_pause = plt.axes([0.25, 0.05, 0.2, 0.075], facecolor='#222222')
        self.btn_pause = Button(ax_pause, 'Pause', color='#333333', hovercolor='#444444')
        self.btn_pause.label.set_color('white')
        self.btn_pause.on_clicked(self.toggle_pause)

        # Button: BG Sub toggle
        ax_bgsub = plt.axes([0.79, 0.05, 0.15, 0.075], facecolor='#222222')
        self.btn_bgsub = Button(ax_bgsub, 'BG Sub: ON', color='#333333', hovercolor='#444444')
        self.btn_bgsub.label.set_color('#ffaa00')
        self.btn_bgsub.on_clicked(self.toggle_bg_sub)

        # Connect events
        self.fig.canvas.mpl_connect('key_press_event', self.on_key_press)
        self.fig.canvas.mpl_connect('motion_notify_event', self.on_mouse_move)

        # Removed Cursor widget (crosshair lines) as requested, 
        # only the text reading from on_mouse_move will be shown.
        self.cursor_widget = None 

        self.ani = FuncAnimation(
            self.fig, 
            self.update, 
            init_func=self.init_plot, 
            blit=False, 
            interval=30,
            cache_frame_data=False
        )
        
        plt.show()
        
        self.stop_event.set()
        t.join(timeout=1.0)
        
        if self.spec:
            self.spec.close()
            print("Spectrometer connection closed.")

if __name__ == "__main__":
    analyzer = LiveSpectrumAnalyzer(integration_time_s=DWELL_TIME_SEC)
    analyzer.run()