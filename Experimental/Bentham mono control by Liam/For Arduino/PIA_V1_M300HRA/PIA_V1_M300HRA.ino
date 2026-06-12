#include <Stepper.h>

#include <Servo.h>
Servo servo;

// Arduino to stepper driver: pin 8 - input 1, pin 9 - input 2, pin 10 - input 3, pin 11 - input 4
// Stepper driver to stepper cable: 1st - green, 2nd connector - red, 3rd connector - yellow, 4th connector - blue, 5th connector - black

// Arduino to servo: 5 V - red wire, GND - brown wire, pin 3 - orange wire

const float STEPS = 100; // 100 steps per nm
int i = 0;
int j = 0;

Stepper steppermotor(STEPS, 8, 9, 10, 11);

void setup()
{
  Serial.begin(9600); 
  delay(1000);
  servo.attach(3);
  steppermotor.setSpeed(200);
}

void loop()
{ 
    while (Serial.available()>0){
    j = 0;
    int wavelengthStep = Serial.parseInt(); // moves integer number of nm when command is received. If 998 or 999 is recevied, operate the shutter and break loop
    
    // command to open/close the shutter
    if (wavelengthStep == 998){ // open
      servo.write(145);
      break;
    }
    if (wavelengthStep == 999){ // close
      servo.write(35);
      break;
    }
    // command to move desired number of steps
    moveStep(wavelengthStep);
    }
}

void moveStep(const float wavelengthStep){ // move the desired number of steps
  while (j < 1){
    steppermotor.step(-STEPS*wavelengthStep);
    j = j + 1;
    }
}


    
