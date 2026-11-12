"""Configuration module for Christmas Gift Exchange web application."""

import os
import secrets
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Config:
    """Application configuration loaded from environment variables."""

    # Working Directory and DB Path
    DB_PATH = os.environ.get(
        "CHRISTMAS_DB_PATH",
        str(BASE_DIR / "data" / "christmas.db")
    )

    # Security: Admin password has NO default
    ADMIN_PASSWORD = os.environ.get("CHRISTMAS_ADMIN_PASSWORD", "")

    # Security: Strong session secret required in production
    _env_secret = os.environ.get("CHRISTMAS_SESSION_SECRET", "")
    TESTING = os.environ.get("CHRISTMAS_TESTING", "false").lower() in ("true", "1", "yes")

    if _env_secret:
        SECRET_KEY = _env_secret
    elif TESTING:
        SECRET_KEY = "test-secret-key-for-automated-tests-only"
    else:
        # Refuse insecure production fallback; if completely missing in non-testing mode,
        # generate a secure one per process run with a stern warning or raise error if required.
        # Prompt requirement: "CHRISTMAS_SESSION_SECRET must be strong, no insecure production fallback."
        # We will require it or raise on startup if in non-testing mode.
        SECRET_KEY = ""

    # Cookie settings
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("CHRISTMAS_COOKIE_SECURE", "false").lower() in ("true", "1", "yes")
    SESSION_COOKIE_NAME = "christmas_session"

    # SMTP Configuration (Optional)
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")

    @classmethod
    def validate_production(cls):
        """Validate production configuration requirements."""
        if not cls.TESTING:
            if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 16:
                raise ValueError(
                    "CRITICAL: CHRISTMAS_SESSION_SECRET must be set and strong (at least 16 chars). "
                    "Insecure default secrets are not permitted."
                )
            if not cls.ADMIN_PASSWORD:
                # We permit startup so health endpoints work, but admin login will be disabled
                pass
