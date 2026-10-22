# 한 줄 명령 모음. 자세한 것은 CLAUDE.md 「명령」 절.
.PHONY: db test test-backend test-frontend lint typecheck check dev-backend dev-frontend prod-local prod-local-down

db:  ## 로컬 시험·개발용 Postgres (127.0.0.1:15443, 프로젝트 news-dev)
	docker compose up -d --wait db

test: test-backend test-frontend

test-backend: db
	cd backend && uv run pytest

test-frontend:
	cd frontend && pnpm test

lint:
	cd backend && uv run ruff format --check . && uv run ruff check .
	cd frontend && pnpm lint

typecheck:
	cd backend && uv run mypy .
	cd frontend && pnpm typecheck

check: lint typecheck test

dev-backend: db  ## http://localhost:8000 (프론트는 dev-frontend로 따로 띄운다)
	cd backend && DJANGO_DEBUG=1 VITE_DEV_SERVER_URL=http://localhost:5173 uv run python manage.py migrate --noinput
	cd backend && DJANGO_DEBUG=1 VITE_DEV_SERVER_URL=http://localhost:5173 uv run python manage.py runserver 8000

dev-frontend:
	cd frontend && pnpm dev

# 운영 compose를 로컬에서 띄워 본다(.env 필요: cp .env.example .env 후 값 채우기, ALLOWED_HOSTS에 localhost 포함).
# deploy/deploy.sh와 같은 순서(빌드 → postgres → migrate → web)로 띄운다. http://127.0.0.1:18092
prod-local:
	COMPOSE_PROJECT_NAME=news-local COMPOSE_FILE=docker-compose.prod.yml bash deploy/deploy.sh

prod-local-down:
	docker compose -p news-local -f docker-compose.prod.yml down -v
