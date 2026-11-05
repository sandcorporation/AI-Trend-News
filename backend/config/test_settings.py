"""Settings for pytest: the production settings with a throwaway secret key."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DJANGO_ADMIN_PATH", "test-admin-7f3c")
os.environ.setdefault("AUTH_OWNER_SUB", "owner")
os.environ.setdefault("AUTH_CLIENT_ID", "news-client")
os.environ.setdefault("AUTH_CLIENT_SECRET", "news-secret")
# `make db` publishes the local MySQL on 15443 (13306 is taken by the coordinator's tunnel).
os.environ.setdefault("DB_PORT", "15443")

from config.settings import *  # noqa: E402, F403

# Fixture feeds carry fixed 2026-10 dates; the age rule is tested with its own settings override.
COLLECT_MAX_AGE_DAYS = 36500
FETCH_FALLBACK_URL = ""
METRICS_SNIPPET_URL = ""

# 시험에서만 싼 비밀번호 해시(PBKDF2는 계정 하나 만들 때마다 CPU 0.5초+). 운영 설정은 기본값 그대로.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# 편집자 파이프라인: 시험은 언제나 가짜 LLM(autouse fake_llm)만. 셸에 키가 있어도 덮는다.
SANDCORP_NEWS_CLAUDE_KEY = ""
EDITOR_PIPELINE_MODE = "off"
EDITOR_MODEL_CLASSIFY_COMPARE = ""
LLM_FORBID_REAL = True
# 운영 설정 암호화 키: 셸 값을 덮는다(필요한 시험이 override_settings로 넣는다).
RUNTIME_SETTINGS_KEY = ""
