/*
 * Monochromator Control Firmware V5.1 (Fixed Speed)
 * ------------------------------------------------------
 * 1. Fixed Motor Noise: Reverted steps-per-revolution to 100.
 * 2. Logic: Accepts RAW STEPS from Python.
 */

#include <Stepper.h>
#include <Servo.h>

Servo shutterServo;

const int PIN_IN1 = 8;
const int PIN_IN2 = 9;
const int PIN_IN3 = 10;
const int PIN_IN4 = 11;
const int PIN_SERVO = 3;

// [恢复原厂设置] 
// 你的电机原来配置的是 100 步/转。
// 我们把它改个名字叫 STEPS_PER_REV 以免混淆，但数值必须是 100。
const int STEPS_PER_REV = 100; 

const int SHUTTER_OPEN_CMD = 998;
const int SHUTTER_CLOSE_CMD = 999;
const int ANGLE_OPEN = 145;
const int ANGLE_CLOSE = 35;

// [恢复原厂设置] 这里改回使用 100，确保速度和以前一模一样
Stepper monoStepper(STEPS_PER_REV, PIN_IN1, PIN_IN2, PIN_IN3, PIN_IN4);

void setup() {
  Serial.begin(9600); 
  delay(1000); 
  
  shutterServo.attach(PIN_SERVO);
  
  // 保持原来的转速设定
  monoStepper.setSpeed(200); 
  
  shutterServo.write(ANGLE_CLOSE);
  releaseMotor();
}

void loop() {
  if (Serial.available() > 0) {
    long command = Serial.parseInt(); // 接收 Python 发来的“步数”

    if (command == SHUTTER_OPEN_CMD) {
      shutterServo.write(ANGLE_OPEN);
    }
    else if (command == SHUTTER_CLOSE_CMD) {
      shutterServo.write(ANGLE_CLOSE);
    }
    else {
      if (command != 0) {
        moveSteps(command); 
      }
    }
  }
}

void moveSteps(long steps_to_move) {
  long total_steps_needed = steps_to_move; 

  // Determine direction
  int direction = 1;
  if (total_steps_needed < 0) {
    direction = -1;
    total_steps_needed = -total_steps_needed; 
  }

  // 分块移动逻辑保持不变
  const int CHUNK_SIZE = 30000;
  while (total_steps_needed > 0) {
    int steps_this_turn = 0;
    if (total_steps_needed > CHUNK_SIZE) {
      steps_this_turn = CHUNK_SIZE;
    } else {
      steps_this_turn = (int)total_steps_needed;
    }
    
    monoStepper.step(steps_this_turn * direction);
    total_steps_needed -= steps_this_turn;
  }
  
  releaseMotor();
}

void releaseMotor() {
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
  digitalWrite(PIN_IN3, LOW);
  digitalWrite(PIN_IN4, LOW);
}