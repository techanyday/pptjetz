from flask import Flask
from dotenv import load_dotenv
import os

# Load environment variables at app initialization
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
print(f"Loading .env from: {env_path}")

# Force reload environment variables
with open(env_path, 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

# Verify API key is loaded
api_key = os.environ.get('OPENAI_API_KEY')
if api_key:
    print(f"Debug - API Key in __init__.py: {api_key[:10]}...")
else:
    print("WARNING: No OpenAI API key found!")

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
