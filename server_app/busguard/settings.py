"""BusGuard Django settings. Secrets and site-specific values come from the .env file."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env(path):
    """Tiny .env loader, so no extra package is needed."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()]

SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("https://") else []
# The map tile server (OpenStreetMap) blocks requests that don't say which site they come from.
# Django's default ("same-origin") hides it, so send just our site address to other sites.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
# On Azure, Nginx handles https and tells Django with this header, so links (e.g. the phone
# camera link on the Connect page) start with https://
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- BusGuard settings ---
FIGHT_MODEL_PATH = BASE_DIR / os.environ.get("FIGHT_MODEL_PATH", "models/fight_cls.pt")
# A photo counts as "fight" when the AI score is at least this (0.0 - 1.0)
FIGHT_THRESHOLD = float(os.environ.get("FIGHT_THRESHOLD", "0.6"))
# Azure tells the bus to record a clip when FIGHT_HITS of the last FIGHT_WINDOW photos
# look like a fight (e.g. 3 of the last 6), so one blocked or blurry photo doesn't stop it
FIGHT_HITS = int(os.environ.get("FIGHT_HITS", os.environ.get("FIGHT_STREAK", "3")))
FIGHT_WINDOW = int(os.environ.get("FIGHT_WINDOW", "6"))
# A fight score only counts when at least this many people are in the picture (0 = don't check)
FIGHT_MIN_PEOPLE = int(os.environ.get("FIGHT_MIN_PEOPLE", "1"))
# Pictures less sharp than this count as "camera blocked or view unclear" (score 0)
VIEW_MIN_SHARPNESS = float(os.environ.get("VIEW_MIN_SHARPNESS", "80"))
# Seconds before the same bus can trigger another alert
ALERT_COOLDOWN = int(os.environ.get("ALERT_COOLDOWN", "60"))
# Self-learning: where the server keeps training photos, and how long a retrain runs
TRAINING_DIR = BASE_DIR / os.environ.get("TRAINING_DIR", "training_data")
RETRAIN_EPOCHS = int(os.environ.get("RETRAIN_EPOCHS", "3"))
# If an officer hasn't tapped "On my way" within this many seconds, the control room is warned
OFFICER_REPLY_SECONDS = int(os.environ.get("OFFICER_REPLY_SECONDS", "60"))
# Length of the clip the phone camera page records (includes a few seconds before the alert)
PHONE_CLIP_SECONDS = int(os.environ.get("PHONE_CLIP_SECONDS", "20"))
# Show each bus's latest photo in the control room (turn off for privacy)
SHOW_LIVE_FRAMES = os.environ.get("SHOW_LIVE_FRAMES", "True") == "True"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "alerts",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "busguard.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "busguard.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 20},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-au"
TIME_ZONE = "Australia/Darwin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "alerts:home"
LOGOUT_REDIRECT_URL = "login"

DATA_UPLOAD_MAX_MEMORY_SIZE = 60 * 1024 * 1024
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"alerts": {"handlers": ["console"], "level": "INFO"}},
}
