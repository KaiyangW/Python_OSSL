import serial
import time

try:
    print("正在连接到 COM3...")
    arduino = serial.Serial('COM3', 9600, timeout=1)
    time.sleep(2) # 等待 Arduino 重启
    
    print("\n--- 激光快门控制中心 ---")
    print("输入 '1' 并回车 : 打开快门")
    print("输入 '0' 并回车 : 关闭快门")
    print("输入 'q' 并回车 : 退出程序")
    print("------------------------\n")

    while True:
        user_input = input("等待指令 (1/0/q): ").strip()

        if user_input == '1':
            arduino.write(b'998\n')
            print(">>> 快门 [打开]")
        elif user_input == '0':
            arduino.write(b'999\n')
            print(">>> 快门 [关闭]")
        elif user_input.lower() == 'q':
            print("退出控制。")
            break
        else:
            print("无效输入，请重新输入。")

except serial.SerialException as e:
    print(f"串口错误，请检查 COM3 是否被占用: {e}")
except KeyboardInterrupt:
    print("\n程序被手动中断。")
finally:
    if 'arduino' in locals() and arduino.is_open:
        arduino.close()
        print("串口已关闭。")