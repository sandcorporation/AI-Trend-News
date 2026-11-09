# syntax=docker/dockerfile:1
# One image: the Vite build is baked in and collected into Django's static files (served by whitenoise).
# Built as linux/arm64 by GitHub Actions (.github/workflows/image.yml, ubuntu-24.04-arm); emergency path: Jenkins BUILD_ON=jenkins (macos, else home).
# oracle only pulls; also builds on x86 for local checks.

FROM node:24-alpine AS frontend
RUN npm install -g pnpm@11.25.0
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# mysqlclient has no Linux wheels: compile it here (MariaDB Connector/C headers via
# default-libmysqlclient-dev) and ship only the shared library (libmariadb3) in the runtime stage.
FROM python:3.14-slim AS deps
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev git openssh-client \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# sandcorp-authkit(sandcorp-auth, 공개). AUTHKIT_VIA_HTTPS=1(GitHub Actions image.yml)이면 키 없이 https로 받는다 — 값은 비밀이 아니다.
# 비어 있으면(Jenkins 비상 길·make prod-local) 지금처럼 BuildKit ssh 마운트로 받는다.
# known_hosts·git 설정은 이 deps 단계에만 남고 runtime은 /opt/venv만 복사하므로 최종 이미지에 없다.
ARG AUTHKIT_VIA_HTTPS=
RUN --mount=type=ssh if [ -n "$AUTHKIT_VIA_HTTPS" ]; then \
      git config --global url."https://github.com/".insteadOf "ssh://git@github.com/"; \
    else \
      mkdir -p -m 0700 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null; \
    fi \
 && uv sync --frozen --no-dev --no-install-project

FROM python:3.14-slim AS runtime
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmariadb3 \
 && rm -rf /var/lib/apt/lists/*
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY --from=deps /opt/venv /opt/venv
COPY backend/ ./
# 편집자 파이프라인이 단계 프롬프트를 automation/editor.md·stages/에서 조립한다(ADR 0006).
COPY automation/ /app/automation/
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN DJANGO_SECRET_KEY=collectstatic-only DJANGO_ADMIN_PATH=collectstatic-only python manage.py collectstatic --noinput \
 && useradd --uid 1000 --no-create-home app
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --timeout 30 --access-logfile -"]
