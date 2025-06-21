"""WSGI entry point for Passenger/Apache on Bluehost.
Adjust the sys.path if your application root differs.
Bluehost will import the variable named ``application``.
"""

import sys
import os

# Ensure project root is on the path (update the path if needed)
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from run import app as application
