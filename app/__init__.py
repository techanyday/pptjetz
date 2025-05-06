from flask import Flask
from dotenv import load_dotenv
import os

# Load environment variables at app initialization
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
print(f"Loading .env from: {env_path}")
load_dotenv(env_path)

# Set environment variables explicitly after loading
api_key = os.environ.get('OPENAI_API_KEY')
if api_key:
    print("API Key loaded.")
else:
    print("WARNING: No OpenAI API key found!")

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-this')
    
    # Ensure the generated directory exists
    os.makedirs(os.path.join(os.path.dirname(app.root_path), 'generated'), exist_ok=True)
    
    # Register blueprint with URL prefix
    from .routes import bp as main_bp
    app.register_blueprint(main_bp, url_prefix='/')
    
    return app
