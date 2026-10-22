# AI-Trend-News

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`sandcorporation/AI-Trend-News`, via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels are used as-is: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## 작업 흐름

- 스펙은 #1, 티켓은 그 하위 이슈(#2~#14)다. 막힘 관계는 GitHub 이슈 의존성으로 걸려 있다.
- 이슈마다 워크트리 `~/workspace/AI-Trend-News-wt/<브랜치>`에서 작업하고 `origin/main`으로 PR을 연다.
- PR CI는 없다. 시험·린트·타입 검사는 로컬에서 돌린다. 통과하면 작업자가 바로 squash 병합한다(main 직접 push 금지).
- 배포와 서버 설정(env 파일, NPMplus, DNS, OCI)은 조율자가 다룬다. 작업자는 원격 호스트에 쓰지 않는다.
- 스택과 명령은 `~/workspace/portfolio`와 같은 형태를 따른다. `backend/`(uv, Python 3.13, Django, django-ninja), `frontend/`(Vite, pnpm), `make test` / `make lint` / `make typecheck` / `make check`.
- 용어는 `GLOSSARY.md`, 결정은 `docs/adr/`를 따른다. 인증은 sandcorp-auth(auth.sandcorp.co.kr)의 리소스 서버로 동작한다.

## 명령

저장소 루트에서 `make <대상>`. 백엔드는 `backend/`(uv, Python 3.13), 프론트는 `frontend/`(pnpm).

- 시험: `make test` (= `make test-backend` + `make test-frontend`). 백엔드 시험은 Postgres를 쓴다 — `make db`가 `news-dev` 프로젝트로 `127.0.0.1:15443`에 띄운다(`make test-backend`가 알아서 부른다). portfolio-dev(15442)와 함께 떠 있어도 된다. 백엔드 시험 전체는 30초 안에 끝나야 한다.
- 시험 위치: `backend/tests/`(pytest-django, 설정 `config.test_settings`). 공용 픽스처·가짜 어댑터는 `backend/tests/conftest.py`, 고정 파일(RSS/HTML 샘플, Vite manifest)은 `backend/tests/fixtures/`. 프론트는 `frontend/src/**/*.test.tsx`(vitest, jsdom).
- 린트: `make lint` (ruff format --check, ruff check, eslint). 고치기: `cd backend && uv run ruff format . && uv run ruff check --fix .`
- 타입: `make typecheck` (mypy strict + django-stubs, `tsc --noEmit`).
- 전부: `make check`.
- HTML 소스: `Source.html_rules`(JSON; 키 item·link·title·published, 선택 excerpt — 선택자는 CSS, published는 `datetime` 속성 우선). 검증은 `services/html_rules.py`. 어댑터 인터페이스는 `collect(url, fetcher, rules)`.
- 수집: `cd backend && uv run python manage.py collect` — 켜진 소스를 모두 수집한다(소스별 독립 실패, 결과는 소스에 기록). 앱은 `backend/collection/`, 규칙은 `services/collection.py`·`services/urls.py`, 페치·소스 어댑터는 `adapters/fetch.py`·`adapters/sources.py`.
- 정리: `manage.py prune_items` — 미처리·버림 수집물 중 수집 시각 30일 지난 것만 지운다(`services/retention.py`). 묶인 수집물과 `SeenUrl`은 남긴다.
- 기사: 앱 `backend/articles/`(Category, Tag, Article, ArticleItem), 규칙은 `services/articles.py` — 함수가 주체 `Actor.EDITOR`/`Actor.OWNER`를 받고 오류는 `ArticleError` 하위 클래스. admin은 대표 주체로 서비스를 거친다.
- 스케줄러: 운영 compose의 `scheduler` 컨테이너(같은 이미지)가 `manage.py run_scheduler`로 시작 즉시 한 번, 이후 매시간(`SCHEDULER_INTERVAL` 초) `services/scheduler.py`의 `COMMANDS`(지금 `collect`, `prune_items`)를 차례로 부른다. 새 주기 작업은 관리 명령을 만들고 `COMMANDS`에 이름을 더한다. 한 명령의 실패는 기록만 하고 계속한다.
- 인증(sandcorp-auth 리소스 서버): 규칙 원본은 sandcorp-auth `docs/resource-server-token-validation.md`. 검증은 `services/tokens.py`, HTTP 의존성은 `core/auth.py` — ninja는 `auth=RequireScope("admin"|"editor"|"login")`, 일반 뷰는 `@require_scope(...)`. 메타데이터 `/.well-known/oauth-protected-resource`. admin은 `DJANGO_ADMIN_PATH`에만 있고 로그인은 sandcorp-auth 코드 플로우(`core/admin_login.py`, 콜백 `/auth/callback`)뿐이다. 시험은 `token_signer`로 서명하고 `fake_jwks`·`fake_auth_server`가 자동으로 끼워진다.
- 공개 사이트: 라우트 `/`(`?category=<분야 slug>`, `?page=`), `/a/<slug>`, `/sitemap.xml`, `/robots.txt`는 `core/public.py`. 데이터는 같은 값을 서버 HTML(`core/templates/core/public/*.html`, #root 안)과 `#page-data` JSON(React: `frontend/src/{Home,ArticlePage,Layout}.tsx`, 타입 `types.ts`)에 두 번 그린다 — 화면을 바꾸면 둘 다 고친다. 공개 조회는 `services/articles.py`의 `public_*`(반드시 `published=True`). Sandcorp 링크 자리는 `core/public.py`의 `SANDCORP_URL`·`SANDCORP_LABEL`(헤더·푸터), 기사 하단 CTA 자리는 `data-slot="article-cta"`(#12).
- 작업자는 이 「명령」 절을 명령이 바뀌는 티켓에서 함께 고친다.
- 로컬 실행: `make dev-frontend`(Vite, :5173)와 `make dev-backend`(Django, :8000)를 따로 띄우고 http://localhost:8000 을 연다. Django가 HTML 셸을 내리고 Vite dev 서버의 스크립트를 붙인다.
- 운영 compose 로컬 확인: `.env.example`을 `.env`로 복사해 값을 채우고(`DJANGO_ALLOWED_HOSTS`에 `localhost`) `make prod-local`(`news-local` 프로젝트로 `deploy/deploy.sh`를 돌린다) → http://127.0.0.1:18092 . 끝나면 `make prod-local-down`.
- 운영: Jenkins(`oracle` 에이전트)가 `COMPOSE_PROJECT_NAME=news`로 `deploy/deploy.sh`를 돌린다. web은 `172.17.0.1:18092`에 묶이고 NPMplus가 `news.sandcorp.co.kr`을 거기로 보낸다.
