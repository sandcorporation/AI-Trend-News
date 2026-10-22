"""Public pages: home, article detail, sitemap, robots.

Each page renders the same data twice: as JSON in #page-data for React, and as plain HTML inside
#root (templates/core/public/*.html) so crawlers and link previews see the text without running JS.
"""

from datetime import datetime
from typing import TypedDict
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from articles.models import Article
from core.shell import INDEX, SITE_DESCRIPTION, SITE_NAME, PageMeta, render_shell
from services import articles as svc
from services import outbound

PAGE_SIZE = 12
FEATURED_COUNT = 3
# Sandcorp links always go through the tracked inflow link (services/outbound.py); label lives here.
SANDCORP_LABEL = "Sandcorp"
CTA_TEXT = "뉴스 수집·요약·게시를 자동화한 이 사이트처럼, 반복 업무를 자동화하고 싶으신가요?"
CTA_PORTFOLIO_LABEL = "Sandcorp 포트폴리오 보기"
CTA_CONTACT_LABEL = "문의하기"


class CategoryData(TypedDict):
    name: str
    slug: str


class CardData(TypedDict):
    slug: str
    title: str
    summary: str
    category: CategoryData
    tags: list[str]
    published_at: str
    published_label: str
    image_url: str
    url: str


class SourceItemData(TypedDict):
    source: str
    title: str
    url: str
    published_label: str


class DetailData(CardData):
    items: list[SourceItemData]


class SiteData(TypedDict):
    name: str
    sandcorp_label: str
    header_url: str
    footer_url: str


class CtaData(TypedDict):
    text: str
    portfolio_label: str
    portfolio_url: str
    contact_label: str
    contact_url: str


class ShareData(TypedDict):
    url: str
    x_url: str


def _label(value: datetime | None) -> str:
    return date_format(timezone.localtime(value), "Y년 n월 j일 H:i") if value else ""


def _site() -> SiteData:
    return {
        "name": SITE_NAME,
        "sandcorp_label": SANDCORP_LABEL,
        "header_url": outbound.go_path("header"),
        "footer_url": outbound.go_path("footer"),
    }


def _cta(slug: str) -> CtaData:
    return {
        "text": CTA_TEXT,
        "portfolio_label": CTA_PORTFOLIO_LABEL,
        "portfolio_url": outbound.go_path("article_cta", slug),
        "contact_label": CTA_CONTACT_LABEL,
        # #13 replaces this with the /contact form.
        "contact_url": outbound.go_path("contact", slug),
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
        "published_at": article.created_at.isoformat(),
        "published_label": _label(article.created_at),
        "image_url": article.image_url,
        "url": reverse("article", args=[article.slug]),
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
    return {**_card(article), "items": items}


def _snippet(text: str, limit: int = 160) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _shell(
    request: HttpRequest,
    meta: PageMeta,
    data: dict[str, object],
    fallback: str,
    canonical: str,
    og_type: str = "website",
) -> HttpResponse:
    body = render_to_string(fallback, {"data": data, "site": _site()}, request)
    return render_shell(request, meta, data=data, body=body, canonical=canonical, og_type=og_type)


def home(request: HttpRequest) -> HttpResponse:
    category = request.GET.get("category", "")
    categories = svc.public_categories()
    current = next((c for c in categories if c.slug == category), None)
    if category and current is None:
        raise Http404("없는 분야입니다.")
    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        raise Http404("잘못된 페이지입니다.") from None
    paginator = Paginator(svc.public_list(category), PAGE_SIZE)
    if page_number < 1 or (page_number > paginator.num_pages):
        raise Http404("없는 페이지입니다.")
    page = paginator.page(page_number)
    featured = svc.public_featured(FEATURED_COUNT, category) if page_number == 1 else []

    data: dict[str, object] = {
        "kind": "home",
        "site": _site(),
        "categories": [{"name": c.name, "slug": c.slug} for c in categories],
        "category": category,
        "featured": [_card(a) for a in featured],
        "articles": [_card(a) for a in page.object_list],
        "page": page_number,
        "num_pages": paginator.num_pages,
    }
    title = SITE_NAME if current is None else f"{current.name} - {SITE_NAME}"
    query = ""
    if category:
        query += f"?category={category}"
    if page_number > 1:
        query += f"{'&' if query else '?'}page={page_number}"
    meta = PageMeta(title=title, description=SITE_DESCRIPTION, robots=INDEX)
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
        raise Http404("없는 기사입니다.") from None
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
    )
    return _shell(
        request,
        meta,
        data,
        "core/public/article.html",
        request.build_absolute_uri(request.path),
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


def sitemap(request: HttpRequest) -> HttpResponse:
    def loc(path: str) -> str:
        return request.build_absolute_uri(path)

    urls: list[tuple[str, datetime | None]] = [(loc("/"), None)]
    urls += [(loc(f"/?category={c.slug}"), None) for c in svc.public_categories()]
    urls += [(loc(reverse("article", args=[a.slug])), a.updated_at) for a in svc.public_sitemap()]
    xml = render_to_string("core/public/sitemap.xml", {"urls": urls})
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")


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
