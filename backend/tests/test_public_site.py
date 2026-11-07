import re
from xml.etree import ElementTree

import pytest
from django.test import Client

from articles.models import Article
from collection.models import Item, Source
from services import articles as svc
from services.articles import Actor
from tests.test_shell import meta, og

pytestmark = pytest.mark.django_db

_counter = 0


def make_article(
    title: str,
    *,
    category: str = "경제",
    published: bool = True,
    featured: bool = False,
    tags: tuple[str, ...] = (),
    image_url: str = "",
    summary: str = "요약 문장입니다.",
) -> Article:
    global _counter
    _counter += 1
    if not any(c.name == category for c in svc.list_categories()):
        svc.add_category(category)
    source, _ = Source.objects.get_or_create(
        name="연합뉴스", defaults={"url": "https://e.com/feed"}
    )
    item = Item.objects.create(
        source=source,
        url=f"https://example.com/news/{_counter}",
        normalized_url=f"https://example.com/news/{_counter}",
        title=f"Original {title}",
    )
    return svc.create_article(
        Actor.EDITOR,
        item_ids=[item.pk],
        title=title,
        summary=summary,
        category=_slug(category),
        tags=tags,
        published=published,
        featured=featured,
        image_url=image_url,
    )


def _slug(name: str) -> str:
    return next(c.slug for c in svc.list_categories() if c.name == name)


def test_home_lists_only_published_articles(client: Client) -> None:
    make_article("공개된 기사")
    make_article("초안 기사", published=False)

    html = client.get("/").content.decode()

    assert "공개된 기사" in html
    assert "초안 기사" not in html


def test_featured_area_shows_only_featured_and_published_articles(client: Client) -> None:
    make_article("평범한 기사")
    make_article("추천 기사", featured=True)
    make_article("추천이지만 비게시", featured=True, published=False)

    html = client.get("/").content.decode()
    featured_block = html.split('id="hero-h"')[1].split('id="latest-h"')[0]

    assert "추천 기사" in featured_block
    assert "평범한 기사" not in featured_block
    assert "추천이지만 비게시" not in html


def test_category_query_filters_articles_and_marks_the_tab(client: Client) -> None:
    make_article("금리 동결", category="경제")
    make_article("신형 칩", category="IT")

    html = client.get(f"/?category={_slug('IT')}").content.decode()

    assert "신형 칩" in html
    assert "금리 동결" not in html
    assert re.search(r'href="/\?category=[^"]+" aria-current="page">IT<', html)


def test_unknown_category_and_out_of_range_page_are_404(client: Client) -> None:
    make_article("기사")

    assert client.get("/?category=nope").status_code == 404
    assert client.get("/?page=2").status_code == 404
    assert client.get("/?page=abc").status_code == 404


def test_home_paginates_latest_first(client: Client) -> None:
    for n in range(14):
        make_article(f"기사 번호 {n:02d}")

    first = client.get("/").content.decode()
    second = client.get("/?page=2").content.decode()

    assert "기사 번호 13" in first and "기사 번호 01" not in first
    assert "기사 번호 01" in second and "기사 번호 13" not in second
    assert 'rel="next"' in first and 'rel="prev"' in second


def test_home_embeds_page_data_and_keeps_canonical_query(client: Client) -> None:
    make_article("금리 동결")
    html = client.get(f"/?category={_slug('경제')}").content.decode()

    assert '<script id="page-data" type="application/json">' in html
    assert og(html, "url") == f"http://testserver/?category={_slug('경제')}"


def test_article_detail_has_og_tags_source_links_and_text(client: Client) -> None:
    article = make_article(
        "금리 동결",
        tags=("금리", "한국은행"),
        image_url="https://img.example.com/a.png",
        summary="한국은행이 기준금리를 동결했다.",
    )

    response = client.get(f"/a/{article.slug}")
    html = response.content.decode()

    assert response.status_code == 200
    assert "<title>금리 동결 - Foresight</title>" in html
    assert og(html, "title") == "금리 동결 - Foresight"
    assert og(html, "type") == "article"
    assert og(html, "image") == "https://img.example.com/a.png"
    assert meta(html, "description") == "한국은행이 기준금리를 동결했다."
    assert og(html, "url") == f"http://testserver/a/{article.slug}"
    assert "#금리" in html and "경제" in html and "연합뉴스" in html
    assert '<a href="https://example.com/news/' in html and 'target="_blank" rel="noopener"' in html


def test_unpublished_or_unknown_article_slug_is_404(client: Client) -> None:
    draft = make_article("초안", published=False)

    assert client.get(f"/a/{draft.slug}").status_code == 404
    assert client.get("/a/no-such-article").status_code == 404


def test_sitemap_lists_home_categories_and_published_articles_only(client: Client) -> None:
    live = make_article("공개")
    draft = make_article("초안", published=False)

    response = client.get("/sitemap.xml")
    root = ElementTree.fromstring(response.content)
    locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]

    assert response["Content-Type"].startswith("application/xml")
    assert "http://testserver/" in locs
    assert f"http://testserver/?category={_slug('경제')}" in locs
    assert f"http://testserver/a/{live.slug}" in locs
    assert not any(draft.slug in (loc or "") for loc in locs)


def test_robots_points_at_the_sitemap_and_hides_api(client: Client) -> None:
    text = client.get("/robots.txt").content.decode()

    assert "Sitemap: http://testserver/sitemap.xml" in text
    assert "Disallow: /api/" in text


def test_old_category_slug_redirects_301_keeping_query(client: Client) -> None:
    svc.add_category("경제", slug="old")
    svc.update_category("old", new_slug="economy")
    res = client.get("/?category=old&page=1")
    assert res.status_code == 301
    assert res["Location"] in ("/?category=economy&page=1",)
    assert client.get("/?category=nope").status_code == 404
    assert client.get("/?category=economy").status_code == 200


def test_old_article_slug_redirects_301_to_new(client: Client) -> None:
    article = make_article("Old address news")
    old = article.slug
    svc.record_article_slug_change(article, "new-address")
    article.save()
    res = client.get(f"/a/{old}")
    assert res.status_code == 301 and res["Location"] == "/a/new-address"
    assert client.get("/a/new-address").status_code == 200
    assert client.get("/a/nothing-here").status_code == 404
    article.published = False
    article.save()
    assert client.get(f"/a/{old}").status_code == 404


def _ld(html: str) -> list[dict[str, object]]:
    import json

    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    return [json.loads(b) for b in blocks]


def _tags(html: str, key: str) -> list[str]:
    return re.findall(rf'<meta (?:name|property)="{key}" content="([^"]*)"', html)


def test_article_head_has_twitter_article_meta_and_news_article_ld(client: Client) -> None:
    article = make_article(
        "메타 기사",
        category="경제",
        tags=("AI", "반도체"),
        image_url="https://img.example.com/a.png",
    )
    html = client.get(f"/a/{article.slug}").content.decode()
    assert _tags(html, "twitter:card") == ["summary_large_image"]
    assert _tags(html, "twitter:title") == ["메타 기사 - Foresight"]
    assert _tags(html, "twitter:image") == ["https://img.example.com/a.png"]
    assert _tags(html, "twitter:site") == []
    assert _tags(html, "og:image:alt") == ["메타 기사"]
    assert _tags(html, "article:section") == ["경제"]
    assert _tags(html, "article:tag") == ["AI", "반도체"]
    published = _tags(html, "article:published_time")[0]
    assert published.endswith("+09:00") and published.startswith(str(article.published_at.year))
    assert _tags(html, "article:modified_time")
    (ld,) = _ld(html)
    assert ld["@type"] == "NewsArticle"
    assert ld["headline"] == "메타 기사"
    assert ld["image"] == ["https://img.example.com/a.png"]
    assert ld["datePublished"] == published
    assert ld["articleSection"] == "경제"
    assert ld["keywords"] == "AI, 반도체"
    assert ld["inLanguage"] == "ko"
    assert ld["mainEntityOfPage"] == ld["url"] == f"http://testserver/a/{article.slug}"
    assert ld["author"]["name"] == "Foresight"  # type: ignore[index]
    assert ld["publisher"]["logo"]["url"].endswith("favicon.svg")  # type: ignore[index]


DEFAULT_OG = "http://testserver/static/core/og-default.png"


def test_article_without_image_falls_back_to_default_og_card(client: Client) -> None:
    article = make_article("이미지 없음")
    html = client.get(f"/a/{article.slug}").content.decode()
    assert _tags(html, "twitter:card") == ["summary_large_image"]
    assert _tags(html, "og:image") == [DEFAULT_OG]
    assert _tags(html, "twitter:image") == [DEFAULT_OG]
    assert _tags(html, "og:image:width") == ["1200"]
    assert _tags(html, "og:image:height") == ["630"]
    assert _tags(html, "og:image:alt") == ["이미지 없음"]
    assert "image" not in _ld(html)[0]


def test_article_with_image_keeps_source_image_without_size(client: Client) -> None:
    article = make_article("사진 있음", image_url="https://img.example.com/a.png")
    html = client.get(f"/a/{article.slug}").content.decode()
    assert _tags(html, "og:image") == ["https://img.example.com/a.png"]
    assert _tags(html, "og:image:width") == []
    assert _tags(html, "og:image:height") == []


@pytest.mark.parametrize("path", ["/", "/predictions", "/contact"])
def test_other_public_pages_have_default_large_card(client: Client, path: str) -> None:
    html = client.get(path).content.decode()
    assert _tags(html, "twitter:card") == ["summary_large_image"]
    assert _tags(html, "og:image") == [DEFAULT_OG]
    assert _tags(html, "twitter:image") == [DEFAULT_OG]
    assert _tags(html, "og:image:width") == ["1200"]
    assert _tags(html, "og:image:height") == ["630"]
    assert len(_tags(html, "og:image:alt")) == 1


@pytest.mark.parametrize("path", ["/", "/predictions", "/contact"])
def test_footer_has_direct_made_by_sandcorp_backlink(client: Client, path: str) -> None:
    html = client.get(path).content.decode()
    m = re.search(r'<a href="(https://sandcorp\.co\.kr/[^"]*)"([^>]*)>Made by Sandcorp</a>', html)
    assert m, "직접 링크 없음"
    assert m.group(1) == "https://sandcorp.co.kr/?utm_source=foresight&amp;utm_medium=footer"
    assert "rel=" not in m.group(2) and "target=" not in m.group(2)
    assert "/go/sandcorp?from=footer" in html


def test_default_og_image_is_served_png_1200x630(client: Client) -> None:
    from pathlib import Path

    from django.contrib.staticfiles import finders

    found = finders.find("core/og-default.png")
    assert found
    data = Path(found).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(data[16:20], "big") == 1200
    assert int.from_bytes(data[20:24], "big") == 630
    assert len(data) <= 300 * 1024


def test_twitter_site_only_when_configured(client: Client, settings) -> None:  # type: ignore[no-untyped-def]
    settings.SITE_TWITTER = "@foresight"
    assert _tags(client.get("/").content.decode(), "twitter:site") == ["@foresight"]


def test_article_ld_survives_script_closing_in_text(client: Client) -> None:
    article = make_article("제목 </script><b>&", summary="요약 </script> & 끝")
    html = client.get(f"/a/{article.slug}").content.decode()
    (ld,) = _ld(html)
    assert ld["headline"] == "제목 </script><b>&"
    assert "</script><b>" not in html


def test_website_ld_only_on_unfiltered_first_home_page(client: Client) -> None:
    make_article("홈 기사")
    types = [b["@type"] for b in _ld(client.get("/").content.decode())]
    assert types == ["WebSite", "Organization"]
    assert _ld(client.get("/?category=" + _slug("경제")).content.decode()) == []
    assert _ld(client.get("/?page=2").content.decode()) == []
    (page,) = _ld(client.get("/predictions").content.decode())
    assert page["@type"] == "WebPage"
    assert _ld(client.get("/contact").content.decode()) == []


def test_footer_privacy_link_in_html_and_page_data(client: Client) -> None:
    url = "https://sandcorporation.github.io/privacy-and-terms/errors/"
    body = client.get("/").content.decode()
    assert f'<a href="{url}">개인정보·오류 기록</a>' in body
    links = re.search(r'<p class="footer-links.*?</p>', body, re.S)
    assert links
    assert links[0].index(">문의</a>") < links[0].index(url) < links[0].index("전망 기록표")
    assert "privacy_url" in body and "privacy_label" in body
