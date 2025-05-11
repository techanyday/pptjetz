from pptx import Presentation
import os

def print_layout_info(prs_path):
    print(f"\nAnalyzing: {os.path.basename(prs_path)}")
    print("-" * 50)
    
    prs = Presentation(prs_path)
    
    print(f"Total slide layouts: {len(prs.slide_layouts)}\n")
    
    for idx, layout in enumerate(prs.slide_layouts):
        print(f"Layout {idx}: {layout.name}")
        for shape in layout.placeholders:
            print(f"  - {shape.name} (idx: {shape.placeholder_format.idx})")
        print()

base_path = os.path.join("app", "static", "presentations", "custom_styles")
minimalist_path = os.path.join(base_path, "Minimalist.pptx")
professional_path = os.path.join(base_path, "Professional.pptx")

print("Current directory:", os.getcwd())
print("Looking for templates in:", base_path)
print("Files in directory:", os.listdir(base_path) if os.path.exists(base_path) else "Directory not found")

if os.path.exists(minimalist_path):
    print_layout_info(minimalist_path)
else:
    print(f"\nError: {minimalist_path} not found")

if os.path.exists(professional_path):
    print_layout_info(professional_path)
else:
    print(f"\nError: {professional_path} not found")
