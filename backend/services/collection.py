"""소스 수집 규칙: 켜진 소스를 순회하고 소스별로 독립 실패하며 결과를 소스에 기록한다."""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adapters.fetch import Fetcher, FetchError, get_fetcher
from adapters.sources import ADAPTERS
from collection.models import Item, SeenUrl, Source
from services.urls import normalize_url, url_hash


@dataclass(frozen=True)
class CollectResult:
    source: Source
    ok: bool
    created: int
    message: str


def collect_source(source: Source, fetcher: Fetcher | None = None) -> CollectResult:
    """소스 하나를 수집한다. 실패해도 예외를 던지지 않고 소스에 기록한다."""
    fetcher = fetcher or get_fetcher()
    created = 0
    too_old = 0
    cutoff = timezone.now() - timedelta(days=settings.COLLECT_MAX_AGE_DAYS)
    try:
        adapter = ADAPTERS.get(source.adapter_name)
        if adapter is None:
            raise FetchError(f"알 수 없는 어댑터: {source.adapter_name}")
        raw_items = adapter.collect(source.url, fetcher, source.html_rules)
        for raw in raw_items:
            normalized = normalize_url(raw.url)
            with transaction.atomic():
                _, is_new = SeenUrl.objects.get_or_create(url_hash=url_hash(normalized))
                if not is_new:
                    continue
                if raw.published_at is not None and raw.published_at < cutoff:
                    too_old += 1  # 오래된 글은 다시 수집하지 않게 SeenUrl 만 남긴다
                    continue
                Item.objects.create(
                    source=source,
                    url=raw.url,
                    normalized_url=normalized,
                    title=raw.title[:500],
                    excerpt=raw.excerpt,
                    published_at=raw.published_at,
                )
                created += 1
        ok, message = True, f"성공: 새 수집물 {created}건 / 항목 {len(raw_items)}건"
        if too_old:
            message += f" / 오래돼 건너뜀 {too_old}건"
    except Exception as exc:  # 소스별 독립 실패: 어떤 오류든 이 소스의 실패로 기록한다
        ok, message = False, f"실패: {type(exc).__name__}: {exc}"
    source.last_collected_at = timezone.now()
    source.last_result = message
    source.last_succeeded = ok
    source.consecutive_failures = 0 if ok else source.consecutive_failures + 1
    source.save(
        update_fields=[
            "last_collected_at",
            "last_result",
            "last_succeeded",
            "consecutive_failures",
        ]
    )
    return CollectResult(source=source, ok=ok, created=created, message=message)


def collect_all(fetcher: Fetcher | None = None) -> list[CollectResult]:
    """켜진 소스만 수집한다."""
    return [collect_source(s, fetcher) for s in Source.objects.filter(enabled=True).order_by("id")]


@dataclass(frozen=True)
class PendingPage:
    items: list[Item]
    total: int
    page: int
    page_size: int


def list_pending_items(page: int = 1, page_size: int = 50) -> PendingPage:
    """미처리 수집물을 오래된 것부터(수집 시각 순) 한 쪽. 편집자가 MCP로 읽는다."""
    qs = Item.objects.filter(status=Item.Status.PENDING).select_related("source")
    start = (page - 1) * page_size
    items = list(qs.order_by("collected_at", "id")[start : start + page_size])
    return PendingPage(items=items, total=qs.count(), page=page, page_size=page_size)
