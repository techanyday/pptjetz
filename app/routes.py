from flask import Blueprint, render_template, request, jsonify, url_for, send_from_directory, current_app
from .utils.ppt_generator import PPTGenerator
import os

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return render_template('index.html')

@bp.route('/generate', methods=['POST'])
def generate_presentation():
    try:
        # Get JSON data
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'No JSON data received'
            }), 400

        # Extract and validate required fields
        title = data.get('title')
        presenter = data.get('presenter')
        prompt = data.get('prompt')
        
        if not all([title, presenter, prompt]):
            return jsonify({
                'success': False,
                'error': 'Missing required fields: title, presenter, and prompt are required'
            }), 400
        
        # Extract optional fields with defaults
        num_slides = int(data.get('num_slides', 5))
        template_style = data.get('template_style', 'Professional')
        include_images = bool(data.get('include_images', False))  # Convert to boolean

        # Initialize PPT generator
        ppt_gen = PPTGenerator()
        
        try:
            # Generate slide content
            slides_content = ppt_gen.generate_slide_content(prompt, num_slides)
            
            # Create presentation
            output_path = ppt_gen.create_presentation(
                title=title,
                presenter=presenter,
                slides_content=slides_content,
                template=template_style,
                include_images=include_images
            )
            
            # Get filename from path
            filename = os.path.basename(output_path)
            
            return jsonify({
                'success': True,
                'filename': filename,
                'file_url': url_for('main.download', filename=filename)
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'Error generating presentation: {str(e)}'
            }), 500

    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Server error: {str(e)}'
        }), 500

@bp.route('/download/<filename>')
def download(filename):
    # Get the absolute path to the generated directory
    generated_dir = os.path.join(current_app.root_path, '..', 'generated')
    return send_from_directory(generated_dir, filename, as_attachment=True)
