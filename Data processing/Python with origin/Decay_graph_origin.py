import originpro as op
import pandas as pd
import tkinter as tk
from tkinter import filedialog
import sys
import os
import time

# 适配高分辨率屏幕
import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def select_files(prompt_text):
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True) 
    file_paths = filedialog.askopenfilenames(
        title=prompt_text,
        filetypes=[("Excel files", "*.xlsx;*.xls;*.csv"), ("All files", "*.*")]
    )
    if not file_paths:
        print(f"❌ 未选择任何文件，操作取消！")
        sys.exit()
    return file_paths

def plot_tcspc_decay_origin(filepath, template_path):
    file_basename = os.path.basename(filepath)
    try:
        df = pd.read_excel(filepath, sheet_name="Fit_Curve")
    except Exception as e:
        print(f"❌ 无法在文件 {file_basename} 中找到 'Fit_Curve' Sheet。跳过...")
        return False

    # 动态查找列名（适配 Floor=X.XX 的变化）
    def find_col(prefix):
        return next((c for c in df.columns if str(c).startswith(prefix)), None)

    col_full_time = 'Full_Time (ns)'
    col_counts = find_col('Plot_Counts')
    
    # Detect IRF (Priority: non-shifted)
    col_irf = 'Plot_IRF_non_shifted' if 'Plot_IRF_non_shifted' in df.columns else \
              ('Plot_IRF' if 'Plot_IRF' in df.columns else None)
    
    col_fit_time = 'Fit_Time (ns)'
    col_fit_data = find_col('Fit_Plot_Fitted Data')

    if not all([col_counts, col_fit_data, col_full_time in df.columns, col_fit_time in df.columns]):
        print(f"❌ 数据缺失必选列。跳过...")
        return False

    # 提取数据
    df_raw = df[[col_full_time, col_counts]].dropna()
    df_fit = df[[col_fit_time, col_fit_data]].dropna()
    
    # Handle IRF data extraction
    irf_cols_to_extract = [col_irf] if col_irf else []
    
    has_irf = len(irf_cols_to_extract) > 0
    if has_irf:
        df_irf = df[[col_full_time] + irf_cols_to_extract].dropna()

    # ==========================================
    # 🌟 核心优化 1：Python 端绝对安全的 X 轴时间自适应缩放
    # ==========================================
    # 找到时间的最大绝对值
    max_time_ns = max(df_raw[col_full_time].max(), df_fit[col_fit_time].max())
    
    # 根据数量级决定除数和单位名称
    if max_time_ns >= 1e9:   # 大于 1 秒
        x_divisor = 1e9
        x_unit_label = "s"
    elif max_time_ns >= 1e6: # 大于 1 毫秒
        x_divisor = 1e6
        x_unit_label = "ms"
    elif max_time_ns >= 1e3: # 大于 1 微秒
        x_divisor = 1e3
        x_unit_label = r"\g(m)s" 
    else:                    # 纳秒级别
        x_divisor = 1
        x_unit_label = "ns"

    # 💡 新增：在 Terminal 里输出提示信息，将 Origin 的转义符替换回直观的 μ 供人阅读
    display_unit = x_unit_label.replace(r"\g(m)", "μ")
    print(f"👉 [分析] {file_basename} | 最大时间: {max_time_ns:.1e} ns ➔ Python 自动识别缩放单位为: 【{display_unit}】")

    # 在 Python 内存中对时间列除以换算因子
    df_raw[col_full_time] = df_raw[col_full_time] / x_divisor
    df_fit[col_fit_time] = df_fit[col_fit_time] / x_divisor
    if col_irf:
        df_irf[col_irf] = df_irf[col_irf] / 1.0 # No Y scaling needed, just keeping structure
    if has_irf:
        df_irf[col_full_time] = df_irf[col_full_time] / x_divisor

    # ==========================================
    # 启动 Origin
    # ==========================================
    if not (op and op.oext):
        op.attach()

    wks = op.new_sheet()
    
    # 传入 Origin
    wks.from_list(0, df_raw[col_full_time].tolist(), 'Full_Time')
    wks.from_list(1, df_raw[col_counts].tolist(), 'Counts')
    
    col_idx = 2
    axis_str = 'XY'
    
    irf_plot_indices = []
    if col_irf:
        wks.from_list(col_idx, df_irf[col_irf].tolist(), 'IRF')
        axis_str += 'Y'
        irf_plot_indices.append(col_idx)
        col_idx += 1
        
    wks.from_list(col_idx, df_fit[col_fit_time].tolist(), 'Fit_Time')
    wks.from_list(col_idx+1, df_fit[col_fit_data].tolist(), 'Fitted_Data')
    axis_str += 'XY'
    
    wks.cols_axis(axis_str)

    graph = op.new_graph(template=template_path)
    layer = graph[0]
    
    layer.add_plot(wks, coly=1, colx=0, type='scatter')
    for p_idx in irf_plot_indices:
        layer.add_plot(wks, coly=p_idx, colx=0, type='line')
    layer.add_plot(wks, coly=col_idx+1, colx=col_idx, type='line')

    # ==========================================
    # 🌟 核心优化 2：精准动态范围设定，并强制改写 X 轴标签
    # ==========================================
    # 1. 强制覆写 X 轴的 Title
    layer.axis('x').title = f"Time ({x_unit_label})"

    # 2. X 轴动态缩放
    x_min = min(df_raw[col_full_time].min(), df_fit[col_fit_time].min())
    x_max = max(df_raw[col_full_time].max(), df_fit[col_fit_time].max())
    x_range = x_max - x_min
    x_from = x_min - (x_range * 0.05) if x_min > 0 else 0 
    x_to = x_max + (x_range * 0.05)
    
    # 3. Y 轴动态缩放
    y_max = max(df_raw[col_counts].max(), df_fit[col_fit_data].max())
    if col_irf:
        y_max = max(y_max, df_irf[col_irf].max())
        
    y_to = y_max * 1.5 
    y_from = 0.5 
    
    ly = layer.lt_range()
    op.lt_exec(f'range ly1 = {ly}; ly1.x.from = {x_from}; ly1.x.to = {x_to};')
    op.lt_exec(f'range ly1 = {ly}; ly1.y.type = 2; ly1.y.from = {y_from}; ly1.y.to = {y_to};')
    op.lt_exec(f'range ly1 = {ly}; ly1.y.label.displayFormat = 4;')
    op.lt_exec('doc -uw;')

    # 保存项目
    base_dir = os.path.dirname(filepath)
    file_name_without_ext = os.path.splitext(file_basename)[0]
    save_path = os.path.abspath(os.path.join(base_dir, f"{file_name_without_ext}_Decay.opju")).replace("\\", "/")
    
    if os.path.exists(save_path):
        try:
            os.remove(save_path)
        except PermissionError:
            print(f"⚠️ 警告：无法覆盖原文件 {save_path}，文件可能被占用。")
            return False

    if op.save(save_path):
        print(f"✅ 保存完毕: {save_path}")
        return True
    return False

if __name__ == "__main__":
    print("🚀 启动程序...")
    files = select_files("选择要处理的 TCSPC Excel 文件 (可多选)")
    template_file = os.path.join(os.path.dirname(__file__), "origin_template", "Decay.otpu")

    if not os.path.exists(template_file):
        print(f"❌ 找不到模板文件，请检查路径: {template_file}")
        sys.exit()

    print(f"📚 共选中 {len(files)} 个文件，开始批量处理...\n")
    for idx, f in enumerate(files, 1):
        print(f"--- 正在处理第 {idx}/{len(files)} 个文件 ---")
        plot_tcspc_decay_origin(filepath=f, template_path=template_file)
        print("-" * 40)
        
    time.sleep(2)
    if op and op.oext:
        op.exit()
    print("✨ 所有处理流程执行结束，Origin 已自动关闭。")