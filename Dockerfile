# syntax=docker/dockerfile:1
# One image: the Vite build is baked in and collected into Django's static files (served by whitenoise).
# Built natively on oracle (arm64) by Jenkins; also builds on x86 for local checks.

FROM node:24-alpine AS frontend
RUN npm install -g pnpm@11.25.0
WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
COPY --from=frontend /app/frontend/dist /app/frontend/dist
RUN DJANGO_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput \
 && useradd --uid 1000 --no-create-home app
USER app
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-2} --timeout 30 --access-logfile -"]
