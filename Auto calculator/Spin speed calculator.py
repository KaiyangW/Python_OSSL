import numpy as np
from scipy.optimize import curve_fit
import re

def power_law(w, k, alpha):
    # Power law model for spin coating: t = k * w^(-alpha)
    return k * np.power(w, -alpha)

def fit_spin_data(w_list, t_list):
    w_arr = np.array(w_list)
    t_arr = np.array(t_list)
    
    # 1. Provide an initial guess using log-linear regression for better convergence
    # ln(t) = -alpha * ln(w) + ln(k)
    x = np.log(w_arr)
    y = np.log(t_arr)
    slope, intercept = np.polyfit(x, y, 1)
    alpha_guess = -slope
    k_guess = np.exp(intercept)
    
    # 2. Use curve_fit to fit the original power-law model directly
    # This prevents the logarithmic transformation from skewing the absolute error distribution
    popt, _ = curve_fit(power_law, w_arr, t_arr, p0=[k_guess, alpha_guess], maxfev=10000)
    k, alpha = popt
    
    # 3. Calculate Goodness of Fit (R-squared)
    residuals = t_arr - power_law(w_arr, k, alpha)
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((t_arr - np.mean(t_arr))**2)
    
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
    
    return alpha, k, r_squared

def calc_speed(target_t, alpha, k):
    # Inverse function to find target spin speed: w = (t / k)^(-1/alpha)
    if target_t <= 0:
        return 0
    return np.power((target_t / k), -1.0 / alpha)

def main():
    print("\n=== 旋涂参数拟合工具 (优化版) ===")
    print("输入 q 退出\n")
    
    while True:
        print("-" * 40)
        print("格式: 转速 厚度, 转速 厚度 (支持空格、逗号、分号等任意分隔)")
        print("示例: 1000 270; 1150,245 | 1500 180")
        
        data_str = input("\n输入拟合数据: ").strip()
        
        if data_str.lower() == 'q': 
            break
        if not data_str: 
            continue

        try:
            # Robust parsing using regular expressions to extract all numeric values
            # Handles negative signs and decimals, ignoring all non-numeric separators
            numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", data_str)
            
            # Ensure the extracted numbers can form complete pairs
            if len(numbers) % 2 != 0:
                print("\n[!] 错误: 数据未成对。请确保每组输入均包含转速和厚度。\n")
                continue
                
            w_list = [float(numbers[i]) for i in range(0, len(numbers), 2)]
            t_list = [float(numbers[i+1]) for i in range(0, len(numbers), 2)]
            
            # Single data point: assume alpha = 0.5 (q0.5) and solve k from that point
            if len(w_list) == 1:
                alpha = 0.5
                w, t = w_list[0], t_list[0]
                k = t * np.power(w, alpha)  # t = k * w^(-alpha) => k = t * w^alpha
                r_squared = None
                print("\n[单点模式] 已默认挥发指数 Alpha = 0.5 (q0.5)，由该点推算 k。")
            elif len(w_list) < 3:
                print("\n[!] 数据不足，请至少输入三组数据以保证拟合精度（单点除外）。\n")
                continue
            else:
                # Perform the fitting calculation
                alpha, k, r_squared = fit_spin_data(w_list, t_list)
            
            # Validation checks for alpha's physical meaning
            if alpha < 0:
                print(f"\n[!] 警告: Alpha 为负值 ({alpha:.3f})。")
                print("    物理上厚度应随转速增加而减小，请检查输入数据是否有误。")
                continue
                
            if abs(alpha) < 1e-6:
                print(f"\n[!] 警告: Alpha 极其接近 0 ({alpha:.2e})。")
                print("    模型显示厚度与转速无关，当前幂律模型失效。")
                continue
            
            print(f"\n[拟合成功] 当前溶液挥发指数 Alpha = {alpha:.3f}")
            if r_squared is not None:
                print(f"拟合优度 (R²) = {r_squared:.4f}")
                # Warn the user if the fit is poor
                if r_squared < 0.90:
                    print("  -> [!] 提示: R² 较低，请检查是否有离群点，或该溶液是否偏离常规幂律模型。")
            
            target_t_str = input("输入目标厚度 (nm): ").strip()
            if target_t_str.lower() == 'q':
                break
                
            target_t = float(target_t_str)
            target_w = calc_speed(target_t, alpha, k)
            
            # Check if the target speed falls outside the calibration range
            min_w, max_w = min(w_list), max(w_list)
            print("\n" + "="*30)
            print(f" 目标转速: {int(target_w)} RPM")
            print("="*30)
            
            if target_w < min_w or target_w > max_w:
                print(f"\n[!] 范围警告: 目标转速 ({int(target_w)} RPM) 超出校准数据范围 ({int(min_w)} - {int(max_w)} RPM)。")
                print("    外推结果可能存在较大误差，建议补充该区间的测试数据。")
            print("\n")
            
        except Exception as e:
            print(f"\n[!] 数据解析或计算失败。错误信息: {e}\n")

if __name__ == "__main__":
    main()