from django.db.models.signals import pre_delete
from django.dispatch import receiver

from articles.models import Article
from collection.models import Item


@receiver(pre_delete, sender=Article)
def release_items(sender: type[Article], instance: Article, **kwargs: object) -> None:
    """기사를 지우면 묶였던 수집물은 미처리로 되돌린다(수집물·SeenUrl은 지우지 않는다)."""
    Item.objects.filter(article_link__article=instance).update(status=Item.Status.PENDING)
