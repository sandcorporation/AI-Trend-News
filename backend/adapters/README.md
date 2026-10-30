# adapters — 외부 연동

외부 시스템은 좁은 인터페이스(`typing.Protocol`) 뒤에 둔다. 실제 구현과 가짜 구현을 나란히 둔다.
서비스 계층은 실제 구현을 직접 import하지 않고 인자나 등록 지점으로 받는다.

| 어댑터 | 실제 구현 | 가짜(시험용) |
| --- | --- | --- |
| 소스 페치 | 소스 주소로 HTTP GET(시간 제한, User-Agent) | `tests/fixtures/`의 고정 RSS/HTML을 돌려줌 |
| HTML 파싱 규칙 | 소스별 어댑터 코드. 소스 레코드가 어댑터 이름을 가리킨다 | 픽스처 HTML로 시험 |
| 문의 전송 | portfolio 문의 API에 서버 간 호출(공유 시크릿 헤더) | 보낸 문의 목록을 남김, 실패를 흉내 냄 |
| 알림 | Discord 웹훅(실패해도 요청은 실패시키지 않음) | 보낸 알림 목록을 남김 |
| 공개 키(JWKS) | sandcorp-auth JWKS를 HTTP로 받아 캐시(TTL 600초, 모르는 kid 재조회 60초 간격, 실패 후 60초, 타임아웃 5초) | 같은 캐시에 시험용 키 쌍의 JWK를 돌려주는 조회 함수 |
| 인가 서버 | sandcorp-auth 메타데이터(RFC 8414)와 토큰 엔드포인트(코드 교환, client_secret_basic) | 고정 주소, 정해 둔 액세스 토큰을 돌려주고 교환 기록을 남김 |
| LLM(ADR 0006) | Anthropic Messages API(`anthropic` SDK, 키는 `SANDCORP_NEWS_CLAUDE_KEY`) | 응답을 큐로 넣고 요청을 남김. `output_config.format` 없으면 JSON 아님, `cache_control`·최소 길이일 때만 캐시 토큰, refusal·max_tokens·API 오류 시나리오 |
| 시계 | `datetime.now(UTC)` | 고정 시계(`advance`) |

시험은 `tests/conftest.py`의 픽스처로 가짜를 끼우고, 가짜에 남은 기록을 확인한다.

구현 위치: 페치는 `fetch.py`(`Fetcher`, `HttpFetcher`, 등록 지점 `get_fetcher`/`set_fetcher`), 소스 어댑터는 `sources.py`(`ADAPTERS` 이름→구현 사전). 시험의 가짜는 `tests/conftest.py`의 `fake_fetcher`.
토큰 검증·JWKS·인가 서버 호출은 어댑터가 아니라 sandcorp-authkit이 한다(`core/auth_adapter.py` 한 파일이 settings를 넘긴다). 시계는 `clock.py`(`get_clock`/`set_clock`). LLM은 `anthropic.py`(`LlmClient`, `AnthropicClient`, `FakeLlm`, `get_llm`/`set_llm`; 가짜 없이 시험 중 `get_llm()`은 `LlmNotConfigured`), 가짜는 autouse `fake_llm`. 가짜는 `frozen_clock`(`FixedClock`)·`fake_jwks`(autouse, `FakeJwks`)·`fake_auth_server`(`FakeTokenEndpoint`), 토큰 서명은 `token_signer`.
