from datetime import timedelta

from django.contrib import admin
from django.db.models import Q, QuerySet, Sum
from django.forms import ModelForm
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone

from articles.models import Article, ArticleItem, Category, Commentary, OutboundClick, Tag
from services import articles as svc
from services import outbound


class ArticleItemInline(admin.TabularInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    model = ArticleItem
    extra = 0
    fields = ("item", "item_status", "item_url")
    readonly_fields = ("item", "item_status", "item_url")

    @admin.display(description="상태")
    def item_status(self, obj: ArticleItem) -> str:
        return obj.item.get_status_display()

    @admin.display(description="원문")
    def item_url(self, obj: ArticleItem) -> str:
        return obj.item.url

    def has_add_permission(self, request: HttpRequest, obj: Article | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: ArticleItem | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: ArticleItem | None = None) -> bool:
        return False


class CommentaryInline(admin.StackedInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """대표가 분석을 고치거나 숨긴다(편집자는 서비스로만 쓴다)."""

    model = Commentary
    extra = 0
    max_num = 1
    can_delete = True


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = (
        "title",
        "category",
        "published",
        "featured",
        "has_commentary",
        "views_7d",
        "created_at",
    )
    list_filter = ("category", "published", "featured")
    search_fields = ("title", "summary")
    readonly_fields = ("old_slugs", "published_overridden", "featured_overridden")

    @admin.display(description="옛 슬러그")
    def old_slugs(self, obj: Article) -> str:
        return ", ".join(a.slug for a in obj.old_slugs.all()) or "-"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Article]:
        since = timezone.localdate() - timedelta(days=6)
        qs: QuerySet[Article] = super().get_queryset(request)
        return qs.select_related("commentary").annotate(
            views_7d_sum=Sum("views__count", filter=Q(views__date__gte=since))
        )

    @admin.display(description="분석 있음", boolean=True)
    def has_commentary(self, obj: Article) -> bool:
        return hasattr(obj, "commentary")

    @admin.display(description="7일 조회", ordering="views_7d_sum")
    def views_7d(self, obj: Article) -> int:
        return getattr(obj, "views_7d_sum", None) or 0

    inlines = [ArticleItemInline, CommentaryInline]

    def save_model(
        self,
        request: HttpRequest,
        obj: Article,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        """admin은 대표의 도구다. 플래그 변경은 서비스 함수(대표 주체)를 거쳐 덮어씀을 표시한다."""
        if not change:
            if not obj.slug:
                obj.slug = svc.make_slug(obj.title, Article, "news")
            obj.save()
            return
        published, featured = obj.published, obj.featured
        stored = Article.objects.get(pk=obj.pk)
        obj.published, obj.featured = stored.published, stored.featured
        if not obj.slug:
            obj.slug = stored.slug
        elif obj.slug != stored.slug:
            new = obj.slug
            obj.slug = stored.slug
            svc.record_article_slug_change(obj, new)
        obj.save()
        svc.set_flags(svc.Actor.OWNER, obj.pk, published=published, featured=featured)
        obj.refresh_from_db()

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Article]) -> None:
        for article in queryset:
            article.delete()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = ("name", "slug", "order")
    list_editable = ("order",)

    @admin.display(description="옛 슬러그")
    def old_slugs(self, obj: Category) -> str:
        return ", ".join(a.slug for a in obj.old_slugs.all()) or "-"

    readonly_fields = ("old_slugs",)

    def delete_model(self, request: HttpRequest, obj: Category) -> None:
        svc.delete_category(obj.slug, confirm=True)

    def delete_queryset(self, request: HttpRequest, queryset: QuerySet[Category]) -> None:
        for category in queryset:
            svc.delete_category(category.slug, confirm=True)

    def save_model(
        self,
        request: HttpRequest,
        obj: Category,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        if not obj.slug:
            obj.slug = svc.make_slug(obj.name, Category, "category")
        elif change:
            old = Category.objects.get(pk=obj.pk).slug
            if old != obj.slug:
                new = obj.slug
                obj.slug = old
                svc.record_slug_change(obj, new)
        super().save_model(request, obj, form, change)


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    search_fields = ("name",)


@admin.register(OutboundClick)
class OutboundClickAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """유입 링크 클릭. 위에 기간(오늘·7일·30일)별 위치·기사 집계를 보인다. 기록은 읽기 전용."""

    change_list_template = "admin/articles/outboundclick/change_list.html"
    list_display = ("clicked_at", "location", "article", "referrer")
    list_filter = ("location", "clicked_at")

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> TemplateResponse:
        context = {**(extra_context or {}), "summaries": outbound.summarize()}
        return super().changelist_view(request, context)  # type: ignore[return-value]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: OutboundClick | None = None) -> bool:
        return False
