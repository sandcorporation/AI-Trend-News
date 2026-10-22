"""유입 링크: sandcorp.co.kr로 가는 모든 링크는 `/go/sandcorp`를 거치며 클릭이 기록된다."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from django.db.models import Count
from django.utils import timezone

from articles.models import Article, OutboundClick

logger = logging.getLogger(__name__)

SANDCORP_SITE = "https://sandcorp.co.kr/"
GO_PATH = "/go/sandcorp"
LOCATIONS: tuple[str, ...] = tuple(v for v, _ in OutboundClick.Location.choices)
_BOT = re.compile(r"bot|crawl|spider|slurp|preview|fetcher|curl|wget", re.IGNORECASE)
PERIODS: dict[str, tuple[str, int]] = {"today": ("오늘", 0), "7d": ("7일", 7), "30d": ("30일", 30)}


class InvalidLocation(ValueError):
    """`from` 값이 정해진 위치가 아니다."""


def go_path(location: str, article_slug: str = "") -> str:
    """사이트 안에서 쓰는 유입 링크 경로."""
    query = {"from": location}
    if article_slug:
        query["article"] = article_slug
    return f"{GO_PATH}?{urlencode(query)}"


def is_bot(user_agent: str) -> bool:
    return bool(_BOT.search(user_agent))


def target_url(location: str, article_slug: str = "") -> str:
    campaign = article_slug or "site"
    return (
        SANDCORP_SITE
        + "?"
        + urlencode({"utm_source": "news", "utm_medium": location, "utm_campaign": campaign})
    )


def follow(location: str, article_slug: str, referrer: str, user_agent: str) -> str:
    """위치를 검증하고, 봇이 아니면 클릭을 기록한 뒤 이동할 주소를 돌려준다. 기록 실패는 삼킨다."""
    if location not in LOCATIONS:
        raise InvalidLocation(location)
    article = (
        Article.objects.filter(slug=article_slug, published=True).first() if article_slug else None
    )
    slug = article.slug if article else ""
    if not is_bot(user_agent):
        try:
            OutboundClick.objects.create(
                location=location,
                article=article,
                referrer=referrer[:500],
                user_agent=user_agent[:200],
            )
        except Exception:  # 기록이 이동을 막으면 안 된다
            logger.exception("outbound click not recorded")
    return target_url(location, slug)


@dataclass(frozen=True)
class CountRow:
    label: str
    count: int


@dataclass(frozen=True)
class PeriodSummary:
    key: str
    label: str
    total: int
    by_location: list[CountRow]
    by_article: list[CountRow]


def summarize(now: datetime | None = None) -> list[PeriodSummary]:
    """오늘·7일·30일 기간별로 위치·기사별 클릭 수. 「오늘」은 오늘 0시부터."""
    now = now or timezone.now()
    midnight = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    labels = dict(OutboundClick.Location.choices)
    result: list[PeriodSummary] = []
    for key, (label, days) in PERIODS.items():
        clicks = OutboundClick.objects.filter(clicked_at__gte=midnight - timedelta(days=days))
        by_location = [
            CountRow(labels.get(r["location"], r["location"]), r["n"])
            for r in clicks.values("location").annotate(n=Count("id")).order_by("-n", "location")
        ]
        by_article = [
            CountRow(r["article__title"] or "(기사 없음)", r["n"])
            for r in clicks.values("article__title").annotate(n=Count("id")).order_by("-n")
        ]
        result.append(PeriodSummary(key, label, clicks.count(), by_location, by_article))
    return result
