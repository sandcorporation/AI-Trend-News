"""소스 수집 규칙: 켜진 소스를 순회하고 소스별로 독립 실패하며 결과를 소스에 기록한다."""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from adapters.fetch import Fetcher, FetchError, get_fetcher
from adapters.sources import ADAPTERS, find_og_image
from collection.models import Item, SeenUrl, Source
from services.urls import normalize_url, url_hash

OG_FETCH_LIMIT = 30  # 소스당 한 수집 실행에서 og:image 보강으로 받는 원문 수
OG_MAX_BYTES = 1_000_000  # 원문은 앞 1MB만 본다(head 안의 meta면 충분)


def _og_image(fetcher: Fetcher, url: str) -> str:
    """원문에서 og:image를 읽는다. 어떤 실패든 조용히 빈 값(본문은 저장하지 않는다)."""
    try:
        return find_og_image(fetcher.fetch(url)[:OG_MAX_BYTES], url)
    except Exception:  # noqa: BLE001  # 보강은 최선 노력: 소스 실패로 치지 않는다
        return ""


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
    og_fetches = 0
    with_image = 0
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
                image_url = raw.image_url
                if not image_url and og_fetches < OG_FETCH_LIMIT:
                    og_fetches += 1
                    image_url = _og_image(fetcher, raw.url)
                if image_url:
                    with_image += 1
                Item.objects.create(
                    source=source,
                    url=raw.url,
                    normalized_url=normalized,
                    title=raw.title[:500],
                    excerpt=raw.excerpt,
                    published_at=raw.published_at,
                    image_url=image_url,
                )
                created += 1
        ok, message = True, f"성공: 새 수집물 {created}건 / 항목 {len(raw_items)}건"
        if too_old:
            message += f" / 오래돼 건너뜀 {too_old}건"
        message += f" / 이미지 {with_image}건"
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


def list_pending_items(
    page: int = 1, page_size: int = 50, *, exclude_ids: Collection[int] = ()
) -> PendingPage:
    """미처리 수집물을 최신순(수집 시각 내림차순) 한 쪽. 편집자가 MCP로 읽는다.

    `exclude_ids`는 게시 모드 파이프라인이 예약해 둔 수집물(#128)을 빼는 데 쓴다.
    """
    qs = Item.objects.filter(status=Item.Status.PENDING).select_related("source")
    if exclude_ids:
        qs = qs.exclude(pk__in=list(exclude_ids))
    start = (page - 1) * page_size
    items = list(qs.order_by("-collected_at", "-id")[start : start + page_size])
    return PendingPage(items=items, total=qs.count(), page=page, page_size=page_size)
