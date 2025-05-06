from pptx import Presentation
from PIL import Image
import win32com.client
import os
import time

def convert_first_slide_to_png(pptx_path, output_path):
    # Initialize PowerPoint application
    powerpoint = win32com.client.Dispatch("PowerPoint.Application")
    powerpoint.Visible = True

    try:
        # Open presentation
        presentation = powerpoint.Presentations.Open(os.path.abspath(pptx_path))
        
        # Export first slide as PNG
        presentation.Slides[1].Export(os.path.abspath(output_path), "PNG", 800, 450)
        
        # Close presentation
        presentation.Close()
    finally:
        # Quit PowerPoint
        powerpoint.Quit()

def create_preview_image(png_path, output_path, size=(400, 225)):
    # Open and resize the PNG
    with Image.open(png_path) as img:
        # Resize maintaining aspect ratio
        img.thumbnail(size)
        # Save as preview
        img.save(output_path, "PNG", optimize=True)
    
    # Remove temporary PNG
    os.remove(png_path)

def main():
    # Get absolute paths
    base_dir = os.path.dirname(os.path.abspath(__file__))
    templates_dir = os.path.join(base_dir, '..', 'presentations', 'custom_styles')
    
    # Process each template
    templates = {
        "Aesthetic": "Aesthetic.pptx",
        "Minimalist": "Minimalist.pptx",
        "Professional": "Professional.pptx",
        "Vintage": "Vintage.pptx"
    }
    
    for name, filename in templates.items():
        print(f"Processing {name}...")
        pptx_path = os.path.join(templates_dir, filename)
        temp_png = os.path.join(base_dir, f"{name.lower()}_temp.png")
        preview_path = os.path.join(base_dir, f"{name.lower()}-preview.png")
        
        # Convert first slide to PNG
        convert_first_slide_to_png(pptx_path, temp_png)
        
        # Create preview image
        create_preview_image(temp_png, preview_path)
        print(f"Created preview for {name}")
        
        # Wait a bit between files to avoid PowerPoint issues
        time.sleep(1)

if __name__ == "__main__":
    main()
    print("All previews updated successfully!")
