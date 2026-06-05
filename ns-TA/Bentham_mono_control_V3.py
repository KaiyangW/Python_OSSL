import serial
import time
import sys
import json
import os

# --- CONFIGURATION ---
ARDUINO_PORT = 'COM3'         
WAVELENGTH_LIMIT = 852        

CALIBRATION_FACTOR_UP = 74.24    # Steps per nm when wavelength increases
CALIBRATION_FACTOR_DOWN = 66.74  # Steps per nm when wavelength decreases

DEFAULT_SPEED = 0.25  
POSITION_FILE = "mono_config.json" 

class Monochromator:
    def __init__(self, port):
        self.port = port
        self.ser = None
        self.connected = False
        self.current_wavelength = 0
        
        # Load last known position
        self.load_position()

        # Connect
        try:
            self.ser = serial.Serial(port, 9600, timeout=1)
            self.ser.dtr = False
            time.sleep(1)
            self.ser.dtr = True
            time.sleep(2) # Wait for Arduino bootloader
            
            print(f"[Hardware] Connected to Mono on {port}")
            self.connected = True
        except Exception as e:
            print(f"[Error] Connection failed on {port}: {e}")
            self.connected = False

    def load_position(self):
        """Loads wavelength from file."""
        if os.path.exists(POSITION_FILE):
            try:
                with open(POSITION_FILE, 'r') as f:
                    data = json.load(f)
                    self.current_wavelength = data.get("wavelength", WAVELENGTH_LIMIT)
            except:
                self.current_wavelength = WAVELENGTH_LIMIT
        else:
            self.current_wavelength = WAVELENGTH_LIMIT

    def save_position(self):
        """Saves wavelength to file."""
        try:
            with open(POSITION_FILE, 'w') as f:
                json.dump({"wavelength": self.current_wavelength}, f)
        except Exception as e:
            print(f"[Error] Could not save position: {e}")

    def verify_start_position(self):
        """Checks if the software wavelength matches reality."""
        print(f"\n--- POSITION CHECK ---")
        print(f"Software thinks we are at: [{self.current_wavelength} nm]")
        user_input = input("Is this correct? (y / enter actual nm): ").strip().lower()

        if user_input == 'y':
            print(f"[Status] Confirmed.")
        else:
            try:
                actual = float(user_input)
                self.current_wavelength = actual
                self.save_position()
                print(f"[Calibration] Re-zeroed to: {self.current_wavelength} nm")
            except ValueError:
                print("[Error] Invalid input. Ignoring.")

    def set_shutter(self, state):
        if not self.connected: return
        try:
            if state == 'open':
                self.ser.write(b"998\n")
                print("[Shutter] OPEN")
            elif state == 'close':
                self.ser.write(b"999\n")
                print("[Shutter] CLOSED")
            time.sleep(0.2) 
        except Exception as e:
            print(f"[Error] Shutter failed: {e}")

    def move_to(self, target_nm):
        """
        Main movement logic.
        Synced with 'Bentham_mono_control_emb.py': 
        Removes complex software backlash (overshoot) in favor of dual calibration factors.
        """
        if not self.connected: 
            print("[Error] Not connected.")
            return

        if target_nm > WAVELENGTH_LIMIT:
            print(f"[Warning] Target {target_nm} nm is out of range!")
            return

        # Direct movement logic (Backlash handling is now done via Factors in _execute_physical_move)
        print(f"[Motor] Moving to {target_nm:.1f} nm...")
        self._execute_physical_move(target_nm)

    def _execute_physical_move(self, target_nm):
        """
        Low-level function to calculate steps and talk to Arduino.
        """
        # Calculate Delta
        delta_nm = target_nm - self.current_wavelength
        
        if abs(delta_nm) < 0.05:
            return True

        # [LOGIC UPDATE] Select Factor based on Direction
        # This matches the logic in Bentham_mono_control_emb.py
        if delta_nm > 0:
            current_factor = CALIBRATION_FACTOR_UP
            direction_str = "UP (>)"
        else:
            current_factor = CALIBRATION_FACTOR_DOWN
            direction_str = "DOWN (<)"

        # Calculate steps (Keep sign of delta_nm, factor is always positive)
        steps_to_send = int(round(delta_nm * current_factor))
        
        # Calculate expected wait time
        calc_wait = abs(delta_nm) * DEFAULT_SPEED
        total_wait = max(calc_wait, 1.2) # Minimum wait of 1.2s
        
        try:
            # 1. Format command with newline
            command = f"{steps_to_send}\n"
            
            # 2. Brief sleep for serial stability
            time.sleep(0.1)
            
            # Send
            # print(f"[DEBUG] Sending: {command.strip()} | Mode: {direction_str}") 
            self.ser.write(command.encode())
            
            # Progress Loop
            start_time = time.time()
            sys.stdout.write(f"      Sending {steps_to_send} steps ({direction_str})... ")
            
            while (time.time() - start_time) < total_wait:
                elapsed = time.time() - start_time
                if int(elapsed * 10) % 5 == 0:
                    sys.stdout.write(".")
                    sys.stdout.flush()
                time.sleep(0.1)
            
            print(" Done.")
            
            # Update Internal State
            self.current_wavelength = target_nm
            self.save_position()
            return True

        except Exception as e:
            print(f"\n[Error] Move failed: {e}")
            return False
        except KeyboardInterrupt:
            print("\n[Interrupt] Stopped by user.")
            return False

    def close(self):
        self.save_position()
        if self.connected:
            self.ser.close()

# --- MAIN EXECUTION BLOCK ---
if __name__ == "__main__":
    print("--- BENTHAM MONO CONTROL V3 (Synced with EMB Dual-Factor Logic) ---")
    
    mono = Monochromator(ARDUINO_PORT)

    if mono.connected:
        mono.verify_start_position()

        print("\nCommands: [Number] to move, 'o'/'c' for shutter, 'q' to quit.")
        
        while True:
            try:
                user_input = input(f"\n[{mono.current_wavelength:.1f} nm] >> ").strip()
                
                if not user_input: continue

                if user_input.lower() == 'q':
                    break
                elif user_input.lower() == 'o':
                    mono.set_shutter('open')
                elif user_input.lower() == 'c':
                    mono.set_shutter('close')
                else:
                    target = float(user_input)
                    mono.move_to(target)
                    
            except ValueError:
                print("Invalid input. Please enter a wavelength number.")
            except KeyboardInterrupt:
                break

    mono.close()
    print("Exited.")