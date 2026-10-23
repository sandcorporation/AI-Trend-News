"""수집물 정리 규칙. `Item`만 지운다 — `SeenUrl`은 절대 지우지 않는다(지우면 재수집된다)."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from collection.models import Item

RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class PruneResult:
    deleted: int
    discarded: int  # 24시간 지나 자동으로 버림 처리한 미처리 수집물
    kept: int


def prune_items(now: Callable[[], datetime] = timezone.now) -> PruneResult:
    """오래 쌓인 미처리를 버림 처리하고(PENDING_MAX_AGE_HOURS, 삭제 아님), 30일 지난 것을 지운다.

    묶인 수집물은 어느 쪽도 건드리지 않는다.
    """
    current = now()
    discarded = Item.objects.filter(
        status=Item.Status.PENDING,
        collected_at__lt=current - timedelta(hours=settings.PENDING_MAX_AGE_HOURS),
    ).update(status=Item.Status.DISCARDED)
    cutoff = current - RETENTION
    stale = Item.objects.filter(
        status__in=[Item.Status.PENDING, Item.Status.DISCARDED], collected_at__lt=cutoff
    )
    deleted, _ = stale.delete()
    return PruneResult(deleted=deleted, discarded=discarded, kept=Item.objects.count())
