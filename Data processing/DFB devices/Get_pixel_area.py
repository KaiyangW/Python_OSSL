import tkinter as tk
from tkinter import filedialog
import numpy as np
import csv
import ctypes
import os
import matplotlib.pyplot as plt

# Enable High DPI awareness on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def analyze_beam_profile():
    # Initialize the tkinter window and hide the main root window
    root = tk.Tk()
    root.withdraw()
    
    # Open a file dialog to select the data file
    file_path = filedialog.askopenfilename(
        title="Select BeamView Export File",
        filetypes=[("CSV files", "*.csv"), ("Text files", "*.txt"), ("All files", "*.*")]
    )
    
    if not file_path:
        print("No file was selected. Process terminated.")
        return

    header_lines = 9
    pixel_width = 1.0
    pixel_height = 1.0
    
    # Read the header to extract the pixel pitch dynamically
    try:
        with open(file_path, 'r', encoding='utf-8-sig') as file:
            reader = csv.reader(file)
            for i in range(header_lines):
                row = next(reader)
                if len(row) >= 2:
                    key = row[0].strip()
                    value = row[1].strip()
                    
                    if key == 'PixelWidth':
                        pixel_width = float(value)
                    elif key == 'PixelHeight':
                        pixel_height = float(value)
    except Exception as e:
        print(f"Error reading the header: {e}")
        return

    # Load the 2D raw data matrix
    try:
        beam_matrix = np.genfromtxt(file_path, skip_header=header_lines, delimiter=',')
        if np.isnan(beam_matrix).all():
            beam_matrix = np.genfromtxt(file_path, skip_header=header_lines)
        beam_matrix = np.nan_to_num(beam_matrix)
    except Exception as e:
        print(f"Error loading the 2D matrix: {e}")
        return

    # Calculate thresholds based on 1/e^2 standard (13.5% of peak intensity)
    threshold_fraction = 0.135
    peak_intensity = np.max(beam_matrix)
    threshold_value = peak_intensity * threshold_fraction
    
    # --- 新增：背景截断阈值设定 ---
    # 设定背景噪声的阈值（例如设定为峰值的 3%），您可以根据背景干净程度调整这个系数
    background_cutoff = peak_intensity * 0.03 
    
    # Count pixels that exceed the threshold
    pixel_count = np.sum(beam_matrix > threshold_value)
    
    # Calculate the physical area
    single_pixel_area_um2 = pixel_width * pixel_height
    physical_area_um2 = pixel_count * single_pixel_area_um2
    physical_area_mm2 = physical_area_um2 / 1_000_000
    physical_area_cm2 = physical_area_mm2 / 100

    print("-" * 30)
    print(f"Data File: {os.path.basename(file_path)}")
    print(f"Pixel Pitch: {pixel_width} x {pixel_height} um")
    print(f"Peak Intensity: {peak_intensity}")
    print(f"Threshold (1/e^2): {threshold_value:.2f}")
    print("-" * 30)
    print(f"Beam Area (Pixels): {pixel_count}")
    print(f"Beam Area (Physical): {physical_area_cm2:.5f} cm^2")
    print(f"Beam Area (Physical): {physical_area_mm2:.6f} mm^2")
    print("-" * 30)

    # ==========================================
    # Generate 600 DPI Plot with Dark Mode
    # ==========================================
    print("Generating 600 DPI plot (Dark Mode)...")
    
    fig = plt.figure(figsize=(8, 6), facecolor='black')
    ax = plt.gca()
    ax.set_facecolor('black')
    
    # --- 新增：处理色条，将低于截断值的部分强制涂黑 ---
    # 获取默认的 viridis 色条并创建一个副本
    current_cmap = plt.get_cmap('viridis').copy()
    # 设置所有低于 vmin 的数据点颜色为纯黑
    current_cmap.set_under('black')
    
    # 绘制热力图时，传入截断下限 vmin
    im = plt.imshow(beam_matrix, cmap=current_cmap, origin='upper', vmin=background_cutoff)
    
    # Format the Colorbar for dark background
    cbar = plt.colorbar(im)
    cbar.set_label('Intensity (A.U.)', color='white')
    cbar.ax.yaxis.set_tick_params(color='white')
    plt.setp(plt.getp(cbar.ax.axes, 'yticklabels'), color='white')
    
    # Add Title and Labels with white color
    plt.xlabel("X (Pixels)", color='white')
    plt.ylabel("Y (Pixels)", color='white')
    
    # Change tick colors and border colors to white
    ax.tick_params(axis='x', colors='white')
    ax.tick_params(axis='y', colors='white')
    for spine in ax.spines.values():
        spine.set_edgecolor('white')
    
    # Determine save paths
    file_dir, file_name = os.path.split(file_path)
    base_name = os.path.splitext(file_name)[0]
    
    # Save the "clean" version WITHOUT red contour (SVG only)
    output_svg_clean_path = os.path.join(file_dir, f"{base_name}_profile_clean.svg")
    plt.savefig(output_svg_clean_path, format='svg', dpi=600, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    
    # Add a red contour specifically at the 1/e^2 threshold value
    plt.contour(beam_matrix, levels=[threshold_value], colors='red', linewidths=1.5)
    
    # Save the standard version WITH red contour (SVG and PDF)
    output_svg_path = os.path.join(file_dir, f"{base_name}_profile.svg")
    output_pdf_path = os.path.join(file_dir, f"{base_name}_profile.pdf")
    plt.savefig(output_svg_path, format='svg', dpi=600, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.savefig(output_pdf_path, format='pdf', dpi=600, bbox_inches='tight', facecolor=fig.get_facecolor(), edgecolor='none')
    plt.close()
    
    print(f"Plots saved successfully at:\n{output_svg_clean_path}\n{output_svg_path}\n{output_pdf_path}")
    print("-" * 30)

if __name__ == "__main__":
    analyze_beam_profile()