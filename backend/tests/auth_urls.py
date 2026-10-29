"""시험 전용 URLconf: 실제 URL에 스코프별 보호 엔드포인트를 더한다(ninja와 일반 뷰 둘 다)."""

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.urls import path
from ninja import NinjaAPI
from sandcorp_authkit.tokens import AccessToken

from config.urls import urlpatterns as site_urlpatterns
from core.auth_adapter import install_bearer_errors, require_editor, scope_auth

probe_api = NinjaAPI(urls_namespace="auth-probe", docs_url=None)
install_bearer_errors(probe_api)


@probe_api.get("/admin-only", auth=scope_auth("admin"))
def admin_only(request: HttpRequest) -> dict[str, str]:
    token = request.auth  # type: ignore[attr-defined]  # ninja가 붙인 값
    assert isinstance(token, AccessToken)
    return {"sub": token.sub}


@probe_api.get("/editor-only", auth=scope_auth("editor"))
def editor_only(request: HttpRequest) -> dict[str, str]:
    return {"ok": "editor"}


@require_editor
def plain_editor_view(request: HttpRequest, token: AccessToken) -> HttpResponse:
    return JsonResponse({"sub": token.sub})


urlpatterns = [
    path("probe/", probe_api.urls),
    path("probe-plain/editor", plain_editor_view),
    *site_urlpatterns,
]
