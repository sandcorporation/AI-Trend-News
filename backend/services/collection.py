"""소스 수집 규칙: 켜진 소스를 순회하고 소스별로 독립 실패하며 결과를 소스에 기록한다."""

from dataclasses import dataclass

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
    try:
        adapter = ADAPTERS.get(source.adapter_name)
        if adapter is None:
            raise FetchError(f"알 수 없는 어댑터: {source.adapter_name}")
        raw_items = adapter.collect(source.url, fetcher)
        for raw in raw_items:
            normalized = normalize_url(raw.url)
            with transaction.atomic():
                _, is_new = SeenUrl.objects.get_or_create(url_hash=url_hash(normalized))
                if not is_new:
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
