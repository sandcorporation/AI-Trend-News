"""관리 기능의 입력·출력 스키마. 관리자 API(`core/admin_api.py`)와 MCP 관리 도구가 같이 쓴다."""

from typing import Literal, Self

from ninja import Schema
from pydantic import ConfigDict, Field

from articles.models import Article, Category
from collection.models import Source


class Strict(Schema):
    model_config = ConfigDict(extra="forbid")


# --- 입력 ---


class SourceCreate(Strict):
    name: str = Field(min_length=1, max_length=100, description="소스 이름")
    kind: Literal["rss", "html"] = Field(description="종류: rss 또는 html")
    url: str = Field(max_length=2000, description="피드(rss) 또는 목록 페이지(html) 주소")
    html_rules: dict[str, str] = Field(
        default_factory=dict,
        description="kind=html일 때 필수. 키 item·link·title·published(CSS 선택자), 선택 excerpt",
    )
    enabled: bool = Field(default=True, description="켜짐(꺼진 소스는 수집하지 않는다)")


class SourceChange(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=100, description="새 이름")
    kind: Literal["rss", "html"] | None = Field(default=None, description="새 종류")
    url: str | None = Field(default=None, max_length=2000, description="새 주소")
    html_rules: dict[str, str] | None = Field(default=None, description="새 HTML 규칙(통째로 교체)")
    enabled: bool | None = Field(default=None, description="켜짐 여부")


class EnabledChange(Strict):
    enabled: bool = Field(description="켜려면 true, 끄려면 false")


class CategoryChange(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=50, description="새 이름")
    order: int | None = Field(default=None, ge=0, description="새 순서(작을수록 앞)")


class CategoryOrder(Strict):
    slugs: list[str] = Field(description="모든 분야의 slug를 원하는 탭 순서대로 한 번씩")


class FlagsChange(Strict):
    published: bool | None = Field(default=None, description="게시 여부(생략하면 그대로)")
    featured: bool | None = Field(default=None, description="추천 여부(생략하면 그대로)")


class ArticleTextChange(Strict):
    title: str | None = Field(default=None, min_length=1, max_length=300, description="새 제목")
    summary: str | None = Field(default=None, description="새 요약")


# --- 출력 ---


class SourceOut(Schema):
    id: int
    name: str
    kind: str
    url: str
    html_rules: dict[str, str]
    enabled: bool
    last_collected_at: str | None
    last_result: str
    last_succeeded: bool | None
    consecutive_failures: int

    @classmethod
    def of(cls, source: Source) -> Self:
        return cls(
            id=source.pk,
            name=source.name,
            kind=source.kind,
            url=source.url,
            html_rules={str(k): str(v) for k, v in source.html_rules.items()},
            enabled=source.enabled,
            last_collected_at=(
                source.last_collected_at.isoformat() if source.last_collected_at else None
            ),
            last_result=source.last_result,
            last_succeeded=source.last_succeeded,
            consecutive_failures=source.consecutive_failures,
        )


class CategoryOut(Schema):
    name: str
    slug: str
    order: int

    @classmethod
    def of(cls, category: Category) -> Self:
        return cls(name=category.name, slug=category.slug, order=category.order)


class ItemRef(Schema):
    id: int
    url: str


class ArticleOut(Schema):
    id: int
    title: str
    summary: str
    slug: str
    category: str
    published: bool
    featured: bool
    published_overridden: bool
    featured_overridden: bool
    items: list[ItemRef]

    @classmethod
    def of(cls, article: Article) -> Self:
        return cls(
            id=article.pk,
            title=article.title,
            summary=article.summary,
            slug=article.slug,
            category=article.category.slug,
            published=article.published,
            featured=article.featured,
            published_overridden=article.published_overridden,
            featured_overridden=article.featured_overridden,
            items=[
                ItemRef(id=link.item.pk, url=link.item.url) for link in article.article_items.all()
            ],
        )
