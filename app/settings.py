

# app/settings.py
import os
import logging
from pathlib import Path
from dotenv import load_dotenv
from typing import Optional

# Load .env for local development (won't override real env by default)
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)

logger = logging.getLogger("app.settings")

def first_not_none(*vals):
    for v in vals:
        if v is not None and v != "":
            return v
    return None


class Settings:
    # Environment chooser: set ENV=production in your deployment
    ENV: str = os.getenv("ENV", os.getenv("FLASK_ENV", "development")).lower()

    # App secrets and config
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me")  # MUST be changed in prod
    FERNET_KEY: str = os.getenv("FERNET_KEY")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:Joe72022%40@127.0.0.1:5432/sahayidb"
    )

    # Razorpay (same keys you already use for the first payment)
    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")

    #DevONlY
    DEV_OTP_MODE = os.getenv("DEV_OTP_MODE", "false").lower() == "true"
    DEV_OTP_CODE = os.getenv("DEV_OTP_CODE", "123456")

    # --- Auth / token settings ---
    ACCESS_TOKEN_SECRET: str = os.getenv("ACCESS_TOKEN_SECRET", "dev_access_secret_change_me")
    ACCESS_TOKEN_ALGORITHM: str = os.getenv("ACCESS_TOKEN_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_SECONDS: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_SECONDS", str(15 * 60)))

    ACTION_TOKEN_SECRET: str = os.getenv("ACTION_TOKEN_SECRET", "dev_action_secret_change_me")
    ACTION_TOKEN_ALGORITHM: str = os.getenv("ACTION_TOKEN_ALGORITHM", "HS256")
    ACTION_TOKEN_EXPIRE_SECONDS: int = int(os.getenv("ACTION_TOKEN_EXPIRE_SECONDS", str(2 * 60)))

    # Whether to allow debug tokens (only for local dev) — default False for safety
    ALLOW_DEV_TOKENS: bool = os.getenv("ALLOW_DEV_TOKENS", "false").lower() in ("1", "true", "yes")

    # Twilio: accept both new and old env names
    TWILIO_ACCOUNT_SID: Optional[str] = first_not_none(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_SID"))
    TWILIO_AUTH_TOKEN: Optional[str] = first_not_none(os.getenv("TWILIO_AUTH_TOKEN"), os.getenv("TWILIO_AUTH"))
    TWILIO_PHONE: Optional[str] = os.getenv("TWILIO_PHONE")
    TWILIO_MESSAGING_SERVICE_SID: Optional[str] = os.getenv("TWILIO_MESSAGING_SERVICE_SID")

    # Mail / other secrets
    GMAIL_USER: Optional[str] = os.getenv("GMAIL_USER")
    GMAIL_PASS: Optional[str] = os.getenv("GMAIL_PASS")

    MAPBOX_ACCESS_TOKEN: Optional[str] = os.getenv("MAPBOX_ACCESS_TOKEN")
    WALLET_HMAC_KEY: Optional[str] = os.getenv("WALLET_HMAC_KEY")

    # File uploads
    UPLOAD_FOLDER: Path = BASE_DIR / "static" / "uploads"
    ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif"}
    ALLOWED_VIDEO_EXT = {"mp4", "mov", "avi"}

    def is_production(self) -> bool:
        return self.ENV == "production"

    def validate_production(self) -> None:
        """
        Fail fast in production if required secrets are missing or obviously default.
        Call this early in startup if you want strict checks.
        """
        if not self.is_production():
            return

        problems = []
        if not self.SECRET_KEY or self.SECRET_KEY in ("change-me", "", "dev"):
            problems.append("SECRET_KEY is not set to a secure value")
        if not self.ACCESS_TOKEN_SECRET or "dev_access" in self.ACCESS_TOKEN_SECRET:
            problems.append("ACCESS_TOKEN_SECRET not set or still default")
        if not self.ACTION_TOKEN_SECRET or "dev_action" in self.ACTION_TOKEN_SECRET:
            problems.append("ACTION_TOKEN_SECRET not set or still default")
        if not self.DATABASE_URL:
            problems.append("DATABASE_URL is missing")

        if problems:
            # Produce a single clear error rather than printing secrets
            raise RuntimeError("Invalid production config: " + "; ".join(problems))


settings = Settings()

# Lightweight diagnostics via logging (no secrets printed)
logger.info(
    "ENV=%s | ALLOW_DEV_TOKENS=%s | UPLOAD_FOLDER=%s",
    settings.ENV,
    settings.ALLOW_DEV_TOKENS,
    str(settings.UPLOAD_FOLDER)
)

# If you want a stricter startup check, uncomment this line in production startup code:
# settings.validate_production()
