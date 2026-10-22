from django.contrib import admin
from django.db.models import QuerySet
from django.http import HttpRequest

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
    )
    list_filter = ("kind", "enabled")
    readonly_fields = (
        "last_collected_at",
        "last_result",
        "last_succeeded",
        "consecutive_failures",
    )
    actions = ["disable_sources"]

    @admin.action(description="선택한 소스 끄기")
    def disable_sources(self, request: HttpRequest, queryset: QuerySet[Source]) -> None:
        queryset.update(enabled=False)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    list_display = ("title", "source", "published_at", "collected_at", "status")
    list_filter = ("status", "source")
    search_fields = ("title", "url")
    date_hierarchy = "collected_at"

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Item | None = None) -> bool:
        return False
