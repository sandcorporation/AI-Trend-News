import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from adapters.fetch import FetchError
from collection.models import Item, SeenUrl, Source
from services.collection import collect_all
from services.urls import normalize_url
from tests.conftest import FakeFetcher

pytestmark = pytest.mark.django_db


def make_source(
    name: str = "Tech Daily", url: str = "https://feeds.example.com/a.xml", **kw: object
) -> Source:
    return Source.objects.create(name=name, url=url, **kw)


def test_normalize_url_strips_tracking_and_noise() -> None:
    assert (
        normalize_url("HTTPS://News.Example.com/a/b/?utm_source=x&id=3&fbclid=z#top")
        == "https://news.example.com/a/b?id=3"
    )
    assert normalize_url("https://x.com/") == "https://x.com"


def test_collect_stores_items_without_duplicates(
    fake_fetcher: FakeFetcher, rss_bytes: bytes
) -> None:
    source = make_source()
    fake_fetcher.responses[source.url] = rss_bytes

    collect_all()
    # 6 항목 중 첫 항목과 여섯째(추적 파라미터만 다름)는 같은 수집물
    assert Item.objects.count() == 5
    collect_all()
    assert Item.objects.count() == 5

    item = Item.objects.get(title__startswith="OpenAI unveils new reasoning model for enterprise")
    assert item.normalized_url == "https://technews.example.com/2026/10/openai-reasoning"
    assert "utm_source" in item.url
    assert item.status == Item.Status.PENDING
    assert item.published_at is not None
    source.refresh_from_db()
    assert source.last_succeeded is True
    assert source.consecutive_failures == 0


def test_seen_url_survives_item_deletion(fake_fetcher: FakeFetcher, rss_bytes: bytes) -> None:
    source = make_source()
    fake_fetcher.responses[source.url] = rss_bytes
    collect_all()
    Item.objects.all().delete()
    assert SeenUrl.objects.count() == 5
    collect_all()
    assert Item.objects.count() == 0


def test_failure_is_recorded_and_does_not_block_other_sources(
    fake_fetcher: FakeFetcher, rss_bytes: bytes
) -> None:
    bad = make_source("Bad", "https://bad.example.com/rss")
    good = make_source("Good", "https://good.example.com/rss")
    fake_fetcher.responses[bad.url] = FetchError("HTTP 503")
    fake_fetcher.responses[good.url] = rss_bytes

    collect_all()
    collect_all()
    bad.refresh_from_db()
    good.refresh_from_db()
    assert bad.last_succeeded is False
    assert "HTTP 503" in bad.last_result
    assert bad.consecutive_failures == 2
    assert good.last_succeeded is True
    assert Item.objects.filter(source=good).count() == 5

    fake_fetcher.responses[bad.url] = rss_bytes
    collect_all()
    bad.refresh_from_db()
    assert bad.consecutive_failures == 0
    assert bad.last_succeeded is True


def test_disabled_source_is_not_collected(fake_fetcher: FakeFetcher, rss_bytes: bytes) -> None:
    off = make_source("Off", "https://off.example.com/rss", enabled=False)
    fake_fetcher.responses[off.url] = rss_bytes
    collect_all()
    assert fake_fetcher.requested == []
    assert Item.objects.count() == 0
    off.refresh_from_db()
    assert off.last_collected_at is None


def test_collect_management_command(fake_fetcher: FakeFetcher, rss_bytes: bytes) -> None:
    source = make_source()
    fake_fetcher.responses[source.url] = rss_bytes
    out = StringIO()
    call_command("collect", stdout=out)
    assert Item.objects.count() == 5
    assert "소스 1개, 실패 0개" in out.getvalue()


def test_unknown_adapter_is_a_recorded_failure(fake_fetcher: FakeFetcher) -> None:
    source = make_source(adapter_name="nope")
    collect_all()
    source.refresh_from_db()
    assert source.last_succeeded is False
    assert source.consecutive_failures == 1


HTML_URL = "https://www.aidaily.example.com/news/"
HTML_RULES = {
    "item": "li.article",
    "link": "a.headline",
    "title": "h3",
    "published": "time",
    "excerpt": "p.summary",
}


def make_html_source(rules: dict[str, str] | None = None) -> Source:
    return Source.objects.create(
        name="AI 데일리",
        kind=Source.Kind.HTML,
        adapter_name="html",
        url=HTML_URL,
        html_rules=HTML_RULES if rules is None else rules,
    )


def test_html_source_collects_items_from_fixture(
    fake_fetcher: FakeFetcher, html_bytes: bytes
) -> None:
    source = make_html_source()
    fake_fetcher.responses[HTML_URL] = html_bytes

    collect_all()

    assert Item.objects.count() == 6
    urls = set(Item.objects.values_list("url", flat=True))
    assert "https://www.aidaily.example.com/news/2026/10/chip-export" in urls  # 상대 링크 절대화
    assert "https://www.aidaily.example.com/archive/open-weights" in urls
    item = Item.objects.get(title="AI 반도체 수출 규제 완화")
    assert item.excerpt == "정부가 규제 완화안을 발표했다."
    assert item.published_at is not None and item.published_at.isoformat().startswith(
        "2026-10-19T23:00"
    )
    source.refresh_from_db()
    assert source.last_succeeded is True


def test_html_selector_matching_nothing_records_source_failure(
    fake_fetcher: FakeFetcher, html_bytes: bytes
) -> None:
    source = make_html_source({**HTML_RULES, "item": "div.nope"})
    fake_fetcher.responses[HTML_URL] = html_bytes

    collect_all()

    assert Item.objects.count() == 0
    source.refresh_from_db()
    assert source.last_succeeded is False
    assert source.consecutive_failures == 1
    assert "div.nope" in source.last_result


def test_admin_edits_html_rules_and_rejects_bad_rules(admin_client: Client) -> None:
    source = make_html_source()
    url = reverse("admin:collection_source_change", args=[source.pk])
    base = {
        "name": source.name,
        "kind": "html",
        "url": HTML_URL,
        "adapter_name": "html",
        "enabled": "on",
    }

    ok = admin_client.post(
        url, {**base, "html_rules": json.dumps({**HTML_RULES, "title": "h3 span"})}
    )
    assert ok.status_code == 302
    source.refresh_from_db()
    assert source.html_rules["title"] == "h3 span"

    bad = admin_client.post(url, {**base, "html_rules": json.dumps({"item": "li"})})
    assert bad.status_code == 200
    assert "필수 키가 없습니다" in bad.content.decode()
    source.refresh_from_db()
    assert source.html_rules["title"] == "h3 span"
