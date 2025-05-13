from flask import Flask
from dotenv import load_dotenv
import os
from flask_login import LoginManager
from oauthlib.oauth2 import WebApplicationClient

# In-memory storage for users (replace with database in production)
users_db = {}

def create_app():
    # Create Flask app
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'),
        static_folder=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    )
    
    # Load environment variables
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
    print("Loading .env from:", os.path.abspath(env_path))
    load_dotenv(env_path)
    
    # Configure app
    app.config['GENERATED_FOLDER'] = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'generated')
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(24)
    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_OAUTH_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET')
    
    # Load OpenAI API key
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        print(f"Debug - API Key loaded: {api_key[:10]}...")
    else:
        print("WARNING: No OpenAI API key found!")
    
    # OAuth 2 client setup
    client = WebApplicationClient(app.config['GOOGLE_CLIENT_ID'])
    app.config['OAUTH_CLIENT'] = client
    
    # User session management setup
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    
    # Flask-Login helper to retrieve a user from our db
    from app.models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.get(user_id)
    
    # Register blueprint with URL prefix
    from .routes import bp as main_bp
    app.register_blueprint(main_bp, url_prefix='/')
    
    # Add debug prints for template loading
    print(f"Template folder path: {app.template_folder}")
    print(f"Static folder path: {app.static_folder}")
    
    # Ensure the generated directory exists
    os.makedirs(os.path.join(os.path.dirname(app.root_path), 'generated'), exist_ok=True)
    
    return app
