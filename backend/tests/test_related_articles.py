from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from articles.models import Article
from tests.test_home_layout import page_data
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db


def aged(title: str, days: int, **kwargs: object) -> Article:
    article = make_article(title, **kwargs)  # type: ignore[arg-type]
    Article.objects.filter(pk=article.pk).update(published_at=timezone.now() - timedelta(days=days))
    return article


def test_related_shows_three_latest_published_same_category(client: Client) -> None:
    current = aged("지금 기사", 0, category="AI")
    for title, days in [("넷째", 4), ("첫째", 1), ("셋째", 3), ("둘째", 2)]:
        aged(title, days, category="AI")
    aged("비게시", 0, category="AI", published=False)
    aged("다른 분야", 0, category="IT")

    html = client.get(f"/a/{current.slug}").content.decode()

    related = page_data(html)["related"]
    assert isinstance(related, list)
    assert [c["title"] for c in related] == ["첫째", "둘째", "셋째"]
    block = html.split('data-slot="related"')[1].split('data-slot="article-cta"')[0]
    assert "같은 분야 기사" in block and block.count('<article class="card">') == 3


def test_related_block_absent_when_no_other_article(client: Client) -> None:
    current = make_article("혼자", category="AI")
    html = client.get(f"/a/{current.slug}").content.decode()
    assert 'data-slot="related"' not in html
    assert page_data(html)["related"] == []


def test_related_costs_exactly_one_query_regardless_of_count(client: Client) -> None:
    current = make_article("지금 기사", category="AI", tags=("가",))

    def queries() -> list[str]:
        with CaptureQueriesContext(connection) as ctx:
            assert client.get(f"/a/{current.slug}").status_code == 200
        return [q["sql"] for q in ctx]

    queries()  # 첫 조회는 조회 수 행을 만든다
    alone = queries()
    for n in range(3):
        make_article(f"이웃 {n}", category="AI", tags=("나",))
    assert len(queries()) == len(alone)
    assert len(alone) == ARTICLE_PAGE_QUERIES


ARTICLE_PAGE_QUERIES = 9  # 「같은 분야 기사」 전 8개 + 1(#237)
