import os
from dotenv import load_dotenv

# Load environment variables at startup
env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')
print("Loading .env from:", os.path.abspath(env_path))
load_dotenv(env_path)

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from oauthlib.oauth2 import WebApplicationClient

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

# OAuth 2 client setup
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

def create_app():
    # Allow OAuth over HTTP in development
    if os.environ.get('FLASK_ENV') == 'development':
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    # Create Flask app
    app = Flask(
        __name__,
        static_folder='static',  # Relative to the app package
        static_url_path='/static'
    )
    
    # Configure CORS
    CORS(app, resources={
        r"/*": {
            "origins": [
                "http://localhost:5000",
                "https://pptjet-dev.onrender.com"
            ],
            "supports_credentials": True
        }
    })
    
    # Print static folder absolute path for debugging
    print(f"Debug - Static folder absolute path: {os.path.abspath(os.path.join(os.path.dirname(__file__), 'static'))}")
    
    # Environment variables already loaded at startup
    
    # Load admin email list from environment (comma-separated)
    admin_emails_env = os.getenv('ADMIN_EMAILS', '')
    app.config['ADMIN_EMAILS'] = [e.strip().lower() for e in admin_emails_env.split(',') if e.strip()]

    # Configure app
    app.config['GENERATED_FOLDER'] = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'generated')
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY') or os.urandom(24)
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///app.db')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    
    # Load OpenAI API key
    api_key = os.environ.get('OPENAI_API_KEY')
    if api_key:
        print(f"Debug - API Key loaded: {api_key[:10]}...")
    else:
        print("WARNING: No OpenAI API key found!")
    
    # Configure OAuth client
    client_secrets = {
        "web": {
            "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
            "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": [
                "http://localhost:5000/login/callback",
                "https://pptjet-dev.onrender.com/login/callback"
            ],
            "javascript_origins": [
                "http://localhost:5000",
                "https://pptjet-dev.onrender.com"
            ]
        }
    }

    # Configure OAuth client
    client = WebApplicationClient(client_secrets["web"]["client_id"])
    app.config['OAUTH_CLIENT'] = client

    # Flask-Login helper to retrieve a user from our db
    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.get(user_id)
    
    # Import models so Alembic can detect them
    from .presentation_log import PresentationLog  # noqa: F401

    # Register blueprint with URL prefix
    from .routes import bp as main_bp
    app.register_blueprint(main_bp, url_prefix='/')
    
    # Add debug prints for template loading
    print(f"Template folder path: {app.template_folder}")
    print(f"Static folder path: {app.static_folder}")
    
    # Ensure the generated directory exists
    os.makedirs(os.path.join(os.path.dirname(app.root_path), 'generated'), exist_ok=True)
    
    # Tell Flask to prefer HTTPS when building external URLs
    app.config['PREFERRED_URL_SCHEME'] = 'https'

    # Ensure Flask respects X-Forwarded-Proto/Host headers so generated URLs use HTTPS and correct host on Render
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    return app
