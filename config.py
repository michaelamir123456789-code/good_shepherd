"""
config.py — Configuration for الراعي الصالح.

Security notes
--------------
* In production the app REFUSES to start without a strong SECRET_KEY.
* In development a random key is generated per start (sessions reset on restart).
"""
import os
import secrets
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def resolve_db_uri(uri: str) -> str:
    """
    A relative SQLite path (e.g. DATABASE_URL=sqlite:///instance/app.db copied from an
    old .env) is resolved against the PROJECT folder, so it always means what it says.
    Without this SQLAlchemy looks inside instance/instance/ and fails with
    "unable to open database file".
    """
    prefix = "sqlite:///"
    if uri.startswith(prefix) and not uri.startswith("sqlite:////"):
        path = uri[len(prefix):]
        if path and path != ":memory:" and not os.path.isabs(path):
            return prefix + os.path.join(BASE_DIR, path)
    return uri


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    DEBUG = False
    TESTING = False

    # ---- Branding -------------------------------------------------------
    SITE_NAME_AR = "الراعي الصالح"
    SITE_NAME_EN = "The Good Shepherd"
    SITE_TAGLINE_AR = "نظام إدارة خدمة مدارس الأحد"
    SITE_TAGLINE_EN = "Sunday School Management System"
    VERSE_AR = ("«أَنَا هُوَ الرَّاعِي الصَّالِحُ، "
                "وَالرَّاعِي الصَّالِحُ يَبْذِلُ نَفْسَهُ عَنِ الْخِرَافِ»")
    VERSE_REF_AR = "يوحنا ١٠: ١١"
    VERSE_EN = ('"I am the good shepherd: '
                'the good shepherd giveth his life for the sheep."')
    VERSE_REF_EN = "John 10:11"

    # ---- Database -------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = resolve_db_uri(
        (os.environ.get("DATABASE_URL") or "").strip()
        or "sqlite:///" + os.path.join(BASE_DIR, "instance", "sunday_school.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    if SQLALCHEMY_DATABASE_URI.startswith("sqlite"):
        SQLALCHEMY_ENGINE_OPTIONS["connect_args"] = {"check_same_thread": False}

    # ---- Sessions / cookies --------------------------------------------
    SESSION_COOKIE_NAME = "shepherd_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_REFRESH_EACH_REQUEST = True
    REMEMBER_COOKIE_HTTPONLY = True

    # ---- CSRF -----------------------------------------------------------
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600 * 8
    WTF_CSRF_SSL_STRICT = True

    # ---- Uploads --------------------------------------------------------
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024      # hard cap for any request (phone photos are big)
    IMPORT_MAX_BYTES = 5 * 1024 * 1024
    PHOTO_SIZE_PX = 320
    IMPORT_MAX_ROWS = 3000

    # ---- Locale ---------------------------------------------------------
    SUPPORTED_LANGUAGES = ("ar", "en")
    DEFAULT_LANGUAGE = "ar"
    TIMEZONE = "Africa/Cairo"

    # ---- Business rules -------------------------------------------------
    ABSENCE_THRESHOLD_DEFAULT = 3
    ATTENDANCE_LOCK_MINUTES = 15
    ATTENDANCE_EDIT_WINDOW_DAYS = 14   # servants can correct this far back
    VISIT_OVERDUE_DAYS = 30
    PASSWORD_MIN_LENGTH = 8
    PASSWORD_MAX_LENGTH = 128
    MAX_LOGIN_ATTEMPTS = 10
    LOGIN_LOCKOUT_MINUTES = 30

    # ---- Proxy / rate limiting -----------------------------------------
    TRUSTED_PROXY_COUNT = _int_env("TRUSTED_PROXY_COUNT", 0)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True
    # Churches share one public IP (Wi-Fi / NAT), so keep the global cap generous.
    RATELIMIT_DEFAULT = "240 per minute"

    # ---- Security headers ----------------------------------------------
    ENABLE_HSTS = False


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"
    ENABLE_HSTS = True


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "testing-only-key-not-for-real-use-0123456789"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    RATELIMIT_ENABLED = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("FLASK_ENV", "development")
    return config.get(name, DevelopmentConfig)


_WEAK_PREFIXES = ("dev", "change", "secret", "test", "password", "your")


def ensure_secret_key(app) -> None:
    """Refuse weak keys in production; auto-generate an ephemeral one in dev."""
    key = app.config.get("SECRET_KEY")
    if app.config.get("TESTING"):
        return
    if app.config.get("DEBUG"):
        if not key:
            app.config["SECRET_KEY"] = secrets.token_hex(32)
        return
    if not key or len(key) < 32 or key.lower().startswith(_WEAK_PREFIXES):
        raise RuntimeError(
            "SECRET_KEY is missing or weak. Generate one with:\n"
            '  python -c "import secrets; print(secrets.token_hex(32))"\n'
            "and put it in your .env file."
        )
