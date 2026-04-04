"""Django settings for pyfx dashboard."""

from pyfx.core.config import settings as pyfx_settings

pyfx_settings.db_path.parent.mkdir(parents=True, exist_ok=True)

SECRET_KEY = pyfx_settings.secret_key
DEBUG = pyfx_settings.debug
ALLOWED_HOSTS = pyfx_settings.allowed_hosts

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "pyfx.web.dashboard",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
]

ROOT_URLCONF = "pyfx.web.pyfx_web.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(pyfx_settings.db_path),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

STATIC_URL = "static/"

USE_TZ = True
TIME_ZONE = "UTC"
