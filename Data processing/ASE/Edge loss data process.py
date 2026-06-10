import os
import sys
import pandas as pd
import numpy as np
import tkinter as tk
from tkinter import filedialog
import ctypes
import re
import traceback

_READER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _READER_ROOT not in sys.path:
    sys.path.insert(0, _READER_ROOT)

from Read_data_unified import read_grid

# -----------------------------------------------------------------------------
# 有机激光 ASE Edge Loss 数据处理脚本 (v2.0)
# 功能：
# 1. 优先扫描子文件夹中的 spectrum.csv 进行批量合并。
# 2. 若无子文件夹，则扫描当前目录下的 spectrum.csv。
# 3. 输出包含距离(归零后)、峰值强度、峰值波长的Excel。
# -----------------------------------------------------------------------------

# Enable High DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def find_files_fuzzy(folder, keywords):

    matches = []
    try:
        files = os.listdir(folder)
        for f in files:
            if f.startswith('.'): continue 
            if all(re.search(k, f, re.IGNORECASE) for k in keywords) and f.lower().endswith('.csv'):
                matches.append(os.path.join(folder, f))
    except Exception:
        return []
    return sorted(matches)

def process_single_csv(file_path, label_name):
    """
    处理单个 spectrum.csv 文件的核心逻辑
    """
    try:
        # 此格式前 2 列是元数据（第 0 列为距离），波长轴/光谱数据从第 3 列开始
        grid = read_grid(file_path, layout="ase_spec_matrix", transpose=False, meta_rows=2)
        wavelength_axis = grid.col_values.astype(float)
        int_matrix = grid.data.astype(float)
        frame_meta = np.asarray(grid.meta.get("frame_metadata"), dtype=object)

        if int_matrix.shape[0] == 0:
            print(f"警告: {label_name} 数据为空")
            return None

        distances = pd.to_numeric(pd.Series(frame_meta[:, 0]), errors='coerce').to_numpy(dtype=float)
        base_distance = distances[0]
        current_data = []
        
        # [修改] 插入单位行，增加一列 ln(a.u.)
        current_data.append(["mm", "Integrated a.u.", "ln(a.u.)", "nm"])

        last_valid_wavelength = None # 用于记录上一个有效的波长
        low_int_skip_count = 0      # 低强度跳过计数

        for i in range(int_matrix.shape[0]):
            distance = distances[i] - base_distance
            intensity_data = int_matrix[i]
            
            # 1. 寻找波峰波长
            max_idx = np.argmax(intensity_data)
            peak_wavelength = wavelength_axis[max_idx]

            # 2. 计算积分强度 (波峰 -10nm 到 +10nm)
            mask = (wavelength_axis >= peak_wavelength - 10) & (wavelength_axis <= peak_wavelength + 10)
            integrated_intensity = np.sum(intensity_data[mask])

            # 3. 自动跳过算法 (规则 1：积分强度 < 500，至多跳过 3 个)
            if integrated_intensity < 500 and low_int_skip_count < 3:
                low_int_skip_count += 1
                print(f"  [跳过] {label_name} 距离 {distance:.3f}mm: 积分强度 {integrated_intensity:.1f} < 500 (跳过计数: {low_int_skip_count}/3)。")
                continue

            # 4. 波长突变检查 (规则 2：突增 > 30nm)
            if last_valid_wavelength is not None:
                if (peak_wavelength - last_valid_wavelength) > 30:
                    print(f"  [跳过] {label_name} 距离 {distance:.3f}mm: 波长突增 ({last_valid_wavelength:.1f} -> {peak_wavelength:.1f}nm)，判定为作废数据。")
                    continue
            
            # 计算自然对数 (对积分强度取对数)
            if integrated_intensity > 0:
                ln_intensity = np.log(integrated_intensity)
            else:
                ln_intensity = np.nan
            
            # 只有通过筛选的数据才会记录
            current_data.append([distance, integrated_intensity, ln_intensity, peak_wavelength])
            last_valid_wavelength = peak_wavelength
        
        # 如果筛选后除了表头没数据了
        if len(current_data) <= 1:
            print(f"警告: {label_name} 经过筛选后无有效数据")
            return None

        # [修改] 更新列名
        cols = [
            f"{label_name}_Dist", 
            f"{label_name}_IntSum", 
            f"{label_name}_Ln_IntSum", 
            f"{label_name}_Wave"
        ]
        return pd.DataFrame(current_data, columns=cols)

    except Exception as e:
        traceback.print_exc()
        print(f"处理 {label_name} 时出错: {e}")
        return None

def process_laser_data_smart():
    # 1. 设置目标路径 (改为弹窗选择)
    root = tk.Tk()
    root.withdraw()  # 隐藏主窗口
    base_path = filedialog.askdirectory(title="选择包含 spectrum.csv 的数据文件夹")
    
    if not base_path:
        print("未选择文件夹，程序退出。")
        return

    dfs_to_combine = []
    print(f"正在分析路径: {base_path}")

    # 2. 逻辑分支：递归扫描包含 spec 的 CSV 文件
    # 使用 os.walk 进行深度递归搜索 (移植自 ASE Threshold 脚本逻辑)
    folders_to_process = []
    for current_root, dirs, files in os.walk(base_path):
        # 查找包含 'spec' 的 csv，且排除 'extract' 和 'process'
        spec_files = find_files_fuzzy(current_root, ["spec"])
        spec_files = [sf for sf in spec_files if "extract" not in sf.lower() and "process" not in sf.lower()]
        
        if spec_files:
            # 如果是根目录，使用根目录文件夹名；否则使用当前子文件夹名
            label = os.path.basename(current_root)
            if not label or current_root == base_path:
                label = os.path.basename(base_path)
            
            folders_to_process.append((current_root, label, spec_files[0]))

    if folders_to_process:
        print(f"检测到 {len(folders_to_process)} 个有效数据文件夹，开始处理...")
        for folder_path, label, file_path in folders_to_process:
            df = process_single_csv(file_path, label)
            if df is not None:
                dfs_to_combine.append(df)
                print(f"  -> 已提取: {label}")
    else:
        print("错误: 在当前目录及其子目录中均未找到有效的 spectrum CSV 文件。")

    # 3. 保存数据
    if dfs_to_combine:
        print("-" * 30)
        print("正在合并并保存 Excel...")
        
        # 横向拼接
        final_df = pd.concat(dfs_to_combine, axis=1)
        output_path = os.path.join(base_path, "Edge loss test_combined.xlsx")
        
        # 使用 ExcelWriter 写入，以便在顶部添加说明文字
        with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
            # 1. 编写说明信息
            instructions = [
                ["ASE Edge Loss 数据处理说明"],
                ["1. 拟合方法：使用 Dist (X轴) 和 Ln_Int (Y轴) 进行线性拟合 (Linear Fit)。"],
                ["2. 损耗计算：拟合得到的斜率(Slope)的绝对值即为损耗系数 α。"],
                ["3. 公式参考：ln(Intensity) = -α * Distance + Constant"],
                [""] # 空行
            ]
            instruct_df = pd.DataFrame(instructions)
            instruct_df.to_excel(writer, index=False, header=False, startrow=0)
            
            # 2. 写入实际数据，从第 6 行开始 (startrow=5)
            final_df.to_excel(writer, index=False, startrow=5)
        
        print(f"成功！文件已保存至:\n{output_path}")
        print("说明：Excel 顶部已插入拟合指导说明。")
    else:
        print("无数据可保存。")

if __name__ == "__main__":
    process_laser_data_smart()