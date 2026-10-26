import io
import json
import re
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
from django.core.cache import cache
from django.test import Client
from PIL import Image

from articles.models import Article
from services import og_card
from tests.conftest import FrozenClock
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)  # 12:00 KST 2026-10-26


@pytest.fixture(autouse=True)
def now(frozen_clock: FrozenClock) -> None:
    frozen_clock._now = NOW
    cache.clear()


def page_data(html: str) -> dict[str, object]:
    match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    data: dict[str, object] = json.loads(match.group(1))
    return data


def meta(html: str, key: str) -> str:
    match = re.search(rf'<meta (?:property|name)="{re.escape(key)}" content="([^"]*)"', html)
    assert match, key
    return match.group(1)


def at(title: str, published_at: datetime, *, featured: bool = False) -> Article:
    article = make_article(title, category="AI", featured=featured)
    Article.objects.filter(pk=article.pk).update(published_at=published_at)
    article.refresh_from_db()
    return article


def test_day_boundary_is_kst_not_utc(client: Client) -> None:
    at("한밤 직전", datetime(2026, 10, 25, 14, 59, tzinfo=UTC))  # 10-25 23:59 KST
    at("자정 직후", datetime(2026, 10, 25, 15, 0, tzinfo=UTC))  # 10-26 00:00 KST
    at("오후", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))

    data = page_data(client.get("/news/2026-10-26").content.decode())
    assert [c["title"] for c in data["articles"]] == ["오후", "자정 직후"]  # type: ignore[attr-defined]
    prev = page_data(client.get("/news/2026-10-25").content.decode())
    assert [c["title"] for c in prev["articles"]] == ["한밤 직전"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "raw", ["2026-1-26", "20261026", "2026-13-01", "2026-02-30", "abc", "2026-10-26x"]
)
def test_bad_format_is_404(client: Client, raw: str) -> None:
    at("기사", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))
    assert client.get(f"/news/{raw}").status_code == 404
    assert client.get(f"/news/{raw}/og.png").status_code == 404


def test_future_and_before_first_day_are_404(client: Client) -> None:
    at("기사", datetime(2026, 10, 24, 2, 0, tzinfo=UTC))
    assert client.get("/news/2026-10-27").status_code == 404
    assert client.get("/news/2026-10-23").status_code == 404
    assert client.get("/news/2026-10-26").status_code == 200  # 오늘, 기사 0건


def test_no_articles_at_all_is_404(client: Client) -> None:
    assert client.get("/news/2026-10-26").status_code == 404


def test_empty_day_is_200_noindex_with_default_image_and_neighbors(client: Client) -> None:
    at("앞", datetime(2026, 10, 22, 2, 0, tzinfo=UTC))
    at("뒤", datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
    response = client.get("/news/2026-10-24")
    html = response.content.decode()
    assert response.status_code == 200
    assert meta(html, "robots") == "noindex, nofollow"
    assert "이 날은 게시된 기사가 없습니다" in html
    assert "/og-default" in meta(html, "og:image")
    data = page_data(html)
    assert data["prev"]["url"] == "/news/2026-10-22"  # type: ignore[index]
    assert data["next"]["url"] == "/news/2026-10-25"  # type: ignore[index]
    # 0건 날짜의 og.png는 기본 이미지
    og = client.get("/news/2026-10-24/og.png")
    assert og.status_code == 200
    assert Image.open(io.BytesIO(og.content)).size == (1200, 630)


def test_neighbors_only_link_days_with_articles(client: Client) -> None:
    at("A", datetime(2026, 10, 20, 2, 0, tzinfo=UTC))
    at("B", datetime(2026, 10, 23, 2, 0, tzinfo=UTC))
    at("C", datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
    data = page_data(client.get("/news/2026-10-23").content.decode())
    assert data["prev"] == {"label": "2026년 10월 20일 (화)", "url": "/news/2026-10-20"}
    assert data["next"] == {"label": "2026년 10월 25일 (일)", "url": "/news/2026-10-25"}
    first = page_data(client.get("/news/2026-10-20").content.decode())
    assert first["prev"] is None


def test_share_meta(client: Client) -> None:
    at("가 기사", datetime(2026, 10, 26, 1, 0, tzinfo=UTC))
    at("나 기사", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))
    at("다 기사", datetime(2026, 10, 26, 2, 30, tzinfo=UTC))
    at("라 기사", datetime(2026, 10, 26, 2, 40, tzinfo=UTC), featured=True)
    response = client.get("/news/2026-10-26")
    html = response.content.decode()
    assert meta(html, "og:title") == "10월 26일 AI·IT 뉴스 4건 — Foresight"
    # 추천 기사가 앞, 나머지는 최신순
    assert meta(html, "og:description") == "라 기사 · 다 기사 · 나 기사"
    assert meta(html, "og:type") == "website"
    assert meta(html, "og:url") == "http://testserver/news/2026-10-26"
    assert meta(html, "twitter:card") == "summary_large_image"
    image = meta(html, "og:image")
    assert urlsplit(image).path == "/news/2026-10-26/og.png"
    assert image.startswith("http://testserver/")
    assert meta(html, "og:image:width") == "1200"
    assert meta(html, "og:image:height") == "630"
    assert meta(html, "og:image:alt") == "10월 26일 AI·IT 뉴스 4건 — Foresight"
    assert 'rel="canonical" href="http://testserver/news/2026-10-26"' in html
    assert "noindex" not in html
    data = page_data(html)
    assert data["heading"] == "2026년 10월 26일 (월) AI·IT 뉴스"
    assert data["count"] == 4
    assert data["share"]["title"] == "10월 26일 AI·IT 뉴스 4건 — Foresight"  # type: ignore[index]


def test_description_fits_160_chars(client: Client) -> None:
    for i in range(3):
        at("긴" * 100 + str(i), datetime(2026, 10, 26, 2, i, tzinfo=UTC))
    html = client.get("/news/2026-10-26").content.decode()
    assert len(meta(html, "og:description")) <= 160


def test_og_png_is_1200x630_and_cached_by_etag(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    article = at("카드에 들어갈 제목입니다", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))
    calls: list[int] = []
    real = og_card.render_day_card

    def counted(day: date, titles: list[str], total: int) -> bytes:
        calls.append(1)
        return real(day, titles, total)

    monkeypatch.setattr(og_card, "render_day_card", counted)
    first = client.get("/news/2026-10-26/og.png")
    assert first.status_code == 200
    assert first["Content-Type"] == "image/png"
    assert first["Cache-Control"] == "public, max-age=3600"
    assert Image.open(io.BytesIO(first.content)).size == (1200, 630)
    second = client.get("/news/2026-10-26/og.png")
    assert second["ETag"] == first["ETag"]
    assert second.content == first.content
    assert len(calls) == 1  # 두 번째는 캐시
    not_modified = client.get("/news/2026-10-26/og.png", headers={"If-None-Match": first["ETag"]})
    assert not_modified.status_code == 304
    # 기사가 바뀌면 키가 바뀌어 다시 합성한다
    Article.objects.filter(pk=article.pk).update(title="바뀐 제목")
    Article.objects.get(pk=article.pk).save()
    third = client.get("/news/2026-10-26/og.png")
    assert third["ETag"] != first["ETag"]
    assert len(calls) == 2


def test_og_png_failure_falls_back_to_default(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    at("기사", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))

    def boom(*args: object, **kwargs: object) -> bytes:
        raise OSError("font missing")

    monkeypatch.setattr(og_card, "render_day_card", boom)
    response = client.get("/news/2026-10-26/og.png")
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).size == (1200, 630)


def test_sitemap_lists_recent_days_with_articles(client: Client) -> None:
    at("최근", datetime(2026, 10, 25, 2, 0, tzinfo=UTC))
    at("오래", datetime(2026, 8, 1, 2, 0, tzinfo=UTC))  # 60일 밖
    xml = client.get("/sitemap.xml").content.decode()
    assert "/news/2026-10-25</loc>" in xml
    assert "/news/2026-08-01</loc>" not in xml


def test_home_sidebar_links_to_today(client: Client) -> None:
    html = client.get("/").content.decode()
    assert 'href="/news/2026-10-26"' in html
    assert page_data(html)["sidebar"]["share_url"] == "/news/2026-10-26"  # type: ignore[index]
