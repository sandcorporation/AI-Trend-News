"""Settings for pytest: the production settings with a throwaway secret key."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")
os.environ.setdefault("DJANGO_ADMIN_PATH", "test-admin-7f3c")
os.environ.setdefault("AUTH_OWNER_SUB", "owner")
os.environ.setdefault("AUTH_CLIENT_ID", "news-client")
os.environ.setdefault("AUTH_CLIENT_SECRET", "news-secret")

from config.settings import *  # noqa: E402, F403

# Fixture feeds carry fixed 2026-10 dates; the age rule is tested with its own settings override.
COLLECT_MAX_AGE_DAYS = 36500
