import numpy as np
import matplotlib
matplotlib.use('Agg') 
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
import os
import warnings

# Enable High DPI awareness on Windows
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# ==========================================
#  Core Algorithm: Upper Envelope + Hinge Model
# ==========================================

def hinge_model(x, x_th, k1, k2, b1):
    """
    Continuous piecewise function (Hinge Model).
    x < x_th: Fluorescence region (slope k1)
    x > x_th: Lasing region (slope k2)
    """
    return np.where(x < x_th, 
                    k1 * x + b1, 
                    k1 * x_th + b1 + k2 * (x - x_th))

def filter_upper_envelope_physical(x, y, window_ratio=0.15, keep_ratio=0.2, min_keep=1):
    """
    核心降噪模块：基于物理能量区间的滑动窗口上包络线提取法
    
    【前置条件说明】:
    输入 x 不强制要求升序排列，函数内部会自动进行排序映射，保证物理逻辑的绝对正确，
    避免跳过主流程直接调用时产生隐蔽的误用风险。
    
    【参数敏感性说明】:
    - window_ratio (推荐范围: 0.1 - 0.25): 窗口覆盖的总能量跨度比例。
      * 权衡：窗口过大 -> 局部阈值拐点被全局极值淹没（阈值模糊）；
      * 窗口过小 -> 退化为逐点保留，无法有效滤除激光器散粒噪声（噪声敏感）。
    - keep_ratio (推荐范围: 0.15 - 0.3): 每个窗口保留的高分位点比例。
      * 若激光器极其不稳定（散弹噪声多），可降低至 0.15；相对稳定可提高至 0.3。
      
    【解决的痛点】:
    1. 消除边界效应：使窗口中心滑过每一个数据点，边缘数据得到公平评估。
    2. 解决物理失真：按“物理能量间距”而非“点数”划分窗口，采样密集与稀疏区域筛选标准一致。
    """
    n_total = len(x)
    # 1. 强制排序（保护机制）
    sort_idx = np.argsort(x)
    x_sorted = x[sort_idx]
    y_sorted = y[sort_idx]
    
    inlier_mask_sorted = np.zeros(n_total, dtype=bool)
    
    if n_total < 5:
        return np.ones(n_total, dtype=bool), {"status": "Too few points"}
        
    # 计算物理能量跨度与对应的窗口宽度
    x_range = x_sorted[-1] - x_sorted[0]
    window_width = x_range * window_ratio
    
    # 2. 物理域滑动窗口：让窗口中心滑过每一个真实存在的能量点
    for i in range(n_total):
        center_x = x_sorted[i]
        # 定义当前物理窗口的左右边界
        left_val = center_x - window_width / 2
        right_val = center_x + window_width / 2
        
        # 使用 searchsorted 快速定位窗口内的数据点索引
        idx_start = np.searchsorted(x_sorted, left_val, side='left')
        idx_end = np.searchsorted(x_sorted, right_val, side='right')
        
        window_indices = np.arange(idx_start, idx_end)
        if len(window_indices) == 0: continue
            
        window_y = y_sorted[window_indices]
        
        # 动态计算当前物理窗口应保留的点数
        keep_k = max(min_keep, int(len(window_indices) * keep_ratio))
        keep_k = min(keep_k, len(window_indices)) 
        
        # 提取当前窗口内 y 值最大的点的相对索引，并映射回全局排序索引
        top_local_idx = np.argsort(window_y)[-keep_k:]
        global_top_idx = window_indices[top_local_idx]
        
        # 只要该点在任何一个以其他点为中心的窗口中胜出，即保留为 inlier
        inlier_mask_sorted[global_top_idx] = True

    # 将 inlier 标记映射回原始未排序的数据顺序
    inlier_mask = np.zeros(n_total, dtype=bool)
    inlier_mask[sort_idx] = inlier_mask_sorted
    
    # 3. 滤波质量诊断 (Diagnostics)
    x_kept = x_sorted[inlier_mask_sorted]
    retention_rate = np.sum(inlier_mask) / n_total
    
    # 将能量轴分为 5 个诊断区间，检查是否有区间被"全军覆没"
    bins = np.linspace(x_sorted[0], x_sorted[-1], 6)
    hist_total, _ = np.histogram(x_sorted, bins=bins)
    hist_kept, _ = np.histogram(x_kept, bins=bins)
    
    # 找出原始数据有值，但滤波后被全部剔除的区间数
    empty_critical_bins = np.sum((hist_total > 0) & (hist_kept == 0))
    
    diagnostics = {
        'retention_rate': retention_rate,
        'empty_critical_bins': empty_critical_bins,
        'warning': "Warning: Over-filtering detected! Some energy intervals lost all data." if empty_critical_bins > 0 else "OK"
    }
    
    return inlier_mask, diagnostics

def find_threshold_robust_atml(x_raw, y_raw):
    # 根据数据点总数稍微做一点自适应：数据量大时缩小比例，提高分辨率
    adaptive_window_ratio = 0.15 if len(x_raw) < 50 else 0.10
    
    # 调用新版基于物理域的滤波算法
    inlier_mask, filter_stats = filter_upper_envelope_physical(
        x_raw, y_raw, 
        window_ratio=adaptive_window_ratio, 
        keep_ratio=0.2, 
        min_keep=1
    )
    
    # 如果有严重过度剔除，打印诊断警告
    if filter_stats.get('empty_critical_bins', 0) > 0:
        warnings.warn(f"[ATML Diagnostic] {filter_stats['warning']}")
        
    outlier_mask = ~inlier_mask
    
    x_clean = x_raw[inlier_mask]
    y_clean = y_raw[inlier_mask]
    x_outliers = x_raw[outlier_mask]
    y_outliers = y_raw[outlier_mask]

    if len(x_clean) < 4: 
        return None 

    # 初始参数估计
    sort_idx = np.argsort(x_clean)
    x_clean = x_clean[sort_idx]
    y_clean = y_clean[sort_idx]

    x_min, x_max = x_clean[0], x_clean[-1] 
    y_min, y_max = np.min(y_clean), np.max(y_clean)
    
    n_fluo = max(3, int(len(x_clean) * 0.3))
    try:
        p_fluo = np.polyfit(x_clean[:n_fluo], y_clean[:n_fluo], 1)
        k1_guess, b1_guess = p_fluo[0], p_fluo[1]
    except:
        k1_guess, b1_guess = 0, y_min

    n_lasing = max(3, int(len(x_clean) * 0.4))
    try:
        p_lasing = np.polyfit(x_clean[-n_lasing:], y_clean[-n_lasing:], 1)
        k2_guess = p_lasing[0]
    except:
        k2_guess = (y_max - y_min) / (x_max - x_min + 1e-9)

    x_th_guess = (x_min + x_max) / 2
    p0 = [x_th_guess, k1_guess, k2_guess, b1_guess]
    
    bounds = (
        [x_min, -np.inf, 0,      -np.inf], 
        [x_max, np.inf,  np.inf, np.inf]   
    )
    
    try:
        popt, pcov = curve_fit(hinge_model, x_clean, y_clean, p0=p0, bounds=bounds)
    except Exception:
        return {
            'fit_success': False,
            'x_used': x_clean,
            'y_used': y_clean,
            'x_outliers': x_outliers,
            'y_outliers': y_outliers,
            'filter_stats': filter_stats
        }
        
    x_th_fit, k1_fit, k2_fit, b1_fit = popt
    
    x_th_err = np.nan
    if pcov is not None:
        try:
            x_th_err = np.sqrt(np.diag(pcov))[0]
        except: pass

    return {
        'fit_success': True,
        'threshold': x_th_fit,
        'threshold_err': x_th_err,
        'params': popt,
        'x_used': x_clean,
        'y_used': y_clean,
        'x_outliers': x_outliers,
        'y_outliers': y_outliers,
        'trimmed_count': len(x_outliers),
        'filter_stats': filter_stats # 将诊断数据向上传递
    }

# ==========================================
#  Interface (Called by Main Program)
# ==========================================

def run_threshold_analysis(x_data, y_data, save_folder, file_prefix):
    # 排序与去 NaN
    sorted_indices = np.argsort(x_data)
    x_sorted = np.array(x_data)[sorted_indices]
    y_sorted = np.array(y_data)[sorted_indices]
    
    mask = np.isfinite(x_sorted) & np.isfinite(y_sorted)
    x_valid = x_sorted[mask]
    y_valid = y_sorted[mask]

    if len(x_valid) < 5:
        return {"status": "Not enough data points", "threshold": np.nan, "error": np.nan, "r_squared": np.nan}

    # 执行 ATML 稳健拟合
    res = find_threshold_robust_atml(x_valid, y_valid)
    
    if res is None:
        return {"status": "Critical Error", "threshold": np.nan, "error": np.nan, "r_squared": np.nan}
    # ==========================================
    # 拦截拟合失败的情况，画出原始数据并高亮警告
    # ==========================================
    if not res.get('fit_success', False):
        fig, ax = plt.subplots(figsize=(12, 8), dpi=200)
        
        if len(res['x_outliers']) > 0:
            ax.scatter(res['x_outliers'], res['y_outliers'], c='red', marker='x', s=120, linewidths=2, label='Filtered Outliers (Drop-outs)')
        ax.scatter(res['x_used'], res['y_used'], c='black', s=100, label='Upper Envelope Data (Unfittable)')
        
        ax.set_title(f"MANUAL REVIEW REQUIRED: {file_prefix}\n[Fit Failed to Converge]", fontsize=20, color='red', pad=20, fontweight='bold')
        ax.set_xlabel('Pump Energy Density (uJ/cm2)', fontsize=16)
        ax.set_ylabel('Integrated Intensity (arb. units)', fontsize=16)
        ax.tick_params(axis='both', which='major', labelsize=14)
        ax.legend(fontsize=14, loc='upper left')
        ax.grid(True, linestyle=':', alpha=0.6)
        
        # 保存一张带有醒目标签的图片，方便你事后一眼扫出哪些需要手动处理
        img_path = os.path.join(save_folder, f"ATML_FAILED_Plot_{file_prefix}.png")
        fig.savefig(img_path, dpi=100, bbox_inches='tight')
        plt.close(fig)
        
        return {"status": "Fit failed to converge", "threshold": np.nan, "error": np.nan, "slope_ratio": np.nan, "r_squared": np.nan}
    # ==========================================
    # 如果拟合成功，画图并保存数据
    # ==========================================
    x_th = res['threshold']
    x_th_err = res['threshold_err']
    k1, k2, b1 = res['params'][1], res['params'][2], res['params'][3]
    
    x_used = res['x_used']
    y_used = res['y_used']
    x_outliers = res['x_outliers']
    y_outliers = res['y_outliers']
    
    # 仅使用 Clean 数据计算 R_squared
    y_fit_eval = hinge_model(x_used, x_th, k1, k2, b1)
    residuals = y_used - y_fit_eval
    ss_res = np.sum(residuals**2)
    ss_tot = np.sum((y_used - np.mean(y_used))**2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else np.nan
    
    # --- 画图部分 ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(19.2, 14.4), dpi=600, 
                                   gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
    plt.subplots_adjust(hspace=0.05) 
    
    # 1. 绘制 滤除的废点 (红色打叉)
    if len(x_outliers) > 0:
        ax1.scatter(x_outliers, y_outliers, c='red', marker='x', s=120, linewidths=2, label='Filtered Outliers (Drop-outs)')
    
    # 2. 绘制 选出的上包络线好点 (黑色圆点)
    ax1.scatter(x_used, y_used, c='black', s=100, label='Upper Envelope Data Used for Fit')
    
    # 3. 绘制 Hinge 模型曲线
    x_plot = np.linspace(min(x_valid), max(x_valid), 200)
    y_plot = hinge_model(x_plot, x_th, k1, k2, b1)
    y_plot[y_plot < 0] = 0 # 物理截断
    ax1.plot(x_plot, y_plot, 'b-', linewidth=3, label='Hinge Fit Model')
    
    # 4. 标记阈值
    ax1.axvline(x=x_th, color='green', linestyle='--', alpha=0.8, linewidth=2)
    y_marker = max(0, hinge_model(x_th, x_th, k1, k2, b1))
    ax1.plot(x_th, y_marker, 'g*', markersize=25, label='Threshold')
    
    # 标题与排版
    slope_ratio = k2/k1 if k1 > 1e-9 else 9999
    if k1 < 0: slope_ratio = np.inf 
    err_str = f"± {x_th_err:.2e}" if (np.isfinite(x_th_err) and x_th_err > 0) else ""
    
    title_text = (f"ATML Fit (Upper Envelope Filtered): {file_prefix}\n"
                  f"Threshold = {x_th:.4e} {err_str} uJ/cm2\n"
                  f"Slope Efficiency Ratio = {slope_ratio:.1f}  |  Clean R$^2$ = {r_squared:.4f}")
    
    ax1.set_title(title_text, fontsize=20, pad=20)
    ax1.set_ylabel('Integrated Intensity (arb. units)', fontsize=16)
    ax1.tick_params(axis='both', which='major', labelsize=14)
    ax1.legend(fontsize=14, loc='upper left')
    ax1.grid(True, linestyle=':', alpha=0.6)
    
    # --- 残差图 ---
    ax2.scatter(x_used, residuals, c='blue', alpha=0.6, s=80, label='Residuals (Upper Envelope)')
    ax2.axhline(0, color='black', linestyle='--', linewidth=2) 
    ax2.set_xlabel('Pump Energy Density (uJ/cm$^2$)', fontsize=16)
    ax2.set_ylabel('Residuals', fontsize=16)
    ax2.tick_params(axis='both', which='major', labelsize=14)
    ax2.legend(fontsize=14, loc='upper left')
    ax2.grid(True, linestyle=':', alpha=0.6)
    
    # 保存输出
    img_path = os.path.join(save_folder, f"ATML_Fit_Plot_{file_prefix}.png")
    fig.savefig(img_path, dpi=100, bbox_inches='tight')
    plt.close(fig)

    return {"status": f"Success: {x_th:.2e}", "threshold": x_th, "error": x_th_err, "slope_ratio": slope_ratio, "r_squared": r_squared}