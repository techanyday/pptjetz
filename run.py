import os
from dotenv import load_dotenv

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=False, use_reloader=False)
