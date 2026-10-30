"""Django settings. Every deploy-specific value comes from the environment (see /.env.example)."""

import os
from decimal import Decimal
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
    "inquiries",
    "posts",
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

# MySQL only (ADR 0004). Production is OCI MySQL HeatWave; local dev and tests use the
# `make db` container (127.0.0.1:15443). The database is utf8mb4 / utf8mb4_0900_as_cs so that
# unique constraints and `=` lookups are case- and accent-sensitive like before (slug 404, tags,
# prediction claims). Django sets the session to READ COMMITTED; leave it
# (REPEATABLE READ deadlocked on gap locks in the spike).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("DB_NAME", "news"),
        "USER": env("DB_USER", "news"),
        "PASSWORD": env("DB_PASSWORD", "news"),
        "HOST": env("DB_HOST", "127.0.0.1"),
        "PORT": env("DB_PORT", "3306"),
        "OPTIONS": {"charset": "utf8mb4"},
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_0900_as_cs"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

# Absolute-URL base for sitemap.xml and robots.txt (they have no request host to trust).
SITE_URL = env("SITE_URL", "https://news.sandcorp.co.kr").rstrip("/")
# twitter:site 값(예: @foresight). 비면 태그를 내지 않는다.
SITE_TWITTER = env("SITE_TWITTER", "").strip()
# 방문 메트릭 스니펫(#76). URL이 비면 <script>를 내지 않는다. 공개 셸 head에만 붙는다.
METRICS_SNIPPET_URL = env("METRICS_SNIPPET_URL", "").strip()
METRICS_SITE_KEY = env("METRICS_SITE_KEY", "foresight").strip()
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

# Collection (#28): items published more than this many days before collection are not stored as
# Item (only SeenUrl), so a first run on a feed with years of archive does not flood the queue.
COLLECT_MAX_AGE_DAYS = int(env("COLLECT_MAX_AGE_DAYS", "3"))
# 편집자 클라우드 예약의 하루 실행 횟수(공개 집계 /api/public/stats가 그대로 낸다).
EDITOR_RUNS_PER_DAY = 3

# 편집자 파이프라인(ADR 0006, docs/design/editor-pipeline.md §6). 키는 대표 지정 이름으로만 읽는다
# (SDK가 스스로 읽는 ANTHROPIC_API_KEY에 기대지 않는다). 비면 run_editor는 기록만 하고 건너뛴다.
SANDCORP_NEWS_CLAUDE_KEY = env("SANDCORP_NEWS_CLAUDE_KEY")
EDITOR_PIPELINE_MODE = env("EDITOR_PIPELINE_MODE", "off")  # off / shadow / live
EDITOR_RUN_HOURS = tuple(int(h) for h in env("EDITOR_RUN_HOURS", "6,12,18").split(",") if h.strip())
EDITOR_MODEL_CLASSIFY = env("EDITOR_MODEL_CLASSIFY", "claude-sonnet-5-5")
EDITOR_EFFORT_CLASSIFY = env("EDITOR_EFFORT_CLASSIFY", "medium")
EDITOR_MODEL_WRITE = env("EDITOR_MODEL_WRITE", "claude-sonnet-5-5")
EDITOR_EFFORT_WRITE = env("EDITOR_EFFORT_WRITE", "low")
# 그림자 비교용 두 번째 분류 모델(예 claude-haiku-5-5). 비우면 끔.
EDITOR_MODEL_CLASSIFY_COMPARE = env("EDITOR_MODEL_CLASSIFY_COMPARE")
# 월 예산 상한(US$, #124). Console 워크스페이스 지출 한도가 월 $20이라 그보다 낮게 둔다 — 넘으면
# 앱이 먼저 실행을 건너뛰고 경보한다. 다음 단계 예상액까지 더해 본다(services/editor_budget.py).
EDITOR_MONTHLY_BUDGET_USD = Decimal(env("EDITOR_MONTHLY_BUDGET_USD", "18"))
EDITOR_MAX_ITEMS = int(env("EDITOR_MAX_ITEMS", "60"))
EDITOR_MAX_WRITES = int(env("EDITOR_MAX_WRITES", "30"))
EDITOR_MAX_FEATURED = int(env("EDITOR_MAX_FEATURED", "6"))

# Fetch fallback (#70): URL template with {url}; used once when the direct GET looks bot-blocked.
FETCH_FALLBACK_URL = env("FETCH_FALLBACK_URL", "")

# Pending items collected longer ago than this are auto-discarded by prune_items (#38).
PENDING_MAX_AGE_HOURS = int(env("PENDING_MAX_AGE_HOURS", "24"))

# Discord webhook for operational alerts (#10). Empty = log only, nothing is sent.
DISCORD_WEBHOOK_URL = env("DISCORD_WEBHOOK_URL")
# portfolio inquiry API (ADR 0003). Either empty = inquiries are only queued, never sent.
PORTFOLIO_INQUIRY_URL = env("PORTFOLIO_INQUIRY_URL")
PORTFOLIO_INQUIRY_SECRET = env("PORTFOLIO_INQUIRY_SECRET")
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
