"""admin 로그인: sandcorp-auth 코드 플로우(PKCE S256)와 대표 세션. 인가 서버는 가짜."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from sandcorp_authkit.testing import FakeTokenEndpoint

ADMIN = "/" + settings.ADMIN_PATH
AUTHORIZE = "https://auth.sandcorp.co.kr/authorize"


def start_login(client: Client) -> str:
    """비로그인으로 admin에 들어가 인가 주소까지 따라가고, 그 전체 주소를 돌려준다."""
    first = client.get(ADMIN)
    assert first.status_code == 302
    second = client.get(first["Location"])
    assert second.status_code == 302
    return str(second["Location"])


def query_of(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.mark.django_db
def test_anonymous_admin_is_sent_to_sandcorp_auth_with_pkce(client: Client) -> None:
    url = start_login(client)
    query = query_of(url)

    assert url.startswith(AUTHORIZE + "?")
    assert query["response_type"] == "code"
    assert query["client_id"] == "news-client"
    assert query["redirect_uri"] == "https://news.sandcorp.co.kr/auth/callback"
    assert query["resource"] == "https://news.sandcorp.co.kr/mcp"
    assert query["scope"] == "login"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 32
    assert query["nonce"]


@pytest.mark.django_db
def test_callback_with_owner_token_opens_admin(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    callback = fake_auth_server.approve(start_login(client), sub="owner")

    response = client.get("/auth/callback", callback)

    assert response.status_code == 302
    assert response["Location"] == ADMIN
    [exchange] = fake_auth_server.requests
    assert exchange.form["redirect_uri"] == "https://news.sandcorp.co.kr/auth/callback"
    owner = get_user_model().objects.get(username="owner")
    assert owner.is_staff and owner.is_superuser and not owner.has_usable_password()
    assert client.get(ADMIN).status_code == 200


@pytest.mark.django_db
def test_callback_keeps_a_safe_next_and_drops_an_unsafe_one(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    first = client.get(f"{ADMIN}login/", {"next": f"{ADMIN}articles/article/"})
    callback = fake_auth_server.approve(first["Location"], sub="owner")
    assert client.get("/auth/callback", callback)["Location"] == f"{ADMIN}articles/article/"

    other = Client()
    first = other.get(f"{ADMIN}login/", {"next": "https://evil.example/x"})
    callback = fake_auth_server.approve(first["Location"], sub="owner")
    assert other.get("/auth/callback", callback)["Location"] == ADMIN


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("sub", "scope"),
    [("someone-else", "login"), ("owner", "admin editor")],
    ids=["other-sub", "no-login-scope"],
)
def test_callback_rejects_token_that_is_not_an_owner_login(
    client: Client, fake_auth_server: FakeTokenEndpoint, sub: str, scope: str
) -> None:
    callback = fake_auth_server.approve(start_login(client), sub=sub, scope=scope)

    response = client.get("/auth/callback", callback)

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session
    assert not get_user_model().objects.exists()


@pytest.mark.django_db
def test_callback_does_not_reactivate_a_deactivated_owner(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    """비활성 대표(퇴직·잠금 처리한 계정)는 로그인으로 되살아나지 않는다."""
    user_model = get_user_model()
    user_model.objects.create_superuser("owner", password="pw", is_active=False)
    callback = fake_auth_server.approve(start_login(client), sub="owner")

    response = client.get("/auth/callback", callback)

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session
    assert not user_model.objects.get(username="owner").is_active


@pytest.mark.django_db
def test_callback_restores_staff_flags_of_an_active_owner(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    user_model = get_user_model()
    user_model.objects.create_user("owner", password="pw")
    callback = fake_auth_server.approve(start_login(client), sub="owner")

    assert client.get("/auth/callback", callback).status_code == 302
    owner = user_model.objects.get(username="owner")
    assert owner.is_active and owner.is_staff and owner.is_superuser


@pytest.mark.django_db
def test_callback_rejects_state_mismatch_without_exchanging(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    callback = fake_auth_server.approve(start_login(client), sub="owner")

    response = client.get("/auth/callback", {"code": callback["code"], "state": "forged"})

    assert response.status_code == 400
    assert fake_auth_server.requests == []
    assert "_auth_user_id" not in client.session
    # 시도는 한 번만 쓴다: 맞는 state로 다시 와도 이미 지워졌다.
    assert client.get("/auth/callback", callback).status_code == 400
    assert client.get(ADMIN).status_code == 302


@pytest.mark.django_db
def test_callback_without_a_started_login_is_rejected(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    response = client.get("/auth/callback", {"code": "c", "state": "anything"})

    assert response.status_code == 400
    assert fake_auth_server.requests == []


@pytest.mark.django_db
def test_callback_denied_by_the_user_is_refused(
    client: Client, fake_auth_server: FakeTokenEndpoint
) -> None:
    query = query_of(start_login(client))

    response = client.get("/auth/callback", {"error": "access_denied", "state": query["state"]})

    assert response.status_code == 403
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_login_start_without_client_settings_is_503(client: Client, settings: Any) -> None:
    settings.AUTH_CLIENT_SECRET = ""

    assert client.get(client.get(ADMIN)["Location"]).status_code == 503


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
