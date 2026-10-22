"""admin 로그인: 이 사이트가 sandcorp-auth의 기밀 클라이언트로 코드 플로우(PKCE S256)를 돈다.

규칙 원본: sandcorp-auth `docs/resource-server-token-validation.md` 7절.
자체 비밀번호 로그인 화면은 없다. admin의 로그인 뷰 자리에 `login_start`를 둔다.
받은 액세스 토큰은 리소스 서버 규칙 그대로(요구 스코프 `login`) 검증하고 버린다.
"""

import base64
import hashlib
import logging
import secrets
import urllib.parse

from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET

from adapters.auth_server import AuthServerError, get_auth_server
from adapters.clock import get_clock
from adapters.jwks import get_key_source
from services.tokens import InsufficientScope, TokenRejected, validate_access_token

logger = logging.getLogger(__name__)

SESSION_KEY = "auth_login"
LOGIN_SCOPE = "login"


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _admin_index() -> str:
    return reverse("admin:index")


@require_GET
def login_start(request: HttpRequest) -> HttpResponse:
    """state·code_verifier를 세션에 두고 sandcorp-auth 인가 엔드포인트로 보낸다."""
    if request.user.is_active and request.user.is_staff:
        return HttpResponseRedirect(_admin_index())
    next_url = request.GET.get("next", "")
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = _admin_index()
    try:
        authorize = get_auth_server().endpoints().authorization_endpoint
    except AuthServerError as exc:
        logger.warning("auth server metadata unavailable: %s", exc)
        return HttpResponse("로그인 서버에 연결할 수 없습니다.", status=502)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    request.session[SESSION_KEY] = {"state": state, "verifier": verifier, "next": next_url}
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.AUTH_CLIENT_ID,
            "redirect_uri": settings.AUTH_REDIRECT_URI,
            "state": state,
            "code_challenge": _s256(verifier),
            "code_challenge_method": "S256",
            "resource": settings.AUTH_RESOURCE,
            "scope": LOGIN_SCOPE,
        }
    )
    return HttpResponseRedirect(f"{authorize}?{query}")


def _deny(reason: str) -> HttpResponse:
    logger.info("admin login denied: %s", reason)
    return HttpResponse("로그인할 수 없습니다.", status=403)


@require_GET
def login_callback(request: HttpRequest) -> HttpResponse:
    """state 확인 → 코드 교환 → 토큰 검증(login 스코프, 대표 sub) → Django 세션."""
    pending = request.session.pop(SESSION_KEY, None)
    state = request.GET.get("state", "")
    if not isinstance(pending, dict) or not secrets.compare_digest(
        str(pending.get("state", "")), state
    ):
        return HttpResponseBadRequest("로그인 요청이 맞지 않습니다. 다시 시도하세요.")
    code = request.GET.get("code", "")
    if not code:  # 거절·오류(error=access_denied 등)
        return _deny(f"no code (error={request.GET.get('error', '')!r})")
    try:
        access_token = get_auth_server().exchange_code(
            code=code,
            redirect_uri=settings.AUTH_REDIRECT_URI,
            code_verifier=str(pending["verifier"]),
        )
    except AuthServerError as exc:
        return _deny(f"code exchange failed: {exc}")
    try:
        validated = validate_access_token(
            access_token,
            keys=get_key_source(),
            issuer=settings.AUTH_ISSUER,
            audience=settings.AUTH_RESOURCE,
            owner_sub=settings.AUTH_OWNER_SUB,
            required_scope=LOGIN_SCOPE,
            clock=get_clock(),
        )
    except (TokenRejected, InsufficientScope) as exc:
        return _deny(f"token rejected: {type(exc).__name__}: {exc}")

    user_model = get_user_model()
    owner, created = user_model.objects.get_or_create(username=validated.sub)
    if created:
        owner.set_unusable_password()
    if created or not (owner.is_active and owner.is_staff and owner.is_superuser):
        owner.is_active = owner.is_staff = owner.is_superuser = True
        owner.save()
    login(request, owner, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponseRedirect(str(pending.get("next") or _admin_index()))
