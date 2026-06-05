import json
import statistics
import os

# Define the filename for saving the last input
DATA_FILE = 'last_input.json'

def run_calculator():
    # Check if the history file exists and load it
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as file:
            try:
                last_numbers = json.load(file)
                print(f"上一次计算的数字是: {last_numbers}")
            except json.JSONDecodeError:
                pass

    while True:
        print("-" * 40)
        # Updated prompt to reflect multiple separators
        user_input = input("请输入至少3个数字 (空格或逗号分隔，输入 'q' 退出): ")
        
        if user_input.lower().strip() == 'q':
            print("退出计算器。")
            break

        try:
            # Step 1: Replace all commas with spaces
            # Step 2: split() without arguments handles any whitespace (spaces, tabs, etc.)
            processed_input = user_input.replace(',', ' ')
            numbers = [float(num) for num in processed_input.split()]
            
            # Enforce the minimum requirement of 3 numbers
            if len(numbers) < 3:
                print("错误: 请至少输入3个数字。")
                continue
            
            # Calculate sample mean and standard deviation
            mean_value = statistics.mean(numbers)
            std_dev = statistics.stdev(numbers)
            
            print(f"平均值 (Mean): {mean_value:.6f}")
            print(f"标准差 (Standard Deviation): {std_dev:.6f}")
            
            # Save the successfully parsed numbers to a JSON file
            with open(DATA_FILE, 'w') as file:
                json.dump(numbers, file)
                
        except ValueError:
            print("输入无效: 请确保只输入数字，并以空格或英文逗号分隔。")

if __name__ == "__main__":
    run_calculator()