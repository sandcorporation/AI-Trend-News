"""리소스 서버 HTTP 경계: 보호된 엔드포인트의 401/403/200, 보호 리소스 메타데이터.
DB 없이 돈다(django_db 없음)."""

import pytest
from django.test import Client, override_settings
from sandcorp_authkit.testing import FixedClock, SigningKey

from tests.conftest import Signer

pytestmark = pytest.mark.urls("tests.auth_urls")

META = "https://news.sandcorp.co.kr/.well-known/oauth-protected-resource"
NO_TOKEN = f'Bearer resource_metadata="{META}"'
INVALID = f'Bearer resource_metadata="{META}", error="invalid_token"'


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("path", ["/probe/admin-only", "/probe-plain/editor"])
def test_valid_token_passes(client: Client, token_signer: Signer, path: str) -> None:
    response = client.get(path, headers=bearer(token_signer.token()))

    assert response.status_code == 200
    assert response.json() == {"sub": "owner"}


def test_bearer_scheme_is_case_insensitive(client: Client, token_signer: Signer) -> None:
    response = client.get(
        "/probe/admin-only", headers={"Authorization": f"bearer {token_signer.token()}"}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/probe/admin-only", "/probe-plain/editor"])
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic b3duZXI6cHc="}, {"Authorization": "Bearer "}],
)
def test_missing_token_gets_401_with_resource_metadata(
    client: Client, path: str, headers: dict[str, str]
) -> None:
    response = client.get(path, headers=headers)

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == NO_TOKEN


def test_token_in_query_string_is_ignored(client: Client, token_signer: Signer) -> None:
    response = client.get(f"/probe/admin-only?access_token={token_signer.token()}")

    assert response.status_code == 401
    assert response["WWW-Authenticate"] == NO_TOKEN


@pytest.mark.parametrize("path", ["/probe/admin-only", "/probe-plain/editor"])
def test_wrong_tokens_get_401_invalid_token(
    client: Client, token_signer: Signer, frozen_clock: FixedClock, path: str
) -> None:
    wrong = [
        "not-a-jwt",
        token_signer.jwks.issue_access_token(
            frozen_clock,
            audience="https://news.sandcorp.co.kr/mcp",
            sub="owner",
            scope="admin",
            key=SigningKey(token_signer.jwks.key.kid),
        ),  # 같은 kid, 다른 키: 잘못된 서명
        token_signer.token(aud="https://sandcorp.co.kr/mcp"),  # 다른 aud
        token_signer.token(sub="intruder"),  # 다른 sub
        token_signer.token(exp=int(frozen_clock.now().timestamp()) - 31),  # 허용 30초 지남
    ]
    for token in wrong:
        response = client.get(path, headers=bearer(token))
        assert response.status_code == 401, token
        assert response["WWW-Authenticate"] == INVALID
        assert response.json() == {"error": "unauthorized"}  # 이유는 싣지 않는다


def test_expiry_leeway_boundary_over_http(
    client: Client, token_signer: Signer, frozen_clock: FixedClock
) -> None:
    token = token_signer.token()
    frozen_clock.advance(600 + 30)
    assert client.get("/probe/admin-only", headers=bearer(token)).status_code == 200

    frozen_clock.advance(1)
    assert client.get("/probe/admin-only", headers=bearer(token)).status_code == 401


@pytest.mark.parametrize(
    ("path", "scope", "required"),
    [
        ("/probe/admin-only", "editor login", "admin"),
        ("/probe/editor-only", "admin", "editor"),
        ("/probe-plain/editor", "admin login", "editor"),
    ],
)
def test_insufficient_scope_gets_403(
    client: Client, token_signer: Signer, path: str, scope: str, required: str
) -> None:
    response = client.get(path, headers=bearer(token_signer.token(scope=scope)))

    assert response.status_code == 403
    assert response["WWW-Authenticate"] == (
        f'Bearer error="insufficient_scope", scope="{required}"'
    )


def test_protected_resource_metadata(client: Client) -> None:
    response = client.get("/.well-known/oauth-protected-resource")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.json() == {
        "resource": "https://news.sandcorp.co.kr/mcp",
        "authorization_servers": ["https://auth.sandcorp.co.kr"],
        "scopes_supported": ["admin", "editor", "login"],
        "bearer_methods_supported": ["header"],
    }


@override_settings(SITE_URL="http://localhost:8000", AUTH_RESOURCE="http://localhost:8000/mcp")
def test_metadata_and_challenge_follow_the_site_setting(client: Client) -> None:
    assert client.get("/.well-known/oauth-protected-resource").json()["resource"] == (
        "http://localhost:8000/mcp"
    )
    assert client.get("/probe/admin-only")["WWW-Authenticate"] == (
        'Bearer resource_metadata="http://localhost:8000/.well-known/oauth-protected-resource"'
    )
