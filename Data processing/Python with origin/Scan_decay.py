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

# ==========================================
# 模块 1：核心处理函数 (从你的旧脚本改造而来)
# 改动：去掉了 GUI 选择和 op.attach()，只负责“吃入一个路径，吐出一个 opju”
# ==========================================
def process_single_file(filepath, template_path):
    file_basename = os.path.basename(filepath)
    
    # 🌟 快速检查：跳过没有 Fit_Curve 的文件，避免加载整个数据浪费时间
    try:
        xl = pd.ExcelFile(filepath)
        if "Fit_Curve" not in xl.sheet_names:
            return "NO_SHEET" # 返回特定状态码，方便日志记录
        df = xl.parse("Fit_Curve")
    except Exception as e:
        return f"ERROR: {str(e)}"

    col_full_time = 'Full_Time (ns)'
    col_counts = 'Plot_Counts (Bkg=10)'
    col_irf = 'Plot_IRF (Bkg=10)'
    col_fit_time = 'Fit_Time (ns)'
    col_fit_data = 'Fit_Plot_Fitted Data (Bkg=10)'

    required_cols = [col_full_time, col_counts, col_fit_time, col_fit_data]
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        return f"MISSING_COLS: {missing_cols}"

    # 提取数据
    df_raw = df[[col_full_time, col_counts]].dropna()
    df_fit = df[[col_fit_time, col_fit_data]].dropna()
    has_irf = col_irf in df.columns
    if has_irf:
        df_irf = df[[col_full_time, col_irf]].dropna()

    # 时间自适应缩放 (保留了你优秀的逻辑)
    max_time_ns = max(df_raw[col_full_time].max(), df_fit[col_fit_time].max())
    if max_time_ns >= 1e9:   
        x_divisor, x_unit_label = 1e9, "s"
    elif max_time_ns >= 1e6: 
        x_divisor, x_unit_label = 1e6, "ms"
    elif max_time_ns >= 1e3: 
        x_divisor, x_unit_label = 1e3, r"\g(m)s" 
    else:                    
        x_divisor, x_unit_label = 1, "ns"

    df_raw[col_full_time] = df_raw[col_full_time] / x_divisor
    df_fit[col_fit_time] = df_fit[col_fit_time] / x_divisor
    if has_irf:
        df_irf[col_full_time] = df_irf[col_full_time] / x_divisor

    # 传入 Origin
    wks = op.new_sheet()
    wks.from_list(0, df_raw[col_full_time].tolist(), 'Full_Time')
    wks.from_list(1, df_raw[col_counts].tolist(), 'Counts')
    
    col_idx = 2
    axis_str = 'XY'
    
    if has_irf:
        wks.from_list(col_idx, df_irf[col_irf].tolist(), 'IRF')
        axis_str += 'Y'
        col_idx += 1
        
    wks.from_list(col_idx, df_fit[col_fit_time].tolist(), 'Fit_Time')
    wks.from_list(col_idx+1, df_fit[col_fit_data].tolist(), 'Fitted_Data')
    axis_str += 'XY'
    wks.cols_axis(axis_str)

    # 绘图与格式调整
    graph = op.new_graph(template=template_path)
    layer = graph[0]
    layer.add_plot(wks, coly=1, colx=0, type='scatter')
    if has_irf:
        layer.add_plot(wks, coly=2, colx=0, type='line')
    layer.add_plot(wks, coly=col_idx+1, colx=col_idx, type='line')

    layer.axis('x').title = f"Time ({x_unit_label})"
    x_min = min(df_raw[col_full_time].min(), df_fit[col_fit_time].min())
    x_max = max(df_raw[col_full_time].max(), df_fit[col_fit_time].max())
    x_range = x_max - x_min
    x_from = x_min - (x_range * 0.05) if x_min > 0 else 0 
    x_to = x_max + (x_range * 0.05)
    
    y_max = max(df_raw[col_counts].max(), df_fit[col_fit_data].max())
    if has_irf:
        y_max = max(y_max, df_irf[col_irf].max())
    y_to = y_max * 1.5 
    y_from = 0.5 
    
    ly = layer.lt_range()
    op.lt_exec(f'range ly1 = {ly}; ly1.x.from = {x_from}; ly1.x.to = {x_to};')
    op.lt_exec(f'range ly1 = {ly}; ly1.y.type = 2; ly1.y.from = {y_from}; ly1.y.to = {y_to};')
    op.lt_exec(f'range ly1 = {ly}; ly1.y.label.displayFormat = 4;')
    op.lt_exec('doc -uw;')

    # 保存 Project
    base_dir = os.path.dirname(filepath)
    file_name_without_ext = os.path.splitext(file_basename)[0]
    save_path = os.path.abspath(os.path.join(base_dir, f"{file_name_without_ext}_Decay.opju")).replace("\\", "/")
    
    if os.path.exists(save_path):
        try:
            os.remove(save_path)
        except PermissionError:
            return "SAVE_ERROR: File in use"

    if op.save(save_path):
        return "SUCCESS"
    return "SAVE_FAILED"


# ==========================================
# 模块 2：批量扫描与生命周期控制器
# ==========================================
def run_scanner():
    # 1. 选择大文件夹
    root_tk = tk.Tk()
    root_tk.withdraw()
    root_tk.attributes('-topmost', True)
    target_dir = filedialog.askdirectory(title="请选择要扫描的数据主文件夹")
    
    if not target_dir:
        print("❌ 未选择文件夹，程序退出。")
        sys.exit()

    template_file = r"C:\My files\Google drive sync\St Andrews\Python\Data processing\Python plot lib\origin_template\Decay.otpu"
    if not os.path.exists(template_file):
        print(f"❌ 找不到模板文件: {template_file}")
        sys.exit()

    print(f"📂 正在扫描文件夹: {target_dir}")
    
    # 收集所有可能是数据的 Excel 文件
    all_files = []
    for root, dirs, files in os.walk(target_dir):
        for file in files:
            if file.endswith(('.xlsx', '.csv', '.xls')) and not file.startswith('~'):
                all_files.append(os.path.join(root, file))

    print(f"🔍 扫描完毕，共发现 {len(all_files)} 个表格文件。准备启动 Origin...")

    # 日志记录准备
    log_path = os.path.join(target_dir, "Manual_Fit_Required_Log.txt")
    success_count = 0
    skipped_records = []

    # 2. 启动单实例 Origin 
    # set_show(False) 可以让 Origin 在后台运行，极大地提升处理速度且不弹窗打扰
    if not (op and op.oext):
        op.set_show(False) 
        op.attach()

    # 3. 逐个处理并刷新内存
    for idx, filepath in enumerate(all_files, 1):
        filename = os.path.basename(filepath)
        print(f"[{idx}/{len(all_files)}] 分析中: {filename} ...", end=" ")
        
        status = process_single_file(filepath, template_file)
        
        if status == "SUCCESS":
            print("✅ 成功存图")
            success_count += 1
            # 🌟 核心动作：存图成功后，强制 Origin 新建空项目，清空内存，防止崩溃
            op.new() 
        elif status == "NO_SHEET":
            print("⏭️ 跳过 (无 Fit_Curve)")
            skipped_records.append(f"缺失 Fit_Curve: {filepath}")
        else:
            print(f"⚠️ 失败 ({status})")
            skipped_records.append(f"处理失败 ({status}): {filepath}")
            # 即使失败也清空一下，防止残留废弃数据
            op.new()

    # 4. 写入日志文件
    if skipped_records:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"扫描时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("以下文件需要手动拟合或检查：\n")
            f.write("-" * 50 + "\n")
            for record in skipped_records:
                f.write(record + "\n")

    # 5. 安全退出
    if op and op.oext:
        op.exit()

    print("\n" + "="*40)
    print(f"✨ 批量处理完成！")
    print(f"📊 成功生成图表: {success_count} 个")
    print(f"📝 需手动处理数: {len(skipped_records)} 个")
    if skipped_records:
        print(f"📁 日志已保存至: {log_path}")
    print("="*40)

if __name__ == "__main__":
    run_scanner()