from pathlib import Path
import os

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'rental_tracker.db'}")
BOARD_TOKEN = os.getenv("BOARD_TOKEN", "dev-board")
MAX_RENT = 5500
TARGET_CITIES = {
    "menlo park",
    "palo alto",
    "mountain view",
    "redwood city",
}

APP_BASE_URL = os.getenv("APP_BASE_URL", f"http://127.0.0.1:8000/board/{BOARD_TOKEN}")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
NOTIFY_EMAILS = [
    email.strip()
    for email in os.getenv("NOTIFY_EMAILS", "").split(",")
    if email.strip()
]
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USERNAME)
