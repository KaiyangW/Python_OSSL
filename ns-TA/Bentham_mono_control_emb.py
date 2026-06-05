import serial
import time
import json
import os
import threading

# --- CONFIGURATION ---
ARDUINO_PORT = 'COM3'         
WAVELENGTH_LIMIT = 852        

CALIBRATION_FACTOR_UP = 74.24    # 波长增加时的系数 (Steps/nm)
CALIBRATION_FACTOR_DOWN = 66.74  # 波长减小时的系数 (Steps/nm)

ENABLE_BACKLASH = False  

POSITION_FILE = "mono_config.json" 

class MonoDriver:
    def __init__(self, port=ARDUINO_PORT):
        self.port = port
        self.ser = None
        self.connected = False
        self.current_wavelength = WAVELENGTH_LIMIT
        self.is_moving = False
        self._stop_event = threading.Event()
        
        self.load_position()
        self.connect()

    def connect(self):
        print(f"[MonoDriver] Connecting to {self.port}...")
        try:
            self.ser = serial.Serial(self.port, 9600, timeout=1)
            self.ser.dtr = False
            time.sleep(1)
            self.ser.dtr = True
            time.sleep(2) 
            self.connected = True
            print(f"[MonoDriver] Hardware connected.")
        except Exception as e:
            print(f"[MonoDriver] Connection failed ({e}). Entering SIMULATION MODE.")
            self.connected = False

    def load_position(self):
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
        try:
            with open(POSITION_FILE, 'w') as f:
                json.dump({"wavelength": self.current_wavelength}, f)
        except Exception as e:
            print(f"[MonoDriver] Could not save position: {e}")

    def move_threaded(self, target_nm, status_callback, finish_callback):
        if target_nm > WAVELENGTH_LIMIT:
            status_callback(f"Error: Target > {WAVELENGTH_LIMIT}nm")
            return

        self._stop_event.clear()
        t = threading.Thread(
            target=self._move_logic, 
            args=(target_nm, status_callback, finish_callback),
            daemon=True
        )
        t.start()

    def _move_logic(self, target_nm, status_callback, finish_callback):
        self.is_moving = True
        
        try:
            # [修改 3] 移除所有回差补偿 (Backlash) 代码
            # 直接执行物理移动，Factor的选择在 _execute_physical_move 内部判断
            
            msg = f"Moving to target {target_nm:.1f}..."
            self._safe_callback(status_callback, msg)
            
            self._execute_physical_move(target_nm, status_callback)

        except Exception as e:
            self._safe_callback(status_callback, f"Error: {e}")
            print(f"[MonoDriver] Logic Error: {e}")
        finally:
            self.is_moving = False
            self._safe_callback(finish_callback)

    def _execute_physical_move(self, target_nm, status_callback):
        delta_nm = target_nm - self.current_wavelength
        
        if abs(delta_nm) < 0.005: 
            return True
        
        if delta_nm > 0:
            current_factor = CALIBRATION_FACTOR_UP
            direction_str = "UP"
        else:
            current_factor = CALIBRATION_FACTOR_DOWN
            direction_str = "DOWN"

        steps_to_send = int(round(delta_nm * current_factor))
        
        print(f"[DEBUG] Delta: {delta_nm:.2f} nm | Mode: {direction_str} | Factor: {current_factor} | Steps: {steps_to_send}")

        if steps_to_send == 0:
            return True
        
        calc_wait = abs(delta_nm) * 0.3 
        total_wait = max(calc_wait, 1.2) 
        
        try:
            if self.connected and self.ser:
                time.sleep(0.1)
                
                command = f"{steps_to_send}\n"
                # print(f"[DEBUG] Sending bytes: {command.encode()}") 
                self.ser.write(command.encode())
            else:
                print(f"[MonoDriver-SIM] Moving {steps_to_send} steps ({direction_str})")

            start_time = time.perf_counter()
            while (time.perf_counter() - start_time) < total_wait:
                if self._stop_event.is_set():
                    self._safe_callback(status_callback, "Move Aborted.")
                    return False
                time.sleep(0.1)

            # 更新位置
            self.current_wavelength = target_nm
            self.save_position()
            self._safe_callback(status_callback, f"At {self.current_wavelength:.1f} nm")
            return True

        except Exception as e:
            self._safe_callback(status_callback, f"HW Error: {e}")
            print(f"[MonoDriver] HW Error: {e}")
            return False

    def _safe_callback(self, callback, *args):
        try:
            if callback:
                callback(*args)
        except Exception as e:
            print(f"[MonoDriver] Callback error: {e}")

    def close(self):
        self._stop_event.set()
        self.save_position()
        if self.connected and self.ser:
            try:
                self.ser.close()
            except:
                pass