"""Konfiguration der Wulmstörper Tipprunde."""
import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))


class Config:
    # Sicherheit
    SECRET_KEY = os.environ.get("SECRET_KEY") or "change-me-in-production-please-use-32-chars-min"
    WTF_CSRF_ENABLED = True

    # Datenbank
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or \
        "sqlite:///" + os.path.join(basedir, "tippspiel.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Redis Cache (optional)
    REDIS_URL = os.environ.get("REDIS_URL")  # z.B. "redis://localhost:6379/0"

    # Sessions
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # Uploads
    UPLOAD_FOLDER = os.path.join(basedir, "static", "uploads")
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # 4 MB
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

    # Mail (Gmail-Beispiel)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@tippspiel.local")

    # APIs
    FOOTBALL_DATA_TOKEN = os.environ.get("FOOTBALL_DATA_TOKEN", "")
    OPENLIGADB_BASE = "https://api.openligadb.de"
    FOOTBALL_DATA_BASE = "https://api.football-data.org/v4"

    # Saison (Bundesliga 2025/26)
    SEASON = "2025"
    COMPETITION = "BL1"

    # Punkteberechnung (Default, in DB überschreibbar)
    POINTS_EXACT = 4
    POINTS_DIFF = 3
    POINTS_TENDENCY = 2

    # VAPID Keys (für Web Push)
    VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
    VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:admin@tippspiel.de")

    # Default-Admin (beim ersten Start automatisch angelegt)
    ADMIN_EMAIL    = os.environ.get("ADMIN_EMAIL",    "admin@tippspiel.de")
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")


class TestConfig(Config):
    """Konfiguration fuer Tests."""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    MAIL_SUPPRESS_SEND = True
    REDIS_URL = None  # Cache deaktiviert in Tests
