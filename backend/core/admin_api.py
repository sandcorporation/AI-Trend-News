"""관리자 API(`/api/admin/...`, 스코프 `admin`). MCP 관리 도구와 같은 서비스 함수를 부른다."""

from http import HTTPStatus

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI, Router

from adapters.clock import get_clock
from core.admin_schemas import (
    ArticleOut,
    ArticleTextChange,
    CategoryChange,
    CategoryDeleted,
    CategoryOrder,
    CategoryOut,
    EnabledChange,
    FlagsChange,
    PredictionJudge,
    PredictionRowOut,
    SourceChange,
    SourceCreate,
    SourceOut,
)
from core.auth_adapter import scope_auth
from services import articles, posts, sources
from services import predictions as predictions_svc
from services.articles import Actor, ArticleError
from services.commentary import CommentaryError
from services.posts import PostError
from services.predictions import PredictionError
from services.sources import SourceError

router = Router(auth=scope_auth("admin"), tags=["admin"])


def install_error_handlers(api: NinjaAPI) -> None:
    """서비스 오류를 JSON 응답으로: 없는 것은 404, 그 밖의 규칙 위반은 400."""

    def respond(request: HttpRequest, exc: Exception, missing: bool) -> HttpResponse:
        status = HTTPStatus.NOT_FOUND if missing else HTTPStatus.BAD_REQUEST
        return api.create_response(request, {"detail": str(exc)}, status=status)

    def article_error(request: HttpRequest, exc: ArticleError | type[ArticleError]) -> HttpResponse:
        assert isinstance(exc, ArticleError)
        return respond(
            request, exc, isinstance(exc, articles.ArticleNotFound | articles.CategoryNotFound)
        )

    def source_error(request: HttpRequest, exc: SourceError | type[SourceError]) -> HttpResponse:
        assert isinstance(exc, SourceError)
        return respond(request, exc, isinstance(exc, sources.SourceNotFound))

    def post_error(request: HttpRequest, exc: PostError | type[PostError]) -> HttpResponse:
        assert isinstance(exc, PostError)
        return respond(request, exc, isinstance(exc, posts.PostNotFound))

    def commentary_error(
        request: HttpRequest, exc: CommentaryError | type[CommentaryError]
    ) -> HttpResponse:
        assert isinstance(exc, CommentaryError)
        return respond(request, exc, False)

    def prediction_error(
        request: HttpRequest, exc: PredictionError | type[PredictionError]
    ) -> HttpResponse:
        assert isinstance(exc, PredictionError)
        return respond(request, exc, isinstance(exc, predictions_svc.PredictionNotFound))

    api.add_exception_handler(PredictionError, prediction_error)
    api.add_exception_handler(ArticleError, article_error)
    api.add_exception_handler(CommentaryError, commentary_error)
    api.add_exception_handler(PostError, post_error)
    api.add_exception_handler(SourceError, source_error)


# --- 소스 ---


@router.get("/sources", response=list[SourceOut])
def list_sources(request: HttpRequest) -> list[SourceOut]:
    return [SourceOut.of(s) for s in sources.list_sources()]


@router.post("/sources", response={201: SourceOut})
def create_source(request: HttpRequest, body: SourceCreate) -> tuple[int, SourceOut]:
    return 201, SourceOut.of(sources.create_source(**body.model_dump()))


@router.patch("/sources/{source_id}", response=SourceOut)
def update_source(request: HttpRequest, source_id: int, body: SourceChange) -> SourceOut:
    return SourceOut.of(sources.update_source(source_id, **body.model_dump()))


@router.put("/sources/{source_id}/enabled", response=SourceOut)
def set_source_enabled(request: HttpRequest, source_id: int, body: EnabledChange) -> SourceOut:
    return SourceOut.of(sources.set_source_enabled(source_id, body.enabled))


# --- 분야 ---


@router.put("/categories/order", response=list[CategoryOut])
def reorder_categories(request: HttpRequest, body: CategoryOrder) -> list[CategoryOut]:
    return [CategoryOut.of(c) for c in articles.reorder_categories(body.slugs)]


@router.patch("/categories/{slug}", response=CategoryOut)
def update_category(request: HttpRequest, slug: str, body: CategoryChange) -> CategoryOut:
    return CategoryOut.of(articles.update_category(slug, **body.model_dump()))


@router.delete("/categories/{slug}", response=CategoryDeleted)
def delete_category(request: HttpRequest, slug: str, confirm: bool = False) -> CategoryDeleted:
    """기사가 있으면 confirm=true일 때만 기사도 함께 지운다."""
    count = articles.delete_category(slug, confirm=confirm)
    return CategoryDeleted(deleted=slug, deleted_articles=count)


# --- 기사 ---


@router.put("/articles/{article_id}/flags", response=ArticleOut)
def set_article_flags(request: HttpRequest, article_id: int, body: FlagsChange) -> ArticleOut:
    article = articles.set_flags(Actor.OWNER, article_id, **body.model_dump())
    return ArticleOut.of(article)


@router.patch("/articles/{article_id}", response=ArticleOut)
def update_article(request: HttpRequest, article_id: int, body: ArticleTextChange) -> ArticleOut:
    article = articles.update_article(Actor.OWNER, article_id, **body.model_dump())
    return ArticleOut.of(article)


# --- 전망 기록표 ---


@router.patch("/predictions/{prediction_id}", response=PredictionRowOut)
def judge_prediction(
    request: HttpRequest, prediction_id: int, body: PredictionJudge
) -> PredictionRowOut:
    row = predictions_svc.judge_prediction(
        prediction_id, body.verdict, body.note, now=get_clock().now()
    )
    return PredictionRowOut.of(row)
