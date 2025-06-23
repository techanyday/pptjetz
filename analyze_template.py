from pptx import Presentation
import os

def analyze_template(template_name):
    template_path = os.path.join("app", "static", "presentations", "custom_styles", template_name)
    print(f"\nAnalyzing template: {template_name}")
    print("-" * 50)
    
    try:
        prs = Presentation(template_path)
        print(f"Number of layouts: {len(prs.slide_layouts)}")
        
        for i, layout in enumerate(prs.slide_layouts):
            print(f"\nLayout {i}: {layout.name}")
            print("Placeholders:")
            for shape in layout.placeholders:
                print(f"  - Index {shape.placeholder_format.idx}: {shape.name} ({shape.placeholder_format.type})")
    except Exception as e:
        print(f"Error analyzing template: {str(e)}")

# Analyze professional template only
analyze_template("Professional.pptx")
