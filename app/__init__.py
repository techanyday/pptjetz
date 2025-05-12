from flask import Flask
from dotenv import load_dotenv
import os

# Load environment variables
try:
    # Try to load from .env file if it exists (local development)
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"Loaded environment variables from: {env_path}")
    else:
        print("No .env file found, using system environment variables")

    # Verify API key is loaded
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        print("API key loaded successfully")
    else:
        print("WARNING: No OpenAI API key found!")
except Exception as e:
    print(f"Warning: Error loading environment variables: {str(e)}")

def create_app():
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    )
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-this')
    
    # Add debug prints for template loading
    print(f"Template folder path: {app.template_folder}")
    print(f"Static folder path: {app.static_folder}")
    
    # Ensure the generated directory exists
    os.makedirs(os.path.join(os.path.dirname(app.root_path), 'generated'), exist_ok=True)
    
    # Register blueprint with URL prefix
    from .routes import bp as main_bp
    app.register_blueprint(main_bp, url_prefix='/')
    
    return app
