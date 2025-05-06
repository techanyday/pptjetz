from PIL import Image, ImageDraw, ImageFont
import os

def create_preview(name, color):
    # Create a new image with a white background
    img = Image.new('RGB', (400, 225), color)
    draw = ImageDraw.Draw(img)
    
    # Add some visual elements
    draw.rectangle([20, 20, 380, 205], outline='gray', width=2)
    draw.text((200, 100), name, fill='gray', anchor='mm')
    
    # Save the image
    img.save(f'{name.lower()}-preview.png')

# Create preview images for each template
styles = {
    'Aesthetic': (255, 240, 245),  # Light pink
    'Minimalist': (255, 255, 255),  # White
    'Professional': (240, 240, 240),  # Light gray
    'Vintage': (245, 245, 220)  # Beige
}

# Change to the img directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Generate previews
for name, color in styles.items():
    create_preview(name, color)
