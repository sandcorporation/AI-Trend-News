"""Django settings. Every deploy-specific value comes from the environment (see /.env.example)."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = env("DJANGO_SECRET_KEY") or ("dev-insecure-key" if DEBUG else "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY is required when DJANGO_DEBUG is not 1")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")
# NPMplus terminates TLS and forwards X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "collection",
    "articles",
    "editor",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "news"),
        "USER": env("POSTGRES_USER", "news"),
        "PASSWORD": env("POSTGRES_PASSWORD", "news"),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "15443"),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# Absolute-URL base for sitemap.xml and robots.txt (they have no request host to trust).
SITE_URL = env("SITE_URL", "https://news.sandcorp.co.kr").rstrip("/")
# Django admin lives on a hard-to-guess path; the default /admin/ is never served (404).
_admin_path = env("DJANGO_ADMIN_PATH").strip("/")
if not _admin_path or _admin_path == "admin":
    if not DEBUG:
        raise RuntimeError("DJANGO_ADMIN_PATH must be set to a hard-to-guess path (not 'admin')")
    _admin_path = "dev-admin"
ADMIN_PATH = _admin_path + "/"

# sandcorp-auth: this site is a resource server (aud = AUTH_RESOURCE) and, for the admin login,
# a confidential client. Rules: sandcorp-auth docs/resource-server-token-validation.md.
AUTH_ISSUER = env("AUTH_ISSUER", "https://auth.sandcorp.co.kr").rstrip("/")
AUTH_RESOURCE = f"{SITE_URL}/mcp"
# The owner's username at sandcorp-auth; tokens whose `sub` differs are rejected. Empty = nobody.
AUTH_OWNER_SUB = env("AUTH_OWNER_SUB")
AUTH_CLIENT_ID = env("AUTH_CLIENT_ID")
AUTH_CLIENT_SECRET = env("AUTH_CLIENT_SECRET")

# Discord webhook for operational alerts (#10). Empty = log only, nothing is sent.
DISCORD_WEBHOOK_URL = env("DISCORD_WEBHOOK_URL")
AUTH_REDIRECT_URI = env("AUTH_REDIRECT_URI", f"{SITE_URL}/auth/callback")

# Static files: whitenoise serves collectstatic output, which includes the Vite build.
STATIC_URL = "/static/"
STATIC_ROOT = Path(env("DJANGO_STATIC_ROOT", str(BASE_DIR / "staticfiles")))
FRONTEND_DIST = Path(env("FRONTEND_DIST", str(BASE_DIR.parent / "frontend" / "dist")))
STATICFILES_DIRS = [FRONTEND_DIST] if FRONTEND_DIST.is_dir() else []
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# HTML shell <-> Vite. In dev, point VITE_DEV_SERVER_URL at `pnpm dev` (http://localhost:5173);
# otherwise the shell reads the build manifest.
VITE_DEV_SERVER_URL = env("VITE_DEV_SERVER_URL").rstrip("/")
VITE_MANIFEST_PATH = FRONTEND_DIST / ".vite" / "manifest.json"
VITE_ENTRY = "src/main.tsx"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", "INFO")},
}
