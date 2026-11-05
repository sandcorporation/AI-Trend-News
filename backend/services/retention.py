"""정리 규칙. 수집물은 `Item`만 지운다(`SeenUrl`은 지우면 재수집된다). 조회 집계도 지우고,
오래된 편집 호출(`EditorCall`)의 입출력 JSON을 비운다."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from articles.models import ArticleView
from collection.models import Item
from editor.models import EditorCall
from services import runtime_settings
from services.editor_reserve import reserved_item_ids

RETENTION = timedelta(days=30)


@dataclass(frozen=True)
class PruneResult:
    deleted: int
    discarded: int  # 24시간 지나 자동으로 버림 처리한 미처리 수집물
    kept: int
    views_deleted: int = 0  # 30일 넘은 기사 조회 집계
    calls_cleared: int = 0  # 30일 넘어 입출력 JSON을 비운 편집 호출(토큰·비용은 남김)


def prune_items(now: Callable[[], datetime] = timezone.now) -> PruneResult:
    """오래 쌓인 미처리를 버림 처리하고(PENDING_MAX_AGE_HOURS, 삭제 아님), 30일 지난 것을 지운다.

    묶인 수집물은 어느 쪽도 건드리지 않는다. 게시 모드 파이프라인이 예약한 수집물은
    자동 버림에서 뺀다.
    """
    current = now()
    discarded = (
        Item.objects.filter(
            status=Item.Status.PENDING,
            collected_at__lt=current
            - timedelta(hours=runtime_settings.PENDING_MAX_AGE_HOURS.get()),
        )
        .exclude(pk__in=list(reserved_item_ids(current)))  # 게시 모드 예약 중(#128)
        .update(status=Item.Status.DISCARDED)
    )
    cutoff = current - RETENTION
    stale = Item.objects.filter(
        status__in=[Item.Status.PENDING, Item.Status.DISCARDED, Item.Status.FILTERED],
        collected_at__lt=cutoff,
    )
    deleted, _ = stale.delete()
    views_deleted, _ = ArticleView.objects.filter(
        date__lt=timezone.localtime(current).date() - RETENTION
    ).delete()
    calls_cleared = (
        EditorCall.objects.filter(created_at__lt=cutoff)
        .exclude(input={}, output__isnull=True)
        .update(input={}, output=None)
    )
    return PruneResult(
        deleted=deleted,
        discarded=discarded,
        kept=Item.objects.count(),
        views_deleted=views_deleted,
        calls_cleared=calls_cleared,
    )
