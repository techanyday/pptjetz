# PPTJet Transcriber module

# Lazy/optional export: avoid requiring FastAPI in Flask runtime.
# In a FastAPI context, you can `from app.transcriber import router` and it will succeed
# provided FastAPI is installed. In Flask-only deployments, this remains harmless.
try:  # pragma: no cover - simple import guard
    from .routes import router  # type: ignore
except Exception:  # FastAPI not installed or other import-time errors
    router = None  # type: ignore
