from django.contrib import admin
from django.db.models import QuerySet
from django.forms import ModelForm
from django.http import HttpRequest
from django.template.response import TemplateResponse

from articles.models import Article, ArticleItem, Category, OutboundClick, Tag
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


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = ("title", "category", "published", "featured", "created_at")
    list_filter = ("category", "published", "featured")
    search_fields = ("title", "summary")
    readonly_fields = ("slug", "published_overridden", "featured_overridden")
    inlines = [ArticleItemInline]

    def save_model(
        self,
        request: HttpRequest,
        obj: Article,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        """admin은 대표의 도구다. 플래그 변경은 서비스 함수(대표 주체)를 거쳐 덮어씀을 표시한다."""
        if not change:
            obj.slug = svc.make_slug(obj.title, Article, "news")
            obj.save()
            return
        published, featured = obj.published, obj.featured
        stored = Article.objects.get(pk=obj.pk)
        obj.published, obj.featured = stored.published, stored.featured
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

    def save_model(
        self,
        request: HttpRequest,
        obj: Category,
        form: ModelForm,  # type: ignore[type-arg]
        change: bool,
    ) -> None:
        if not change:
            obj.slug = svc.make_slug(obj.name, Category, "category")
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
