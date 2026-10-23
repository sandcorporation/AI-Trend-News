"""Public pages: home, article detail, sitemap, robots.

Each page renders the same data twice: as JSON in #page-data for React, and as plain HTML inside
#root (templates/core/public/*.html) so crawlers and link previews see the text without running JS.
"""

from datetime import datetime
from typing import TypedDict
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateformat import format as date_format
from django.views.decorators.csrf import csrf_exempt

from adapters.clock import get_clock
from articles.models import Article
from core.shell import INDEX, NOINDEX, SITE_DESCRIPTION, SITE_NAME, PageMeta, render_shell
from inquiries.models import Inquiry
from services import articles as svc
from services import commentary as commentary_svc
from services import inquiries as inquiry_svc
from services import outbound

PAGE_SIZE = 12
FEATURED_COUNT = 5
# Sandcorp links always go through the tracked inflow link (services/outbound.py); label lives here.
SANDCORP_LABEL = "Sandcorp"
CTA_TEXT = "뉴스 수집·요약·게시를 자동화한 이 사이트처럼, 반복 업무를 자동화하고 싶으신가요?"
CTA_PORTFOLIO_LABEL = "Sandcorp 포트폴리오 보기"
CTA_CONTACT_LABEL = "문의하기"
CONTACT_PATH = "/contact"
FOOTER_CONTACT_LABEL = "문의"
SIDEBAR_SANDCORP_TEXT = "반복 업무를 자동화하는 팀. 이 사이트도 Sandcorp가 만들었습니다."
SIDEBAR_SANDCORP_LINK = "Sandcorp 알아보기"
SIDEBAR_CONTACT_LABEL = "문의하기"
SITE_MONOGRAM = "AT"
SECTION_COUNT = 4
MOST_READ_COUNT = 5


class CategoryData(TypedDict):
    name: str
    slug: str


class SectionData(TypedDict):
    category: CategoryData
    more_url: str
    articles: list["CardData"]


class SidebarData(TypedDict):
    most_read_title: str
    sandcorp_title: str
    sandcorp_text: str
    sandcorp_label: str
    sandcorp_url: str
    contact_label: str
    contact_url: str


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


class CommentaryOut(TypedDict):
    background: str
    significance: str
    outlook_bull: str
    outlook_bear: str
    opinion: str
    predictions: list[PredictionOut]


class DetailData(CardData):
    items: list[SourceItemData]
    commentary: CommentaryOut | None


class SiteData(TypedDict):
    name: str
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


def _sidebar() -> SidebarData:
    return {
        "most_read_title": "많이 읽은 기사",
        "sandcorp_title": SANDCORP_LABEL,
        "sandcorp_text": SIDEBAR_SANDCORP_TEXT,
        "sandcorp_label": SIDEBAR_SANDCORP_LINK,
        "sandcorp_url": outbound.go_path("sidebar"),
        "contact_label": SIDEBAR_CONTACT_LABEL,
        "contact_url": CONTACT_PATH,
    }


def _site() -> SiteData:
    return {
        "name": SITE_NAME,
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
        "published_at": article.created_at.isoformat(),
        "published_label": _relative_label(article.created_at),
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
        "published_label": _label(article.created_at),
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
        "predictions": [
            {
                "claim": p["claim"],
                "horizon": p["horizon"],
                "horizon_label": f"{p['horizon'][:-1]}개월 안",
                "basis": p["basis"],
            }
            for p in found.predictions
        ],
    }


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
        "site": _site(),
        "categories": [{"name": c.name, "slug": c.slug} for c in categories],
        "category": category,
        "featured": [_card(a) for a in featured],
        "articles": [_card(a) for a in page.object_list],
        "sections": sections,
        "most_read": [_card(a) for a in most_read],
        "sidebar": _sidebar(),
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
    data: dict[str, object] = {
        "kind": "contact",
        "site": _site(),
        "state": state,
        "form": form,
        "kinds": list(Inquiry.Kind.values),
    }
    meta = PageMeta(
        title=f"문의 - {SITE_NAME}", description="문의나 제안을 남겨 주세요.", robots=NOINDEX
    )
    body = render_to_string("core/public/contact.html", {"data": data, "site": _site()}, request)
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
