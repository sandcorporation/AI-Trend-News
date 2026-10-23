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
