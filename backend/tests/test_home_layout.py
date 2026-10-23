import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from articles.models import Article, ArticleView
from services import articles as svc
from tests.conftest import FrozenClock
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)  # 12:00 KST


def page_data(html: str) -> dict[str, object]:
    match = re.search(r'<script id="page-data" type="application/json">(.*?)</script>', html, re.S)
    assert match
    data: dict[str, object] = json.loads(match.group(1))
    return data


def age(article: Article, delta: timedelta) -> None:
    Article.objects.filter(pk=article.pk).update(published_at=NOW - delta)


@pytest.fixture(autouse=True)
def now(frozen_clock: FrozenClock) -> None:
    frozen_clock._now = NOW


def labels(client: Client) -> dict[str, str]:
    cards = page_data(client.get("/").content.decode())["articles"]
    assert isinstance(cards, list)
    return {c["title"]: c["published_label"] for c in cards}


def test_relative_labels_follow_the_clock(client: Client) -> None:
    for title, delta in [
        ("방금", timedelta(seconds=20)),
        ("분", timedelta(minutes=5)),
        ("시간", timedelta(hours=3, minutes=10)),
        ("어제", timedelta(hours=30)),
        ("작년", timedelta(days=400)),
    ]:
        age(make_article(title, category="AI"), delta)

    assert labels(client) == {
        "방금": "방금 전",
        "분": "5분 전",
        "시간": "3시간 전",
        "어제": "10월 22일",
        "작년": "2025년 9월 18일",
    }


def test_server_html_and_page_data_show_the_same_label(client: Client) -> None:
    age(make_article("라벨 기사", category="AI"), timedelta(minutes=7))
    html = client.get("/").content.decode()

    assert html.count("7분 전") >= 2  # 서버 HTML + #page-data
    assert re.search(r"<time datetime=\"[^\"]+\">7분 전</time>", html)


def test_home_has_hero_sections_and_most_read_on_first_unfiltered_page(client: Client) -> None:
    for n in range(6):
        age(make_article(f"AI 기사 {n}", category="AI", featured=n == 0), timedelta(hours=n + 1))
    for n in range(2):
        age(make_article(f"IT 기사 {n}", category="IT"), timedelta(hours=n + 1))
    popular = Article.objects.get(title="AI 기사 3")
    ArticleView.objects.create(article=popular, date=timezone.localdate(), count=9)

    html = client.get("/").content.decode()
    data = page_data(html)

    sections = data["sections"]
    assert isinstance(sections, list)
    assert [s["category"]["name"] for s in sections] == ["AI", "IT"]
    assert [len(s["articles"]) for s in sections] == [4, 2]
    assert sections[0]["articles"][0]["title"] == "AI 기사 0"
    assert sections[0]["more_url"].startswith("/?category=")
    most_read = data["most_read"]
    assert isinstance(most_read, list) and [c["title"] for c in most_read] == ["AI 기사 3"]
    assert 'data-slot="hero"' in html and "많이 읽은 기사" in html and "더보기 →" in html


def test_hero_most_read_and_sections_are_omitted_when_filtered_or_paged(client: Client) -> None:
    for n in range(14):
        make_article(f"기사 {n:02d}", category="AI", featured=n == 0)
    ArticleView.objects.create(
        article=Article.objects.get(title="기사 05"), date=timezone.localdate(), count=1
    )
    slug = next(c.slug for c in svc.list_categories() if c.name == "AI")

    for url in (f"/?category={slug}", "/?page=2"):
        html = client.get(url).content.decode()
        data = page_data(html)
        assert data["sections"] == [] and data["most_read"] == [] and data["featured"] == []
        assert 'data-slot="hero"' not in html and "많이 읽은 기사" not in html
        assert 'class="cat-section"' not in html


def test_empty_most_read_hides_the_block(client: Client) -> None:
    make_article("조회 없음", category="AI")
    html = client.get("/").content.decode()

    assert "많이 읽은 기사" not in html
    assert "/go/sandcorp?from=sidebar" in html


def test_sidebar_link_is_tracked_inflow_link(client: Client) -> None:
    response = client.get("/go/sandcorp?from=sidebar")

    assert response.status_code == 302
    assert "utm_medium=sidebar" in response["Location"]


def test_home_runs_at_most_eleven_queries(client: Client) -> None:
    for n in range(14):
        make_article(
            f"기사 {n:02d}", category="AI" if n % 2 else "IT", featured=n < 3, tags=("태그",)
        )
    for article in Article.objects.all()[:5]:
        ArticleView.objects.create(article=article, date=timezone.localdate(), count=3)

    with CaptureQueriesContext(connection) as ctx:
        assert client.get("/").status_code == 200

    assert len(ctx) <= 11, [q["sql"][:80] for q in ctx]


def test_hero_carries_up_to_six_featured_slides_with_only_the_first_visible(
    client: Client,
) -> None:
    for n in range(7):
        age(make_article(f"추천 {n}", category="AI", featured=True), timedelta(hours=n + 1))

    html = client.get("/").content.decode()
    data = page_data(html)

    featured = data["featured"]
    assert isinstance(featured, list) and len(featured) == 6
    hero = html.split('id="hero-h"')[1].split("</section>")[0]
    assert hero.count('role="group"') == 6
    assert 'aria-label="1 / 6"' in hero and 'aria-label="6 / 6"' in hero
    assert hero.count('aria-hidden="true" inert') == 5
    assert hero.count("추천 0") >= 1 and "추천 6" not in hero
