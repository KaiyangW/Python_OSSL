import serial
import time
from pymeasure.instruments.srs import SR830
import numpy as np
import matplotlib.pyplot as plt
import csv

wavelengthLimit = 852 # upper wavelength limit (measured 20/01/24). Dial says 285

wavelengthStep = int(input("Wavelength step (nm):")) # positive step corresponds to decreasing wavelength
wavelengthStart = int(input("Wavelength start (nm):")) # <= wavelengthLimit
wavelengthFinish = int(input("Wavelength finish (nm):"))
timeConstant = float(input("Time constant:")) # sets lock-in time constant
numMeasurements = int(input("Number of measurements:")) # defines the number of repeat scans
fileName = input("File name:") # the name of the saved file

wavelength = wavelengthStart # the current wavelength
numSteps = abs(int((wavelengthStart-wavelengthFinish)/wavelengthStep + 1))

lightData_x = []
lightData_y = []
darkData_x = []
darkData_y = []

lightData_x_Avg = np.zeros(numSteps) # averages are np arrays to avoid problems
lightData_y_Avg = np.zeros(numSteps)
darkData_x_Avg = np.zeros(numSteps)
darkData_y_Avg = np.zeros(numSteps)

arduino = serial.Serial('COM4',9600)
lockin = SR830("GPIB1::15::INSTR")

input("Press enter key to proceed when motor reaches limit")

arduino.write(str.encode(str(wavelengthLimit-wavelengthStart))) # moves to starting position
time.sleep(2) # need to add extra sleep here

# light lock-in measurement function (called in scan function)
def lightMeasure(timeConstant):
    arduino.write(str.encode(str(998))) # open shutter
    lockin.clear()
    lockin.time_constant = timeConstant
    time.sleep(20*timeConstant) # allow reading to settle
    lightData_x.append(lockin.x) # save magnitude and phase to array
    lightData_y.append(lockin.y)
    
# dark lock-in measurement function (called in scan function)
def darkMeasure(timeConstant):
    arduino.write(str.encode(str(999))) # close shutter
    lockin.clear()
    lockin.time_constant = timeConstant
    time.sleep(20*timeConstant) 
    darkData_x.append(lockin.x)
    darkData_y.append(lockin.y)
    
# scan function
def scan(wavelengthStart,wavelengthFinish,wavelengthStep):
    global wavelength
    
    while(wavelength >= wavelengthFinish):
        print("Current wavelength:", wavelength)
        wavelengthList.append(wavelength)
        lightMeasure(timeConstant) # measure with lock-in with shutter open and closed
        darkMeasure(timeConstant)
        arduino.flush()
        arduino.write(str.encode(str(wavelengthStep)))
        time.sleep(wavelengthStep*0.5) # takes approximately 4 s to travel 10 nm. Sleep for 5 s to be safe
        wavelength = wavelength - wavelengthStep

    arduino.write(str.encode(str(wavelength-wavelengthStart))) # return to start
    #arduino.write(str.encode(str(999))) # close shutter
    time.sleep(abs((wavelength-wavelengthStart-wavelengthStep)*0.5)) # allow time to return to start
    wavelength = wavelengthStart
    
def plot(): # make sure it is plotting in the rigt order
    fig, (ax1,ax2) = plt.subplots(1,2)
    fig.suptitle('Photo-modulation spectrum_V1')
    
    ax1.plot(wavelengthList,lightData_x_Avg, label = 'Light data')
    ax1.plot(wavelengthList,darkData_x_Avg, label = 'Dark data')
    ax1.set(xlabel='Wavelength (nm)',ylabel='x voltage (V)')
    ax1.legend()
    
    ax2.plot(wavelengthList,lightData_y_Avg, label = 'Light data')
    ax2.plot(wavelengthList,darkData_y_Avg, label = 'Dark data')
    ax2.set(xlabel='Wavelength (nm)',ylabel='y voltage (V)')
    ax2.legend()
    
    plt.show()
    
def save(): # save the data to a CSV file
    with open(fileName+".csv","w",newline='') as savedFile:
        writer = csv.writer(savedFile)
        writer.writerow(['Wavelength (nm)','Light data x (V)','Dark data x (V)','Light data y (V)','Dark data y (V)'])
        writer.writerows(np.transpose(combineData))
        
# main body of code
for i in range(numMeasurements): # perform multiple scans
    wavelengthList = []
    scan(wavelengthStart,wavelengthFinish,wavelengthStep)   
    
    for j in range(numSteps): # find the running average at each wavelength
        lightData_x_Avg[j] = (lightData_x_Avg[j] + lightData_x[j])/(i+1)
        lightData_y_Avg[j] = (lightData_y_Avg[j] + lightData_y[j])/(i+1)
        darkData_x_Avg[j] = (darkData_x_Avg[j] + darkData_x[j])/(i+1)
        darkData_y_Avg[j] = (darkData_y_Avg[j] + darkData_y[j])/(i+1)
 
    combineData = [wavelengthList,lightData_x_Avg,darkData_x_Avg,lightData_y_Avg,darkData_y_Avg]
    print(combineData)
    print("Light data x (V):", lightData_x_Avg) # print running averages
    print("Light data y (V):", lightData_y_Avg)
    print("Dark data x (V):", darkData_x_Avg)
    print("Dark data y (V):", darkData_y_Avg)
    
    plot() # plot and save the running average thus far
    save() 
    
    lightData_x = []
    lightData_y = []
    darkData_x = []
    darkData_y = []

arduino.close()