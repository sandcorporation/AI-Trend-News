"""오류를 자체 GlitchTip(Sentry SDK)으로 보낸다(#168).

받는 쪽은 sentry.io가 아니라 자체 GlitchTip이고, 어디로 보낼지는 DSN 환경 변수
(`SENTRY_DSN_BACKEND`·`SENTRY_DSN_FRONTEND`)가 정한다. SDK는 같아서 DSN만 그쪽을 가리킨다.
본보기는 codemit `backend/core/observability.py`.

- **DSN이 없으면 아무것도 하지 않는다.** dev·CI·시험은 `SENTRY_DSN_BACKEND`를 주지 않으므로
  SDK가 꺼진 채로 돈다(네트워크를 치지 않는다).
- `release`는 배포한 커밋 sha 40자(`SENTRY_RELEASE`, 운영 compose가 `SANDCORP_IMAGE_SHA`를
  넣는다), `environment`는 `production` 고정 — 운영에서만 DSN이 있기 때문이다.
- 보내지 않는 것: 요청 본문(`max_request_body_size="never"`)·쿠키·자격증명 헤더·스택 프레임
  지역 변수(`include_local_variables=False` + `scrub_secrets`), 그리고 토큰 모양 문자열.
- **정상적인 거절은 오류가 아니다**: django-ninja `HttpError` 4xx·`Http404`·`PermissionDenied`·
  `SuspiciousOperation`·검증 오류는 버린다(`drop_expected_error`). 5xx·처리 안 된 예외만 보낸다.
- `manage.py shell`에서 손으로 친 명령의 예외는 앱 결함이 아니라서 버린다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, MutableMapping
from typing import Literal

import sentry_sdk
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

ENVIRONMENT = "production"

_SHELL_COMMAND_MODULE = "django.core.management.commands.shell"
_SHELL_COMMAND_PATH = "django/core/management/commands/shell.py"

#: 이벤트에서 지우는 요청 헤더(소문자). 프런트 `observability.ts`의 목록과 같다.
CREDENTIAL_HEADERS = frozenset(
    {"cookie", "set-cookie", "authorization", "proxy-authorization", "x-api-key"}
)

#: 토큰 모양 문자열: JWT, `Bearer …`, Anthropic·일반 비밀 키 접두어, 긴 hex/base64 덩어리.
_TOKEN_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
)
#: 쿼리 문자열에서 값을 지우는 키(소문자 부분 일치).
_SECRET_QUERY_KEYS = ("token", "code", "secret", "key", "password", "session", "state")
REDACTED = "[redacted]"


def _is_manage_shell_error(event: Event) -> bool:
    """예외 스택 프레임에 `manage.py shell` 명령(Django)이 있으면 True."""
    for value in event.get("exception", {}).get("values") or []:
        frames = (value.get("stacktrace") or {}).get("frames") or []
        for frame in frames:
            if frame.get("module") == _SHELL_COMMAND_MODULE:
                return True
            for key in ("abs_path", "filename"):
                path = str(frame.get(key) or "").replace("\\", "/")
                if path.endswith(_SHELL_COMMAND_PATH):
                    return True
    return False


def _is_expected_rejection(exc: BaseException) -> bool:
    from django.core.exceptions import PermissionDenied, SuspiciousOperation
    from django.core.exceptions import ValidationError as DjangoValidationError
    from django.http import Http404
    from ninja.errors import ValidationError as NinjaValidationError

    if isinstance(
        exc,
        (
            Http404,
            PermissionDenied,
            SuspiciousOperation,
            DjangoValidationError,
            NinjaValidationError,
        ),
    ):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


def drop_expected_error(event: Event, hint: Hint) -> Event | None:
    """의도한 4xx 거절·shell 예외면 None(버림), 그 밖은 그대로."""
    if _is_manage_shell_error(event):
        return None
    exc_info = hint.get("exc_info")
    if exc_info and isinstance(exc_info[1], BaseException) and _is_expected_rejection(exc_info[1]):
        return None
    return event


def scrub_text(text: str) -> str:
    """문자열 안의 토큰 모양 값을 `[redacted]`로 바꾼다."""
    for pattern in _TOKEN_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _scrub_query(query: str) -> str:
    parts = []
    for pair in query.split("&"):
        name, sep, value = pair.partition("=")
        if sep and any(key in name.lower() for key in _SECRET_QUERY_KEYS):
            value = REDACTED
        parts.append(f"{name}{sep}{scrub_text(value)}")
    return "&".join(parts)


def _drop_frame_vars(container: Mapping[Literal["values"], list[dict[str, object]]] | None) -> None:
    for value in (container or {}).get("values") or ():
        stacktrace = value.get("stacktrace")
        frames = stacktrace.get("frames") if isinstance(stacktrace, dict) else None
        for frame in frames or ():
            if isinstance(frame, dict):
                frame.pop("vars", None)


def _scrub_headers(headers: MutableMapping[str, str]) -> None:
    for name in list(headers):
        if name.lower() in CREDENTIAL_HEADERS:
            del headers[name]
        else:
            headers[name] = scrub_text(str(headers[name]))


def scrub_secrets(event: Event) -> Event:
    """요청의 쿠키·본문·자격증명 헤더, 쿼리·메시지·예외 문구의 토큰 값, 프레임 변수를 지운다."""
    request = event.get("request")
    if request is not None:
        request.pop("cookies", None)
        request.pop("data", None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            _scrub_headers(headers)
        query = request.get("query_string")
        if isinstance(query, str):
            request["query_string"] = _scrub_query(query)
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = scrub_text(url)
    message = event.get("message")
    if isinstance(message, str):
        event["message"] = scrub_text(message)
    logentry = event.get("logentry")
    if isinstance(logentry, dict) and isinstance(logentry.get("message"), str):
        logentry["message"] = scrub_text(logentry["message"])
    exception = event.get("exception")
    for value in (exception or {}).get("values") or []:
        if isinstance(value.get("value"), str):
            value["value"] = scrub_text(value["value"])
    _drop_frame_vars(exception)
    _drop_frame_vars(event.get("threads"))
    return event


def before_send(event: Event, hint: Hint) -> Event | None:
    """SDK `before_send` — 의도한 거절·shell 예외는 버리고, 남은 것은 비밀을 지워 보낸다."""
    kept = drop_expected_error(event, hint)
    return None if kept is None else scrub_secrets(kept)


def init_sentry(
    dsn: str,
    release: str = "",
    transport: type[Transport] | Callable[[Event], None] | None = None,
) -> None:
    """운영 설정으로 SDK를 켠다. 시험은 `transport`로 네트워크 대신 이벤트를 모은다."""
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        release=release.strip() or None,
        environment=ENVIRONMENT,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=0.0,
        include_local_variables=False,
        before_send=before_send,
        transport=transport,
    )


def configure_sentry(dsn: str, release: str = "") -> bool:
    """DSN이 있으면 SDK를 켠다(켰으면 True). `settings.py` 끝에서 한 번 부른다 —
    web(gunicorn)·scheduler·관리 명령이 모두 Django 설정을 거치므로 이 한 줄로 켜진다."""
    if not dsn.strip():
        return False
    init_sentry(dsn.strip(), release)
    return True


def sentry_enabled() -> bool:
    """SDK가 켜져 있는가(DSN이 있는 클라이언트가 붙었나)."""
    return sentry_sdk.get_client().is_active()
