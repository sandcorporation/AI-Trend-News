"""기사 조회 집계(#45): 기록 규칙, 많이 읽은 기사, 30일 정리."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.utils import timezone

from articles.models import Article, ArticleView
from services import articles as svc
from services.retention import prune_items
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db

BROWSER = "Mozilla/5.0 (X11; Linux x86_64) Firefox/130.0"


def view(client: Client, article: Article, agent: str = BROWSER) -> int:
    return client.get(f"/a/{article.slug}", headers={"User-Agent": agent}).status_code


def add(article: Article, days_ago: int, count: int) -> None:
    ArticleView.objects.create(
        article=article, date=timezone.localdate() - timedelta(days=days_ago), count=count
    )


def test_two_views_count_two_on_one_row(client: Client) -> None:
    article = make_article("a")
    assert view(client, article) == 200 and view(client, article) == 200

    row = ArticleView.objects.get()
    assert (row.article_id, row.date, row.count) == (article.pk, timezone.localdate(), 2)


def test_bot_unpublished_and_missing_are_not_counted(client: Client) -> None:
    article = make_article("a")
    hidden = make_article("b")
    Article.objects.filter(pk=hidden.pk).update(published=False)

    assert view(client, article, "Googlebot/2.1") == 200
    assert view(client, hidden) == 404
    assert client.get("/a/nope-nope").status_code == 404

    assert ArticleView.objects.count() == 0


def test_recording_failure_does_not_break_the_page(client: Client) -> None:
    article = make_article("a")
    with patch("services.articles.ArticleView.objects.get_or_create", side_effect=RuntimeError):
        assert view(client, article) == 200


def test_date_follows_seoul_midnight(client: Client) -> None:
    article = make_article("a")
    # 2026-10-22 16:00 UTC = 2026-10-23 01:00 KST
    now = datetime(2026, 10, 22, 16, 0, tzinfo=ZoneInfo("UTC"))
    with patch("django.utils.timezone.now", return_value=now):
        view(client, article)
    assert ArticleView.objects.get().date == date(2026, 10, 23)


def test_most_read_window_order_limit_and_category() -> None:
    a, b, c, old, unread = (make_article(t) for t in "abcde")
    add(a, 0, 3)
    add(a, 6, 2)  # 합 5, 창 안(오늘 포함 7일)
    add(b, 0, 5)  # 합 5 — a와 동률, b가 더 최신
    add(c, 1, 1)
    add(old, 7, 99)  # 창 밖

    assert svc.public_most_read() == [b, a, c]
    assert svc.public_most_read(limit=2) == [b, a]
    assert svc.public_most_read(days=1) == [b, a]
    assert unread not in svc.public_most_read()
    assert svc.public_most_read(category=c.category.slug) == [
        x for x in [b, a, c] if x.category_id == c.category_id
    ]
    Article.objects.filter(pk=b.pk).update(published=False)
    assert svc.public_most_read() == [a, c]


def test_most_read_is_one_query(
    django_assert_num_queries: Callable[[int], AbstractContextManager[object]],
) -> None:
    add(make_article("a"), 0, 1)
    with django_assert_num_queries(2):  # 본 쿼리 + 태그 prefetch
        svc.public_most_read()


def test_prune_deletes_views_older_than_30_days() -> None:
    article = make_article("a")
    add(article, 31, 1)
    add(article, 30, 1)
    add(article, 0, 1)

    result = prune_items()

    assert result.views_deleted == 1
    assert ArticleView.objects.count() == 2
