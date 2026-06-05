import serial
import time
from pymeasure.instruments.srs import SR830
import numpy as np
import matplotlib.pyplot as plt
import csv
import pyvisa

wavelengthLimit = 852 # upper wavelength limit (measured 20/01/24). Dial says 285

# make sure to convert the frequencies to numbers
wavelengthSet = int(input("Wavelength (nm):")) # the wavelength the measurement will be performed at
frequencyStep = float(input("Frequency step (kHz):"))
frequencyStart = float(input("Start frequency (kHz):"))  # avoid multiples of 50 Hz at low frequency
frequencyFinish = float(input("Finish frequency (kHz):")) # <= 100 kHz
timeConstant = float(input("Time constant (s):")) # sets lock-in time constant
numMeasurements = int(input("Number of measurements:")) # defines the number of repeat sweeps
fileName = input("File name:") # the name of the saved file

frequency = frequencyStart
numSteps = abs(int(((frequencyFinish-frequencyStart)/frequencyStep) + 1))

lightData_x = []
lightData_y = []
darkData_x = []
darkData_y = []

lightData_x_Avg = np.zeros(numSteps) # averages are np arrays to avoid problems
lightData_y_Avg = np.zeros(numSteps)
darkData_x_Avg = np.zeros(numSteps)
darkData_y_Avg = np.zeros(numSteps)

arduino = serial.Serial('COM4',9600) # Arduino
lockin = SR830("GPIB1::15::INSTR") # lock-in

rm = pyvisa.ResourceManager()
funcGen = rm.open_resource("GPIB0::14::INSTR") # function generator

input("Press enter key to proceed when motor reaches limit")

funcGen.write(":OUTP ON") # turns function generator output on

arduino.write(str.encode(str(wavelengthLimit-wavelengthSet))) # moves to measurement wavelength
time.sleep(abs((wavelengthLimit-wavelengthSet)*0.5)) # allow time to get to measurement wavelength


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


# function to enable pump modulation frequency to be swept
def sweep(frequencyStart,frequencyFinish,frequencyStep):
    global frequency
    
    while(frequency <= frequencyFinish):
        
        print("Current frequency:", frequency, "kHz")
        frequencyList.append(frequency)
        
        funcGen.write(":FREQ "+str(frequency)+" kHz") # set the frequency
        
        lightMeasure(timeConstant) # measure with lock-in with shutter open and closed
        darkMeasure(timeConstant)
        
        frequency = frequency + frequencyStep # increase incrementally
    
    frequency = frequencyStart # reset when finished
    
    
def plot(): # make sure it is plotting in the rigt order
    fig, (ax1,ax2) = plt.subplots(1,2)
    fig.suptitle('Photo-modulation lifetime_V1')
    
    ax1.plot(frequencyList,lightData_x_Avg, label = 'Light data')
    ax1.plot(frequencyList,darkData_x_Avg, label = 'Dark data')
    ax1.set(xlabel='Frequency (kHz)',ylabel='x voltage (V)')
    ax1.legend()
    
    ax2.plot(frequencyList,lightData_y_Avg, label = 'Light data')
    ax2.plot(frequencyList,darkData_y_Avg, label = 'Dark data')
    ax2.set(xlabel='Frequency (kHz)',ylabel='y voltage (V)')
    ax2.legend()
    
    plt.show()
    
    
def save(): # save the data to a CSV file
    with open(fileName+".csv","w",newline='') as savedFile:
        writer = csv.writer(savedFile)
        writer.writerow(['Frequency (kHz)','Light data x (V)','Dark data x (V)','Light data y (V)','Dark data y (V)'])
        writer.writerows(np.transpose(combineData))
    
    
# main body of code
for i in range(numMeasurements): # perform multiple sweeps
    frequencyList = []
    sweep(frequencyStart,frequencyFinish,frequencyStep)
    
    # check this as I think it is wrong
    for j in range(numSteps): # find the running average at each wavelength
        lightData_x_Avg[j] = (lightData_x_Avg[j] + lightData_x[j])/(i+1)
        lightData_y_Avg[j] = (lightData_y_Avg[j] + lightData_y[j])/(i+1)
        darkData_x_Avg[j] = (darkData_x_Avg[j] + darkData_x[j])/(i+1)
        darkData_y_Avg[j] = (darkData_y_Avg[j] + darkData_y[j])/(i+1)
 
    combineData = [frequencyList,lightData_x_Avg,darkData_x_Avg,lightData_y_Avg,darkData_y_Avg]
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

funcGen.write(":OUTP OFF") # turns function generator output off
arduino.close()
