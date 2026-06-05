import originpro as op
import pandas as pd
import tkinter as tk
from tkinter import filedialog
import sys
import os
import math
import time
import numpy as np

# 适配高分辨率屏幕
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def select_files(prompt_text):
    """弹窗一次性选择多个 Excel 文件"""
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True) 
    file_paths = filedialog.askopenfilenames(
        title=prompt_text,
        filetypes=[("Excel files", "*.xlsx;*.xls;*.csv"), ("All files", "*.*")]
    )
    if not file_paths or len(file_paths) < 2:
        print(f"❌ 取消操作或文件数量不足。请按住 Ctrl 键同时选中 2 个文件！")
        sys.exit()
    return file_paths

def identify_and_load_data(file_paths):
    """读取逻辑保持不变"""
    df_other = None
    df_fit = None
    df_plot_data = None

    for filepath in file_paths:
        try:
            all_sheets = pd.read_excel(filepath, sheet_name=None)
        except Exception as e:
            continue
        
        if 'Plot_data' in all_sheets:
            df = all_sheets['Plot_data']
            df_plot_data = df.iloc[:, 0:3].copy()
            df_plot_data.columns = ['Fluence', 'Intensity', 'FWHM']
            df_plot_data = df_plot_data.dropna()
            
            if 'Fit_Line' in all_sheets:
                df_f = all_sheets['Fit_Line']
                cols = [str(c).strip() for c in df_f.columns]
                if 'Fit_Line_X' in cols and 'Fit_Line_Y' in cols:
                    df_fit = df_f[['Fit_Line_X', 'Fit_Line_Y']].copy()
                    df_fit.columns = ['x', 'y']
                    df_fit = df_fit.dropna()
        else:
            for sheet_name, df in all_sheets.items():
                cols_lower = [str(c).lower() for c in df.columns]
                if any('fluence' in c for c in cols_lower):
                    df_other = df.iloc[:, 0:3].copy()
                    df_other.columns = ['Fluence', 'Intensity', 'FWHM']
                    df_other = df_other.dropna()
                    break
            if df_other is None:
                df_other = pd.read_excel(filepath, sheet_name=0, usecols=[0, 1, 2])
                df_other.columns = ['Fluence', 'Intensity', 'FWHM']
                df_other = df_other.dropna()

    if df_plot_data is None:
        print("❌ 找不到包含 'Plot_data' 页的文件。")
        sys.exit()

    return df_plot_data, df_fit, df_other

def plot_origin_threshold(df_scatter, df_fit, template_path, base_dir):
    """
    将数据推送到 Origin 模板，自动缩放 Intensity 数量级，保存并退出
    """
    if op and op.oext:
        op.set_show(True)
    else:
        print("启动 Origin 并在后台处理...")
        op.attach()

    # ==========================================
    # 🔧 核心 1：自动识别数量级并进行数据除法
    # ==========================================
    max_intensity = df_scatter['Intensity'].max()
    exponent = 0
    if max_intensity > 0:
        exponent = int(math.floor(math.log10(max_intensity)))

    # 例如：最大值为 4000，exponent 为 3；最大值为 150000，exponent 为 5
    if exponent >= 2:
        scale_factor = 10**exponent
        df_scatter['Intensity'] = df_scatter['Intensity'] / scale_factor
        
        if df_fit is not None and not df_fit.empty:
            df_fit['y'] = df_fit['y'] / scale_factor
            
        # 组装格式化字符串，使用 \+() 作为 Origin 的原生上标标签
        y_label_str = f"Integrated Intensity (a.u. × 10\\+({exponent}))"
    else:
        y_label_str = "Integrated Intensity (a.u.)"


    # 2. 写入数据到 Worksheet
    wks = op.new_sheet()
    
    wks.from_list(0, df_scatter['Fluence'].tolist(), 'Fluence')
    wks.from_list(1, df_scatter['Intensity'].tolist(), 'Intensity')
    wks.from_list(2, df_scatter['FWHM'].tolist(), 'FWHM (nm)')
    
    axis_str = 'XYY'
    if df_fit is not None and not df_fit.empty:
        wks.from_list(3, df_fit['x'].tolist(), 'Fit_X')
        wks.from_list(4, df_fit['y'].tolist(), 'Fit_Y')
        axis_str += 'XY' 
    wks.cols_axis(axis_str)

    # 3. 创建 Graph 并在图层画图
    graph = op.new_graph(template=template_path)
    layer1 = graph[0]
    layer1.add_plot(wks, coly=1, colx=0, type='scatter')
    if df_fit is not None and not df_fit.empty:
        layer1.add_plot(wks, coly=4, colx=3, type='line')
        
    if len(graph) > 1:
        layer2 = graph[1]
        layer2.add_plot(wks, coly=2, colx=0, type='scatter')
        op.lt_exec(f'{layer2.lt_range()}.yr.text$ = "FWHM (nm)";')

    # ==========================================
    # 🔨 核心 2：强接管坐标轴范围，彻底无视模板旧设定
    # ==========================================
    # 1. 寻找 Fluence (X) 和 Intensity (左Y) 的最大值
    x_min = df_scatter['Fluence'].min()
    x_max = df_scatter['Fluence'].max()
    y1_max = df_scatter['Intensity'].max()
    if df_fit is not None and not df_fit.empty:
        x_min = min(x_min, df_fit['x'].min())
        x_max = max(x_max, df_fit['x'].max())
        y1_max = max(y1_max, df_fit['y'].max())
        
    # X轴起点为最小值的80%，终点为最大值的1.05倍
    x_min_padded = x_min * 0.8
    x_max_padded = x_max * 1.05
    y1_min_padded = - (y1_max * 0.05)
    y1_max_padded = y1_max * 1.1

    # 使用 LabTalk 的 range 对象强制写入上下限，无视 Fixed 锁定
    op.lt_exec(f'range ly1 = {layer1.lt_range()}; ly1.x.from = {x_min_padded}; ly1.x.to = {x_max_padded};')
    op.lt_exec(f'range ly1 = {layer1.lt_range()}; ly1.y.from = {y1_min_padded}; ly1.y.to = {y1_max_padded};')

    # 2. 如果存在右 Y 轴（FWHM），单独计算它的合理范围
    if len(graph) > 1:
        y2_min = df_scatter['FWHM'].min()
        y2_max = df_scatter['FWHM'].max()
        diff = y2_max - y2_min if y2_max > y2_min else y2_max * 0.1
        
        # FWHM 不为负，下方留一点，上方留 20% 以防遮挡文字
        y2_min_padded = max(0, y2_min - diff * 0.2)
        y2_max_padded = y2_max + diff * 0.2
        op.lt_exec(f'range ly2 = {layer2.lt_range()}; ly2.y.from = {y2_min_padded}; ly2.y.to = {y2_max_padded};')

    # ==========================================
    # 📝 写入正确的上下标文字
    # ==========================================
    layer1.axis('x').title = r'Incident Pump Fluence (μJ/cm\+(2))'
    layer1.axis('y').title = y_label_str

    op.lt_exec('doc -uw;')


def select_four_spectra_columns(df_mask, threshold):
    """根据阈值和拟合掩码，挑选 4 个有效数据点的列名"""
    # 仅保留真正参与拟合的点
    valid_df = df_mask[df_mask['Used_in_Fit'] == True].copy()
    fluences = valid_df['Incident Fluence (uJ/cm2)'].values
    
    if len(fluences) < 4:
        return [f"{e:.2f}" for e in fluences] # 如果有效点太少，全拿

    # 1. 寻找离阈值最近的两个点
    diffs = np.abs(fluences - threshold)
    sorted_indices_by_diff = np.argsort(diffs)
    idx_th1 = sorted_indices_by_diff[0]
    idx_th2 = sorted_indices_by_diff[1]

    # 2. 寻找低能量区和高能量区的点
    idx_low = 0                  # 有效点中的最低能量
    idx_high = len(fluences) - 1 # 有效点中的最高能量

    # 组合去重并排序（确保能量从小到大，方便图例排序）
    selected_indices = sorted(list(set([idx_low, min(idx_th1, idx_th2), max(idx_th1, idx_th2), idx_high])))
    
    # 将数值转为保留两位小数的字符串，以匹配 Raw Spec 的列名
    return [f"{fluences[i]:.2f}" for i in selected_indices]

def plot_origin_spectra(analysed_file, manual_fit_file, template_path):
    """读取光谱并推送到 Origin"""
    print("正在处理并绘制归一化光谱图...")
    
    # 读取阈值和拟合掩码
    df_params = pd.read_excel(manual_fit_file, sheet_name='Parameters')
    threshold = df_params['Threshold'].iloc[0]
    df_mask = pd.read_excel(manual_fit_file, sheet_name='Data_and_Mask')
    
    # 读取原始光谱 (以波长为 Index)
    df_raw = pd.read_excel(analysed_file, sheet_name='Raw Spec (nm)', index_col=0)
    wavelengths = df_raw.index.tolist()

    # 获取要画的 4 个列名
    selected_cols = select_four_spectra_columns(df_mask, threshold)

    # 写入 Origin Worksheet
    wks = op.new_sheet(lname="Spectra_Data")
    wks.from_list(0, wavelengths, 'Wavelength')
    
    col_idx = 1
    current_offset = 0.0  # 🚀 引入手动偏移量基准
    
    for col_name in selected_cols:
        if col_name in df_raw.columns:
            raw_y = df_raw[col_name].values
            max_y = np.max(raw_y)
            
            # 🚀 核心修改 1：归一化并直接在 Python 数组层面加 Offset 
            # 这样画出来的线自带平移，彻底摆脱 Origin 模板 Offset 的玄学 Bug
            norm_y = (raw_y / max_y if max_y > 0 else raw_y) + current_offset
            
            wks.from_list(col_idx, norm_y.tolist(), f'{col_name}')
            col_idx += 1
            current_offset += 1.2  # 下一条线自动往上平移 1.2
        else:
            print(f"⚠️ 警告: 在 Raw Spec 中找不到能量为 {col_name} 的列。")

    wks.cols_axis('X' + 'Y' * (col_idx - 1))
    
    num_curves = col_idx - 1
    if num_curves == 0:
        return

    # ==========================================
    # 🌟 强力绘图逻辑
    # ==========================================
    graph = op.new_graph(template=template_path)
    layer = graph[0]
    
    for i in range(1, col_idx):
        layer.add_plot(wks, coly=i, colx=0, type='line')
    
    op.lt_exec(f'win -a {graph.name};')
    op.lt_exec('page.active = 1;')
    
    # 打包为 Group。这一步依然需要，目的是激活模板里配置的颜色渐变 (Colormap)
    op.lt_exec('layer -g;')
    
    # 🚀 强制关闭 Origin 系统自带的 Group Offset
    # 防止模板原本带有 Offset，导致与我们手加的 Offset 发生叠加
    op.lt_exec('set 1 -gcy 0;') 
    
    # 🚀 核心修改 2：提取 Wavelength 的真实边界，强写 X 轴范围
    x_min = np.min(wavelengths)
    x_max = np.max(wavelengths)
    diff_x = x_max - x_min if x_max > x_min else 10
    x_min_padded = x_min - diff_x * 0.05
    x_max_padded = x_max + diff_x * 0.05
    
    y_max_padded = num_curves + 0.2
    y_min_padded = -0.1
    
    # 强制覆盖模板的 X 和 Y 坐标轴范围
    op.lt_exec(f'range ly1 = {layer.lt_range()}; ly1.x.from = {x_min_padded}; ly1.x.to = {x_max_padded};')
    op.lt_exec(f'range ly1 = {layer.lt_range()}; ly1.y.from = {y_min_padded}; ly1.y.to = {y_max_padded};')
    
    # 刷新画面，落地修改
    op.lt_exec('doc -uw;')

if __name__ == "__main__":
    print("启动程序...")
    files = select_files("同时选中需要处理的两个 Excel 文件 (Analysed 和 ManualFit_Result)")
    
    selected_dir = os.path.dirname(files[0])
    
    manual_fit_file = next((f for f in files if "ManualFit_Result" in f), None)
    analysed_file = next((f for f in files if "DFB_Analysed" in f), None)

    if not manual_fit_file or not analysed_file:
        print("❌ 未同时找到 'ManualFit_Result' 和 'DFB_Analysed' 文件，请确保两个文件都被选中！")
        sys.exit()

    df_plot_data, df_fit, df_other = identify_and_load_data(files)

    # 定义模板路径
    threshold_template = r"C:\My files\Google drive sync\St Andrews\Python\Data processing\Python plot lib\origin_template\Threshold.otpu"
    spectra_template = r"C:\My files\Google drive sync\St Andrews\Python\Data processing\Python plot lib\origin_template\Lasing_spectra.otpu"

    # 1. 画阈值图
    plot_origin_threshold(
        df_scatter=df_plot_data, 
        df_fit=df_fit, 
        template_path=threshold_template,
        base_dir=selected_dir
    )
    
    # 2. 画光谱图
    plot_origin_spectra(
        analysed_file=analysed_file, 
        manual_fit_file=manual_fit_file, 
        template_path=spectra_template
    )

    # 3. 统一保存并退出
    save_path = os.path.abspath(os.path.join(selected_dir, "Threshold_and_Spectra_Origin.opju")).replace("\\", "/")
    if os.path.exists(save_path):
        try:
            os.remove(save_path)
        except PermissionError:
            print("⚠️ 警告：无法覆盖原文件！请强制结束 Origin64.exe 后再试。")
            op.set_show(True)
            sys.exit()

    print(f"正在将所有图表写入 Origin 工程文件...")
    if op.save(save_path):
        print(f"✅ 文件已成功写入: {save_path}")
    else:
        print(f"❌ Origin 返回了保存失败的信号！")

    time.sleep(3) 
    op.exit()
    print("程序执行结束。")