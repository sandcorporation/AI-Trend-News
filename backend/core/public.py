"""Public pages: home, article detail, sitemap, robots.

Each page renders the same data twice: as JSON in #page-data for React, and as plain HTML inside
#root (templates/core/public/*.html) so crawlers and link previews see the text without running JS.
"""

import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime
from email.utils import format_datetime
from typing import TypedDict
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.core.paginator import Paginator
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.views.decorators.csrf import csrf_exempt

from adapters.clock import get_clock
from articles.models import Article, Category, Prediction
from core.shell import (
    DEFAULT_OG_IMAGE,
    INDEX,
    NOINDEX,
    SITE_DESCRIPTION,
    SITE_NAME,
    PageMeta,
    render_shell,
)
from inquiries.models import Inquiry
from services import articles as svc
from services import commentary as commentary_svc
from services import inquiries as inquiry_svc
from services import og_card, outbound
from services import predictions as predictions_svc

PAGE_SIZE = 12
FEATURED_COUNT = 6
# Sandcorp links always go through the tracked inflow link (services/outbound.py); label lives here.
SANDCORP_LABEL = "Sandcorp"
CTA_TEXT = "뉴스 수집·요약·게시를 자동화한 이 사이트처럼, 반복 업무를 자동화하고 싶으신가요?"
CTA_PORTFOLIO_LABEL = "Sandcorp 포트폴리오 보기"
CTA_CONTACT_LABEL = "문의하기"
CONTACT_PATH = "/contact"
PREDICTIONS_PATH = "/predictions"
FOOTER_CONTACT_LABEL = "문의"
SIDEBAR_SANDCORP_TEXT = "반복 업무를 자동화하는 팀. 이 사이트도 Sandcorp가 만들었습니다."
SIDEBAR_SANDCORP_LINK = "Sandcorp 알아보기"
SIDEBAR_CONTACT_LABEL = "문의하기"
SITE_MONOGRAM = "AT"
SECTION_COUNT = 4
MOST_READ_COUNT = 5
DAY_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
SITEMAP_DAYS = 60
DAY_DESCRIPTION_TITLES = 3
DAY_EMPTY_DESCRIPTION = "이 날은 게시된 기사가 없습니다. 다른 날의 AI·IT 뉴스를 확인해 보세요."
SIDEBAR_SHARE_LABEL = "오늘 뉴스 공유 →"
OG_CACHE_SECONDS = 3600
OG_CARD_TTL = 24 * 3600
logger = logging.getLogger(__name__)


class CategoryData(TypedDict):
    name: str
    slug: str


class SectionData(TypedDict):
    category: CategoryData
    more_url: str
    articles: list[CardData]


class SidebarData(TypedDict):
    share_label: str
    share_url: str
    most_read_title: str
    sandcorp_title: str
    sandcorp_text: str
    sandcorp_label: str
    sandcorp_url: str
    contact_label: str
    contact_url: str
    prediction_rate: SidebarRateData | None


class CardData(TypedDict):
    slug: str
    title: str
    summary: str
    category: CategoryData
    tags: list[str]
    published_at: str
    published_label: str
    image_url: str
    tone: str
    url: str
    has_commentary: bool


class SourceItemData(TypedDict):
    source: str
    title: str
    url: str
    published_label: str


class PredictionOut(TypedDict):
    claim: str
    horizon: str
    horizon_label: str
    basis: str
    due_label: str
    verdict: str  # 미평가면 빈 문자열, 아니면 hit·miss·undecidable
    verdict_label: str
    verdict_note: str


class PredictionRowData(TypedDict):
    claim: str
    horizon_label: str
    due_label: str
    verdict: str
    verdict_label: str
    verdict_note: str
    article_title: str
    article_url: str


class RateData(TypedDict):
    percent: int | None  # 평가된 것이 없으면 None
    judged: int
    hits: int
    undecidable: int
    pending: int  # 이 기간 목록 중 미평가 건수


class SidebarRateData(TypedDict):
    title: str
    percent: int
    judged: int
    link_label: str
    url: str


class CommentaryOut(TypedDict):
    background: str
    significance: str
    outlook_bull: str
    outlook_bear: str
    opinion: str
    predictions: list[PredictionOut]
    watchpoints: list[str]


class DetailData(CardData):
    items: list[SourceItemData]
    commentary: CommentaryOut | None


class NavItemData(TypedDict):
    label: str
    url: str
    current: bool


class SiteData(TypedDict):
    name: str
    today_label: str
    nav: list[NavItemData]
    monogram: str
    sandcorp_label: str
    header_url: str
    footer_url: str
    contact_label: str
    contact_url: str


class CtaData(TypedDict):
    text: str
    portfolio_label: str
    portfolio_url: str
    contact_label: str
    contact_url: str


class ShareData(TypedDict):
    url: str
    x_url: str


class DayShareData(ShareData):
    """날짜 페이지의 공유 조각: `navigator.share`에 넘길 제목·문구를 더한다."""

    title: str
    text: str


class DayLinkData(TypedDict):
    label: str
    url: str


class DayTabData(TypedDict):
    label: str
    url: str
    current: bool


def _label(value: datetime | None) -> str:
    return date_format(timezone.localtime(value), "Y년 n월 j일 H:i") if value else ""


def _relative_label(value: datetime) -> str:
    """24시간 안은 N분/N시간 전, 그 뒤는 「10월 22일」(해가 다르면 연도까지). 기준은 시계 어댑터."""
    now = get_clock().now()
    seconds = (now - value).total_seconds()
    if seconds < 60:
        return "방금 전"
    if seconds < 3600:
        return f"{int(seconds // 60)}분 전"
    if seconds < 86400:
        return f"{int(seconds // 3600)}시간 전"
    local = timezone.localtime(value)
    same_year = local.year == timezone.localtime(now).year
    return date_format(local, "n월 j일" if same_year else "Y년 n월 j일")


def _tone(category_name: str) -> str:
    return "it" if category_name.strip().lower() == "it" else "ai"


def _prediction_label(row: Prediction) -> str:
    return "" if row.verdict == Prediction.Verdict.PENDING else row.get_verdict_display()


def _horizon_label(horizon: str) -> str:
    return f"{horizon[:-1]}개월 안"


def _due_label(due: date) -> str:
    return f"{due.year}.{due.month}.{due.day}"


def _sidebar(today: date) -> SidebarData:
    rate = predictions_svc.sidebar_rate(today)
    percent = rate.percent if rate else None
    return {
        "share_label": SIDEBAR_SHARE_LABEL,
        "share_url": day_path(today),
        "prediction_rate": (
            {
                "title": "전망 적중률",
                "percent": percent,
                "judged": rate.judged,
                "link_label": "전망 기록표 보기",
                "url": PREDICTIONS_PATH,
            }
            if rate and percent is not None
            else None
        ),
        "most_read_title": "많이 읽은 기사",
        "sandcorp_title": SANDCORP_LABEL,
        "sandcorp_text": SIDEBAR_SANDCORP_TEXT,
        "sandcorp_label": SIDEBAR_SANDCORP_LINK,
        "sandcorp_url": outbound.go_path("sidebar"),
        "contact_label": SIDEBAR_CONTACT_LABEL,
        "contact_url": CONTACT_PATH,
    }


WEEKDAYS = "월화수목금토일"


def _today_label() -> str:
    """머리띠 날짜: 서버 시계(Asia/Seoul) 기준 「2026.10.23 (금)」."""
    today = timezone.localtime(get_clock().now()).date()
    return f"{today:%Y.%m.%d} ({WEEKDAYS[today.weekday()]})"


def _nav(current: str | None, categories: Iterable[Category] | None = None) -> list[NavItemData]:
    """머리띠 내비: 전체 · 분야들 · 전망 기록표 · 문의.

    current는 ""(전체)·분야 slug·"predictions"·"contact", 해당 없으면 None.
    """
    cats = svc.public_categories() if categories is None else categories
    items: list[tuple[str, str, str]] = [("", "전체", "/")]
    items += [(c.slug, c.name, f"/?category={c.slug}") for c in cats]
    items += [("predictions", "전망 기록표", PREDICTIONS_PATH), ("contact", "문의", CONTACT_PATH)]
    return [{"label": label, "url": url, "current": key == current} for key, label, url in items]


def _site(current: str | None = None, categories: Iterable[Category] | None = None) -> SiteData:
    return {
        "name": SITE_NAME,
        "today_label": _today_label(),
        "nav": _nav(current, categories),
        "monogram": SITE_MONOGRAM,
        "sandcorp_label": SANDCORP_LABEL,
        "header_url": outbound.go_path("header"),
        "footer_url": outbound.go_path("footer"),
        "contact_label": FOOTER_CONTACT_LABEL,
        "contact_url": CONTACT_PATH,
    }


def _cta(slug: str) -> CtaData:
    return {
        "text": CTA_TEXT,
        "portfolio_label": CTA_PORTFOLIO_LABEL,
        "portfolio_url": outbound.go_path("article_cta", slug),
        "contact_label": CTA_CONTACT_LABEL,
        "contact_url": CONTACT_PATH,
    }


def _share(title: str, url: str) -> ShareData:
    query = urlencode({"text": title, "url": url})
    return {"url": url, "x_url": f"https://twitter.com/intent/tweet?{query}"}


def _card(article: Article) -> CardData:
    return {
        "slug": article.slug,
        "title": article.title,
        "summary": article.summary,
        "category": {"name": article.category.name, "slug": article.category.slug},
        "tags": [t.name for t in article.tags.all()],
        "published_at": article.published_at.isoformat(),
        "published_label": _relative_label(article.published_at),
        "image_url": article.image_url,
        "tone": _tone(article.category.name),
        "url": reverse("article", args=[article.slug]),
        "has_commentary": commentary_svc.public_commentary(article) is not None,
    }


def _detail(article: Article) -> DetailData:
    items: list[SourceItemData] = [
        {
            "source": link.item.source.name,
            "title": link.item.title,
            "url": link.item.url,
            "published_label": _label(link.item.published_at),
        }
        for link in article.article_items.all()
    ]
    return {
        **_card(article),
        "published_label": _label(article.published_at),
        "items": items,
        "commentary": _commentary(article),
    }


def _commentary(article: Article) -> CommentaryOut | None:
    found = commentary_svc.public_commentary(article)
    if found is None:
        return None
    return {
        "background": found.background,
        "significance": found.significance,
        "outlook_bull": found.outlook_bull,
        "outlook_bear": found.outlook_bear,
        "opinion": found.opinion,
        "watchpoints": [str(w) for w in found.watchpoints],
        "predictions": [
            {
                "claim": p.claim,
                "horizon": p.horizon,
                "horizon_label": _horizon_label(p.horizon),
                "basis": p.basis,
                "due_label": _due_label(p.due_date),
                "verdict": "" if p.verdict == Prediction.Verdict.PENDING else p.verdict,
                "verdict_label": _prediction_label(p),
                "verdict_note": p.verdict_note,
            }
            for p in found.prediction_rows.all()
        ],
    }


def _snippet(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _iso(value: datetime) -> str:
    return timezone.localtime(value).isoformat(timespec="seconds")


def _logo_url() -> str:
    return settings.SITE_URL + static("core/favicon.svg")


def _page_ld(kind: str, name: str, url: str, description: str) -> dict[str, object]:
    return {
        "@context": "https://schema.org",
        "@type": kind,
        "name": name,
        "url": url,
        "description": description,
        "inLanguage": "ko",
    }


def _article_ld(article: Article, canonical: str, image: str) -> dict[str, object]:
    publisher: dict[str, object] = {
        "@type": "Organization",
        "name": SITE_NAME,
        "url": settings.SITE_URL,
        "logo": {"@type": "ImageObject", "url": _logo_url()},
    }
    ld: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": article.title,
        "description": _snippet(article.summary) or SITE_DESCRIPTION,
        "url": canonical,
        "mainEntityOfPage": canonical,
        "inLanguage": "ko",
        "datePublished": _iso(article.published_at),
        "dateModified": _iso(article.updated_at),
        "articleSection": article.category.name,
        "author": {"@type": "Organization", "name": SITE_NAME, "url": settings.SITE_URL},
        "publisher": publisher,
    }
    if image:
        ld["image"] = [image]
    tags = [t.name for t in article.tags.all()]
    if tags:
        ld["keywords"] = ", ".join(tags)
    return ld


def _shell(
    request: HttpRequest,
    meta: PageMeta,
    data: dict[str, object],
    fallback: str,
    canonical: str,
    og_type: str = "website",
) -> HttpResponse:
    body = render_to_string(fallback, {"data": data, "site": data["site"]}, request)
    return render_shell(request, meta, data=data, body=body, canonical=canonical, og_type=og_type)


def home(request: HttpRequest) -> HttpResponse:
    category = request.GET.get("category", "")
    categories = svc.public_categories()
    current = next((c for c in categories if c.slug == category), None)
    if category and current is None:
        renamed = svc.find_category_by_old_slug(category)
        if renamed is not None:
            params = request.GET.copy()
            params["category"] = renamed.slug
            return HttpResponsePermanentRedirect(f"{request.path}?{params.urlencode()}")
        raise Http404("없는 분야입니다.")
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        raise Http404("잘못된 페이지입니다.") from None
    paginator = Paginator(svc.public_list(category), PAGE_SIZE)
    if page_number < 1 or (page_number > paginator.num_pages):
        raise Http404("없는 페이지입니다.")
    page = paginator.page(page_number)
    first_unfiltered = page_number == 1 and not category
    featured = svc.public_featured(FEATURED_COUNT) if first_unfiltered else []

    sections: list[SectionData] = []
    if first_unfiltered:
        by_category = svc.public_sections(SECTION_COUNT)
        sections = [
            {
                "category": {"name": c.name, "slug": c.slug},
                "more_url": f"/?category={c.slug}",
                "articles": [_card(a) for a in by_category.get(c.pk, [])],
            }
            for c in categories
            if by_category.get(c.pk)
        ]
    most_read = svc.public_most_read(7, MOST_READ_COUNT) if first_unfiltered else []

    data: dict[str, object] = {
        "kind": "home",
        "site": _site(category, categories),
        "categories": [{"name": c.name, "slug": c.slug} for c in categories],
        "category": category,
        "featured": [_card(a) for a in featured],
        "articles": [_card(a) for a in page.object_list],
        "sections": sections,
        "most_read": [_card(a) for a in most_read],
        "sidebar": _sidebar(predictions_svc.today_of(get_clock().now())),
        "page": page_number,
        "num_pages": paginator.num_pages,
    }
    title = SITE_NAME if current is None else f"{current.name} - {SITE_NAME}"
    query = ""
    if category:
        query += f"?category={category}"
    if page_number > 1:
        query += f"{'&' if query else '?'}page={page_number}"
    json_ld: tuple[dict[str, object], ...] = ()
    if not category and page_number == 1:
        site_url = request.build_absolute_uri(request.path)
        json_ld = (
            _page_ld("WebSite", SITE_NAME, site_url, SITE_DESCRIPTION),
            {
                "@context": "https://schema.org",
                "@type": "Organization",
                "name": SITE_NAME,
                "url": site_url,
                "logo": _logo_url(),
            },
        )
    meta = PageMeta(title=title, description=SITE_DESCRIPTION, robots=INDEX, json_ld=json_ld)
    return _shell(
        request,
        meta,
        data,
        "core/public/home.html",
        request.build_absolute_uri(request.path) + query,
    )


def article_detail(request: HttpRequest, slug: str) -> HttpResponse:
    try:
        article = svc.public_article(slug)
    except svc.ArticleNotFound:
        renamed = svc.find_article_by_old_slug(slug)
        if renamed is None:
            raise Http404("없는 기사입니다.") from None
        return HttpResponsePermanentRedirect(f"/a/{renamed.slug}")
    svc.record_view(article, request.headers.get("User-Agent", ""))
    detail = _detail(article)
    data: dict[str, object] = {
        "kind": "article",
        "site": _site(),
        "article": detail,
        "cta": _cta(article.slug),
        "share": _share(article.title, request.build_absolute_uri(request.path)),
    }
    meta = PageMeta(
        title=f"{article.title} - {SITE_NAME}",
        description=_snippet(article.summary) or SITE_DESCRIPTION,
        robots=INDEX,
        image=article.image_url,
        image_alt=article.title,
        published_time=_iso(article.published_at),
        modified_time=_iso(article.updated_at),
        section=article.category.name,
        tags=tuple(t.name for t in article.tags.all()),
    )
    canonical = request.build_absolute_uri(request.path)
    image = request.build_absolute_uri(article.image_url) if article.image_url else ""
    meta = replace(meta, json_ld=(_article_ld(article, canonical, image),))
    return _shell(
        request,
        meta,
        data,
        "core/public/article.html",
        canonical,
        og_type="article",
    )


def go_sandcorp(request: HttpRequest) -> HttpResponse:
    """유입 링크: 클릭을 기록하고 UTM을 붙여 sandcorp.co.kr로 302."""
    try:
        target = outbound.follow(
            request.GET.get("from", ""),
            request.GET.get("article", ""),
            request.META.get("HTTP_REFERER", ""),
            request.META.get("HTTP_USER_AGENT", ""),
        )
    except outbound.InvalidLocation:
        return HttpResponse(
            "잘못된 위치입니다.", status=400, content_type="text/plain; charset=utf-8"
        )
    response = HttpResponseRedirect(target)
    response["Cache-Control"] = "no-store"
    return response


PERIODS = {"all": "전체", "3m": "3개월"}


def predictions_page(request: HttpRequest) -> HttpResponse:
    """전망 기록표: 평가된 예측의 적중률과 목록. `?period=3m`이면 기한이 최근 3개월인 것만."""
    period = request.GET.get("period", "all")
    if period not in PERIODS:
        raise Http404("없는 기간입니다.")
    today = predictions_svc.today_of(get_clock().now())
    recent = period == "3m"
    rate = predictions_svc.hit_rate(recent=recent, today=today)
    rows: list[PredictionRowData] = [
        {
            "claim": p.claim,
            "horizon_label": _horizon_label(p.horizon),
            "due_label": _due_label(p.due_date),
            "verdict": "" if p.verdict == Prediction.Verdict.PENDING else p.verdict,
            "verdict_label": _prediction_label(p) or "평가 대기",
            "verdict_note": p.verdict_note,
            "article_title": p.commentary.article.title,
            "article_url": reverse("article", args=[p.commentary.article.slug]),
        }
        for p in predictions_svc.public_predictions(recent=recent, today=today)
    ]
    rate_data: RateData = {
        "percent": rate.percent,
        "judged": rate.judged,
        "hits": rate.hits,
        "undecidable": rate.undecidable,
        "pending": sum(1 for r in rows if not r["verdict"]),
    }
    data: dict[str, object] = {
        "kind": "predictions",
        "site": _site("predictions"),
        "period": period,
        "periods": [
            {
                "key": key,
                "label": label,
                "url": PREDICTIONS_PATH + ("" if key == "all" else f"?period={key}"),
            }
            for key, label in PERIODS.items()
        ],
        "rate": rate_data,
        "rows": rows,
    }
    meta = PageMeta(
        title=f"전망 기록표 - {SITE_NAME}",
        description="AI 분석의 예측 문장을 모아 기한이 지난 뒤 적중 여부를 기록합니다.",
        robots=INDEX,
    )
    meta = replace(
        meta,
        json_ld=(
            _page_ld(
                "WebPage",
                meta.title,
                request.build_absolute_uri(PREDICTIONS_PATH),
                meta.description,
            ),
        ),
    )
    query = "" if period == "all" else f"?period={period}"
    return _shell(
        request,
        meta,
        data,
        "core/public/predictions.html",
        request.build_absolute_uri(PREDICTIONS_PATH) + query,
    )


def day_path(day: date) -> str:
    return f"/news/{day.isoformat()}"


def _day_long(day: date) -> str:
    return f"{day.year}년 {day.month}월 {day.day}일 ({WEEKDAYS[day.weekday()]})"


def _day_short(day: date) -> str:
    return f"{day.month}월 {day.day}일"


def _parse_day(raw: str) -> date:
    """`YYYY-MM-DD`(KST)만. 형식 오류·없는 날짜·미래·첫 게시일 이전·게시 없음은 404."""
    if not DAY_RE.fullmatch(raw):
        raise Http404("잘못된 날짜입니다.")
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise Http404("잘못된 날짜입니다.") from None
    first = svc.public_first_day()
    if first is None or day < first or day > predictions_svc.today_of(get_clock().now()):
        raise Http404("기사가 없는 날짜입니다.")
    return day


def _day_titles(articles: list[Article]) -> list[str]:
    """카드·설명에 쓸 제목: 추천 기사를 앞세우고 나머지는 최신순."""
    return [a.title for a in sorted(articles, key=lambda a: not a.featured)]


def _day_stamp_key(day: date, count: int, latest: datetime | None, category: str = "") -> str:
    raw = f"{day.isoformat()}|{category}|{count}|{latest.isoformat() if latest else ''}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:16]


def day_category_path(day: date, category: str = "") -> str:
    return f"{day_path(day)}/{category}" if category else day_path(day)


def _resolve_day_category(
    request: HttpRequest, when: date, category: str
) -> tuple[Category | None, HttpResponse | None]:
    """경로 분야 slug(없으면 옛 `?category=`)를 현재 분야로. 경로 주소가 아니면 301, 없으면 404."""
    from_query = not category
    wanted = category or request.GET.get("category", "")
    if not wanted:
        return None, None
    current = next((c for c in svc.public_categories() if c.slug == wanted), None)
    if current is None:
        current = svc.find_category_by_old_slug(wanted)
    if current is None:
        raise Http404("없는 분야입니다.")
    if from_query or current.slug != category:
        params = request.GET.copy()
        params.pop("category", None)
        query = f"?{params.urlencode()}" if params else ""
        target = day_category_path(when, current.slug) + query
        return current, HttpResponsePermanentRedirect(target)
    return current, None


def news_day(request: HttpRequest, day: str, category: str = "") -> HttpResponse:
    """날짜(·분야) 카드 PNG(1200×630). 첫 요청 때 합성해 캐시한다. 0건·실패는 기본 이미지."""
    when = _parse_day(day)
    current, redirect = _resolve_day_category(request, when, category)
    if redirect is not None:
        return redirect
    slug = current.slug if current else ""
    scope = current.name if current else "AI·IT"
    articles = svc.public_day(when, slug)
    prev_day, next_day = svc.public_neighbor_days(when, slug)
    here = day_category_path(when, slug)
    canonical = request.build_absolute_uri(here)
    count = len(articles)
    titles = _day_titles(articles)
    short = _day_short(when)
    if count:
        meta_title = f"{short} {scope} 뉴스 {count}건 — {SITE_NAME}"
        description = _snippet(" · ".join(titles[:DAY_DESCRIPTION_TITLES]), 160)
    else:
        meta_title = f"{short} {scope} 뉴스 — {SITE_NAME}"
        description = DAY_EMPTY_DESCRIPTION
    share_title = f"{_day_long(when)} {scope} 뉴스"
    x_url = _share(meta_title, canonical)["x_url"]
    tabs: list[DayTabData] = [{"label": "전체", "url": day_path(when), "current": current is None}]
    tabs += [
        {
            "label": c.name,
            "url": day_category_path(when, c.slug),
            "current": current is not None and c.pk == current.pk,
        }
        for c in svc.public_categories()
    ]
    if count:
        empty_text = ""
    elif current:
        empty_text = f"이 날 {current.name} 기사가 없습니다."
    else:
        empty_text = "이 날은 게시된 기사가 없습니다."
    data: dict[str, object] = {
        "kind": "day",
        "site": _site(),
        "date": when.isoformat(),
        "heading": share_title,
        "count": count,
        "category": slug,
        "tabs": tabs,
        "all_url": day_path(when),
        "articles": [_card(a) for a in articles],
        "empty_text": empty_text,
        "prev": _day_link(prev_day, slug),
        "next": _day_link(next_day, slug),
        "home_url": "/",
        "share": {
            "url": canonical,
            "x_url": x_url,
            "title": meta_title,
            "text": description,
        },
    }
    image = ""
    if count:
        _, latest = svc.public_day_stamp(when, slug)
        image = f"{here}/og.png?v={_day_stamp_key(when, count, latest, slug)}"
    meta = PageMeta(
        title=meta_title,
        description=description,
        robots=INDEX if count else NOINDEX,
        image=image,
        image_alt=meta_title,
        image_sized=bool(image),
    )
    return _shell(request, meta, data, "core/public/day.html", canonical)


def _day_link(day: date | None, category: str = "") -> DayLinkData | None:
    if day is None:
        return None
    return {"label": _day_long(day), "url": day_category_path(day, category)}


def news_day_og(request: HttpRequest, day: str, category: str = "") -> HttpResponse:
    """날짜(·분야) 카드 이미지(1200×630 PNG). 첫 요청 때 합성해 캐시한다. 0건·실패는 기본 이미지."""
    when = _parse_day(day)
    current = None
    if category:
        current = next((c for c in svc.public_categories() if c.slug == category), None)
        if current is None:
            renamed = svc.find_category_by_old_slug(category)
            if renamed is not None:
                return HttpResponsePermanentRedirect(
                    day_category_path(when, renamed.slug) + "/og.png"
                )
            raise Http404("없는 분야입니다.")
    count, latest = svc.public_day_stamp(when, category)
    if not count:
        return _default_og_response()
    key = _day_stamp_key(when, count, latest, category)
    etag = f'"{key}"'
    headers = {"ETag": etag, "Cache-Control": f"public, max-age={OG_CACHE_SECONDS}"}
    if request.headers.get("If-None-Match") == etag:
        return HttpResponse(status=304, headers=headers)
    cache_key = f"og-day:{key}"
    png = cache.get(cache_key)
    if png is None:
        try:
            articles = svc.public_day(when, category)
            png = og_card.render_day_card(
                when, _day_titles(articles), len(articles), current.name if current else ""
            )
        except Exception:  # 카드 합성이 깨져도 공유 미리보기는 기본 이미지로 이어 간다.
            logger.exception("날짜 카드 이미지 생성 실패: %s", when)
            return _default_og_response()
        cache.set(cache_key, png, OG_CARD_TTL)
    return HttpResponse(png, content_type="image/png", headers=headers)


def _default_og_response() -> HttpResponse:
    path = settings.BASE_DIR / "core" / "static" / DEFAULT_OG_IMAGE
    return HttpResponse(
        path.read_bytes(),
        content_type="image/png",
        headers={"Cache-Control": "public, max-age=300"},
    )


def sitemap(request: HttpRequest) -> HttpResponse:
    def loc(path: str) -> str:
        return request.build_absolute_uri(path)

    urls: list[tuple[str, datetime | None]] = [(loc("/"), None)]
    urls += [(loc(f"/?category={c.slug}"), None) for c in svc.public_categories()]
    urls.append((loc(PREDICTIONS_PATH), None))
    now = get_clock().now()
    urls += [
        (loc(day_path(d)), updated) for d, updated in svc.public_recent_days(SITEMAP_DAYS, now)
    ]
    urls += [
        (loc(day_category_path(d, slug)), updated)
        for d, slug, updated in svc.public_recent_category_days(SITEMAP_DAYS, now)
    ]
    urls += [(loc(reverse("article", args=[a.slug])), a.updated_at) for a in svc.public_sitemap()]
    xml = render_to_string("core/public/sitemap.xml", {"urls": urls})
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


def feed(request: HttpRequest) -> HttpResponse:
    """RSS 2.0 최근 30건(#73). 이미지는 enclosure 대신 media:content."""
    found = svc.public_recent(30)
    rows = [
        {
            "title": a.title,
            "link": request.build_absolute_uri(reverse("article", args=[a.slug])),
            "summary": _snippet(a.summary),
            "pub_date": format_datetime(timezone.localtime(a.published_at)),
            "category": a.category.name,
            "image_url": a.image_url,
        }
        for a in found
    ]
    xml = render_to_string(
        "core/public/feed.xml",
        {
            "site_name": SITE_NAME,
            "description": SITE_DESCRIPTION,
            "site_url": request.build_absolute_uri("/"),
            "feed_url": request.build_absolute_uri("/feed.xml"),
            "last_build": rows[0]["pub_date"] if rows else "",
            "rows": rows,
        },
    )
    response = HttpResponse(xml, content_type="application/rss+xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response


def robots(request: HttpRequest) -> HttpResponse:
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /mcp",
        f"Sitemap: {request.build_absolute_uri('/sitemap.xml')}",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


class ContactForm(TypedDict):
    name: str
    company: str
    contact: str
    message: str
    kind: str
    errors: dict[str, str]


def _client_ip(request: HttpRequest) -> str:
    """NPMplus가 덧붙인 X-Forwarded-For의 오른쪽 끝 한 칸만 믿는다(왼쪽은 방문자가 꾸밀 수 있다)."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    last = forwarded.split(",")[-1].strip()
    return last or str(request.META.get("REMOTE_ADDR", ""))


@csrf_exempt  # 로그인 없는 공개 폼이라 노릴 세션이 없다. 남용은 honeypot과 IP 제한으로 막는다.
def contact(request: HttpRequest) -> HttpResponse:
    form: ContactForm = {
        "name": "",
        "company": "",
        "contact": "",
        "message": "",
        "kind": Inquiry.Kind.INQUIRY.value,
        "errors": {},
    }
    state = "form"
    status = 200
    if request.method == "POST":
        post = request.POST
        for key in ("name", "company", "contact", "message", "kind"):
            form[key] = post.get(key, "")
        submission = inquiry_svc.Submission(
            name=form["name"],
            company=form["company"],
            contact=form["contact"],
            message=form["message"],
            kind=form["kind"],
            honeypot=post.get("website", ""),
        )
        try:
            inquiry_svc.submit(submission, _client_ip(request), get_clock().now())
        except inquiry_svc.InquiryInvalid as invalid:
            form["errors"] = invalid.errors
            status = 400
        except inquiry_svc.RateLimited:
            state = "limited"
            status = 429
        else:
            state = "done"
            form = {**form, "name": "", "company": "", "contact": "", "message": ""}
    site = _site("contact")
    data: dict[str, object] = {
        "kind": "contact",
        "site": site,
        "state": state,
        "form": form,
        "kinds": list(Inquiry.Kind.values),
    }
    meta = PageMeta(
        title=f"문의 - {SITE_NAME}", description="문의나 제안을 남겨 주세요.", robots=NOINDEX
    )
    body = render_to_string("core/public/contact.html", {"data": data, "site": site}, request)
    response = render_shell(
        request,
        meta,
        status=status,
        data=data,
        body=body,
        canonical=request.build_absolute_uri("/contact"),
    )
    response["Cache-Control"] = "no-store"
    return response
