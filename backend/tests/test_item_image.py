"""수집물 대표 이미지(#44): RSS 안 이미지 → 원문 og:image 보강."""

from typing import Protocol

import pytest

from adapters.fetch import FetchError
from adapters.sources import ADAPTERS, find_og_image
from collection.models import Item, Source
from services.collection import OG_FETCH_LIMIT, collect_all
from services.html_rules import HtmlRulesError, validate_html_rules
from tests.conftest import FakeFetcher


class SettingsLike(Protocol):
    COLLECT_MAX_AGE_DAYS: int


pytestmark = pytest.mark.django_db

FEED = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>t</title>
<item><title>enc</title><link>https://t.example.com/enc</link>
 <description>x</description>
 <enclosure url="https://cdn.example.com/enc.jpg" type="image/jpeg" length="1"/>
 <media:content url="https://cdn.example.com/ignored.jpg" medium="image"/></item>
<item><title>audio</title><link>https://t.example.com/audio</link>
 <enclosure url="https://cdn.example.com/a.mp3" type="audio/mpeg" length="1"/>
 <media:thumbnail url="https://cdn.example.com/thumb.jpg"/></item>
<item><title>media</title><link>https://t.example.com/media</link>
 <media:content url="https://cdn.example.com/m.jpg" medium="image"/></item>
<item><title>body</title><link>https://t.example.com/2026/body</link>
 <description>&lt;p&gt;&lt;img src="/img/rel.png"&gt;hi&lt;/p&gt;</description></item>
<item><title>none</title><link>https://t.example.com/none</link>
 <description>plain</description></item>
</channel></rss>"""


def _source() -> Source:
    return Source.objects.create(name="s", url="https://feeds.example.com/a.xml")


def test_rss_image_priority() -> None:
    items = {
        i.title: i.image_url for i in ADAPTERS["rss"].collect("u", FakeFetcher({"u": FEED}), {})
    }
    assert items == {
        "enc": "https://cdn.example.com/enc.jpg",
        "audio": "https://cdn.example.com/thumb.jpg",
        "media": "https://cdn.example.com/m.jpg",
        "body": "https://t.example.com/img/rel.png",
        "none": "",
    }


def test_html_image_rule() -> None:
    html = (
        b'<li class="i"><a href="/a"><h2>T</h2></a><time datetime="2026-10-20T00:00:00Z"></time>'
        b'<img class="th" src="/t.png"></li>'
    )
    rules = {"item": "li.i", "link": "a", "title": "h2", "published": "time", "image": "img.th"}
    [raw] = ADAPTERS["html"].collect(
        "https://h.example.com/list", FakeFetcher({"https://h.example.com/list": html}), rules
    )
    assert raw.image_url == "https://h.example.com/t.png"
    assert validate_html_rules(rules)["image"] == "img.th"
    with pytest.raises(HtmlRulesError):
        validate_html_rules({**rules, "images": "x"})


def test_find_og_image_falls_back_to_twitter() -> None:
    page = b'<head><meta name="twitter:image" content="/tw.png"></head>'
    assert find_og_image(page, "https://a.example.com/x") == "https://a.example.com/tw.png"
    og = (
        b'<meta property="og:image" content="https://c/o.png">'
        b'<meta name="twitter:image" content="/t">'
    )
    assert find_og_image(og, "https://a.example.com/x") == "https://c/o.png"
    assert find_og_image(b"<html></html>", "https://a.example.com/x") == ""


def test_og_fetched_only_when_feed_has_no_image(fake_fetcher: FakeFetcher) -> None:
    source = _source()
    fake_fetcher.responses[source.url] = FEED
    page = b'<meta property="og:image" content="https://cdn.example.com/og.png">'
    for slug in ("audio", "media", "none"):  # enc·body 는 피드에 이미지가 있다
        fake_fetcher.responses[f"https://t.example.com/{slug}"] = page
    fake_fetcher.responses["https://t.example.com/2026/body"] = FetchError("쓰이면 안 됨")
    fake_fetcher.responses["https://t.example.com/enc"] = FetchError("쓰이면 안 됨")
    collect_all()
    got = dict(Item.objects.values_list("title", "image_url"))
    assert got["none"] == "https://cdn.example.com/og.png"
    assert got["enc"] == "https://cdn.example.com/enc.jpg"
    # audio 는 thumbnail, media 는 media:content 가 있어 원문을 받지 않는다
    assert fake_fetcher.requested == [source.url, "https://t.example.com/none"]
    source.refresh_from_db()
    assert source.last_result.endswith("/ 이미지 5건")

    # 이미 본 글은 다시 받지 않는다
    fake_fetcher.requested.clear()
    collect_all()
    assert fake_fetcher.requested == [source.url]


def test_og_failure_is_silent(fake_fetcher: FakeFetcher) -> None:
    source = _source()
    fake_fetcher.responses[source.url] = FEED
    fake_fetcher.responses["https://t.example.com/none"] = FetchError("timeout")
    collect_all()
    source.refresh_from_db()
    assert source.last_succeeded and source.consecutive_failures == 0
    assert Item.objects.get(title="none").image_url == ""
    assert source.last_result.endswith("/ 이미지 4건")


def test_og_fetch_capped_per_run(fake_fetcher: FakeFetcher) -> None:
    source = _source()
    n = OG_FETCH_LIMIT + 5
    body = "".join(
        f"<item><title>t{i}</title><link>https://t.example.com/p{i}</link></item>" for i in range(n)
    )
    fake_fetcher.responses[source.url] = (
        f'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>{body}</channel></rss>'
    ).encode()
    for i in range(n):
        fake_fetcher.responses[f"https://t.example.com/p{i}"] = (
            b'<meta property="og:image" content="https://i.example.com/x.png">'
        )
    collect_all()
    assert len(fake_fetcher.requested) == 1 + OG_FETCH_LIMIT
    assert Item.objects.exclude(image_url="").count() == OG_FETCH_LIMIT
    assert Item.objects.count() == n


def test_old_items_do_not_trigger_og_fetch(
    fake_fetcher: FakeFetcher, settings: SettingsLike
) -> None:
    settings.COLLECT_MAX_AGE_DAYS = 3
    source = _source()
    fake_fetcher.responses[source.url] = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><item><title>o</title>'
        b"<link>https://t.example.com/o</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate>"
        b"</item></channel></rss>"
    )
    collect_all()
    assert fake_fetcher.requested == [source.url]
