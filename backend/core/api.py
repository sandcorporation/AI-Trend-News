"""The ninja API. Endpoints stay thin: they validate input and call the service layer."""

from django.conf import settings
from django.db import connection
from django.db.utils import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import NinjaAPI, Query, Schema, Status

from core.admin_api import install_error_handlers
from core.admin_api import router as admin_router
from core.auth import install_auth_handlers
from core.shell import SITE_NAME
from services import articles as svc
from services.commentary import public_commentary

api = NinjaAPI(title="Sandcorp news API", docs_url=None)
# Protected endpoints use `auth=RequireScope("<scope>")` (core/auth.py).
install_auth_handlers(api)
install_error_handlers(api)
api.add_router("/admin", admin_router)


class Health(Schema):
    status: str
    db: str


@api.get("/health", response={200: Health, 503: Health}, auth=None)
def health(request: HttpRequest) -> Status[Health]:
    """Liveness plus a real round trip to the database (MySQL); the deploy waits on this."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        return Status(503, Health(status="error", db="unavailable"))
    return Status(200, Health(status="ok", db="ok"))


PUBLIC_MAX_LIMIT = 20
PUBLIC_CACHE = "public, max-age=300"


class PublicCategory(Schema):
    name: str
    slug: str


class PublicArticle(Schema):
    slug: str
    title: str
    summary: str
    category: PublicCategory
    image_url: str
    published_at: str
    url: str
    featured: bool
    has_commentary: bool


class PublicSite(Schema):
    name: str
    url: str


class PublicArticles(Schema):
    items: list[PublicArticle]
    site: PublicSite


class PublicArticlesQuery(Schema):
    limit: int = 5
    category: str = ""
    featured: bool = False


@api.get("/public/articles", response=PublicArticles, auth=None)
def public_articles(
    request: HttpRequest, response: HttpResponse, params: Query[PublicArticlesQuery]
) -> PublicArticles:
    """인증 없는 최근 기사 목록(서버 간 호출용, 포트폴리오 홈 등). CORS 헤더는 두지 않는다."""
    limit = min(max(params.limit, 1), PUBLIC_MAX_LIMIT)
    found = svc.public_recent(limit, params.category, params.featured)
    response["Cache-Control"] = PUBLIC_CACHE
    return PublicArticles(
        items=[
            PublicArticle(
                slug=a.slug,
                title=a.title,
                summary=a.summary,
                category=PublicCategory(name=a.category.name, slug=a.category.slug),
                image_url=a.image_url,
                published_at=timezone.localtime(a.published_at).isoformat(),
                url=f"{settings.SITE_URL}/a/{a.slug}",
                featured=a.featured,
                has_commentary=public_commentary(a) is not None,
            )
            for a in found
        ],
        site=PublicSite(name=SITE_NAME, url=settings.SITE_URL),
    )
