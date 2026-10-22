# adapters — 외부 연동

외부 시스템은 좁은 인터페이스(`typing.Protocol`) 뒤에 둔다. 실제 구현과 가짜 구현을 나란히 둔다.
서비스 계층은 실제 구현을 직접 import하지 않고 인자나 등록 지점으로 받는다.

| 어댑터 | 실제 구현 | 가짜(시험용) |
| --- | --- | --- |
| 소스 페치 | 소스 주소로 HTTP GET(시간 제한, User-Agent) | `tests/fixtures/`의 고정 RSS/HTML을 돌려줌 |
| HTML 파싱 규칙 | 소스별 어댑터 코드. 소스 레코드가 어댑터 이름을 가리킨다 | 픽스처 HTML로 시험 |
| 문의 전송 | portfolio 문의 API에 서버 간 호출(공유 시크릿 헤더) | 보낸 문의 목록을 남김, 실패를 흉내 냄 |
| 알림 | Discord 웹훅(실패해도 요청은 실패시키지 않음) | 보낸 알림 목록을 남김 |
| 토큰 검증 | sandcorp-auth JWKS | 시험용 키 쌍으로 발급한 토큰 |

시험은 `tests/conftest.py`의 픽스처로 가짜를 끼우고, 가짜에 남은 기록을 확인한다.

구현 위치: 페치는 `fetch.py`(`Fetcher`, `HttpFetcher`, 등록 지점 `get_fetcher`/`set_fetcher`), 소스 어댑터는 `sources.py`(`ADAPTERS` 이름→구현 사전). 시험의 가짜는 `tests/conftest.py`의 `fake_fetcher`.
