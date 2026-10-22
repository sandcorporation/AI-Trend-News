"""admin 로그인: sandcorp-auth 코드 플로우(PKCE S256)와 대표 세션. 인가 서버는 가짜."""

import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from tests.conftest import FAKE_AUTHORIZE, FakeAuthServer, Signer

ADMIN = "/" + settings.ADMIN_PATH


def start_login(client: Client) -> dict[str, str]:
    """비로그인으로 admin에 들어가 인가 주소까지 따라가고, 그 쿼리를 돌려준다."""
    first = client.get(ADMIN)
    assert first.status_code == 302
    second = client.get(first["Location"])
    assert second.status_code == 302
    url = urlsplit(second["Location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == FAKE_AUTHORIZE
    return {k: v[0] for k, v in parse_qs(url.query).items()}


@pytest.mark.django_db
def test_anonymous_admin_is_sent_to_sandcorp_auth_with_pkce(client: Client) -> None:
    query = start_login(client)

    assert query["response_type"] == "code"
    assert query["client_id"] == "news-client"
    assert query["redirect_uri"] == "https://news.sandcorp.co.kr/auth/callback"
    assert query["resource"] == "https://news.sandcorp.co.kr/mcp"
    assert query["scope"] == "login"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 32
    verifier = client.session["auth_login"]["verifier"]
    digest = hashlib.sha256(verifier.encode()).digest()
    assert query["code_challenge"] == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@pytest.mark.django_db
def test_callback_with_owner_token_opens_admin(
    client: Client, token_signer: Signer, fake_auth_server: FakeAuthServer
) -> None:
    query = start_login(client)
    verifier = client.session["auth_login"]["verifier"]
    fake_auth_server.access_token = token_signer.token(scope="login")

    response = client.get("/auth/callback", {"code": "the-code", "state": query["state"]})

    assert response.status_code == 302
    assert response["Location"] == ADMIN
    assert fake_auth_server.exchanges == [
        {
            "code": "the-code",
            "redirect_uri": "https://news.sandcorp.co.kr/auth/callback",
            "code_verifier": verifier,
        }
    ]
    owner = get_user_model().objects.get(username="owner")
    assert owner.is_staff and owner.is_superuser and not owner.has_usable_password()
    assert client.get(ADMIN).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize(
    "claims",
    [{"sub": "someone-else", "scope": "login"}, {"scope": "admin editor"}],
    ids=["other-sub", "no-login-scope"],
)
def test_callback_rejects_token_that_is_not_an_owner_login(
    client: Client,
    token_signer: Signer,
    fake_auth_server: FakeAuthServer,
    claims: dict[str, str],
) -> None:
    query = start_login(client)
    fake_auth_server.access_token = token_signer.token(**claims)

    response = client.get("/auth/callback", {"code": "c", "state": query["state"]})

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_callback_rejects_state_mismatch_without_exchanging(
    client: Client, token_signer: Signer, fake_auth_server: FakeAuthServer
) -> None:
    start_login(client)
    fake_auth_server.access_token = token_signer.token(scope="login")

    response = client.get("/auth/callback", {"code": "c", "state": "forged"})

    assert response.status_code == 400
    assert fake_auth_server.exchanges == []
    assert "_auth_user_id" not in client.session
    # state는 한 번만 쓴다: 맞는 state로 다시 와도 이미 지워졌다.
    assert client.get(ADMIN).status_code == 302


@pytest.mark.django_db
def test_callback_without_a_started_login_is_rejected(
    client: Client, fake_auth_server: FakeAuthServer
) -> None:
    response = client.get("/auth/callback", {"code": "c", "state": "anything"})

    assert response.status_code == 400
    assert fake_auth_server.exchanges == []


@pytest.mark.django_db
def test_default_admin_path_is_404(client: Client) -> None:
    assert settings.ADMIN_PATH != "admin/"
    assert client.get("/admin/").status_code == 404
    assert client.get("/admin/login/").status_code == 404


@pytest.mark.django_db
def test_admin_has_no_password_login(client: Client) -> None:
    get_user_model().objects.create_superuser("owner", password="pw")

    response = client.post(f"{ADMIN}login/", {"username": "owner", "password": "pw"})

    assert response.status_code == 405
    assert "_auth_user_id" not in client.session
