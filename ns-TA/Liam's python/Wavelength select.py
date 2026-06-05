import serial
import time

wavelengthLimit = 852 # upper wavelength limit (measured 20/01/24). Dial says 285
wavelength = wavelengthLimit

arduino = serial.Serial('COM4',9600)

input("Press enter key to proceed when motor reaches limit (enter 0 to close program):")

while True: 
    wavelengthNew = int(input("Select wavelength (nm):"))
    
    if wavelengthNew > wavelengthLimit:
        print("Outwith range")
    
    elif wavelengthNew == 0:
        arduino.close()
        break
        
    elif wavelengthNew <= wavelengthLimit:
        arduino.write(str.encode(str(wavelength-wavelengthNew)))
        time.sleep(abs(wavelength-wavelengthNew)*0.5) # allow time to return to start
        wavelength = wavelengthNew
        print("Current wavelength (nm):", wavelength)
    
    
