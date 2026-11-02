# syntax=docker/dockerfile:1
# `make check`를 노드 도구와 상관없이 돌리는 검사 이미지(scripts/ci/check.sh가 굽고 지운다). 운영 이미지가 아니다.
# 빌드 노드(macos·home)의 네이티브 아키텍처로 굽는다 — QEMU를 쓰지 않는다. 의존성 층은 노드의 로컬 캐시에 남는다.
FROM node:24-bookworm-slim AS node

FROM python:3.14-slim
# build-essential·pkg-config·libmysqlclient: mysqlclient 컴파일. git·openssh-client: 비공개 의존성 sandcorp-authkit(ssh).
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev git openssh-client make \
 && rm -rf /var/lib/apt/lists/*
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g pnpm@11.25.0
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p -m 0700 /root/.ssh && ssh-keyscan github.com >> /root/.ssh/known_hosts

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=ssh uv sync --frozen --no-install-project

WORKDIR /app/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

WORKDIR /app
COPY . .
RUN --mount=type=ssh cd backend && uv sync --frozen
