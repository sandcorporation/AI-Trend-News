# services — 도메인 서비스 계층

소스·수집물·기사·분야·포스트·편집자 실행 같은 도메인 규칙은 이 아래 모듈에 둔다.
ninja 관리자 API(`core/api.py`와 이후 앱의 api), MCP 도구, Django admin은 **같은 서비스 함수**를 부른다.
엔드포인트·도구는 입력 검증과 응답 모양만 맡고, 규칙은 여기서만 정한다(스펙 #1).

- 외부 의존(소스 HTTP 페치, portfolio 문의 API, Discord 웹훅, sandcorp-auth JWKS)은 함수 인자나 `adapters/`의 등록 지점을 통해 받는다.
- 모델은 Django 앱(예: 수집 앱)에 두고, 모델을 읽고 쓰는 규칙은 여기 모듈에 둔다.
- 서버에는 LLM 호출 코드가 없다(ADR 0002). 번역·요약은 편집자가 MCP로 넣는다.
