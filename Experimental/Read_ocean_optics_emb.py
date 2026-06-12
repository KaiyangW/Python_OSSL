import numpy as np
import threading
import datetime
import os
import time
import sys

# Standard library imports needed for the connection logic
import usb.core

# SeaBreeze
import seabreeze
# Specific backend usage as per your standalone script
seabreeze.use('pyseabreeze') 

from seabreeze.spectrometers import Spectrometer, list_devices, SeaBreezeError

class OceanDriver:
    def __init__(self, integration_time_ms=100):
        self.spec = None
        self.integration_micros = integration_time_ms * 1000
        self.stop_event = threading.Event()
        self.data_lock = threading.Lock()
        
        # Data containers (Initialized with dummy data to prevent GUI crash if hardware missing)
        self.wl = np.linspace(300, 900, 100) 
        self.inten = np.zeros(100)
        self.connected = False
        
        # Fixed path as per your requirement
        self.save_dir = r"C:\My files\Google drive sync\St Andrews\Data\Oceanoptics data"

    def connect(self):
        """
        Implements the robust connection logic:
        1. Attempts raw USB reset (fixes 'Resource Busy').
        2. Lists devices.
        3. Tries standard init, falls back to raw device.open() if needed.
        """
        print("[OceanDriver] Searching for spectrometers...")

        # --- Step 1: Raw USB Reset (Fixes 'Resource Busy' errors) ---
        try:
            # Ocean Optics Vendor ID = 0x2457
            print("[OceanDriver] DEBUG: Attempting raw USB reset...")
            devs = usb.core.find(find_all=True, idVendor=0x2457)
            for dev in devs:
                try:
                    dev.reset()
                    print(f"[OceanDriver] DEBUG: Reset sent to USB device {dev.idProduct:x}")
                except Exception as e:
                    print(f"[OceanDriver] DEBUG: USB Reset warning: {e}")
            
            # Wait for device to reboot after reset
            time.sleep(2.0) 
        except Exception as e:
            print(f"[OceanDriver] DEBUG: Raw USB reset skipped: {e}")

        # --- Step 2: Connect to SeaBreeze Device ---
        try:
            devices = list_devices()
            if not devices:
                print("[OceanDriver] Error: No devices found. Check Zadig driver (try libusbK).")
                self.connected = False
                return False
            
            print(f"[OceanDriver] Device found. Force opening...")
            device = devices[0]
            
            try:
                # Attempt standard initialization
                self.spec = Spectrometer(device)
                print("[OceanDriver] Standard connection successful.")
            except Exception as e:
                print(f"[OceanDriver] Standard init failed ({e}), trying raw mode...")
                device.open() 
                self.spec = Spectrometer(device) 

            # If we reached here without error, we are connected
            print("[OceanDriver] Spectrometer link established.")
            
            self.spec.integration_time_micros(self.integration_micros)
            self.connected = True
            return True

        except Exception as e:
            print(f"[OceanDriver] CRITICAL ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.connected = False
            return False

    def start_acquisition(self):
        """Starts the background thread for data reading."""
        if not self.connected: 
            print("[OceanDriver] Cannot start acquisition: Not connected.")
            return
            
        self.stop_event.clear()
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()
        print("[OceanDriver] Acquisition thread started.")

    def _run_loop(self):
        """
        [MODIFIED] Continuously fetches data with safety delays and error tolerance.
        """
        error_count = 0 # Counter for consecutive errors

        while not self.stop_event.is_set() and self.spec:
            try:
                # 1. Get Wavelengths
                wl_new = self.spec.wavelengths()
                
                # 2. Get Intensities (This blocks for integration time)
                inten_new = self.spec.intensities()
                
                # 3. Update Shared Data
                with self.data_lock:
                    self.wl = wl_new
                    self.inten = inten_new
                
                # Reset error counter on success
                error_count = 0

                # [CRITICAL FIX] 
                # Increase sleep to 5ms to allow USB buffer to clear.
                # Prevents "Resource Busy" or driver freeze over time.
                time.sleep(0.005) 

            except Exception as e:
                error_count += 1
                print(f"[OceanDriver] Read Warning ({error_count}/10): {e}")
                
                # If error occurs, pause briefly to let hardware recover
                time.sleep(0.5)

                # Only kill the thread if we have >10 CONSECUTIVE errors
                if error_count > 10:
                    print("[OceanDriver] Too many errors. Stopping acquisition.")
                    self.connected = False
                    break

    def get_data(self):
        """Returns a COPY of the current data for the GUI to plot."""
        with self.data_lock:
            return self.wl.copy(), self.inten.copy()

    def set_integration_time(self, ms):
        """Allows updating integration time dynamically."""
        self.integration_micros = ms * 1000
        if self.spec:
            try:
                # Pause thread briefly to avoid collision (optional but safer)
                time.sleep(0.05)
                self.spec.integration_time_micros(self.integration_micros)
                print(f"[OceanDriver] Integration time set to {ms} ms")
            except Exception as e:
                print(f"[OceanDriver] Failed to set integration time: {e}")

    def save_current_data(self, wl_data, inten_data):
        """Saves the provided data (what is currently on screen) to CSV."""
        if not os.path.exists(self.save_dir):
            try:
                os.makedirs(self.save_dir)
            except OSError as e:
                print(f"[OceanDriver] Error creating directory: {e}")
                return None
            
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"Spectrum_{timestamp}.csv"
        filepath = os.path.join(self.save_dir, filename)

        header = (f"Saved: {timestamp}\n"
                  f"Integration: {self.integration_micros/1000}ms\n"
                  f"Wavelength(nm),Intensity")
        
        try:
            data = np.column_stack((wl_data, inten_data))
            np.savetxt(filepath, data, fmt='%.4f', delimiter=',', header=header)
            print(f"[OceanDriver] File saved: {filename}")
            return filename
        except Exception as e:
            print(f"[OceanDriver] Error saving file: {e}")
            return None

    def close(self):
        """Stops the thread and closes the USB connection."""
        self.stop_event.set()
        # Give the thread a moment to finish
        time.sleep(0.5)
        if self.spec:
            try:
                self.spec.close()
                print("[OceanDriver] Spectrometer closed.")
            except:
                pass
        self.connected = False