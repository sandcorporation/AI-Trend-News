"""기사·분야·태그 규칙(#6). admin·ninja·MCP가 같은 함수를 부른다.

주체(`Actor`)는 편집자 또는 대표다. 오류는 `ArticleError`의 하위 클래스이고
메시지가 구체적이라 MCP가 그대로 돌려줄 수 있다.
"""

import logging
import secrets
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from enum import Enum

from django.db import transaction
from django.db.models import Count, F, Max, Min, Q, QuerySet, Sum, Window
from django.db.models.functions import RowNumber
from django.utils import timezone
from django.utils.text import slugify

from articles.models import (
    ARTICLE_SLUG_RE,
    CATEGORY_SLUG_RE,
    Article,
    ArticleItem,
    ArticleSlugAlias,
    ArticleView,
    Category,
    CategorySlugAlias,
    Tag,
)
from collection.models import Item
from services.outbound import is_bot


class Actor(Enum):
    EDITOR = "editor"
    OWNER = "owner"


class ArticleError(Exception):
    """기사 규칙 위반의 공통 부모."""


class CategoryNotFound(ArticleError):
    pass


class CategoryExists(ArticleError):
    pass


class InvalidCategorySlug(ArticleError):
    pass


class InvalidArticleSlug(ArticleError):
    pass


class CategoryHasArticles(ArticleError):
    pass


class CategoryOrderMismatch(ArticleError):
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


def make_slug(text: str, exists: type[Category] | type[Article], fallback: str) -> str:
    """텍스트에서 slug를 만든다. 라틴 문자가 3자 미만이면 접두어+무작위 접미, 충돌해도 접미."""
    base = slugify(text)[:40].strip("-")
    if sum(c.isalpha() for c in base) < 3:
        base = ""
    candidate = base or f"{fallback}-{secrets.token_hex(3)}"
    while _slug_taken(exists, candidate):
        candidate = f"{base or fallback}-{secrets.token_hex(3)}"
    return candidate


def _slug_taken(model: type[Category] | type[Article], slug: str) -> bool:
    if model.objects.filter(slug=slug).exists():
        return True
    return model is Article and ArticleSlugAlias.objects.filter(slug=slug).exists()


def unique_article_slug(slug: str) -> str:
    """편집자가 준 slug를 검증하고, 이미 있으면 -2, -3을 붙여 유일하게 만든다."""
    slug = slug.strip()
    if not 3 <= len(slug) <= 60 or not ARTICLE_SLUG_RE.fullmatch(slug):
        raise InvalidArticleSlug(
            "slug는 라틴 소문자·숫자·하이픈 3~60자이고 첫 글자는 소문자여야 합니다"
            f"(앞뒤·연속 하이픈 불가): {slug!r}"
        )
    candidate, n = slug, 2
    while _slug_taken(Article, candidate):
        suffix = f"-{n}"
        candidate = f"{slug[: 60 - len(suffix)].rstrip('-')}{suffix}"
        n += 1
    return candidate


def find_article_by_old_slug(slug: str) -> Article | None:
    """옛 slug로 들어온 요청의 현재 기사(게시된 것만). 없으면 None."""
    alias = ArticleSlugAlias.objects.select_related("article").filter(slug=slug).first()
    if alias is None or not alias.article.published:
        return None
    return alias.article


def record_article_slug_change(article: Article, new_slug: str) -> None:
    """article.slug를 new_slug로 바꾸고 옛 slug를 기록한다(저장은 호출부)."""
    ArticleSlugAlias.objects.filter(slug=new_slug).delete()
    if article.slug:
        ArticleSlugAlias.objects.get_or_create(slug=article.slug, article=article)
    article.slug = new_slug


# --- 분야 ---


def list_categories() -> list[Category]:
    return list(Category.objects.all())


def _check_new_slug(slug: str, *, exclude: Category | None = None) -> str:
    """대표가 지정한 slug 검증: 형식, 다른 분야의 현재·옛 slug와 겹치지 않음."""
    slug = slug.strip()
    if not 1 <= len(slug) <= 60 or not CATEGORY_SLUG_RE.fullmatch(slug):
        raise InvalidCategorySlug(
            f"slug는 라틴 소문자·숫자·하이픈 1~60자여야 합니다(앞뒤·연속 하이픈 불가): {slug!r}"
        )
    taken = Category.objects.filter(slug=slug)
    aliased = CategorySlugAlias.objects.filter(slug=slug)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
        aliased = aliased.exclude(category=exclude)
    if taken.exists() or aliased.exists():
        raise CategoryExists(f"이미 쓰는 slug입니다: {slug}")
    return slug


def find_category_by_old_slug(slug: str) -> Category | None:
    """옛 slug로 들어온 요청의 현재 분야. 없으면 None."""
    alias = CategorySlugAlias.objects.select_related("category").filter(slug=slug).first()
    return alias.category if alias else None


def add_category(name: str, order: int | None = None, slug: str | None = None) -> Category:
    name = name.strip()
    if not name:
        raise ArticleError("분야 이름이 비어 있습니다.")
    if Category.objects.filter(name=name).exists():
        raise CategoryExists(f"이미 있는 분야입니다: {name}")
    if order is None:
        last = Category.objects.order_by("-order").first()
        order = last.order + 1 if last else 0
    slug = _check_new_slug(slug) if slug else make_slug(name, Category, "category")
    return Category.objects.create(name=name, order=order, slug=slug)


def get_category(slug: str) -> Category:
    try:
        return Category.objects.get(slug=slug)
    except Category.DoesNotExist:
        known = ", ".join(c.slug for c in Category.objects.all()) or "없음"
        raise CategoryNotFound(f"없는 분야입니다: {slug} (있는 분야: {known})") from None


@transaction.atomic
def update_category(
    slug: str,
    *,
    name: str | None = None,
    order: int | None = None,
    new_slug: str | None = None,
) -> Category:
    """이름·순서·slug를 고친다. slug를 바꾸면 옛 값을 기록해 옛 링크가 301로 이어진다."""
    category = get_category(slug)
    if name is not None:
        name = name.strip()
        if not name:
            raise ArticleError("분야 이름이 비어 있습니다.")
        if Category.objects.filter(name=name).exclude(pk=category.pk).exists():
            raise CategoryExists(f"이미 있는 분야입니다: {name}")
        category.name = name
    if order is not None:
        category.order = order
    if new_slug is not None and new_slug.strip() != category.slug:
        record_slug_change(category, _check_new_slug(new_slug, exclude=category))
    category.save()
    return category


def record_slug_change(category: Category, new_slug: str) -> None:
    """category.slug를 new_slug로 바꾸고 옛 slug를 기록한다(저장은 호출부)."""
    CategorySlugAlias.objects.filter(slug=new_slug).delete()
    if category.slug:
        CategorySlugAlias.objects.get_or_create(slug=category.slug, category=category)
    category.slug = new_slug


@transaction.atomic
def delete_category(slug: str, *, confirm: bool = False) -> int:
    """분야를 지운다. 기사가 있으면 confirm=True일 때만 기사도 함께 지우고 지운 기사 수를 돌려준다.

    기사에서 풀린 수집물은 남기되 버림으로 바꾼다(편집자가 같은 수집물로 다시 묶지 않게).
    """
    category = get_category(slug)
    count = category.articles.count()
    if count and not confirm:
        raise CategoryHasArticles(
            f"분야 「{category.name}」에는 기사 {count}건이 있습니다. "
            f"confirm=true로 다시 부르면 기사 {count}건도 함께 지웁니다."
        )
    item_ids = list(
        ArticleItem.objects.filter(article__category=category).values_list("item_id", flat=True)
    )
    category.delete()
    Item.objects.filter(pk__in=item_ids).update(status=Item.Status.DISCARDED)
    return count


@transaction.atomic
def reorder_categories(slugs: Iterable[str]) -> list[Category]:
    """모든 분야의 slug를 원하는 순서로 한 번씩 준다. 빠지거나 모르는 slug가 있으면 오류."""
    wanted = list(slugs)
    existing = {c.slug: c for c in Category.objects.all()}
    unknown = sorted(set(wanted) - set(existing))
    missing = sorted(set(existing) - set(wanted))
    if unknown or missing or len(wanted) != len(set(wanted)):
        raise CategoryOrderMismatch(
            "모든 분야를 한 번씩 주어야 합니다. "
            f"모르는 slug: {', '.join(unknown) or '없음'}; "
            f"빠진 slug: {', '.join(missing) or '없음'}; "
            f"중복: {len(wanted) - len(set(wanted))}건"
        )
    for position, slug in enumerate(wanted):
        Category.objects.filter(pk=existing[slug].pk).update(order=position)
    return list_categories()


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


def _earliest_published(article: Article) -> datetime:
    """묶인 수집물 발행 시각 중 가장 이른 것(수집 시각보다 뒤는 제외). 없으면 created_at."""
    times = [
        t
        for t, collected in ArticleItem.objects.filter(
            article=article, item__published_at__isnull=False
        ).values_list("item__published_at", "item__collected_at")
        if t is not None and t <= collected
    ]
    return min(times) if times else article.created_at


def _sync_published_at(article: Article, *, only_earlier: bool) -> None:
    """발행 시각을 다시 계산한다. 대표가 덮어쓴 값은 두고, only_earlier면 앞당기기만 한다."""
    if article.published_at_overridden:
        return
    value = _earliest_published(article)
    if only_earlier and value >= article.published_at:
        return
    article.published_at = value


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
    slug: str | None = None,
) -> Article:
    """수집물들을 묶어 기사를 만든다. category는 분야 slug, slug가 없으면 제목에서 만든다.

    처음 정하는 플래그는 덮어씀이 아니다.
    """
    ids = list(dict.fromkeys(item_ids))
    if not ids:
        raise EmptyBundle("기사에는 수집물이 하나 이상 필요합니다.")
    cat = get_category(category)
    final_slug = (
        unique_article_slug(slug) if slug is not None else make_slug(title, Article, "news")
    )
    article = Article.objects.create(
        title=title,
        summary=summary,
        category=cat,
        published=published,
        featured=featured,
        image_url=image_url,
        slug=final_slug,
    )
    _bind(article, ids)
    _sync_published_at(article, only_earlier=False)
    article.save(update_fields=["published_at", "updated_at"])
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
    _sync_published_at(article, only_earlier=True)
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
        .select_related("category", "commentary")
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
        Article.objects.filter(published=True)
        .select_related("category", "commentary")
        .prefetch_related("tags")
    )


def public_featured(limit: int, category: str = "") -> list[Article]:
    """추천이면서 게시된 기사, 최신순 limit개. category(slug)가 있으면 그 분야만."""
    qs = _public().filter(featured=True)
    if category:
        qs = qs.filter(category__slug=category)
    return list(qs[:limit])


def public_recent(
    limit: int, category: str = "", featured: bool = False, commentary: bool = False
) -> list[Article]:
    """공개 JSON용 최신 기사(`-published_at, -id`). category는 현재·옛 slug 모두, 모르면 빈 목록.

    태그 prefetch 없이 select_related 한 번이라 쿼리 1개(분야 해석은 서브쿼리).
    """
    qs = Article.objects.filter(published=True).select_related("category", "commentary")
    if featured:
        qs = qs.filter(featured=True)
    if commentary:  # public_commentary와 같은 조건: 분석 존재·숨기지 않음(게시는 위에서 이미)
        qs = qs.filter(commentary__isnull=False, commentary__hidden=False)
    if category:
        matched = Category.objects.filter(Q(slug=category) | Q(old_slugs__slug=category)).values(
            "pk"
        )
        qs = qs.filter(category_id__in=matched)
    return list(qs.order_by("-published_at", "-id")[:limit])


def public_sections(per_category: int = 4) -> dict[int, list[Article]]:
    """분야 id별 게시된 최신 기사 per_category건(쿼리 하나 + 태그 prefetch)."""
    ranked = _public().annotate(
        rn=Window(
            RowNumber(),
            partition_by=F("category_id"),
            order_by=[F("published_at").desc(), F("id").desc()],
        )
    )
    result: dict[int, list[Article]] = {}
    for article in ranked.filter(rn__lte=per_category).order_by("-published_at", "-id"):
        result.setdefault(article.category_id, []).append(article)
    return result


def public_list(category: str = "") -> QuerySet[Article]:
    """게시된 기사 최신순. category(slug)가 있으면 그 분야만. 페이지는 호출부가 자른다."""
    qs = _public()
    return qs.filter(category__slug=category) if category else qs


def public_article(slug: str) -> Article:
    """게시된 기사 하나. 없거나 게시 전이면 ArticleNotFound. 묶인 수집물과 소스를 함께 읽는다."""
    try:
        return (
            _public()
            .prefetch_related("article_items__item__source", "commentary__prediction_rows")
            .get(slug=slug)
        )
    except Article.DoesNotExist:
        raise ArticleNotFound(f"게시된 기사가 없습니다: {slug}") from None


def public_sitemap() -> QuerySet[Article]:
    """사이트맵용: 게시된 기사만(slug, 수정 시각)."""
    return Article.objects.filter(published=True).only("slug", "updated_at")


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """서비스 시간대(KST)의 하루 [시작, 다음 날 시작)."""
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))


def public_day(day: date, category: str = "") -> list[Article]:
    """KST 날짜 `day`에 게시된 기사(`published_at` 기준), 최신순. `category`(slug)면 그 분야만."""
    start, end = day_bounds(day)
    qs = _public().filter(published_at__gte=start, published_at__lt=end)
    if category:
        qs = qs.filter(category__slug=category)
    return list(qs.order_by("-published_at", "-id"))


def public_day_stamp(day: date, category: str = "") -> tuple[int, datetime | None]:
    """그날(그 분야) 게시 기사 건수와 updated_at 최대값(카드 이미지 캐시 키)."""
    start, end = day_bounds(day)
    qs = Article.objects.filter(published=True, published_at__gte=start, published_at__lt=end)
    if category:
        qs = qs.filter(category__slug=category)
    agg = qs.aggregate(n=Count("pk"), latest=Max("updated_at"))
    return agg["n"], agg["latest"]


def public_first_day() -> date | None:
    """게시된 기사의 가장 이른 KST 날짜. 게시가 없으면 None."""
    first = Article.objects.filter(published=True).aggregate(m=Min("published_at"))["m"]
    return timezone.localtime(first).date() if first else None


def public_neighbor_days(day: date, category: str = "") -> tuple[date | None, date | None]:
    """기사가 있는(`category` slug가 있으면 그 분야 기사가 있는) 가장 가까운 이전·다음 KST 날짜."""
    start, end = day_bounds(day)
    published = Article.objects.filter(published=True)
    if category:
        published = published.filter(category__slug=category)
    before = published.filter(published_at__lt=start).aggregate(m=Max("published_at"))["m"]
    after = published.filter(published_at__gte=end).aggregate(m=Min("published_at"))["m"]
    return (
        timezone.localtime(before).date() if before else None,
        timezone.localtime(after).date() if after else None,
    )


def public_recent_days(days: int, now: datetime) -> list[tuple[date, datetime]]:
    """최근 days일 중 기사가 있는 KST 날짜와 그날 updated_at 최대값(사이트맵용), 최신 날짜부터."""
    since = timezone.make_aware(
        datetime.combine(timezone.localtime(now).date() - timedelta(days=days - 1), time.min)
    )
    latest: dict[date, datetime] = {}
    for published_at, updated_at in Article.objects.filter(
        published=True, published_at__gte=since
    ).values_list("published_at", "updated_at"):
        key = timezone.localtime(published_at).date()
        if key not in latest or updated_at > latest[key]:
            latest[key] = updated_at
    return sorted(latest.items(), reverse=True)


def public_recent_category_days(days: int, now: datetime) -> list[tuple[date, str, datetime]]:
    """최근 days일 중 분야별로 기사가 있는 (KST 날짜, 분야 slug, updated_at 최대값)(사이트맵용)."""
    since = timezone.make_aware(
        datetime.combine(timezone.localtime(now).date() - timedelta(days=days - 1), time.min)
    )
    latest: dict[tuple[date, str], datetime] = {}
    for published_at, updated_at, slug in Article.objects.filter(
        published=True, published_at__gte=since
    ).values_list("published_at", "updated_at", "category__slug"):
        key = (timezone.localtime(published_at).date(), slug)
        if key not in latest or updated_at > latest[key]:
            latest[key] = updated_at
    return [(d, slug, u) for (d, slug), u in sorted(latest.items(), reverse=True)]


def public_categories() -> list[Category]:
    """탭으로 보일 분야(order 순). 게시된 기사가 없는 분야도 포함한다."""
    return list_categories()


logger = logging.getLogger(__name__)


def record_view(article: Article, user_agent: str) -> None:
    """게시된 기사의 오늘(Asia/Seoul) 조회 수를 1 올린다. 봇은 제외, 실패는 삼킨다."""
    if not article.published or is_bot(user_agent):
        return
    try:
        view, _ = ArticleView.objects.get_or_create(article=article, date=timezone.localdate())
        ArticleView.objects.filter(pk=view.pk).update(count=F("count") + 1)
    except Exception:  # 기록이 페이지를 막으면 안 된다
        logger.exception("article view not recorded")


def public_most_read(days: int = 7, limit: int = 5, category: str = "") -> list[Article]:
    """최근 days일(오늘 포함) 조회 합계 내림차순, 동률은 최신순. 조회 없는 기사는 뺀다."""
    since = timezone.localdate() - timedelta(days=days - 1)
    qs = (
        _public()
        .annotate(read_count=Sum("views__count", filter=Q(views__date__gte=since)))
        .filter(read_count__gt=0)
        .order_by("-read_count", "-published_at", "-id")
    )
    if category:
        qs = qs.filter(category__slug=category)
    return list(qs[:limit])
