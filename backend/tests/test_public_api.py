import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from articles.models import Article, Commentary
from services import articles as svc
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
