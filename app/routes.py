from flask import Blueprint, request, render_template, send_from_directory, jsonify, url_for
from app.utils.ppt_generator import PPTGenerator
import os
from dotenv import load_dotenv

# Load environment variables
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
print("Loading .env from:", os.path.abspath(env_path))
load_dotenv(env_path)
api_key = os.environ.get('OPENAI_API_KEY')
if api_key:
    print(f"Debug - API Key in routes.py: {api_key[:10]}...")
else:
    print("No API Key found.")

bp = Blueprint("main", __name__)
# Get the absolute path to the generated directory
GENERATED_FOLDER = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'generated'))
os.makedirs(GENERATED_FOLDER, exist_ok=True)


@bp.route("/")
def index():
    try:
        print("Attempting to render index.html")
        return render_template("index.html")
    except Exception as e:
        print(f"Error rendering index.html: {str(e)}")
        return f"Error: {str(e)}", 500


@bp.route("/generate", methods=["POST"])
def generate():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No JSON data received"}), 400

        # Extract and validate required fields
        title = data.get("title")
        presenter = data.get("presenter")
        prompt = data.get("prompt")
        
        if not all([title, presenter, prompt]):
            return jsonify({"error": "Missing required fields: title, presenter, or prompt"}), 400

        # Extract optional fields with defaults
        num_slides = int(data.get("num_slides", 5))
        template_style = data.get("template_style", "Professional")

        # Initialize PPT generator
        ppt_generator = PPTGenerator()
        
        try:
            # Generate slide content
            slides_content = ppt_generator.generate_slide_content(prompt, num_slides)
            
            # Create presentation
            filepath = ppt_generator.create_presentation(
                title=title,
                presenter=presenter,
                slides_content=slides_content,
                template=template_style
            )
            
            # Get filename from path
            filename = os.path.basename(filepath)
            
            return jsonify({
                'success': True,
                'filename': filename,
                'file_url': f'/download/{filename}'
            })
            
        except Exception as e:
            return jsonify({"error": f"Error generating presentation: {str(e)}"}), 500

    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


@bp.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(GENERATED_FOLDER, filename, as_attachment=True)

