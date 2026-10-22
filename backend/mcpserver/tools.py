"""MCP tools: thin shells over `services/`. A tool validates its input and shapes its output.

Add a tool with `@tool(name, title, description, InputModel, scope=..., read_only=...)` on a
function `(token: AccessToken, args: InputModel) -> dict[str, JSON]`. The input model (Pydantic,
`extra="forbid"`) becomes the tool's `inputSchema`; raise `ToolError` for a failure the caller
should read (it becomes an `isError` result, not a protocol error). Service errors
(`SERVICE_ERRORS`) become `ToolError` on the way out, so a tool just calls the service.

Every tool runs behind the `/mcp` bearer check (scope `editor`). A tool registered with
`scope="admin"` is listed and callable only when the token also carries `admin`; to any other
token it does not exist. Editor tools act as `Actor.EDITOR` (the token's `sub` is always the
owner, so the actor follows the scope, not the subject).

Same shape as the portfolio repo's `mcpserver/tools.py`; the scope filter and `read_only` are
additions.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from articles.models import Article
from mcpserver.jsontypes import JSON
from services import articles, collection, editor_runs
from services.articles import Actor, ArticleError
from services.editor_runs import EditorRunError
from services.tokens import AccessToken, Scope

type ToolResult = dict[str, JSON]

SERVICE_ERRORS: tuple[type[Exception], ...] = (ArticleError, EditorRunError)


class ToolError(Exception):
    """A tool-level failure; the message is shown to the MCP client."""


class RegisteredTool(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def scope(self) -> Scope: ...

    def definition(self) -> dict[str, JSON]: ...

    def call(self, token: AccessToken, arguments: dict[str, JSON]) -> ToolResult: ...


@dataclass(frozen=True)
class Tool[M: BaseModel]:
    name: str
    title: str
    description: str
    input_model: type[M]
    handler: Callable[[AccessToken, M], ToolResult]
    scope: Scope
    read_only: bool

    def definition(self) -> dict[str, JSON]:
        schema: dict[str, JSON] = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {"readOnlyHint": self.read_only},
        }

    def call(self, token: AccessToken, arguments: dict[str, JSON]) -> ToolResult:
        args = self.input_model.model_validate(arguments)
        try:
            return self.handler(token, args)
        except SERVICE_ERRORS as exc:
            raise ToolError(str(exc)) from exc


TOOLS: dict[str, RegisteredTool] = {}


def tool[M: BaseModel](
    name: str,
    title: str,
    description: str,
    input_model: type[M],
    *,
    scope: Scope = "editor",
    read_only: bool = False,
) -> Callable[[Callable[[AccessToken, M], ToolResult]], Callable[[AccessToken, M], ToolResult]]:
    def register(
        handler: Callable[[AccessToken, M], ToolResult],
    ) -> Callable[[AccessToken, M], ToolResult]:
        if name in TOOLS:
            raise ValueError(f"MCP tool {name!r} is registered twice")
        TOOLS[name] = Tool(name, title, description, input_model, handler, scope, read_only)
        return handler

    return register


def visible_tools(token: AccessToken) -> list[RegisteredTool]:
    """The tools this token may see and call (registration order)."""
    return [registered for registered in TOOLS.values() if registered.scope in token.scopes]


def visible_tool(name: str, token: AccessToken) -> RegisteredTool | None:
    registered = TOOLS.get(name)
    if registered is None or registered.scope not in token.scopes:
        return None
    return registered


# --- 입력 모델 ---


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoInput(_Input):
    pass


class PageInput(_Input):
    page: int = Field(default=1, ge=1, description="1부터 시작하는 쪽 번호")
    page_size: int = Field(default=20, ge=1, le=100, description="한 쪽의 수집물 수(최대 100)")


class DaysInput(_Input):
    days: int = Field(default=7, ge=1, le=90, description="오늘부터 거슬러 볼 일수(최대 90)")


class CreateArticleInput(_Input):
    item_ids: list[int] = Field(min_length=1, description="묶을 미처리 수집물 id들(하나 이상)")
    title: str = Field(min_length=1, max_length=300, description="한국어 제목")
    summary: str = Field(description="한국어 요약")
    category: str = Field(description="분야 slug(list_categories로 확인)")
    tags: list[str] = Field(default_factory=list, description="태그 이름들")
    published: bool = Field(default=False, description="게시 여부")
    featured: bool = Field(default=False, description="추천 여부")
    image_url: str = Field(default="", max_length=2000, description="대표 이미지 URL(없으면 빈 값)")


class UpdateArticleInput(_Input):
    id: int = Field(description="기사 id")
    add_item_ids: list[int] = Field(default_factory=list, description="더 묶을 미처리 수집물 id들")
    title: str | None = Field(default=None, min_length=1, max_length=300, description="새 제목")
    summary: str | None = Field(default=None, description="새 요약")
    tags: list[str] | None = Field(default=None, description="태그 전체를 이 목록으로 바꾼다")
    image_url: str | None = Field(default=None, max_length=2000, description="새 대표 이미지 URL")


class SetFlagsInput(_Input):
    id: int = Field(description="기사 id")
    published: bool | None = Field(default=None, description="게시 여부(생략하면 그대로)")
    featured: bool | None = Field(default=None, description="추천 여부(생략하면 그대로)")


class DiscardItemInput(_Input):
    item_id: int = Field(description="버릴 미처리 수집물 id")


class AddCategoryInput(_Input):
    name: str = Field(min_length=1, max_length=50, description="새 분야 이름(예: 경제)")


class FinishRunInput(_Input):
    run_id: int = Field(description="start_editor_run이 돌려준 실행 id")
    created: int = Field(ge=0, description="이번 실행에서 만든 기사 수")
    updated: int = Field(ge=0, description="이번 실행에서 갱신한 기사 수")
    discarded: int = Field(ge=0, description="이번 실행에서 버린 수집물 수")
    note: str = Field(default="", description="메모(대표가 admin에서 읽는다)")


# --- 응답 모양 ---


def _article(article: Article) -> ToolResult:
    return {
        "id": article.pk,
        "title": article.title,
        "slug": article.slug,
        "category": article.category.slug,
        "published": article.published,
        "featured": article.featured,
        "items": [
            {"id": link.item.pk, "url": link.item.url} for link in article.article_items.all()
        ],
    }


# --- 편집자 도구 ---


@tool(
    "whoami",
    "Who am I",
    "이 연결이 누구로 동작하는지: 토큰의 sub·client·스코프·만료 시각.",
    NoInput,
    read_only=True,
)
def whoami(token: AccessToken, args: NoInput) -> ToolResult:
    return {
        "sub": token.sub,
        "client_id": token.client_id,
        "scopes": [scope for scope in sorted(token.scopes)],
        "expires_at": token.exp,
    }


@tool(
    "list_pending_items",
    "미처리 수집물",
    "아직 기사로 묶이지도 버려지지도 않은 수집물을 수집 시각이 오래된 것부터 한 쪽씩. "
    "소스 이름·수집 시각·발행 시각·제목·발췌·원문 URL·수집물 id와 전체 건수를 준다.",
    PageInput,
    read_only=True,
)
def list_pending_items(token: AccessToken, args: PageInput) -> ToolResult:
    found = collection.list_pending_items(args.page, args.page_size)
    return {
        "total": found.total,
        "page": found.page,
        "page_size": found.page_size,
        "items": [
            {
                "id": item.pk,
                "source": item.source.name,
                "collected_at": item.collected_at.isoformat(),
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "title": item.title,
                "excerpt": item.excerpt,
                "url": item.url,
            }
            for item in found.items
        ],
    }


@tool(
    "list_recent_articles",
    "최근 기사",
    "최근 days일 안에 만든 기사(게시 여부 무관) 최신순: id·제목·slug·분야 slug·게시·추천·"
    "묶인 수집물(id, URL). 같은 사건의 새 수집물을 기존 기사에 더할지 판단할 때 쓴다.",
    DaysInput,
    read_only=True,
)
def list_recent_articles(token: AccessToken, args: DaysInput) -> ToolResult:
    return {"articles": [_article(a) for a in articles.recent_articles(args.days)]}


@tool(
    "create_article",
    "기사 생성",
    "미처리 수집물 하나 이상을 묶어 기사를 만든다. 분야는 slug. 이미 다른 기사에 묶인 수집물이면 "
    "그 기사를 알려 주는 오류가 난다.",
    CreateArticleInput,
)
def create_article(token: AccessToken, args: CreateArticleInput) -> ToolResult:
    article = articles.create_article(
        Actor.EDITOR,
        item_ids=args.item_ids,
        title=args.title,
        summary=args.summary,
        category=args.category,
        tags=args.tags,
        published=args.published,
        featured=args.featured,
        image_url=args.image_url,
    )
    return _article(article)


@tool(
    "update_article",
    "기사 갱신",
    "기사에 수집물을 더 묶거나 제목·요약·태그·대표 이미지를 고친다. 생략한 값은 그대로. "
    "slug와 게시·추천은 바뀌지 않는다(게시·추천은 set_article_flags).",
    UpdateArticleInput,
)
def update_article(token: AccessToken, args: UpdateArticleInput) -> ToolResult:
    article = articles.update_article(
        Actor.EDITOR,
        args.id,
        add_item_ids=args.add_item_ids,
        title=args.title,
        summary=args.summary,
        tags=args.tags,
        image_url=args.image_url,
    )
    return _article(article)


@tool(
    "set_article_flags",
    "게시·추천 변경",
    "기사의 게시·추천을 바꾼다. 대표가 admin에서 정한 값은 편집자가 바꿀 수 없다(오류).",
    SetFlagsInput,
)
def set_article_flags(token: AccessToken, args: SetFlagsInput) -> ToolResult:
    article = articles.set_flags(
        Actor.EDITOR, args.id, published=args.published, featured=args.featured
    )
    return _article(article)


@tool(
    "discard_item",
    "수집물 버림",
    "기사로 만들지 않을 미처리 수집물을 버린다. 기사에 묶인 수집물은 버릴 수 없다.",
    DiscardItemInput,
)
def discard_item(token: AccessToken, args: DiscardItemInput) -> ToolResult:
    item = articles.discard_item(Actor.EDITOR, args.item_id)
    return {"id": item.pk, "status": item.status}


@tool(
    "list_categories",
    "분야 목록",
    "기사에 고를 수 있는 분야(이름, slug) 목록, 탭 순서대로.",
    NoInput,
    read_only=True,
)
def list_categories(token: AccessToken, args: NoInput) -> ToolResult:
    return {"categories": [{"name": c.name, "slug": c.slug} for c in articles.list_categories()]}


@tool(
    "add_category",
    "분야 추가",
    "새 분야를 맨 뒤에 더하고 slug를 돌려준다. 같은 이름이 있으면 오류.",
    AddCategoryInput,
)
def add_category(token: AccessToken, args: AddCategoryInput) -> ToolResult:
    category = articles.add_category(args.name)
    return {"name": category.name, "slug": category.slug}


@tool(
    "start_editor_run",
    "편집 실행 시작",
    "편집 실행을 시작하고 run_id를 받는다. 일을 마치면 finish_editor_run으로 건수를 보고한다. "
    "보고 없이 24시간이 지난 실행은 미실행으로 본다.",
    NoInput,
)
def start_editor_run(token: AccessToken, args: NoInput) -> ToolResult:
    run = editor_runs.start_run()
    return {"run_id": run.pk, "started_at": run.started_at.isoformat()}


@tool(
    "finish_editor_run",
    "편집 실행 종료",
    "편집 실행을 끝내며 만든 기사·갱신한 기사·버린 수집물 건수와 메모를 보고한다.",
    FinishRunInput,
)
def finish_editor_run(token: AccessToken, args: FinishRunInput) -> ToolResult:
    run = editor_runs.finish_run(
        args.run_id,
        created=args.created,
        updated=args.updated,
        discarded=args.discarded,
        note=args.note,
    )
    assert run.finished_at is not None
    return {
        "run_id": run.pk,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
        "created": run.created,
        "updated": run.updated,
        "discarded": run.discarded,
    }
