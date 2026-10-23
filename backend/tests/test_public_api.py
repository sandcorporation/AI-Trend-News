import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from articles.models import Article, Commentary, Prediction
from services import articles as svc
from tests.conftest import FrozenClock
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

API = "/api/public/articles"
MEDIA = "{http://search.yahoo.com/mrss/}"


def stamp(article: Article, hours_ago: int) -> None:
    Article.objects.filter(pk=article.pk).update(
        published_at=datetime(2026, 10, 20, 12, tzinfo=UTC) - timedelta(hours=hours_ago)
    )


def test_only_published_newest_first_with_documented_fields(client: Client) -> None:
    old = make_article("오래된 글")
    new = make_article("새 글", image_url="https://img.example/a.png", featured=True)
    make_article("초안", published=False)
    stamp(old, 5)
    stamp(new, 1)
    Commentary.objects.create(
        article=new,
        background="b",
        significance="s",
        outlook_bull="u",
        outlook_bear="d",
        opinion="o",
    )
    res = client.get(API)
    assert res.status_code == 200
    body = res.json()
    assert [i["title"] for i in body["items"]] == ["새 글", "오래된 글"]
    first = body["items"][0]
    assert set(first) == {
        "slug", "title", "summary", "category", "image_url", "published_at",
        "url", "featured", "has_commentary",
    }  # fmt: skip
    assert first["published_at"] == "2026-10-20T20:00:00+09:00"
    assert first["url"] == f"https://news.sandcorp.co.kr/a/{first['slug']}"
    assert first["category"] == {"name": "경제", "slug": first["category"]["slug"]}
    assert first["image_url"] == "https://img.example/a.png"
    assert first["featured"] is True and first["has_commentary"] is True
    assert body["items"][1]["has_commentary"] is False
    assert body["site"] == {"name": "Foresight", "url": "https://news.sandcorp.co.kr"}


def test_hidden_commentary_does_not_count(client: Client) -> None:
    a = make_article("숨김 분석")
    Commentary.objects.create(
        article=a, background="b", significance="s", outlook_bull="u", outlook_bear="d",
        opinion="o", hidden=True,
    )  # fmt: skip
    assert client.get(API).json()["items"][0]["has_commentary"] is False


def test_limit_default_cap_and_floor(client: Client) -> None:
    for i in range(25):
        make_article(f"글 {i}")
    assert len(client.get(API).json()["items"]) == 5
    assert len(client.get(API, {"limit": 100}).json()["items"]) == 20
    assert len(client.get(API, {"limit": 0}).json()["items"]) == 1


def test_category_old_slug_and_unknown(client: Client) -> None:
    make_article("경제 글", category="경제")
    make_article("과학 글", category="과학")
    slug = svc.list_categories()[0].slug
    other = [c for c in svc.list_categories() if c.name == "경제"][0]
    assert [i["title"] for i in client.get(API, {"category": other.slug}).json()["items"]] == [
        "경제 글"
    ]
    svc.update_category(other.slug, new_slug="economy")
    by_old = client.get(API, {"category": other.slug}).json()["items"]
    assert [i["title"] for i in by_old] == ["경제 글"] and by_old[0]["category"][
        "slug"
    ] == "economy"
    assert client.get(API, {"category": "nope"}).json()["items"] == []
    assert slug


def test_featured_only(client: Client) -> None:
    make_article("보통")
    make_article("추천", featured=True)
    items = client.get(API, {"featured": "true"}).json()["items"]
    assert [i["title"] for i in items] == ["추천"]


def _comment(a: Article, hidden: bool = False) -> None:
    Commentary.objects.create(
        article=a, background="b", significance="s", outlook_bull="u", outlook_bear="d",
        opinion="o", hidden=hidden,
    )  # fmt: skip


def test_commentary_filter_excludes_none_hidden_unpublished_and_applies_limit(
    client: Client,
) -> None:
    make_article("분석 없음")
    _comment(make_article("숨김"), hidden=True)
    unpub = make_article("미게시")
    _comment(unpub)
    Article.objects.filter(pk=unpub.pk).update(published=False)
    for i in range(3):
        _comment(make_article(f"분석 {i}"))
    items = client.get(API, {"commentary": "true", "limit": "2"}).json()["items"]
    assert [i["title"] for i in items] == ["분석 2", "분석 1"]
    assert all(i["has_commentary"] for i in items)
    all_items = client.get(API, {"commentary": "true"}).json()["items"]
    assert sorted(i["title"] for i in all_items) == ["분석 0", "분석 1", "분석 2"]


def test_commentary_filter_combines_with_featured_and_default_unchanged(client: Client) -> None:
    _comment(make_article("분석만"))
    _comment(make_article("분석 추천", featured=True))
    make_article("추천만", featured=True)
    both = client.get(API, {"commentary": "true", "featured": "true"}).json()["items"]
    assert [i["title"] for i in both] == ["분석 추천"]
    base = client.get(API).content
    assert client.get(API, {"commentary": "false"}).content == base
    assert len(client.get(API).json()["items"]) == 3
    with CaptureQueriesContext(connection) as ctx:
        client.get(API, {"commentary": "true", "limit": "20"})
    assert len(ctx) <= 2


def test_cache_header_no_cors_and_query_count(client: Client) -> None:
    for i in range(6):
        make_article(f"글 {i}")
    with CaptureQueriesContext(connection) as ctx:
        res = client.get(API, {"category": "x", "featured": "true"}, HTTP_ORIGIN="https://e.com")
    assert len(ctx) <= 2
    assert res["Cache-Control"] == "public, max-age=300"
    assert "Access-Control-Allow-Origin" not in res
    with CaptureQueriesContext(connection) as ctx:
        client.get(API, {"limit": 20})
    assert len(ctx) <= 2


def test_feed_rss(client: Client) -> None:
    a = make_article("첫 & 글", image_url="https://img.example/a.png")
    b = make_article("둘째 글")
    make_article("초안", published=False)
    stamp(a, 1)
    stamp(b, 3)
    res = client.get("/feed.xml")
    assert res.status_code == 200
    assert res["Content-Type"] == "application/rss+xml; charset=utf-8"
    assert res["Cache-Control"] == "public, max-age=300"
    channel = ET.fromstring(res.content).find("channel")
    assert channel is not None
    assert channel.findtext("title") == "Foresight"
    items = channel.findall("item")
    assert [i.findtext("title") for i in items] == ["첫 & 글", "둘째 글"]
    assert items[0].findtext("link") == f"http://testserver/a/{a.slug}"
    assert items[0].findtext("guid") == items[0].findtext("link")
    assert items[0].findtext("pubDate") == "Tue, 20 Oct 2026 20:00:00 +0900"
    assert items[0].findtext("category") == "경제"
    assert channel.findtext("lastBuildDate") == items[0].findtext("pubDate")
    media = items[0].find(f"{MEDIA}content")
    assert media is not None and media.attrib == {
        "url": "https://img.example/a.png",
        "medium": "image",
    }
    assert items[1].find(f"{MEDIA}content") is None
    assert items[0].find("enclosure") is None


def test_feed_empty_and_capped(client: Client) -> None:
    channel = ET.fromstring(client.get("/feed.xml").content).find("channel")
    assert channel is not None and channel.findall("item") == []
    for i in range(32):
        make_article(f"글 {i}")
    channel = ET.fromstring(client.get("/feed.xml").content).find("channel")
    assert channel is not None and len(channel.findall("item")) == 30


def test_feed_not_in_sitemap(client: Client) -> None:
    assert "feed.xml" not in client.get("/sitemap.xml").content.decode()


STATS = "/api/public/stats"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    cache.clear()


def make_predictions(verdicts: list[str], *, hidden: bool = False, published: bool = True) -> None:
    article = make_article(f"전망 {len(verdicts)}{hidden}{published}", published=published)
    com = Commentary.objects.create(
        article=article, background="b", significance="s", outlook_bull="u", outlook_bear="d",
        opinion="o", hidden=hidden,
    )  # fmt: skip
    for i, verdict in enumerate(verdicts):
        Prediction.objects.create(
            commentary=com, claim=f"c{i}", horizon="1m", basis="b",
            due_date=NOW.date(), verdict=verdict,
        )  # fmt: skip


def test_stats_empty(client: Client, frozen_clock: FrozenClock) -> None:
    frozen_clock.advance(0)
    body = client.get(STATS).json()
    assert body["articles_total"] == 0
    assert body["articles_last_7d"] == 0
    assert body["articles_per_day_7d"] == 0
    assert body["days_counted"] == 0
    assert body["published_since"] is None
    assert body["editor_runs_per_day"] == 3
    assert body["predictions"] == {"judged": 0, "hit_rate": None}
    assert body["generated_at"].startswith("2026-01-01")


def test_stats_counts_published_and_seven_day_boundary(
    client: Client, frozen_clock: FrozenClock
) -> None:
    frozen_clock._now = NOW
    inside = make_article("안")
    edge = make_article("경계")
    outside = make_article("밖")
    make_article("비게시", published=False)
    for art, hours in ((inside, 24), (edge, 7 * 24), (outside, 7 * 24 + 1)):
        stamp(art, hours)
    # stamp()는 기준이 NOW와 같다. 경계 정확히 7일 전은 포함, 1시간 더 이전은 제외.
    body = client.get(STATS).json()
    assert body["articles_total"] == 3
    assert body["articles_last_7d"] == 2
    assert body["articles_per_day_7d"] == 0.3
    assert body["published_since"] == "2026-10-13"
    assert body["days_counted"] == 7


@pytest.mark.parametrize(("hours", "days"), [(0, 1), (48, 3), (10 * 24, 7)])
def test_stats_per_day_divides_by_actual_days_when_young(
    client: Client, frozen_clock: FrozenClock, hours: int, days: int
) -> None:
    frozen_clock._now = NOW
    first = make_article("처음")
    stamp(first, hours)
    make_article("둘")
    make_article("셋")
    body = client.get(STATS).json()
    assert body["days_counted"] == days
    assert body["articles_per_day_7d"] == round(body["articles_last_7d"] / days, 1)


def test_stats_hit_rate_null_under_five_and_excludes_undecidable_hidden(
    client: Client, frozen_clock: FrozenClock
) -> None:
    frozen_clock._now = NOW
    make_predictions(["hit", "hit", "miss", "undecidable", "pending"])
    body = client.get(STATS).json()
    assert body["predictions"] == {"judged": 3, "hit_rate": None}
    cache.clear()
    make_predictions(["hit", "miss"])
    make_predictions(["miss"] * 5, hidden=True)
    make_predictions(["miss"] * 5, published=False)
    body = client.get(STATS).json()
    assert body["predictions"] == {"judged": 5, "hit_rate": 0.6}


def test_stats_cache_header_no_cors_query_count_and_five_minute_cache(
    client: Client, frozen_clock: FrozenClock
) -> None:
    frozen_clock._now = NOW
    make_article("하나")
    with CaptureQueriesContext(connection) as ctx:
        res = client.get(STATS, HTTP_ORIGIN="https://e.com")
    assert len(ctx) <= 3
    assert res["Cache-Control"] == "public, max-age=300"
    assert "Access-Control-Allow-Origin" not in res
    assert res.json()["articles_total"] == 1
    make_article("둘")
    with CaptureQueriesContext(connection) as ctx:
        again = client.get(STATS)
    assert len(ctx) == 0
    assert again.json()["articles_total"] == 1
    frozen_clock.advance(300)
    assert client.get(STATS).json()["articles_total"] == 2
