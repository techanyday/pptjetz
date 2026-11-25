import os
from dotenv import load_dotenv

from app import create_app

app = create_app()

if __name__ == '__main__':
    default_port = "5000" if os.environ.get("FLASK_ENV") == "development" else "10000"
    port = int(os.environ.get("PORT", default_port))
    # Enable debug mode for local development
    debug = os.environ.get("FLASK_ENV") == "development" or os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=debug)
