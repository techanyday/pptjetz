# WSGI entry point for Phusion Passenger on Bluehost
# It simply exposes the existing Flask app instance created in run.py

from run import app as application
