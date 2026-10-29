"""sandcorp-auth 리소스 서버 어댑터 — 이 저장소에서 인증 설정을 읽는 유일한 파일.

토큰 검증·bearer 401/403·메타데이터·로그인 코드 플로우는 `sandcorp_authkit`(sandcorp-auth
`clients/python`)이 한다. 여기서는 settings를 함수 인자로 바꾸고, 뷰에 거는 의존성과
admin 로그인 뷰(대표 계정 찾기·세션)만 둔다. 규칙 원본: sandcorp-auth
`docs/resource-server-token-validation.md`.

- ninja: `auth=scope_auth("admin")` — 통과하면 `request.auth`가 `AccessToken`.
  API마다 `install_bearer_errors(api)`를 한 번 부른다.
- 일반 Django 뷰: `@require_editor` — 뷰가 `(request, token)`을 받는다.
"""

import logging
from datetime import datetime
from functools import cache
from typing import Literal, get_args

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from sandcorp_authkit.django import (
    AuthNotConfigured,
    BearerAuth,
    BearerConfig,
    install_bearer_errors,
    metadata_response,
    require_bearer,
)
from sandcorp_authkit.login import (
    LoginConfig,
    LoginRejected,
    SessionAttemptStore,
    TokenTransport,
    complete_login,
    start_login,
)
from sandcorp_authkit.tokens import AccessToken, JwksCache, http_jwks_fetcher, owner_only

from adapters.clock import get_clock

__all__ = [
    "SCOPES",
    "AccessToken",
    "Scope",
    "install_bearer_errors",
    "login_callback",
    "login_start",
    "protected_resource_metadata",
    "scope_auth",
    "require_editor",
]

logger = logging.getLogger(__name__)

Scope = Literal["admin", "editor", "login"]
SCOPES: tuple[Scope, ...] = get_args(Scope)


class _Clock:
    """저장소 시계 등록 지점(`adapters.clock`)을 부른 순간 읽는다 — 시험이 고정 시계를 끼운다."""

    def now(self) -> datetime:
        return get_clock().now()


clock = _Clock()


@cache  # 프로세스마다 JWKS 캐시 하나(시험은 이 이름을 monkeypatch한다)
def jwks_cache() -> JwksCache:
    return JwksCache(http_jwks_fetcher(f"{settings.AUTH_ISSUER}/.well-known/jwks.json"), clock)


def token_transport() -> TokenTransport | None:
    """`/token` 호출 전송. None이면 urllib. 시험이 `FakeTokenEndpoint`로 바꾸는 자리."""
    return None


def bearer_config() -> BearerConfig:
    if not settings.AUTH_OWNER_SUB:
        raise AuthNotConfigured("AUTH_OWNER_SUB is empty")
    return BearerConfig(
        issuer=settings.AUTH_ISSUER,
        audience=settings.AUTH_RESOURCE,
        resource_metadata_url=f"{settings.SITE_URL}/.well-known/oauth-protected-resource",
        jwks=jwks_cache(),
        clock=clock,
        extra_claims_hook=owner_only(settings.AUTH_OWNER_SUB),
    )


def login_config() -> LoginConfig:
    return LoginConfig(
        client_id=settings.AUTH_CLIENT_ID,
        client_secret=settings.AUTH_CLIENT_SECRET,
        redirect_uri=settings.AUTH_REDIRECT_URI,
        authorize_url=f"{settings.AUTH_ISSUER}/authorize",
        token_url=f"{settings.AUTH_ISSUER}/token",
        issuer=settings.AUTH_ISSUER,
        resource=settings.AUTH_RESOURCE,
        scope="login",
    )


def scope_auth(scope: Scope) -> BearerAuth:
    """ninja `auth=`용. 통과하면 `request.auth`가 `AccessToken`."""
    return BearerAuth(scope, config=bearer_config)


# 일반 Django 뷰용(MCP): 뷰는 `view(request, token, …)`로 불린다.
require_editor = require_bearer("editor", config=bearer_config)


def protected_resource_metadata(request: HttpRequest) -> HttpResponse:
    """RFC 9728 보호 리소스 메타데이터(규칙 문서 6절). 인증 없이 GET."""
    return metadata_response(settings.AUTH_RESOURCE, settings.AUTH_ISSUER, list(SCOPES))


# --- admin 로그인: sandcorp-auth 코드 플로우. 자체 비밀번호 로그인은 없다. ---


def _admin_index() -> str:
    return reverse("admin:index")


def _safe_next(request: HttpRequest, value: object) -> str:
    if isinstance(value, str) and url_has_allowed_host_and_scheme(
        value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return value
    return _admin_index()


@require_GET
def login_start(request: HttpRequest) -> HttpResponse:
    """state·PKCE·nonce를 세션에 두고 sandcorp-auth 인가 엔드포인트로 보낸다."""
    if request.user.is_active and request.user.is_staff:
        return HttpResponseRedirect(_admin_index())
    try:
        url = start_login(
            login_config(),
            SessionAttemptStore(request.session),
            clock=clock,
            extra={"next": _safe_next(request, request.GET.get("next", ""))},
        )
    except LoginRejected as exc:
        logger.warning("admin login not started: %s", exc.reason)
        return HttpResponse("로그인 서버에 연결할 수 없습니다.", status=503)
    return HttpResponseRedirect(url)


def _deny(reason: str) -> HttpResponse:
    logger.info("admin login denied: %s", reason)
    return HttpResponse("로그인할 수 없습니다.", status=403)


@require_GET
def login_callback(request: HttpRequest) -> HttpResponse:
    """state 확인 → 코드 교환 → 토큰 검증(login 스코프, 대표 sub) → Django 세션."""
    try:
        result = complete_login(
            request.GET.dict(),
            login_config(),
            SessionAttemptStore(request.session),
            jwks=jwks_cache(),
            clock=clock,
            extra_claims_hook=owner_only(settings.AUTH_OWNER_SUB),
            transport=token_transport(),
        )
    except LoginRejected as exc:
        if exc.reason == "state":
            return HttpResponseBadRequest("로그인 요청이 맞지 않습니다. 다시 시도하세요.")
        return _deny(exc.reason)

    user_model = get_user_model()
    owner, created = user_model.objects.get_or_create(username=result.access.sub)
    if created:
        owner.set_unusable_password()
    elif not owner.is_active:
        return _deny("owner account is deactivated")  # 비활성 대표는 로그인으로 되살리지 않는다
    if created or not (owner.is_staff and owner.is_superuser):
        owner.is_active = owner.is_staff = owner.is_superuser = True
        owner.save()
    login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponseRedirect(_safe_next(request, result.extra.get("next")))
