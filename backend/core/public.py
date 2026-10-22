"""Public pages: home, article detail, sitemap, robots.

Each page renders the same data twice: as JSON in #page-data for React, and as plain HTML inside
#root (templates/core/public/*.html) so crawlers and link previews see the text without running JS.
"""

from datetime import datetime
from typing import TypedDict

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format

from articles.models import Article
from core.shell import INDEX, SITE_DESCRIPTION, SITE_NAME, PageMeta, render_shell
from services import articles as svc

PAGE_SIZE = 12
FEATURED_COUNT = 3
# #12 swaps these for the tracked inflow links; templates and components read them from here.
SANDCORP_URL = "https://sandcorp.co.kr"
SANDCORP_LABEL = "Sandcorp"


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
    sandcorp_url: str
    sandcorp_label: str


def _label(value: datetime | None) -> str:
    return date_format(timezone.localtime(value), "Y년 n월 j일 H:i") if value else ""


def _site() -> SiteData:
    return {"name": SITE_NAME, "sandcorp_url": SANDCORP_URL, "sandcorp_label": SANDCORP_LABEL}


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
    data: dict[str, object] = {"kind": "article", "site": _site(), "article": detail}
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
