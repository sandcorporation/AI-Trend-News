import importlib
from datetime import UTC, datetime, timedelta

import pytest
from django.apps import apps
from django.utils import timezone

from articles.models import Article, ArticleItem, Category
from collection.models import Item, Source

pytestmark = pytest.mark.django_db

mig = importlib.import_module("articles.migrations.0011_backfill_article_published_at")


def test_backfill_sets_earliest_item_time_or_created_at() -> None:
    cat = Category.objects.create(name="c", slug="c")
    src = Source.objects.create(name="s", url="https://e.com/f")

    def item(n: int, published_at: datetime | None) -> Item:
        return Item.objects.create(
            source=src,
            url=f"https://e.com/{n}",
            normalized_url=f"https://e.com/{n}",
            title="t",
            image_url="",
            published_at=published_at,
        )

    early = datetime(2026, 1, 1, tzinfo=UTC)
    a1 = Article.objects.create(title="a1", slug="aaa-1", category=cat)
    ArticleItem.objects.create(article=a1, item=item(1, datetime(2026, 1, 3, tzinfo=UTC)))
    ArticleItem.objects.create(article=a1, item=item(2, early))
    a2 = Article.objects.create(title="a2", slug="aaa-2", category=cat)
    ArticleItem.objects.create(article=a2, item=item(3, None))
    a3 = Article.objects.create(title="a3", slug="aaa-3", category=cat)
    ArticleItem.objects.create(article=a3, item=item(4, timezone.now() + timedelta(days=5)))

    mig.backfill(apps, None)  # 시험: 지금 앱 레지스트리로 부름

    assert Article.objects.get(pk=a1.pk).published_at == early
    for pk in (a2.pk, a3.pk):
        row = Article.objects.get(pk=pk)
        assert row.published_at == row.created_at
