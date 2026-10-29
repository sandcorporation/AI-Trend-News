import io
import json
import re
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

import pytest
from django.core.cache import cache
from django.test import Client
from PIL import Image
from sandcorp_authkit.testing import FixedClock

from articles.models import Article, Category
from services import articles as svc
from services import og_card
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)  # 12:00 KST 2026-10-26


@pytest.fixture(autouse=True)
def now(frozen_clock: FixedClock) -> None:
    frozen_clock.moment = NOW
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


def at(
    title: str, published_at: datetime, *, featured: bool = False, category: str = "AI"
) -> Article:
    article = make_article(title, category=category, featured=featured)
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

    def counted(day: date, titles: list[str], total: int, category_name: str = "") -> bytes:
        calls.append(1)
        return real(day, titles, total, category_name)

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


# --- 분야별 날짜 모음 (#104) ---


def two_categories() -> None:
    """AI(ai)·IT(it) 두 분야 기사: 10-23 AI·IT, 10-25 IT, 10-26 AI 2건 + IT 1건."""
    at("AI 23일", datetime(2026, 10, 23, 2, 0, tzinfo=UTC))
    at("IT 23일", datetime(2026, 10, 23, 3, 0, tzinfo=UTC), category="IT")
    at("IT 25일", datetime(2026, 10, 25, 2, 0, tzinfo=UTC), category="IT")
    at("AI 26일 가", datetime(2026, 10, 26, 1, 0, tzinfo=UTC))
    at("AI 26일 나", datetime(2026, 10, 26, 2, 0, tzinfo=UTC))
    at("IT 26일", datetime(2026, 10, 26, 3, 0, tzinfo=UTC), category="IT")
    Category.objects.filter(name="AI").update(slug="ai")
    Category.objects.filter(name="IT").update(slug="it")


def test_category_day_lists_only_that_category_with_meta(client: Client) -> None:
    two_categories()
    response = client.get("/news/2026-10-26/ai")
    html = response.content.decode()
    assert response.status_code == 200
    data = page_data(html)
    assert [c["title"] for c in data["articles"]] == ["AI 26일 나", "AI 26일 가"]  # type: ignore[attr-defined]
    assert data["category"] == "ai"
    assert data["heading"] == "2026년 10월 26일 (월) AI 뉴스"
    assert meta(html, "og:title") == "10월 26일 AI 뉴스 2건 — Foresight"
    assert meta(html, "og:url") == "http://testserver/news/2026-10-26/ai"
    assert 'rel="canonical" href="http://testserver/news/2026-10-26/ai"' in html
    assert urlsplit(meta(html, "og:image")).path == "/news/2026-10-26/ai/og.png"
    assert "noindex" not in html
    assert data["share"]["url"] == "http://testserver/news/2026-10-26/ai"  # type: ignore[index]
    # 서버 HTML에도 같은 목록과 탭
    assert "AI 26일 가" in html and "IT 26일" not in html
    assert 'data-slot="day-tabs"' in html


def test_category_tabs_follow_category_order_and_mark_current(client: Client) -> None:
    two_categories()
    data = page_data(client.get("/news/2026-10-26/it").content.decode())
    assert data["tabs"] == [
        {"label": "전체", "url": "/news/2026-10-26", "current": False},
        {"label": "AI", "url": "/news/2026-10-26/ai", "current": False},
        {"label": "IT", "url": "/news/2026-10-26/it", "current": True},
    ]
    whole = page_data(client.get("/news/2026-10-26").content.decode())
    assert whole["tabs"][0]["current"] is True  # type: ignore[index]
    assert whole["category"] == ""


def test_category_neighbors_keep_category(client: Client) -> None:
    two_categories()
    # IT는 23·25·26일, AI는 23·26일: AI 26일의 이전은 25일이 아니라 23일
    ai = page_data(client.get("/news/2026-10-26/ai").content.decode())
    assert ai["prev"]["url"] == "/news/2026-10-23/ai"  # type: ignore[index]
    assert ai["next"] is None
    it = page_data(client.get("/news/2026-10-23/it").content.decode())
    assert it["next"]["url"] == "/news/2026-10-25/it"  # type: ignore[index]
    # 전체 모음의 이웃은 분야 없는 주소
    whole = page_data(client.get("/news/2026-10-26").content.decode())
    assert whole["prev"]["url"] == "/news/2026-10-25"  # type: ignore[index]


def test_unknown_category_is_404(client: Client) -> None:
    two_categories()
    assert client.get("/news/2026-10-26/nope").status_code == 404
    assert client.get("/news/2026-10-26/nope/og.png").status_code == 404
    assert client.get("/news/2026-10-26?category=nope").status_code == 404


def test_empty_category_day_is_200_noindex_with_link_to_all(client: Client) -> None:
    two_categories()
    response = client.get("/news/2026-10-25/ai")  # 그날 AI 기사 없음
    html = response.content.decode()
    assert response.status_code == 200
    assert meta(html, "robots") == "noindex, nofollow"
    assert "이 날 AI 기사가 없습니다." in html
    assert 'href="/news/2026-10-25"' in html
    assert "/og-default" in meta(html, "og:image")
    data = page_data(html)
    assert data["empty_text"] == "이 날 AI 기사가 없습니다."
    assert data["all_url"] == "/news/2026-10-25"
    assert data["prev"]["url"] == "/news/2026-10-23/ai"  # type: ignore[index]
    og = client.get("/news/2026-10-25/ai/og.png")
    assert og.status_code == 200
    assert Image.open(io.BytesIO(og.content)).size == (1200, 630)


def test_query_category_redirects_to_path_keeping_other_params(client: Client) -> None:
    two_categories()
    response = client.get("/news/2026-10-26?category=ai&utm_source=x")
    assert response.status_code == 301
    assert response["Location"] == "/news/2026-10-26/ai?utm_source=x"
    plain = client.get("/news/2026-10-26?category=ai")
    assert plain["Location"] == "/news/2026-10-26/ai"
    assert client.get("/news/2026-10-26?category=").status_code == 200


def test_old_category_slug_redirects_to_new_path(client: Client) -> None:
    two_categories()
    svc.update_category("ai", new_slug="artificial")
    path = client.get("/news/2026-10-26/ai?x=1")
    assert path.status_code == 301
    assert path["Location"] == "/news/2026-10-26/artificial?x=1"
    query = client.get("/news/2026-10-26?category=ai")
    assert query["Location"] == "/news/2026-10-26/artificial"
    og = client.get("/news/2026-10-26/ai/og.png")
    assert og.status_code == 301
    assert og["Location"] == "/news/2026-10-26/artificial/og.png"
    assert client.get("/news/2026-10-26/artificial").status_code == 200


def test_category_og_png_uses_category_and_own_cache(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    two_categories()
    seen: list[tuple[int, str]] = []
    real = og_card.render_day_card

    def spy(day: date, titles: list[str], total: int, category_name: str = "") -> bytes:
        seen.append((total, category_name))
        return real(day, titles, total, category_name)

    monkeypatch.setattr(og_card, "render_day_card", spy)
    ai = client.get("/news/2026-10-26/ai/og.png")
    assert ai.status_code == 200
    assert Image.open(io.BytesIO(ai.content)).size == (1200, 630)
    whole = client.get("/news/2026-10-26/og.png")
    assert whole["ETag"] != ai["ETag"]
    assert seen == [(2, "AI"), (3, "")]


def test_sitemap_lists_category_days_with_articles_only(client: Client) -> None:
    two_categories()
    xml = client.get("/sitemap.xml").content.decode()
    assert "/news/2026-10-26/ai</loc>" in xml
    assert "/news/2026-10-25/it</loc>" in xml
    assert "/news/2026-10-25/ai</loc>" not in xml  # 그날 AI 0건
