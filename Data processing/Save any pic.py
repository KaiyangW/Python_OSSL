import tkinter as tk
from tkinter import filedialog, simpledialog, messagebox
from PIL import Image, ImageDraw, ImageFont
import os
import textwrap
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

def create_folder_indicator():
    """
    Creates a high-resolution (600 DPI) PNG image with custom text.
    Designed to be used as a visual indicator for folder contents.
    """
    # 1. Setup Tkinter for dialogs
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    # Ensure dialogs appear on top
    root.attributes('-topmost', True)

    # 2. Get user input for the text
    prompt_text = "Enter the message for the folder (e.g., 'Data invalid due to low signal'):"
    user_text = simpledialog.askstring("Input Message", prompt_text, parent=root)
    
    if not user_text or user_text.strip() == "":
        return

    # 3. Choose save location
    save_path = filedialog.asksaveasfilename(
        defaultextension=".png",
        filetypes=[("PNG files", "*.png")],
        title="Choose where to save the indicator image",
        initialfile="IMPORTANT_NOTE.png"
    )

    if not save_path:
        return

    # 4. Image Settings (600 DPI)
    DPI = 600
    # Image size: 5 inches x 3.5 inches (provides a large, clear thumbnail in folders)
    width_px = int(5 * DPI)
    height_px = int(3.5 * DPI)
    
    # Aesthetics
    bg_color = (30, 0, 50)     # Dark Purple
    text_color = (255, 255, 255) # White
    
    # 5. Create Image
    img = Image.new('RGB', (width_px, height_px), color=bg_color)
    draw = ImageDraw.Draw(img)
    
    # 6. Load Font (Arial Bold)
    # Search for Arial Bold on Windows
    font_paths = [
        "C:\\Windows\\Fonts\\arialbd.ttf", # Arial Bold
        "C:\\Windows\\Fonts\\arial.ttf",   # Arial Regular
        "arial.ttf"                         # System default search
    ]
    
    font = None
    # Use a large starting size
    font_size = 300 
    
    for path in font_paths:
        try:
            font = ImageFont.truetype(path, font_size)
            break
        except:
            continue
            
    if font is None:
        # Fallback to basic font if Arial isn't found
        font = ImageFont.load_default()
        print("Warning: Arial font not found, using default.")

    # 7. Word Wrapping & Dynamic Centering
    # Wrap text to fit width (approx 12-15 characters per line at this font size)
    wrapper = textwrap.TextWrapper(width=14) 
    lines = wrapper.wrap(user_text)
    
    # Calculate total height of the text block
    total_height = 0
    line_data = []
    
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_data.append((line, w, h))
        total_height += h + 40 # 40px spacing
    
    # Adjust starting Y to center vertically
    current_y = (height_px - total_height) // 2
    
    # Draw each line centered horizontally
    for line, w, h in line_data:
        current_x = (width_px - w) // 2
        draw.text((current_x, current_y), line, font=font, fill=text_color)
        current_y += h + 40

    # 8. Save with 600 DPI metadata
    try:
        img.save(save_path, "PNG", dpi=(DPI, DPI))
        messagebox.showinfo("Success", f"Indicator image saved to:\n{save_path}")
    except Exception as e:
        messagebox.showerror("Error", f"Failed to save image: {e}")

if __name__ == "__main__":
    create_folder_indicator()
