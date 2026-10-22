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
- 수집: `cd backend && uv run python manage.py collect` — 켜진 소스를 모두 수집한다(소스별 독립 실패, 결과는 소스에 기록). 앱은 `backend/collection/`, 규칙은 `services/collection.py`·`services/urls.py`, 페치·소스 어댑터는 `adapters/fetch.py`·`adapters/sources.py`. 발행 시각이 수집 시각보다 `COLLECT_MAX_AGE_DAYS`(settings, 기본 3일, 환경 변수로 덮음) 넘게 오래된 글은 `Item` 없이 `SeenUrl`에만 남기고(경계 정확히 N일·발행 시각 없음은 수집) 결과 문구 뒤에 「/ 오래돼 건너뜀 N건」을 붙인다.
- 정리: `manage.py prune_items` — 미처리·버림 수집물 중 수집 시각 30일 지난 것만 지운다(`services/retention.py`). 묶인 수집물과 `SeenUrl`은 남긴다.
- 기사: 앱 `backend/articles/`(Category, Tag, Article, ArticleItem), 규칙은 `services/articles.py` — 함수가 주체 `Actor.EDITOR`/`Actor.OWNER`를 받고 오류는 `ArticleError` 하위 클래스. admin은 대표 주체로 서비스를 거친다.
- 기사 slug(#32): 편집자 `create_article`은 `slug`가 필수다(라틴 소문자·숫자·하이픈 3~60자, 첫 글자 소문자; 틀리면 `InvalidArticleSlug`, 중복이면 `-2`·`-3`으로 유일화). `update_article`은 slug를 바꾸지 않는다. 대표는 admin에서 slug를 바꿀 수 있고 옛 값은 `ArticleSlugAlias`에 남아 `/a/<옛 slug>`가 301(게시된 기사만). 자동 생성 `make_slug`는 라틴 문자가 3자 미만이면 `news-<hex>`.
- 관리 기능(#9): 소스 규칙은 `services/sources.py`(`SourceError` 계열, `Source.clean` 검증을 서비스에서도 부른다), 분야 수정·순서·삭제는 `services/articles.py`. 같은 함수를 관리자 API `/api/admin/...`(`core/admin_api.py`, 스코프 `admin`)와 MCP 관리 도구(`scope="admin"`, 주체 대표)가 부르고 입력·출력 스키마는 `core/admin_schemas.py`를 공유한다.
- 분야 slug(#29): 대표가 정한다 — admin 폼(비우면 `category-<hex>` 자동)·관리자 API `PATCH /categories/{slug}`·MCP `update_category`의 `new_slug`(라틴 소문자·숫자·하이픈 1~60자, 다른 분야의 현재·옛 slug와 겹치면 `CategoryExists`, 형식 오류는 `InvalidCategorySlug`). 바꾸면 옛 값이 `CategorySlugAlias`에 남아 `/?category=<옛 slug>`가 새 slug로 301(쿼리 유지). 기사 slug는 그대로.
- 스케줄러: 운영 compose의 `scheduler` 컨테이너(같은 이미지)가 `manage.py run_scheduler`로 시작 즉시 한 번, 이후 매시간(`SCHEDULER_INTERVAL` 초) `services/scheduler.py`의 `COMMANDS`(지금 `collect`, `prune_items`, `check_alerts`, `resend_inquiries`)를 차례로 부른다. 새 주기 작업은 관리 명령을 만들고 `COMMANDS`에 이름을 더한다. 한 명령의 실패는 기록만 하고 계속한다.
- 경보(#10): `check_alerts`(매시간, `services/alerts.py`)가 편집자 미실행(마지막 종료 24시간 초과·없음·미종료 실행)과 소스 연속 실패(`consecutive_failures >= 3`)를 점검해 Discord로 보낸다. 상태는 `editor.models.Alert`(종류·대상·활성·마지막 발송) — 같은 조건은 한 번만 보내고 풀리면 「복구」 한 번. 전송은 `adapters/discord.py`(`get_notifier`/`set_notifier`, 설정 `DISCORD_WEBHOOK_URL`, 없으면 로그만; 시험은 autouse `fake_discord`). 편집자 프롬프트는 `automation/editor.md`(클라우드 예약이 그대로 쓴다) — 편집자 도구를 바꾸면 `` `도구이름()` `` 표기로 함께 고친다(`tests/test_alerts.py`가 등록부와 맞는지 본다).
- 인증(sandcorp-auth 리소스 서버): 규칙 원본은 sandcorp-auth `docs/resource-server-token-validation.md`. 검증은 `services/tokens.py`, HTTP 의존성은 `core/auth.py` — ninja는 `auth=RequireScope("admin"|"editor"|"login")`, 일반 뷰는 `@require_scope(...)`. 메타데이터 `/.well-known/oauth-protected-resource`. admin은 `DJANGO_ADMIN_PATH`에만 있고 로그인은 sandcorp-auth 코드 플로우(`core/admin_login.py`, 콜백 `/auth/callback`)뿐이다. 시험은 `token_signer`로 서명하고 `fake_jwks`·`fake_auth_server`가 자동으로 끼워진다.
- 공개 사이트: 라우트 `/`(`?category=<분야 slug>`, `?page=`), `/a/<slug>`, `/sitemap.xml`, `/robots.txt`는 `core/public.py`. 데이터는 같은 값을 서버 HTML(`core/templates/core/public/*.html`, #root 안)과 `#page-data` JSON(React: `frontend/src/{Home,ArticlePage,Layout}.tsx`, 타입 `types.ts`)에 두 번 그린다 — 화면을 바꾸면 둘 다 고친다. 공개 조회는 `services/articles.py`의 `public_*`(반드시 `published=True`). Sandcorp 링크는 모두 유입 링크 `/go/sandcorp?from=<header|footer|article_cta|contact>&article=<slug>`(`services/outbound.py`, 뷰 `public.go_sandcorp`, 클릭 모델 `articles.OutboundClick`, 봇 UA는 미기록·잘못된 위치는 400, 302로 UTM을 붙여 sandcorp.co.kr) — 라벨·CTA 문구는 `core/public.py`, 기사 하단 CTA는 `data-slot="article-cta"`, 공유 버튼(X intent·링크 복사)은 `data-slot="share"`. 클릭 집계는 admin 「유입 링크 클릭」 changelist 상단(기간 오늘·7일·30일 × 위치·기사).
- 문의(#13, ADR 0003): 폼 `/contact`(`core/public.py`의 `contact`, 서버 HTML `contact.html` + React `ContactPage.tsx`, JS 없이 되는 일반 POST). honeypot 필드 `website`가 차 있으면 저장·전송 없이 성공 화면, IP별 15분 5회 제한(`inquiries.SubmitAttempt` DB 기록, XFF 오른쪽 끝 한 칸만 믿음, 초과 429). 규칙은 `services/inquiries.py`: 제출 즉시 1회 전송, 성공하면 지우고, 5xx·연결 실패는 `Inquiry`(status pending)로 남겨 `resend_inquiries`(매시간)가 다시 보내며, 4xx는 status failed로 남아 재전송하지 않고 admin 「문의 대기열」에서 본다. 전송 어댑터 `adapters/portfolio.py`(`InquiryGateway` Protocol, `PortfolioGateway`/`FakeGateway`, `get_gateway`/`set_gateway`; 시험은 autouse `fake_portfolio`가 설정도 채운다), 설정 `PORTFOLIO_INQUIRY_URL`·`PORTFOLIO_INQUIRY_SECRET`(하나라도 비면 대기열에만 쌓음). 로그·오류 칸에 내용·연락처를 남기지 않는다. 기사 CTA·푸터의 문의 링크는 `/contact`.
- 포스트(#14): 앱 `backend/posts/`(`Post`: 기사 FK·채널·문구·상태 초안/올림·올린 시각, 기사·채널 유일), 규칙은 `services/posts.py`(`PostError` 계열 — MCP `SERVICE_ERRORS`와 `install_error_handlers` 두 곳에 등록). 편집자 도구 `upsert_post_draft(article_id, channel, text)`는 추천이면서 게시된 기사에만, 문구 1~500자·기사 링크(`SITE_URL/a/<slug>`, 기사 도구 결과의 `url`) 필수, 올림 포스트는 수정 불가. 「올림」은 admin 액션(`mark_post_posted`, 대표 주체)뿐. admin 상세에 문구 복사 버튼. 채널을 더하려면 `Post.Channel`에 선택지+마이그레이션, 카카오 전송 연동은 범위 밖(`services/posts.py` 위에 어댑터를 얹는다). 프롬프트 「포스트 초안」 절은 `automation/editor.md`.
- 작업자는 이 「명령」 절을 명령이 바뀌는 티켓에서 함께 고친다.
- 로컬 실행: `make dev-frontend`(Vite, :5173)와 `make dev-backend`(Django, :8000)를 따로 띄우고 http://localhost:8000 을 연다. Django가 HTML 셸을 내리고 Vite dev 서버의 스크립트를 붙인다.
- 운영 compose 로컬 확인: `.env.example`을 `.env`로 복사해 값을 채우고(`DJANGO_ALLOWED_HOSTS`에 `localhost`) `make prod-local`(`news-local` 프로젝트로 `deploy/deploy.sh`를 돌린다) → http://127.0.0.1:18092 . 끝나면 `make prod-local-down`.
- 운영: Jenkins(`oracle` 에이전트)가 `COMPOSE_PROJECT_NAME=news`로 `deploy/deploy.sh`를 돌린다. web은 `172.17.0.1:18092`에 묶이고 NPMplus가 `news.sandcorp.co.kr`을 거기로 보낸다.
