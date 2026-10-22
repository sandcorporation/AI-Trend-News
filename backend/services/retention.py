"""수집물 정리 규칙. `Item`만 지운다 — `SeenUrl`은 절대 지우지 않는다(지우면 재수집된다)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from collection.models import Item

RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class PruneResult:
    deleted: int
    kept: int


def prune_items(now: Callable[[], datetime] = timezone.now) -> PruneResult:
    """미처리·버림 수집물 중 수집 시각이 30일 지난 것을 지운다. 묶인 수집물은 남긴다."""
    cutoff = now() - RETENTION
    stale = Item.objects.filter(
        status__in=[Item.Status.PENDING, Item.Status.DISCARDED], collected_at__lt=cutoff
    )
    deleted, _ = stale.delete()
    return PruneResult(deleted=deleted, kept=Item.objects.count())
