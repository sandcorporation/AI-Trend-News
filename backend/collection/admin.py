from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone

from collection.models import Item, Source


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = (
        "name",
        "kind",
        "enabled",
        "last_collected_at",
        "last_succeeded",
        "consecutive_failures",
        "filtered_7d",
    )
    list_filter = ("kind", "enabled")
    readonly_fields = (
        "last_collected_at",
        "last_result",
        "last_succeeded",
        "consecutive_failures",
    )
    actions = ["disable_sources"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[Source]:
        since = timezone.now() - timedelta(days=7)
        qs: QuerySet[Source] = (
            super()
            .get_queryset(request)
            .annotate(
                _filtered_7d=Count(
                    "items",
                    filter=Q(items__status=Item.Status.FILTERED, items__collected_at__gte=since),
                )
            )
        )
        return qs

    @admin.display(description="7일 걸러냄", ordering="_filtered_7d")
    def filtered_7d(self, obj: Source) -> int:
        return int(getattr(obj, "_filtered_7d", 0))

    @admin.action(description="선택한 소스 끄기")
    def disable_sources(self, request: HttpRequest, queryset: QuerySet[Source]) -> None:
        queryset.update(enabled=False)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = (
        "title",
        "source",
        "published_at",
        "collected_at",
        "status",
        "filter_reason",
        "has_image",
    )
    list_filter = ("status", "source")
    search_fields = ("title", "url")
    actions = ["revive_filtered"]

    @admin.action(description="되살리기(걸러냄 → 미처리)")
    def revive_filtered(self, request: HttpRequest, queryset: QuerySet[Item]) -> None:
        n = queryset.filter(status=Item.Status.FILTERED).update(
            status=Item.Status.PENDING, filter_reason=""
        )
        self.message_user(request, f"{n}건을 되살렸습니다.", messages.SUCCESS)

    date_hierarchy = "collected_at"

    @admin.display(boolean=True, description="이미지")
    def has_image(self, obj: Item) -> bool:
        return bool(obj.image_url)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Item | None = None) -> bool:
        return False
