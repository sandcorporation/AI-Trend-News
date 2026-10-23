from django.db import migrations


def backfill(apps, schema_editor):
    """기존 기사 발행 시각 = 묶인 수집물 중 가장 이른 값(미래 값 제외, 없으면 created_at)."""
    Article = apps.get_model("articles", "Article")
    ArticleItem = apps.get_model("articles", "ArticleItem")
    earliest: dict[int, object] = {}
    rows = ArticleItem.objects.filter(item__published_at__isnull=False).values_list(
        "article_id", "item__published_at", "item__collected_at"
    )
    for article_id, published, collected in rows:
        if published <= collected and (
            article_id not in earliest or published < earliest[article_id]
        ):
            earliest[article_id] = published
    for article in Article.objects.all():
        article.published_at = earliest.get(article.pk, article.created_at)
        article.save(update_fields=["published_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("articles", "0010_article_published_at"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
