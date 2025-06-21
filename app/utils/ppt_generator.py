from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR, PP_PARAGRAPH_ALIGNMENT, MSO_AUTO_SIZE
from pptx.dml.color import RGBColor
from pptx.enum.dml import MSO_THEME_COLOR
from pptx.enum.shapes import PP_PLACEHOLDER
import os
import json
from openai import OpenAI
import requests
import uuid
from io import BytesIO
from typing import List, Dict, Optional

class PPTGenerator:
    def __init__(self):
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OpenAI API key not found in environment variables. Please set OPENAI_API_KEY in your .env file.")
        
        print(f"Debug - API Key loaded in PPTGenerator: {api_key[:10]}...")
        
        try:
            self.client = OpenAI(api_key=api_key)
            print("Debug - OpenAI client initialized successfully")
            
            # Test the API key with a simple completion request
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "system", "content": "Test message"}],
                max_tokens=5
            )
            print("Debug - OpenAI API key verified successfully")
            
        except Exception as e:
            print(f"Debug - Error with OpenAI setup: {str(e)}")
            if 'Invalid API key' in str(e):
                raise ValueError("Invalid OpenAI API key. Please check your .env file.")
            elif 'Rate limit' in str(e):
                raise ValueError("OpenAI API rate limit reached. Please try again later.")
            else:
                raise ValueError(f"OpenAI API error: {str(e)}")

        # Define available template styles
        self.TEMPLATE_STYLES = {
            "Aesthetic": "Aesthetic.pptx",
            "Minimalist": "Minimalist.pptx",
            "Professional": "Professional.pptx",
            "Vintage": "Vintage.pptx",
            "Corporate": "Corporate.pptx",
            "Creative": "Creative.pptx",
            "Modern": "Modern.pptx",
            "Simple": "Simple.pptx"
        }

        # Define default font styles (fallback)
        self.FONTS = {
            'title': {
                'name': 'Calibri',
                'size': Pt(44),
                'bold': True,
                'color': RGBColor(33, 33, 33)
            },
            'subtitle': {
                'name': 'Calibri',
                'size': Pt(32),
                'bold': False,
                'color': RGBColor(0, 123, 255)
            },
            'body': {
                'name': 'Calibri',
                'size': Pt(18),
                'bold': False,
                'color': RGBColor(33, 37, 41)
            }
        }
        
    # Image generation helper
    def _generate_image(self, prompt: str) -> str:
        """Generate an image using DALL·E 3 and save it locally. Returns the file path or empty string on failure."""
        try:
            print(f"Debug - Generating image for prompt: {prompt}")
            response = self.client.images.generate(
                model="dall-e-3",
                prompt=prompt,
                n=1,
                size="1024x1024"
            )
            image_url = response.data[0].url
            img_bytes = requests.get(image_url).content
            images_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'generated', 'images'))
            os.makedirs(images_dir, exist_ok=True)
            file_path = os.path.join(images_dir, f"{uuid.uuid4().hex}.png")
            with open(file_path, "wb") as f:
                f.write(img_bytes)
            return file_path
        except Exception as e:
            print(f"Warning - Image generation failed: {str(e)}")
            return ""

        # Define available template styles
        self.TEMPLATE_STYLES = {
            "Aesthetic": "Aesthetic.pptx",
            "Minimalist": "Minimalist.pptx",
            "Professional": "Professional.pptx",
            "Vintage": "Vintage.pptx",
            "Corporate": "Corporate.pptx",
            "Creative": "Creative.pptx",
            "Modern": "Modern.pptx",
            "Simple": "Simple.pptx"
        }
        
        # Define font settings (fallback for missing template styles)
        self.FONTS = {
            'title': {
                'name': 'Calibri',
                'size': Pt(44),
                'bold': True,
                'color': RGBColor(33, 33, 33)
            },
            'subtitle': {
                'name': 'Calibri',
                'size': Pt(32),
                'bold': False,
                'color': RGBColor(0, 123, 255)
            },
            'body': {
                'name': 'Calibri',
                'size': Pt(18),
                'bold': False,
                'color': RGBColor(33, 37, 41)
            }
        }

    def get_template_path(self, style: str) -> str:
        """Get the path to the selected template style"""
        filename = self.TEMPLATE_STYLES.get(style, "Professional.pptx")  # Default to Professional if style not found
        # Get absolute path to the project root directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        template_path = os.path.join(project_root, "app", "static", "presentations", "custom_styles", filename)
        print(f"Debug - Using template path: {template_path}")
        return template_path

    def _remove_all_slides(self, prs: Presentation) -> None:
        """Remove all existing slides from the presentation while preserving the template"""
        xml_slides = prs.slides._sldIdLst
        slides = list(xml_slides)  # Create a list from iterator
        for slide_id in slides:
            xml_slides.remove(slide_id)

    def _apply_text_style(self, shape, style_type='body'):
        """Apply text style to a shape (only used if template styles are missing)"""
        if not shape.has_text_frame:
            return

        style = self.FONTS[style_type]
        text_frame = shape.text_frame
        text_frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        
        for paragraph in text_frame.paragraphs:
            paragraph.font.name = style['name']
            paragraph.font.size = style['size']
            paragraph.font.bold = style['bold']
            paragraph.font.color.rgb = style['color']
            if style_type in ['title', 'subtitle']:
                paragraph.alignment = PP_ALIGN.CENTER

    def _get_layout_by_name(self, prs: Presentation, layout_name: str) -> Optional[any]:
        """Find a slide layout by its name"""
        for layout in prs.slide_layouts:
            if layout.name.lower() == layout_name.lower():
                return layout
        return None

    def _get_title_layout(self, prs: Presentation) -> any:
        """Get the title slide layout
        Strictly selects a proper title slide layout for the first slide.
        """
        # First check if we have any layouts
        if not prs.slide_layouts:
            raise ValueError("Template has no slide layouts")

        # First priority: Look for layout named "Title Slide"
        for layout in prs.slide_layouts:
            if layout.name and "Title Slide" in layout.name:
                print(f"Debug - Found Title Slide layout: {layout.name}")
                return layout

        # Second priority: Check common title slide names
        title_names = ["Title", "Cover", "Cover Page", "Opening"]
        for layout in prs.slide_layouts:
            if any(name.lower() in layout.name.lower() for name in title_names):
                print(f"Debug - Found title layout by name: {layout.name}")
                return layout

        # Last resort: Use the first layout (usually the title layout)
        print(f"Debug - Using first layout as title: {prs.slide_layouts[0].name}")
        return prs.slide_layouts[0]

    def _get_content_layout(self, prs: Presentation) -> any:
        """Get the content slide layout
        Strictly selects a proper content slide layout (not the title slide layout).
        """
        # First check if we have any layouts
        if not prs.slide_layouts:
            raise ValueError("Template has no slide layouts")

        # Skip the title slide layout
        title_layout = self._get_title_layout(prs)

        # First priority: Look for "Title and Content" layout
        for layout in prs.slide_layouts:
            if layout != title_layout and layout.name and "Title and Content" in layout.name:
                print(f"Debug - Found Title and Content layout: {layout.name}")
                return layout

        # Second priority: Look for any content-specific layout
        content_names = ["Content", "Text and Content", "Text", "Title and Text", "Section Header", "Two Content"]
        for layout in prs.slide_layouts:
            if layout != title_layout and any(name.lower() in layout.name.lower() for name in content_names):
                print(f"Debug - Found content layout by name: {layout.name}")
                return layout

        # Last resort: Use any layout that's not the title layout
        for layout in prs.slide_layouts:
            if layout != title_layout:
                print(f"Debug - Using alternate layout: {layout.name}")
                return layout

        # Absolute fallback: Use the second layout if available
        if len(prs.slide_layouts) > 1:
            print(f"Debug - Using second layout: {prs.slide_layouts[1].name}")
            return prs.slide_layouts[1]

        # If all else fails, use any non-first layout
        for i, layout in enumerate(prs.slide_layouts):
            if i > 0:
                print(f"Debug - Using layout {i}: {layout.name}")
                return layout

        print("Warning: Could not find distinct content layout")
        return prs.slide_layouts[0]
        # Content names are already defined above
        for name in layout_names:
            layout = self._get_layout_by_name(prs, name)
            if layout:
                print(f"Debug - Found named content layout: {name}")
                return layout
        
        # Try to find any layout with at least a title placeholder
        for layout in prs.slide_layouts:
            if has_placeholder_type(layout, 1):  # Has title
                print(f"Debug - Using layout with title: {layout.name}")
                return layout
        
        # Final fallback to first layout
        print(f"Debug - Using fallback first layout: {prs.slide_layouts[0].name}")
        return prs.slide_layouts[0]

    def _add_title_slide(self, prs: Presentation, title: str, presenter: str):
        """Add title slide"""
        layout = self._get_title_layout(prs)
        slide = prs.slides.add_slide(layout)
        
        # Add title if title placeholder exists
        if slide.shapes.title:
            title_frame = slide.shapes.title.text_frame
            title_frame.text = title
            title_frame.word_wrap = True
            
            # Adjust font size based on title length
            if len(title) > 50:
                font_size = 32
            elif len(title) > 30:
                font_size = 40
            else:
                font_size = 44
                
            for paragraph in title_frame.paragraphs:
                paragraph.font.size = Pt(font_size)
        
        # Add subtitle to the standard subtitle placeholder (type 2)
        for shape in slide.placeholders:
            print(f"Debug - Found placeholder: type={shape.placeholder_format.type}, idx={shape.placeholder_format.idx}")
            # First try standard subtitle placeholder (type 2)
            if shape.placeholder_format.type == 2 and shape != slide.shapes.title:
                # Found a standard subtitle placeholder
                subtitle_frame = shape.text_frame
                subtitle_frame.text = f"Presented by {presenter}"
                subtitle_frame.word_wrap = True
                # Set subtitle font size
                for paragraph in subtitle_frame.paragraphs:
                    paragraph.font.size = Pt(24)
                return  # Exit after finding standard subtitle placeholder

        # Fallback: Look for any empty text placeholder that's not the title
        for shape in slide.placeholders:
            if (hasattr(shape, 'text') and 
                not shape.text and 
                shape != slide.shapes.title and
                shape.placeholder_format.type != 1):  # Ensure it's not another title placeholder
                # Found a potential subtitle placeholder
                subtitle_frame = shape.text_frame
                subtitle_frame.text = f"Presented by {presenter}"
                subtitle_frame.word_wrap = True
                # Set subtitle font size
                for paragraph in subtitle_frame.paragraphs:
                    paragraph.font.size = Pt(24)
                break

    def _add_content_slide(self, prs: Presentation, title: str, content: str):
        """Add content slide"""
        layout = self._get_content_layout(prs)
        print(f"Debug - Using layout: {layout.name} for content slide")
        slide = prs.slides.add_slide(layout)
        print(f"Debug - Available placeholders in slide: {[f'{ph.placeholder_format.type}:{ph.placeholder_format.idx}' for ph in slide.placeholders]}")
        
        # Add title if title placeholder exists
        if slide.shapes.title:
            slide.shapes.title.text = title
        
        # Find content placeholder using multiple approaches
        content_placeholder = None
        print("Debug - Searching for content placeholder...")
        for shape in slide.placeholders:
            print(f"Debug - Checking placeholder: type={shape.placeholder_format.type}, idx={shape.placeholder_format.idx}")
            # Skip the title placeholder entirely
            if shape == slide.shapes.title:
                continue

            # Accept this placeholder if it is a body/content placeholder OR an empty text placeholder
            is_body_placeholder = shape.placeholder_format.type in [2, 7]  # Body (2) or Content (7)
            is_common_content_idx = shape.placeholder_format.idx in [1, 2]
            is_empty_text_placeholder = hasattr(shape, 'text') and not shape.text

            if is_body_placeholder or (is_common_content_idx and not is_body_placeholder) or is_empty_text_placeholder:
                content_placeholder = shape
                print(f"Debug - Found content placeholder: type={shape.placeholder_format.type}, idx={shape.placeholder_format.idx}")
                break
        
        # Add content if placeholder exists
        if content_placeholder:
            try:
                # Ensure content placeholder sits below the title to avoid overlap
                if slide.shapes.title is not None:
                    title_bottom = slide.shapes.title.top + slide.shapes.title.height
                    margin = Pt(10)
                    if content_placeholder.top < title_bottom + margin:
                        # Move content placeholder just below title
                        delta = (title_bottom + margin) - content_placeholder.top
                        content_placeholder.top += delta
                        # Reduce height so it still fits on the slide
                        if content_placeholder.height > delta:
                            content_placeholder.height -= delta
                
                text_frame = content_placeholder.text_frame
                text_frame.word_wrap = True
                # Shrink text automatically to prevent overflow beyond placeholder bounds
                text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                
                # Add bullet points
                first = True
                for point in content.split('\n'):
                    if first:
                        p = text_frame.paragraphs[0]
                        first = False
                    else:
                        p = text_frame.add_paragraph()
                    
                    p.text = point.strip()
                    p.level = 0
                    p.space_before = Pt(12)
                    p.space_after = Pt(12)
                    p.line_spacing = 1.2
                print("Debug - Successfully added content to slide")
            except Exception as e:
                print(f"Debug - Error adding content to placeholder: {str(e)}")
        else:
            error_msg = "No suitable content placeholder found in the selected template"
            print(f"Debug - {error_msg}")
            raise ValueError(error_msg)


    def generate_title(self, description: str) -> str:
        """Generate an intelligent, professional title from the user's description"""
        try:
            system_prompt = """You are a professional presentation title generator. Your task is to create a polished, 
            engaging title from a given description. The title should be:
            - Professional and presentation-appropriate
            - Concise (max 60 characters)
            - Capture the key theme
            - Use proper title case
            - Optionally use a colon to separate main title and subtitle
            DO NOT use quotes or any special formatting. Return ONLY the title text."""

            user_prompt = f"Create a professional presentation title from this description: {description}"

            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=60,
                temperature=0.7
            )

            title = response.choices[0].message.content.strip()
            print(f"Debug - Generated title: {title}")
            return title

        except Exception as e:
            print(f"Warning: Could not generate title: {str(e)}. Using description as fallback.")
            return description

    def generate_slide_content(self, prompt: str, num_slides: int) -> List[Dict]:
        """Generate slide content using GPT-3.5"""
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a presentation content generator. Generate a JSON object with exactly this structure:\n"
                        "{\"slides\": [{\"title\": \"string\", \"content\": [\"string\"]}]}"
                        "\nThe 'slides' array MUST contain exactly the requested number of slides where:"
                        "\n- 'title' is the slide title"
                        "\n- 'content' is an array of 4-5 bullet points as strings"
                        ""
"\n- The FIRST slide must be an 'Agenda' slide outlining the main sections."
"\n- The LAST slide must be an 'Outro' or 'Conclusion' slide summarizing key takeaways."
                        "\nDo not include any explanation or other text, just the JSON."
                        "\nEnsure you generate exactly the requested number of unique slides."
                    )
                },
                {
                    "role": "user",
                    "content": (
                      f"Create exactly {num_slides} unique slides about: {prompt}. "
                      f"The slides must follow this order: "
                      f"1) Agenda slide; "
                      f"(n-1) topic slides; "
                      f"last slide titled 'Conclusion' or 'Outro'. "
                      f"Each slide must have a unique title and exactly 4-5 concise bullet points (maximum 5)."
                )
                }
            ]
            
            response = self.client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.7,
                max_tokens=2000
            )
        
            # Extract and parse the response
            response_text = response.choices[0].message.content
            print(f"Debug - OpenAI Response: {response_text}")
            response_data = json.loads(response_text)
            
            if not isinstance(response_data, dict) or 'slides' not in response_data:
                raise ValueError("Invalid response format from GPT. Expected object with 'slides' array.")
            
            slides_data = response_data['slides']
            print(f"Debug - Number of slides in response: {len(slides_data)}")
            
            if not isinstance(slides_data, list):
                raise ValueError("Invalid 'slides' format. Expected array.")
            
            # Ensure we have the right number of slides and format the content
            if len(slides_data) < num_slides:
                raise ValueError(f"OpenAI returned only {len(slides_data)} slides, but {num_slides} were requested")

            slides = []
            seen_titles = set()

            for slide in slides_data:
                if not isinstance(slide, dict) or 'title' not in slide or 'content' not in slide:
                    raise ValueError("Invalid slide format. Expected object with 'title' and 'content'.")
                
                if not isinstance(slide['content'], list):
                    raise ValueError("Invalid content format. Expected array of strings.")
                
                if slide['title'] in seen_titles:
                    continue  # Skip duplicate titles
                
                seen_titles.add(slide['title'])
                
                # Join content without bullet points - PowerPoint will add them automatically
                formatted_content = "\n".join(point.strip() for point in slide['content'])
                slides.append({
                    "title": slide['title'],
                    "content": formatted_content
                })
                print(f"Debug - Added slide: {slide['title']}")
                
                if len(slides) == num_slides:
                    break

            if len(slides) < num_slides:
                raise ValueError(f"Could not generate enough unique slides. Got {len(slides)}, needed {num_slides}")
                
            return slides
        except json.JSONDecodeError as e:
            raise Exception(f"Error parsing GPT response: {str(e)}")
        except ValueError as e:
            raise Exception(f"Error in response format: {str(e)}")
        except Exception as e:
            raise Exception(f"Error generating content: {str(e)}")

    def create_presentation(self,
                    title: str,
                    presenter: str,
                    slides_content: List[Dict],
                    template_style: str = "Professional",
                    include_images: bool = False) -> str:
        """Create PowerPoint presentation using a selected template style"""
        # Generate an intelligent title from the input description
        presentation_title = self.generate_title(title)
        # Load template
        template_path = self.get_template_path(template_style)
        if not os.path.exists(template_path):
            raise ValueError(f"Template file not found: {template_path}")
        try:
            prs = Presentation(template_path)
            # Remove any existing slides while preserving the template
            self._remove_all_slides(prs)
        except Exception as e:
            print(f"Warning: Could not load template {template_path}. Using blank presentation. Error: {str(e)}")
            prs = Presentation()

        # Add slides
        print(f"Debug - Adding title slide with generated title: {presentation_title}")
        self._add_title_slide(prs, presentation_title, presenter)
        
        print(f"Debug - Number of content slides to add: {len(slides_content)}")
        for slide_content in slides_content:
            # Add content slide first
            print(f"Debug - Adding content slide: {slide_content['title']}")
            self._add_content_slide(prs, slide_content['title'], slide_content['content'])

            # Optionally add an image generated by DALL·E 3
            if include_images:
                try:
                    img_prompt = f"{slide_content['title']} illustrative image"
                    img_path = self._generate_image(img_prompt)
                    if img_path:
                        # Add the picture roughly on the right half of the slide
                        slide = prs.slides[-1]
                        # Try to insert into a dedicated picture placeholder if present
                        pic_placeholder = None
                        for shp in slide.placeholders:
                            try:
                                if shp.placeholder_format.type == PP_PLACEHOLDER.PICTURE:
                                    pic_placeholder = shp
                                    break
                            except Exception:
                                pass

                        if pic_placeholder:
                            pic_placeholder.insert_picture(img_path)
                            # Determine left bound of image for text wrap adjustment
                            left = pic_placeholder.left
                            pic_width = pic_placeholder.width
                        else:
                            # Fallback: place on right half, respecting slide margins
                            pic_width = Inches(4)
                            left = prs.slide_width - pic_width - Inches(0.5)
                            top = Inches(1.5)
                            slide.shapes.add_picture(img_path, left, top, width=pic_width)
                        # Reduce width of text-containing shapes to avoid overlap
                        available_width = left - Inches(0.3)
                        for shp in slide.shapes:
                            # Skip pictures
                            if shp.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                                continue
                            if hasattr(shp, "text_frame") and shp.text_frame is not None:
                                # Only shrink if current right edge goes beyond image left
                                if shp.left + shp.width > available_width:
                                    shp.width = max(Inches(2), available_width - shp.left)
                        print("Debug - Image added to slide")
                except Exception as e:
                    print(f"Warning - Could not add image to slide: {str(e)}")



        # Get the absolute path to the generated directory
        generated_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'generated'))
        
        # Ensure output directory exists
        os.makedirs(generated_dir, exist_ok=True)

        # Clean filename - more restrictive sanitization
        def sanitize_filename(s: str, max_length: int = 50) -> str:
            # Replace any non-alphanumeric chars with underscore
            safe = ''.join(c if c.isalnum() else '_' for c in s)
            # Remove repeated underscores
            while '__' in safe:
                safe = safe.replace('__', '_')
            # Trim to max length and remove trailing underscores
            safe = safe[:max_length].rstrip('_')
            return safe or 'presentation'  # Fallback if empty
        
        filename = f"{sanitize_filename(title)}.pptx"
        output_path = os.path.join(generated_dir, filename)

        prs.save(output_path)
        return output_path
