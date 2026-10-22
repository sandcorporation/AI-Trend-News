"""기사·분야·태그 규칙(#6). admin·ninja·MCP가 같은 함수를 부른다.

주체(`Actor`)는 편집자 또는 대표다. 오류는 `ArticleError`의 하위 클래스이고
메시지가 구체적이라 MCP가 그대로 돌려줄 수 있다.
"""

import secrets
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import Enum

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.text import slugify

from articles.models import Article, ArticleItem, Category, Tag
from collection.models import Item


class Actor(Enum):
    EDITOR = "editor"
    OWNER = "owner"


class ArticleError(Exception):
    """기사 규칙 위반의 공통 부모."""


class CategoryNotFound(ArticleError):
    pass


class CategoryExists(ArticleError):
    pass


class ItemNotFound(ArticleError):
    pass


class ItemAlreadyBundled(ArticleError):
    pass


class ItemNotPending(ArticleError):
    pass


class EmptyBundle(ArticleError):
    pass


class FlagOverridden(ArticleError):
    pass


class ArticleNotFound(ArticleError):
    pass


def make_slug(text: str, exists: "type[Category] | type[Article]", fallback: str) -> str:
    """텍스트에서 slug를 만든다. 한글처럼 비어 버리면 접두어+무작위 접미, 충돌해도 접미를 붙인다."""
    base = slugify(text)[:40].strip("-")
    candidate = base or f"{fallback}-{secrets.token_hex(3)}"
    while exists.objects.filter(slug=candidate).exists():
        candidate = f"{base or fallback}-{secrets.token_hex(3)}"
    return candidate


# --- 분야 ---


def list_categories() -> list[Category]:
    return list(Category.objects.all())


def add_category(name: str, order: int | None = None) -> Category:
    name = name.strip()
    if not name:
        raise ArticleError("분야 이름이 비어 있습니다.")
    if Category.objects.filter(name=name).exists():
        raise CategoryExists(f"이미 있는 분야입니다: {name}")
    if order is None:
        last = Category.objects.order_by("-order").first()
        order = last.order + 1 if last else 0
    return Category.objects.create(
        name=name, order=order, slug=make_slug(name, Category, "category")
    )


def get_category(slug: str) -> Category:
    try:
        return Category.objects.get(slug=slug)
    except Category.DoesNotExist:
        known = ", ".join(c.slug for c in Category.objects.all()) or "없음"
        raise CategoryNotFound(f"없는 분야입니다: {slug} (있는 분야: {known})") from None


# --- 기사 ---


def _tags(names: Iterable[str]) -> list[Tag]:
    cleaned = dict.fromkeys(n.strip() for n in names if n.strip())
    return [Tag.objects.get_or_create(name=n)[0] for n in cleaned]


def _bind(article: Article, item_ids: Iterable[int]) -> None:
    ids = list(dict.fromkeys(item_ids))
    items = {i.pk: i for i in Item.objects.select_for_update().filter(pk__in=ids)}
    missing = [i for i in ids if i not in items]
    if missing:
        raise ItemNotFound(f"없는 수집물입니다: {', '.join(map(str, missing))}")
    for item_id in ids:
        link = ArticleItem.objects.filter(item_id=item_id).select_related("article").first()
        if link is not None:
            other = link.article
            raise ItemAlreadyBundled(
                f"수집물 {item_id}은(는) 이미 기사 {other.pk}"
                f"(「{other.title}」, {other.slug})에 묶여 있습니다."
            )
        if items[item_id].status == Item.Status.DISCARDED:
            raise ItemNotPending(f"수집물 {item_id}은(는) 버려진 수집물이라 묶을 수 없습니다.")
    for item in items.values():
        ArticleItem.objects.create(article=article, item=item)
        item.status = Item.Status.BUNDLED
        item.save(update_fields=["status"])


@transaction.atomic
def create_article(
    actor: Actor,
    *,
    item_ids: Iterable[int],
    title: str,
    summary: str,
    category: str,
    tags: Iterable[str] = (),
    published: bool = False,
    featured: bool = False,
    image_url: str = "",
) -> Article:
    """수집물들을 묶어 기사를 만든다. category는 분야 slug. 처음 정하는 플래그는 덮어씀이 아니다."""
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise EmptyBundle("기사에는 수집물이 하나 이상 필요합니다.")
    cat = get_category(category)
    article = Article.objects.create(
        title=title,
        summary=summary,
        category=cat,
        published=published,
        featured=featured,
        image_url=image_url,
        slug=make_slug(title, Article, "news"),
    )
    _bind(article, ids)
    article.tags.set(_tags(tags))
    return article


def get_article(article_id: int) -> Article:
    try:
        return Article.objects.get(pk=article_id)
    except Article.DoesNotExist:
        raise ArticleNotFound(f"없는 기사입니다: {article_id}") from None


@transaction.atomic
def update_article(
    actor: Actor,
    article_id: int,
    *,
    add_item_ids: Iterable[int] = (),
    title: str | None = None,
    summary: str | None = None,
    tags: Iterable[str] | None = None,
    image_url: str | None = None,
) -> Article:
    """slug와 플래그는 건드리지 않는다(플래그는 set_flags)."""
    article = get_article(article_id)
    _bind(article, add_item_ids)
    if title is not None:
        article.title = title
    if summary is not None:
        article.summary = summary
    if image_url is not None:
        article.image_url = image_url
    article.save()
    if tags is not None:
        article.tags.set(_tags(tags))
    return article


@transaction.atomic
def set_flags(
    actor: Actor,
    article_id: int,
    *,
    published: bool | None = None,
    featured: bool | None = None,
) -> Article:
    """게시·추천 변경. 편집자는 대표가 덮어쓴 플래그를 못 바꾸고, 대표가 바꾸면 표시가 남는다."""
    article = get_article(article_id)
    for name, value in (("published", published), ("featured", featured)):
        if value is None or getattr(article, name) == value:
            continue
        label = "게시" if name == "published" else "추천"
        if actor is Actor.EDITOR:
            if getattr(article, f"{name}_overridden"):
                raise FlagOverridden(
                    f"기사 {article.pk}의 {label}은(는) 대표가 정했으므로 "
                    "편집자가 바꿀 수 없습니다."
                )
        else:
            setattr(article, f"{name}_overridden", True)
        setattr(article, name, value)
    article.save()
    return article


def recent_articles(days: int, now: datetime | None = None) -> list[Article]:
    """최근 days일 안에 만든 기사(게시 여부 무관) 최신순. 묶인 수집물을 함께 읽는다."""
    since = (now or timezone.now()) - timedelta(days=days)
    return list(
        Article.objects.filter(created_at__gte=since)
        .select_related("category")
        .prefetch_related("article_items__item")
    )


def delete_article(article_id: int) -> None:
    """수집물은 미처리로 돌아가고 SeenUrl은 그대로다(pre_delete 신호)."""
    get_article(article_id).delete()


# --- 수집물 ---


def discard_item(actor: Actor, item_id: int) -> Item:
    try:
        item = Item.objects.get(pk=item_id)
    except Item.DoesNotExist:
        raise ItemNotFound(f"없는 수집물입니다: {item_id}") from None
    if item.status == Item.Status.BUNDLED:
        link = ArticleItem.objects.select_related("article").get(item=item)
        raise ItemAlreadyBundled(
            f"수집물 {item_id}은(는) 기사 {link.article.pk}"
            f"(「{link.article.title}」)에 묶여 있어 버릴 수 없습니다."
        )
    item.status = Item.Status.DISCARDED
    item.save(update_fields=["status"])
    return item


# --- 공개 조회 (방문자용: 반드시 게시된 기사만) ---
# 추천은 게시를 강제하지 않으므로 모든 공개 조회가 published=True 를 직접 건다.


def _public() -> QuerySet[Article]:
    return (
        Article.objects.filter(published=True).select_related("category").prefetch_related("tags")
    )


def public_featured(limit: int, category: str = "") -> list[Article]:
    """추천이면서 게시된 기사, 최신순 limit개. category(slug)가 있으면 그 분야만."""
    qs = _public().filter(featured=True)
    if category:
        qs = qs.filter(category__slug=category)
    return list(qs[:limit])


def public_list(category: str = "") -> QuerySet[Article]:
    """게시된 기사 최신순. category(slug)가 있으면 그 분야만. 페이지는 호출부가 자른다."""
    qs = _public()
    return qs.filter(category__slug=category) if category else qs


def public_article(slug: str) -> Article:
    """게시된 기사 하나. 없거나 게시 전이면 ArticleNotFound. 묶인 수집물과 소스를 함께 읽는다."""
    try:
        return _public().prefetch_related("article_items__item__source").get(slug=slug)
    except Article.DoesNotExist:
        raise ArticleNotFound(f"게시된 기사가 없습니다: {slug}") from None


def public_sitemap() -> QuerySet[Article]:
    """사이트맵용: 게시된 기사만(slug, 수정 시각)."""
    return Article.objects.filter(published=True).only("slug", "updated_at")


def public_categories() -> list[Category]:
    """탭으로 보일 분야(order 순). 게시된 기사가 없는 분야도 포함한다."""
    return list_categories()
