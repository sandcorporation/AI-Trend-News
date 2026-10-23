from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = pytest.mark.django_db(transaction=True)


def test_backfill_sets_earliest_item_time_or_created_at() -> None:
    executor = MigrationExecutor(connection)
    before = [("articles", "0009_predictions"), *executor.loader.graph.leaf_nodes("collection")]
    executor.migrate(before)
    apps = executor.loader.project_state(before).apps
    Source, Item = apps.get_model("collection", "Source"), apps.get_model("collection", "Item")
    Category, Article = (
        apps.get_model("articles", "Category"),
        apps.get_model("articles", "Article"),
    )
    ArticleItem = apps.get_model("articles", "ArticleItem")
    cat = Category.objects.create(name="c", slug="c")
    src = Source.objects.create(name="s", url="https://e.com/f")

    def item(n: int, published_at: datetime | None) -> object:
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

    executor = MigrationExecutor(connection)
    after = [("articles", "0011_backfill_article_published_at")]
    executor.migrate(after)
    new = executor.loader.project_state(after).apps.get_model("articles", "Article")
    assert new.objects.get(pk=a1.pk).published_at == early
    for pk in (a2.pk, a3.pk):
        row = new.objects.get(pk=pk)
        assert row.published_at == row.created_at
