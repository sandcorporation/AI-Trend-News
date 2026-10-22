"""보호된 엔드포인트의 인증 의존성(sandcorp-auth 리소스 서버).

- ninja: `auth=RequireScope("admin")` — 통과하면 `request.auth`가 `AccessToken`이다.
  API에 `install_auth_handlers(api)`를 한 번 불러 401/403 응답 모양을 붙인다.
- 일반 Django 뷰(예: MCP 엔드포인트): `@require_scope("editor")` — 통과하면
  `request.access_token`에 `AccessToken`을 둔다.
- `/.well-known/oauth-protected-resource`: `protected_resource_metadata`.

응답 모양은 규칙 문서 5·6절 그대로. 거부 이유는 응답에 싣지 않고 로그에만 남긴다.
"""

import functools
import logging
from collections.abc import Callable
from typing import Concatenate

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import NinjaAPI

from adapters.clock import get_clock
from adapters.jwks import get_key_source
from services.tokens import (
    SCOPES,
    AccessToken,
    InsufficientScope,
    Scope,
    TokenRejected,
    validate_access_token,
    www_authenticate,
)

logger = logging.getLogger(__name__)


class MissingToken(Exception):
    """`Authorization: Bearer …` 헤더가 없음(B2). 401, `error` 없음."""


def authenticate(request: HttpRequest, scope: Scope) -> AccessToken:
    """요청의 Bearer 토큰을 검증한다. MissingToken / TokenRejected / InsufficientScope를 던진다."""
    kind, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if kind.lower() != "bearer" or not token:  # B1, B2: 헤더로만 받는다
        raise MissingToken
    return validate_access_token(
        token,
        keys=get_key_source(),
        issuer=settings.AUTH_ISSUER,
        audience=settings.AUTH_RESOURCE,
        owner_sub=settings.AUTH_OWNER_SUB,
        required_scope=scope,
        clock=get_clock(),
    )


AuthFailure = MissingToken | TokenRejected | InsufficientScope


def challenge_response(request: HttpRequest, exc: AuthFailure) -> HttpResponse:
    """실패 하나를 401/403 + `WWW-Authenticate` 응답으로 바꾼다."""
    site = settings.SITE_URL
    if isinstance(exc, InsufficientScope):
        logger.info("bearer rejected: insufficient scope (%s) %s", exc.required_scope, request.path)
        status, error = 403, "forbidden"
        challenge = www_authenticate(site, error="insufficient_scope", scope=exc.required_scope)
    elif isinstance(exc, TokenRejected):
        logger.info("bearer rejected: %s %s", exc, request.path)
        status, error = 401, "unauthorized"
        challenge = www_authenticate(site, error="invalid_token")
    else:
        status, error = 401, "unauthorized"
        challenge = www_authenticate(site)
    response = JsonResponse({"error": error}, status=status)
    response["WWW-Authenticate"] = challenge
    return response


class RequireScope:
    """ninja 인증 의존성. 엔드포인트마다 필요한 스코프 하나를 받는다."""

    def __init__(self, scope: Scope) -> None:
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        self.scope: Scope = scope

    def __call__(self, request: HttpRequest) -> AccessToken:
        return authenticate(request, self.scope)


def install_auth_handlers(api: NinjaAPI) -> None:
    """RequireScope가 던진 실패를 규칙 문서 5절의 응답으로 바꾸는 처리기를 붙인다."""

    def handle(request: HttpRequest, exc: Exception | type[Exception]) -> HttpResponse:
        if isinstance(exc, MissingToken | TokenRejected | InsufficientScope):
            return challenge_response(request, exc)
        raise TypeError(f"not an auth failure: {exc!r}")  # 위 세 종류에만 등록한다

    for failure in (MissingToken, TokenRejected, InsufficientScope):
        api.add_exception_handler(failure, handle)


def require_scope[**P](
    scope: Scope,
) -> Callable[
    [Callable[Concatenate[HttpRequest, P], HttpResponse]],
    Callable[Concatenate[HttpRequest, P], HttpResponse],
]:
    """일반 Django 뷰용 같은 의존성. 통과하면 `request.access_token`에 토큰을 둔다."""
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")

    def decorate(
        view: Callable[Concatenate[HttpRequest, P], HttpResponse],
    ) -> Callable[Concatenate[HttpRequest, P], HttpResponse]:
        def wrapper(request: HttpRequest, /, *args: P.args, **kwargs: P.kwargs) -> HttpResponse:
            try:
                request.access_token = authenticate(request, scope)  # type: ignore[attr-defined]  # 요청에 붙이는 값
            except (MissingToken, TokenRejected, InsufficientScope) as exc:
                return challenge_response(request, exc)
            return view(request, *args, **kwargs)

        functools.update_wrapper(wrapper, view)
        return wrapper

    return decorate


def protected_resource_metadata(request: HttpRequest) -> JsonResponse:
    """RFC 9728 보호 리소스 메타데이터(규칙 문서 6절). 인증 없이 GET."""
    return JsonResponse(
        {
            "resource": settings.AUTH_RESOURCE,
            "authorization_servers": [settings.AUTH_ISSUER],
            "scopes_supported": list(SCOPES),
            "bearer_methods_supported": ["header"],
        }
    )
