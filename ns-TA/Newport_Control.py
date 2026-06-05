import pyvisa
import time
import sys

'''This version is modified from Newport's code, allow you to change wavelength
and shut down the monochrmator, which will give white light'''

class TAMonochromator:
    """
    Controller class for Newport Cornerstone B Monochromator 
    specifically for Transient Absorption setup.
    """
    def __init__(self):
        self.rm = pyvisa.ResourceManager()
        self.instrument = None
        self.connected = False

    def connect(self):
        """
        Scans for the Newport Monochromator via USB and connects.
        Based on the vendor ID 0x1FDE provided in the documentation.
        """
        print("Scanning for Newport Monochromator...")
        try:
            # Get list of all connected resources
            resource_list = self.rm.list_resources()
            target_resource = None

            # Iterate through resources to find Newport (Vendor ID 0x1FDE)
            # Product ID 0x0006 is CS130B, 0x0014 is CS260B
            for res in resource_list:
                if "0x1FDE" in res:
                    target_resource = res
                    break
            
            if target_resource:
                self.instrument = self.rm.open_resource(target_resource)
                # Set timeout (ms) - extended to allow for grating movement
                self.instrument.timeout = 10000 
                
                # specific command to clear buffer
                self.instrument.write("*CLS") 
                
                # Query ID to confirm connection
                idn = self.instrument.query("*IDN?")
                print(f"Successfully connected to: {idn.strip()}")
                self.connected = True
            else:
                print("Error: No Newport Monochromator found on USB.")
                self.connected = False

        except Exception as e:
            print(f"Connection Error: {e}")
            self.connected = False

    def wait_for_opc(self):
        """
        Waits for the 'Operation Complete' (*OPC?) flag.
        This ensures the mechanical grating has stopped moving before we proceed.
        Critical for TA experiments to avoid noise.
        """
        try:
            # The instrument returns '1' when the operation is complete
            self.instrument.query("*OPC?")
        except Exception as e:
            print(f"Warning during wait: {e}")

    def set_wavelength(self, nm):
        """
        Sets the monochromator to a specific wavelength (Monochromatic Mode).
        """
        if not self.connected:
            print("Instrument not connected.")
            return

        try:
            print(f"Moving grating to {nm} nm...")
            # 'GOWAVE' is the command to move wavelength
            self.instrument.write(f"GOWAVE {nm}")
            
            # Wait for physical movement to finish
            self.wait_for_opc()
            print(f"Reached {nm} nm.")
            
        except Exception as e:
            print(f"Error setting wavelength: {e}")

    def set_white_light(self):
        """
        Sets the monochromator to White Light mode.
        This moves the grating to 0 nm (0th Order), acting as a mirror.
        """
        if not self.connected:
            print("Instrument not connected.")
            return

        try:
            print("Switching to White Light (0th Order)...")
            # 0 nm is the standard position for broadband reflection
            self.instrument.write("GOWAVE 0")
            
            # Wait for physical movement to finish
            self.wait_for_opc()
            print("White Light mode active (Grating at 0 nm).")
            
        except Exception as e:
            print(f"Error switching to white light: {e}")

    def close(self):
        """
        Closes the connection cleanly.
        """
        if self.instrument:
            self.instrument.close()
        self.rm.close()
        print("Connection closed.")

# --- Main Execution Block ---
if __name__ == "__main__":
    # Initialize the controller
    mono = TAMonochromator()
    mono.connect()

    if mono.connected:
        while True:
            print("\n--- TA Probe Source Control ---")
            print("1. Set Wavelength (Monochromatic Probe)")
            print("2. Switch to White Light (Broadband Probe)")
            print("3. Exit")
            
            choice = input("Select option (1-3): ")

            if choice == "1":
                try:
                    wl = float(input("Enter target wavelength (nm): "))
                    mono.set_wavelength(wl)
                except ValueError:
                    print("Invalid number format.")
            
            elif choice == "2":
                mono.set_white_light()
                
            elif choice == "3":
                mono.close()
                break
            
            else:
                print("Invalid choice, please try again.")