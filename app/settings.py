# app/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

def first_not_none(*vals):
    for v in vals:
        if v:
            return v
    return None

class Settings:
    SECRET_KEY: str = os.getenv("SECRET_KEY", "change-me")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:Joe72022%40@127.0.0.1:5432/sahayidb"
    )

    # Twilio: accept both the new and your old key names
    TWILIO_ACCOUNT_SID: str | None = first_not_none(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_SID"),                 # <-- fallback to your .env
    )
    TWILIO_AUTH_TOKEN: str | None = first_not_none(
        os.getenv("TWILIO_AUTH_TOKEN"),
        os.getenv("TWILIO_AUTH"),                # <-- fallback to your .env
    )
    TWILIO_PHONE: str | None = os.getenv("TWILIO_PHONE")
    TWILIO_MESSAGING_SERVICE_SID: str | None = os.getenv("TWILIO_MESSAGING_SERVICE_SID")

    GMAIL_USER: str | None = os.getenv("GMAIL_USER")
    GMAIL_PASS: str | None = os.getenv("GMAIL_PASS")

    MAPBOX_ACCESS_TOKEN: str | None = os.getenv("MAPBOX_ACCESS_TOKEN")
    WALLET_HMAC_KEY: str | None = os.getenv("WALLET_HMAC_KEY")

    UPLOAD_FOLDER: Path = BASE_DIR / "static" / "uploads"
    ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif"}
    ALLOWED_VIDEO_EXT = {"mp4", "mov", "avi"}

settings = Settings()

# quick diagnostics
print(
    "[ENV] TWILIO_ACCOUNT_SID:", "set" if settings.TWILIO_ACCOUNT_SID else "MISSING",
    "| TWILIO_AUTH_TOKEN:", "set" if settings.TWILIO_AUTH_TOKEN else "MISSING",
    "| TWILIO_PHONE:", "set" if settings.TWILIO_PHONE else "MISSING",
    "| MSG_SVC_SID:", "set" if settings.TWILIO_MESSAGING_SERVICE_SID else "MISSING",
)
