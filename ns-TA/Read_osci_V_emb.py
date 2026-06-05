import pyvisa as visa
import numpy as np
import time

class OscilloscopeReader:
    def __init__(self, visa_address='GPIB0::1::INSTR', timeout_ms=6000):
        """Initializes the connection to the oscilloscope."""
        self.visa_address = visa_address
        self.rm = visa.ResourceManager()
        
        try:
            self.scope = self.rm.open_resource(self.visa_address)
        except Exception as e:
            raise ConnectionError(f"Could not connect to scope at {self.visa_address}: {e}")

        # Configure connection parameters
        self.scope.timeout = timeout_ms
        self.scope.encoding = 'latin_1'
        self.scope.read_termination = '\n'
        self.scope.write_termination = None
        
        self._setup_scope()

    def _setup_scope(self):
        """Applies the initial configuration to the scope."""
        self.scope.write('*cls') # Clear status registers
        self.scope.write('header 0') 
        self.scope.write('data:encdg RIB')
        self.scope.write('data:source CH1') 
        self.scope.write('data:start 1') 
        
        # Get record length and configure data stop point
        self.record_len = int(self.scope.query('horizontal:recordlength?'))
        self.scope.write(f'data:stop {self.record_len}') 
        self.scope.write('wfmpre:byt_n 1') 

    def acquire_trace(self):
        """
        Arms the scope, waits for the trigger, retrieves the data, 
        and returns the scaled time and voltage arrays.
        """
        # 1. Arm the scope for a single sequence
        self.scope.write('acquire:stopafter SEQUENCE')
        self.scope.write('acquire:state 1') 

        # 2. Wait for Trigger
        try:
            # Pauses execution here until the trigger event occurs
            self.scope.query('*opc?') 
        except visa.errors.VisaIOError:
            raise TimeoutError("Waiting for trigger timed out. Did the laser fire?")

        # 3. Retrieve raw binary data
        bin_wave = self.scope.query_binary_values('curve?', datatype='b', container=np.array)
        
        # 4. Read scaling factors
        tscale = float(self.scope.query('wfmpre:xincr?'))
        tstart = float(self.scope.query('wfmpre:xzero?'))
        vscale = float(self.scope.query('wfmpre:ymult?'))
        voff = float(self.scope.query('wfmpre:yzero?'))
        vpos = float(self.scope.query('wfmpre:yoff?'))

        # 5. Convert raw data to Time (s) and Voltage (V)
        total_time = tscale * self.record_len
        tstop = tstart + total_time
        time_array = np.linspace(tstart, tstop, num=self.record_len, endpoint=False)
        
        unscaled_wave = np.array(bin_wave, dtype='double')
        voltage_array = (unscaled_wave - vpos) * vscale + voff

        return time_array, voltage_array

    def close(self):
        """Safely closes the connection to the instrument."""
        if hasattr(self, 'scope'):
            self.scope.close()
        if hasattr(self, 'rm'):
            self.rm.close()

# --- Optional: Keep a quick test block at the bottom ---
if __name__ == "__main__":
    # This block only runs if you execute this file directly, 
    # not when you import it into another script.
    print("Testing OscilloscopeReader...")
    try:
        reader = OscilloscopeReader()
        print("Waiting for trigger...")
        t, v = reader.acquire_trace()
        print(f"Success! Acquired {len(t)} points.")
        print(f"Max Voltage: {np.max(v)} V")
        reader.close()
    except Exception as e:
        print(f"Test failed: {e}")