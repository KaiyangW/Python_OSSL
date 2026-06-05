import pandas as pd
import numpy as np

def align_baseline_to_one(df, x_col='X', y_col='Y'):
    """
    处理 TRPL 数据的基线。
    寻找脉冲上升沿之前的纯暗背景区域，计算其基线值，
    将数据的基线扣除后，统一平移并截断至 y=1，以完美适配 Log 坐标系的对比图。
    
    参数:
        df: 包含 TRPL 数据的 pandas DataFrame。
        x_col: 时间列的列名，默认为 'X'。
        y_col: 计数/强度列的列名，默认为 'Y'。
        
    返回:
        df_processed: 处理后的 DataFrame (基线已被平移且抹平至 y=1)。
        baseline: 计算出的原始基线中位数值 (保留此返回值以便查错或记录)。
    """
    # 复制数据，防止直接修改引发链式赋值警告或污染原始数据
    df_processed = df.copy()
    y_data = df_processed[y_col].values
    
    # 1. 寻找峰值并设定阈值 (设定为最大值的 2% 作为上升沿判定标准)
    max_y = np.max(y_data)
    threshold = max_y * 0.02
    
    # 2. 寻找上升沿 (第一个超过阈值的点的索引)
    over_threshold_indices = np.where(y_data > threshold)[0]
    
    if len(over_threshold_indices) == 0:
        # 极小概率事件：如果没有找到上升沿（数据全平或异常），直接把全局平移到 >= 1
        min_y = np.min(y_data)
        y_new = y_data - min_y + 1
        y_new[y_new < 1] = 1
        df_processed[y_col] = y_new
        return df_processed, min_y
        
    rise_idx = over_threshold_indices[0]
    
    # 3. 选取纯暗计数区间计算基线 (使用中位数，忽略单点散粒噪声)
    # 留出 5 个点的安全缓冲，避免把真实的瞬发荧光信号算进基线
    if rise_idx > 55:
        background_region = y_data[rise_idx - 55 : rise_idx - 5]
        baseline = np.median(background_region)
    elif rise_idx > 5:
        background_region = y_data[0 : rise_idx - 5]
        baseline = np.median(background_region)
    else:
        # 如果峰值太靠前，没有足够的背景点，取整体最小值作为后备方案
        baseline = np.min(y_data)
        
    # 4. 执行扣除，并将无信号/背景区域统一平移到 y=1 的位置
    # 数学逻辑：新 Y = 原始 Y - 基线计数 + 1
    y_new = y_data - baseline + 1
    
    # 5. 抹平底噪波动：把所有因噪声波动导致小于 1 的值强制设为 1
    # 这将确保在 log-Y 坐标下，背景是一条完美的直线
    y_new[y_new < 1] = 1
    
    df_processed[y_col] = y_new
    
    return df_processed, baseline

if __name__ == "__main__":
    # 这里是一个简单的自测代码块。只有当你直接运行这个脚本时才会执行。
    # 当被主脚本 import 时，这部分不会运行。
    print("TRPL baseline processor is ready to be imported.")