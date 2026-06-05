import numpy as np
from scipy.optimize import fsolve
import cmath
import math

'''
This code is based one the exact solution proposed in paper: Tsuchiya, Y. et al. Exact Solution 
of Kinetic Analysis for Thermally Activated Delayed Fluorescence Materials. 
J. Phys. Chem. A 125, 8074–8089 (2021).

'''

def calculate_tadf_rates_direct(PLQY_total, Phi_PF, Phi_DE, tau_p_ns, tau_d_ns, R_DE_DF=1.0):
    """
    直接使用校正后的 Phi_PF 和 Phi_DE，跳过校正步骤。
    """
    tau_p = tau_p_ns * 1e-9
    tau_d = tau_d_ns * 1e-9
    k_p = 1.0 / tau_p
    k_d = 1.0 / tau_d

    # 直接使用传入的 Phi_PF 和 Phi_DE，不进行任何校正
    # 注意：论文中 Phi_DF 用于 k_ISC 初始猜测，此处可用 Phi_DE 近似，因 R_DE_DF=1
    Phi_DF = Phi_DE * R_DE_DF

    # 三次方程求解 k_S 的函数
    def solve_kS(k_isc):
        d = -(k_isc + 3*k_p + k_d*(1 - Phi_DE/Phi_PF))
        e = (k_isc * k_d * (1 - (Phi_DE/Phi_PF)*(2 - R_DE_DF))
             - k_p*(3*k_p + 2*d) - (k_d**2)*Phi_DE/Phi_PF)
        f = -(k_isc + k_p)*(k_p + k_d)*(k_p - k_d*Phi_DE/Phi_PF)

        # 使用 numpy.roots 解三次方程，避免复数立方根选枝问题
        coeffs = [1, d, e, f]
        roots = np.roots(coeffs)
        
        # 提取所有根的实部，放宽虚部限制，避免因数值精度产生的微小虚部导致物理根被错误剔除
        real_parts = sorted([r.real for r in roots])
        
        # 物理约束：k_RISC = k_p - k_S > 0，因此 k_S 必须严格小于 k_p
        # 选取所有小于 k_p 的根中最大的一个（最接近 k_p 但在其下方）
        below_kp = [r for r in real_parts if r < k_p]
        if below_kp:
            k_S = max(below_kp)
        else:
            # 若所有根均大于 k_p（极端情况），取最接近 k_p 的根作为应急备选
            k_S = min(real_parts, key=lambda x: abs(x - k_p))
        return k_S

    from scipy.optimize import minimize_scalar

    # 定义优化目标函数（即残差绝对值）
    def objective(k_isc):
        if k_isc <= 0 or k_isc >= k_p:
            return 1e9
        
        k_S = solve_kS(k_isc)

        a = k_d * (Phi_DE/Phi_PF) * R_DE_DF - k_p + k_S
        b = (k_p - k_S)*(k_S - k_p - k_d) - a*k_S
        c = ((k_p - k_S)**2) * (k_S - k_d)

        if abs(a) < 1e-15:
            k_isc_new = -c / b if b != 0 else 1e9
        else:
            det = b**2 - 4*a*c
            if det < 0:
                return 1e9
            k_isc_new = (-b - math.sqrt(det)) / (2*a)
            
        return abs(k_isc_new - k_isc)

    # 使用 minimize_scalar 进行有界搜索，避免 fsolve 陷入伪固定点或局部死胡同
    # 物理上 k_ISC 的解域在 (0, k_p) 之间，我们在此区间内寻找最接近的自洽解
    res = minimize_scalar(objective, bounds=(k_p * 1e-4, k_p * 0.9999), method='bounded')
    k_isc = float(res.x)
    k_S = solve_kS(k_isc)

    k_r_S = k_p * Phi_PF
    k_nr_S = k_S - k_r_S - k_isc
    k_RISC = ((k_p - k_S)*(k_S - k_d)) / k_isc

    # 磷光相关速率（R_DE_DF=1 时 k_r_T 为 0）
    den_T = k_isc + k_p - k_S
    k_r_T = (k_p * k_d * Phi_DE * (1 - R_DE_DF)) / den_T if den_T != 0 else 0.0
    k_nr_T = k_p + k_d - k_S - k_RISC - k_r_T

    return {
        'k_S': k_S,
        'k_r_S': k_r_S,
        'k_nr_S': k_nr_S,
        'k_ISC': k_isc,
        'k_RISC': k_RISC,
        'k_r_T': k_r_T,
        'k_nr_T': k_nr_T
    }

def format_rate(val):
    if val <= 0:
        return f"{val:.3e} s^-1"
    tau = 1.0 / val
    if tau < 1e-6:
        unit_str = f"({tau*1e9:.2f} ns)"
    elif tau < 1e-3:
        unit_str = f"({tau*1e6:.2f} μs)"
    else:
        unit_str = f"({tau*1e3:.2f} ms)"
    return f"{val:.3e} s^-1 {unit_str}"

if __name__ == "__main__":
    PLQY_total = 0.8723         
    Phi_PF = 0.54
    Phi_DE = 0.3323             
    tau_p_ns = 2.82        
    tau_d_ns = 525.46       

    print("计算精确解...")
    print(f"输入: PLQY={PLQY_total}, Φ_PF={Phi_PF}, Φ_DE={Phi_DE}, τ_p={tau_p_ns} ns, τ_d={tau_d_ns} ns")
    print("假定 R_DE_DF = 1.0 (无磷光)\n")

    rates = calculate_tadf_rates_direct(PLQY_total, Phi_PF, Phi_DE, tau_p_ns, tau_d_ns, R_DE_DF=1.0)

    print("计算结果:")
    for k, v in rates.items():
        print(f"  {k:8}: {format_rate(v)}")