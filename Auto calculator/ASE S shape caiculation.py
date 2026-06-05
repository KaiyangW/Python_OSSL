import numpy as np
from scipy.integrate import solve_bvp
import os

# ==========================================
# 1. 物理参数设置
# ==========================================
# 针对你的 TADF 有机激光研究，请根据你的材料修改以下参数
params = {
    'N_tot': 3.0e18,       # 总分子浓度 (cm^-3)
    'L': 1.0,              # 增益介质长度 (cm)
    'sigma_em': 1.8e-16,   # 受激辐射截面 (cm^2) at emission wavelength
    'sigma_abs': 0.1e-16,  # 基态吸收/自吸收截面 (cm^2)
    'tau': 5.5e-9,         # 荧光寿命 (s)
    'geom_factor': 1e-4,   # 几何因子
}

# ==========================================
# 2. 微分方程定义
# ==========================================

def ase_ode(x, y, p, W_pump):
    """
    y[0] = I_plus (向右传播的光强)
    y[1] = I_minus (向左传播的光强)
    """
    I_plus = y[0]
    I_minus = y[1]
    
    # 避免负值导致的数值不稳定
    I_plus = np.maximum(I_plus, 1e-30)
    I_minus = np.maximum(I_minus, 1e-30)
    I_total = I_plus + I_minus
    
    # 计算激发态布居数 N1 (稳态近似 - Ganiel Paper Eq. 16)
    numerator = W_pump + params['sigma_abs'] * I_total
    denominator = W_pump + (1.0/params['tau']) + (params['sigma_em'] + params['sigma_abs']) * I_total
    
    N1 = params['N_tot'] * (numerator / denominator)
    N0 = params['N_tot'] - N1
    
    # 净增益系数
    gain_coeff = params['sigma_em'] * N1 - params['sigma_abs'] * N0
    
    # 自发辐射源项
    spont_term = (N1 / params['tau']) * params['geom_factor']
    
    # 构建方程: dI/dx
    dIp_dx = gain_coeff * I_plus + spont_term
    dIm_dx = - (gain_coeff * I_minus + spont_term) # 反向传播，导数为负
    
    return np.vstack((dIp_dx, dIm_dx))

def bc(ya, yb, p, W_pump):
    # 边界条件：左端向右光为0，右端向左光为0
    return np.array([ya[0], yb[1]])

# ==========================================
# 3. 计算主循环
# ==========================================

def calculate_s_curve():
    print("开始计算 ASE S-Curve (Ganiel Model)...")
    
    # 扫描泵浦速率 W (10^3 到 10^9 s^-1)
    # 对应功率密度大约在 kW/cm2 到 MW/cm2 量级 (取决于吸收截面)
    W_values = np.logspace(4, 9, 40) 
    
    results = [] # 存储结果 [W, I_out]
    
    # 初始网格和猜测
    x_mesh = np.linspace(0, params['L'], 50)
    y_guess = np.zeros((2, x_mesh.size))
    
    success_count = 0
    
    for i, W in enumerate(W_values):
        fun_w = lambda x, y, p=None: ase_ode(x, y, p, W)
        bc_w = lambda ya, yb, p=None: bc(ya, yb, p, W)
        
        # 求解 BVP
        # max_nodes 增加以应对高增益时的陡峭梯度
        res = solve_bvp(fun_w, bc_w, x_mesh, y_guess, tol=1e-3, max_nodes=10000)
        
        if res.success:
            I_out = res.y[0][-1]
            results.append([W, I_out])
            
            # 更新网格和猜测解以用于下一个点 (关键步骤)
            x_mesh = res.x 
            y_guess = res.y
            
            success_count += 1
            if i % 10 == 0:
                print(f"进度: W = {W:.1e}, I_out = {I_out:.1e}")
        else:
            print(f"Warning: W={W:.1e} 处不收敛，已跳过。")

    print(f"计算完成。成功点数: {success_count}/{len(W_values)}")
    return np.array(results)

# ==========================================
# 4. 数据保存
# ==========================================

if __name__ == "__main__":
    data = calculate_s_curve()
    
    # === 指定保存路径 ===
    # 使用 raw string (r"...") 处理 Windows 路径中的反斜杠
    save_dir = r"C:\My files\Google drive sync\St Andrews\Data"
    
    # 如果文件夹不存在，自动创建
    if not os.path.exists(save_dir):
        try:
            os.makedirs(save_dir)
            print(f"文件夹不存在，已自动创建: {save_dir}")
        except OSError as e:
            print(f"无法创建文件夹，将保存在当前目录。错误: {e}")
            save_dir = "."

    filename = os.path.join(save_dir, "ase_s_curve_data.csv")
    
    # 保存为 CSV 格式
    # Col 1: Pump Rate W (s^-1) -> Log-Log图的X轴
    # Col 2: Output Flux I (photons cm^-2 s^-1) -> Log-Log图的Y轴
    header = "Pump_Rate_W(s^-1), Output_Intensity_I(photons_cm-2_s-1)"
    np.savetxt(filename, data, delimiter=",", header=header, comments='')
    
    print(f"\n数据已成功保存至:\n{filename}")