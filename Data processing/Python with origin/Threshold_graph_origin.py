import originpro as op
import pandas as pd
import tkinter as tk
from tkinter import filedialog
import sys
import os
import math
import time

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

    # ==========================================
    # 💾 核心 3：保存到目标文件夹并安全退出 Origin
    # ==========================================
    # 1. 路径净化：转换为绝对路径，并强制使用 Origin 最喜欢的正斜杠
    save_path = os.path.abspath(os.path.join(base_dir, "Threshold_Origin.opju"))
    save_path = save_path.replace("\\", "/")
    
    # 2. 防残留机制
    if os.path.exists(save_path):
        try:
            os.remove(save_path)
            print("已清理旧的同名文件。")
        except PermissionError:
            print("⚠️ 警告：无法覆盖原文件！它可能被隐藏的 Origin 进程占用了。")
            print("请按 Ctrl+Shift+Esc 打开任务管理器，强制结束所有 Origin64.exe 进程后再试。")
            op.set_show(True) 
            return 

    # 3. 执行保存
    print(f"正在写入 Origin 工程文件...")
    success = op.save(save_path)
    
    if success:
        print(f"✅ 文件已成功写入: {save_path}")
    else:
        print(f"❌ Origin 返回了保存失败的信号！")

    # 4. 🛑 强制等待：给 Origin 的硬盘 I/O 留出足够的缓冲时间
    time.sleep(3) 
    
    # 无痕关闭 Origin
    op.exit()


if __name__ == "__main__":
    print("启动程序...")
    files = select_files("同时选中需要处理的两个 Excel 文件")
    
    selected_dir = os.path.dirname(files[0])
    df_plot_data, df_fit, df_other = identify_and_load_data(files)

    template_file = r"C:\My files\Google drive sync\St Andrews\Python\Data processing\Python plot lib\origin_template\Threshold.otpu"

    if not os.path.exists(template_file):
        print(f"❌ 找不到模板文件，请检查路径: {template_file}")
        sys.exit()

    plot_origin_threshold(
        df_scatter=df_plot_data, 
        df_fit=df_fit, 
        template_path=template_file,
        base_dir=selected_dir
    )
    print("程序执行结束。")