import time
import threading
import os
import datetime
import pyvisa as visa
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button
import tkinter as tk
from tkinter import messagebox

# --- Configuration ---
VISA_ADDRESS = 'GPIB0::1::INSTR'
SAVE_DIRECTORY = r"C:\My files\Google drive sync\St Andrews\Data\ns-TA data test"
TIMEOUT_MS = 5000 # 5 seconds timeout for VISA

# Global variables for thread safety
data_lock = threading.Lock()
shared_time = np.array([])
shared_wave = np.array([])
last_update_time = 0 # To track when we last got data
is_acquiring = True
status_message = "Initializing..." # For UI display

def acquire_data_thread(visa_address):
    """Background thread to fetch data."""
    global shared_time, shared_wave, is_acquiring, last_update_time, status_message
    
    rm = visa.ResourceManager()
    try:
        scope = rm.open_resource(visa_address)
    except Exception as e:
        status_message = f"Connection Error: {e}"
        return

    scope.timeout = 6000 # Increase timeout slightly to allow for slow triggers
    scope.encoding = 'latin_1'
    scope.read_termination = '\n'
    scope.write_termination = None
    
    try:
        # scope.write('*rst')  <-- CRITICAL CHANGE: DO NOT RESET!
        # We want to keep your manual trigger levels and scale settings.
        
        scope.write('*cls') # Clear status registers (okay to keep)
        scope.write('header 0') 
        scope.write('data:encdg RIB')
        scope.write('data:source CH1') 
        scope.write('data:start 1') 
        
        # Get record length from the scope (in case you changed it manually)
        record_len = int(scope.query('horizontal:recordlength?'))
        scope.write(f'data:stop {record_len}') 
        scope.write('wfmpre:byt_n 1') 
        
        print(f"Connected. Record Length: {record_len}")

        while is_acquiring:
            try:
                # 1. ARM THE SCOPE
                scope.write('acquire:stopafter SEQUENCE')
                scope.write('acquire:state 1') 

                # 2. WAIT FOR TRIGGER
                # The script will pause here until the laser fires
                try:
                    scope.query('*opc?') 
                except visa.errors.VisaIOError:
                    # This happens if the laser doesn't fire within 'scope.timeout'
                    status_message = "WAITING FOR TRIGGER..."
                    continue 

                # 3. GET DATA
                bin_wave = scope.query_binary_values('curve?', datatype='b', container=np.array)
                
                # 4. READ SCALING FACTORS (Every loop, in case you turn knobs)
                tscale = float(scope.query('wfmpre:xincr?'))
                tstart = float(scope.query('wfmpre:xzero?'))
                vscale = float(scope.query('wfmpre:ymult?'))
                voff = float(scope.query('wfmpre:yzero?'))
                vpos = float(scope.query('wfmpre:yoff?'))

                # 5. MATH
                total_time = tscale * record_len
                tstop = tstart + total_time
                temp_time = np.linspace(tstart, tstop, num=record_len, endpoint=False)
                
                unscaled_wave = np.array(bin_wave, dtype='double')
                temp_wave = (unscaled_wave - vpos) * vscale + voff

                # 6. UPDATE DISPLAY
                with data_lock:
                    shared_time = temp_time
                    shared_wave = temp_wave
                    last_update_time = time.time()
                    status_message = "Status: Capturing"
                
                # Small pause to keep UI responsive
                time.sleep(0.01) 

            except Exception as e:
                status_message = f"Error: {str(e)}"
                time.sleep(0.5)

    finally:
        if 'scope' in locals():
            scope.close()
        rm.close()

def save_current_data(event):
    """Callback for Save button."""
    if not os.path.exists(SAVE_DIRECTORY):
        try:
            os.makedirs(SAVE_DIRECTORY)
        except OSError:
            print("Error creating directory.")
            return

    with data_lock:
        if len(shared_time) == 0:
            print("No data to save.")
            return
        t_save = shared_time.copy()
        v_save = shared_wave.copy()

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"scope_data_{timestamp}.csv"
    filepath = os.path.join(SAVE_DIRECTORY, filename)

    try:
        data_stack = np.column_stack((t_save, v_save))
        np.savetxt(filepath, data_stack, delimiter=",", header="Time (s),Voltage (V)", comments='')
        print(f"Saved: {filename}")

        root = tk.Tk()
        root.withdraw() # 隐藏 tkinter 的主窗口，只留弹窗
        root.attributes('-topmost', True) # 确保弹窗在最顶层，不会被图表挡住
        messagebox.showinfo("Success", "Saved!") # 显示包含 "Saved!" 的提示框
        root.destroy() # 关闭后销毁进程

    except Exception as e:
        print(f"Save failed: {e}")
        
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        messagebox.showerror("Error", f"Save failed:\n{e}")
        root.destroy()

def update_plot(frame):
    """Refreshes plot and status text."""
    global status_text
    
    current_time = time.time()
    
    # 1. Update Waveform
    with data_lock:
        if len(shared_time) > 0:
            line.set_data(shared_time, shared_wave)
            ax.relim()
            ax.autoscale_view()
    
    # 2. Update Status Text
    # If data is older than 2 seconds, warn the user
    if current_time - last_update_time > 2.0:
        warning_msg = "NO DATA RECEIVED"
        # If the thread reported a specific error (like Trigger), use that
        if "TIMEOUT" in status_message:
            warning_msg = "WAITING FOR TRIGGER..."
        
        status_text.set_text(warning_msg)
        status_text.set_color('red')
    else:
        status_text.set_text(f"Status: Active")
        status_text.set_color('green')
        
    return line, status_text



# --- Main Execution ---
if __name__ == "__main__":
    
    acq_thread = threading.Thread(target=acquire_data_thread, args=(VISA_ADDRESS,), daemon=True)
    acq_thread.start()

    fig, ax = plt.subplots()
    plt.subplots_adjust(bottom=0.2) 
    
    line, = ax.plot([], [], lw=1, color='blue')
    ax.set_title('Oscilloscope Monitor')
    ax.set_xlabel('Time (s)')
    ax.set_ylabel('Voltage (V)')
    ax.grid(True)
    
    # Add Status Text in the top-left corner
    status_text = ax.text(0.02, 0.95, "Initializing...", transform=ax.transAxes, 
                          fontsize=12, fontweight='bold', color='orange')

    ax_button = plt.axes([0.7, 0.05, 0.2, 0.075])
    btn_save = Button(ax_button, 'Save CSV')
    btn_save.on_clicked(save_current_data)

    ani = FuncAnimation(fig, update_plot, interval=100, blit=False)

    plt.show()

    is_acquiring = False
    acq_thread.join()