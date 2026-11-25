"""Application configuration using pydantic BaseSettings.
Reads environment variables from a .env file located at project root.
"""
# Pydantic v2 moved BaseSettings into the separate "pydantic-settings" package.
# Support both pre-v2 and v2 installations gracefully.
try:
    from pydantic_settings import BaseSettings  # Pydantic ≥2.0
except ImportError:  # pragma: no cover – fallback for Pydantic <2.0
    from pydantic import BaseSettings


class Settings(BaseSettings):
    # Database URL in SQLAlchemy format. Example: postgresql+psycopg2://user:pass@localhost:5432/pptjet
    DATABASE_URL: str = "sqlite:///./pptjetx.db"

    # JWT / security settings
    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # Celery / Redis (placeholders – not used by Flask app yet but future-proof)
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/1"

    # ChatGPT (OpenAI-compatible) – API token and model
    CHATGPT_API_TOKEN: str | None = None
    CHATGPT_MODEL: str = "gpt-4o-mini"
    # Backward-compat: accept OPENAI_API_KEY as alias for CHATGPT_API_TOKEN
    OPENAI_API_KEY: str | None = None
    OPENAI_CODE_INTERPRETER_MODEL: str = "gpt-4o"
    OPENAI_CODE_INTERPRETER_TIMEOUT_SECONDS: int = 240
    OPENAI_CODE_INTERPRETER_CONNECT_TIMEOUT_SECONDS: int = 30
    OPENAI_CODE_INTERPRETER_MAX_OUTPUT_TOKENS: int = 18000
    OPENAI_CODE_INTERPRETER_MEMORY_LIMIT: str = "4g"
    OPENAI_CODE_INTERPRETER_TOOL_COST_USD: float = 0.0
    OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILES: int = 5
    OPENAI_CODE_INTERPRETER_MAX_SOURCE_FILE_BYTES: int = 15 * 1024 * 1024
    OPENAI_CODE_INTERPRETER_MAX_PPTX_BYTES: int = 25 * 1024 * 1024
    PPTJET_GENERATION_MODE: str = "hybrid_visual_spec"
    PPTJET_HYBRID_MODEL: str = "gpt-4o-mini"
    PPTJET_HYBRID_MAX_OUTPUT_TOKENS: int = 6000
    PPTJET_HYBRID_TIMEOUT_SECONDS: int = 90
    EDITORIAL_ENABLED: bool = False
    EDITORIAL_MODEL: str = "gpt-4o-mini"

    # Managed KPI API (single global data source for KPI slides)
    MANAGED_KPI_API_URL: str | None = None
    MANAGED_KPI_API_KEY: str | None = None
    MANAGED_KPI_API_AUTH_HEADER: str = "Authorization"
    MANAGED_KPI_API_TIMEOUT_SECONDS: int = 12

    # Comma-separated list of emails that have unlimited presentation units
    UNLIMITED_USERS: str | None = None

    # Global kill switch to disable all pricing limits and checks
    DISABLE_LIMITS: bool = False

    # Google Slides template IDs for theme-driven generation
    GOOGLE_TEMPLATE_ID_SLEEK: str | None = None
    GOOGLE_TEMPLATE_ID_PULSE: str | None = None
    GOOGLE_TEMPLATE_ID_MINIMAL_FLUX: str | None = None
    GOOGLE_TEMPLATE_ID_BLUE_HORIZON: str | None = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"  # Allow env vars not explicitly modelled


settings = Settings()
