"""404·500 화면(#202): 사이트 틀을 갖춘 한국어 404, 최소한의 한국어 500."""

from datetime import UTC, datetime

import pytest
from django.test import Client, RequestFactory
from pytest_django import DjangoAssertNumQueries
from sandcorp_authkit.testing import FixedClock

from config import urls
from services import articles as svc
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def now(frozen_clock: FixedClock) -> None:
    frozen_clock.moment = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "path",
    [
        "/no-such-page",
        "/a/no-such-article",
        "/a/없는-slug",
        "/a/hidden-draft",
        "/news/2026-13-01",
        "/news/abc",
        "/?category=nope",
        "/?page=abc",
        "/predictions?period=1y",
    ],
)
def test_every_404_is_the_same_korean_page_with_site_frame(client: Client, path: str) -> None:
    published = make_article("공개된 기사")
    draft = make_article("초안 기사", published=False)
    draft.slug = "hidden-draft"
    draft.save(update_fields=["slug"])

    response = client.get(path)

    assert response.status_code == 404
    html = response.content.decode()
    # React 없이도 읽히는 서버 HTML(#root 안)
    body = html.split('<div id="root">', 1)[1].split('<script id="page-data"', 1)[0]
    assert "<h1>페이지를 찾을 수 없습니다</h1>" in body
    assert '<a class="skip-link" href="#main">' in body
    assert '<header class="masthead">' in body
    assert '<footer class="site-footer">' in body
    assert '<a class="btn" href="/">홈으로</a>' in body
    assert f'<a href="/a/{published.slug}">공개된 기사</a>' in body
    assert "초안 기사" not in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert '"kind": "not_found"' in html


def test_500_is_korean_and_needs_no_database(
    monkeypatch: pytest.MonkeyPatch, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    def boom() -> None:
        raise RuntimeError("secret-detail")

    monkeypatch.setattr(svc, "public_categories", boom)
    response = Client(raise_request_exception=False).get("/")

    assert response.status_code == 500
    html = response.content.decode()
    assert "<h1>일시적인 오류가 났습니다</h1>" in html
    assert '<meta name="robots" content="noindex, nofollow">' in html
    assert '<a href="/">홈으로</a>' in html
    assert "secret-detail" not in html
    with django_assert_num_queries(0):
        assert urls.handler500(RequestFactory().get("/")).status_code == 500
