import os
import tkinter as tk
from tkinter import filedialog
from PIL import Image
import torch
import torchvision.transforms.functional as F

try:
    from transformers import Swin2SRForImageSuperResolution, Swin2SRImageProcessor
except ImportError:
    print("Please install transformers: pip install transformers")
    exit()

def select_image():
    root = tk.Tk()
    root.attributes('-topmost', True)
    root.withdraw()  # Hide the main window
    file_path = filedialog.askopenfilename(
        title="Select an Image to Upscale to 4K",
        filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.tiff")]
    )
    return file_path

def pad_image(image, multiple=8):
    """
    Pad image to a multiple of a given number, as required by SwinIR architectures.
    """
    w, h = image.size
    new_w = (w + multiple - 1) // multiple * multiple
    new_h = (h + multiple - 1) // multiple * multiple
    return F.pad(image, (0, 0, new_w - w, new_h - h), padding_mode='reflect'), w, h

def main():
    print("Initializing 4K Upscaler...")
    
    # 1. Select image using a popup window
    img_path = select_image()
    if not img_path:
        print("No image selected. Exiting.")
        return
    
    print(f"Selected image: {img_path}")
    
    # 2. Setup device to use RTX 5090 (CUDA)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")
    
    # Load Swin2SR model (an updated version of SwinIR available in huggingface transformers)
    # We use a real-world x4 super-resolution model which works well for general images
    model_name = "caidas/swin2SR-realworld-sr-x4-64-bsrgan-psnr"
    print(f"Loading SwinIR model ({model_name})...")
    
    processor = Swin2SRImageProcessor.from_pretrained(model_name)
    model = Swin2SRForImageSuperResolution.from_pretrained(model_name).to(device)
    model.eval()
    
    # 3. Optimization: On Windows, torch.compile is difficult to use due to Triton missing.
    # But with an RTX 5090, standard inference is already incredibly fast.
    print("Optimization: Using standard GPU inference mode.")

    # 4. Load and process the image
    print("Processing image... (Please wait, inference might take a while depending on image size)")
    image = Image.open(img_path).convert("RGB")
    original_w, original_h = image.size
    
    # Pad image dimensions to be multiples of 8 (required for windowed attention in SwinIR)
    padded_img, w, h = pad_image(image, multiple=8)
    
    inputs = processor(images=padded_img, return_tensors="pt").to(device)
    
    # Inference
    autocast_device = device.type  # "cuda" or "cpu"
    print("Starting inference...")
    with torch.inference_mode(), torch.autocast(device_type=autocast_device, enabled=(autocast_device == "cuda")):
        outputs = model(**inputs)
    
    # The model scales by x4
    scale = 4
    output_tensor = outputs.reconstruction.squeeze(0).cpu().clamp_(0, 1)
    
    # Crop out the padded area (scaled by 4)
    output_tensor = output_tensor[:, :original_h * scale, :original_w * scale]
    
    # Convert tensor back to PIL Image (native x4 of input, no extra resize)
    output_image = F.to_pil_image(output_tensor)
    
    dir_name = os.path.dirname(img_path)
    base_name = os.path.basename(img_path)
    name, _ = os.path.splitext(base_name)
    
    # 5. Save native x4 super-resolution (original upscaled pixel dimensions)
    save_native = os.path.join(dir_name, f"{name}_x4.png")
    output_image.save(save_native, format="PNG")
    print(f"Saved native x4 upscaling ({output_image.size[0]}x{output_image.size[1]}) to: {save_native}")
    
    # 6. True 4K UHD (3840x2160) via Lanczos
    target_width, target_height = 3840, 2160
    print(f"Resizing to true 4K ({target_width}x{target_height})...")
    final_4k = output_image.resize((target_width, target_height), Image.Resampling.LANCZOS)
    save_4k = os.path.join(dir_name, f"{name}_4K.png")
    final_4k.save(save_4k, format="PNG")
    print(f"Saved true 4K (resized) to: {save_4k}")

if __name__ == "__main__":
    main()
