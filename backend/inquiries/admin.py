from django.contrib import admin
from django.http import HttpRequest

from inquiries.models import Inquiry


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """읽기 전용. 영구실패 건을 보고 직접 처리한 뒤 지운다."""

    list_display = ("id", "created_at", "kind", "name", "status", "attempts", "last_error")
    list_filter = ("status", "kind")
    fields = (
        "status",
        "kind",
        "name",
        "company",
        "contact",
        "message",
        "attempts",
        "last_error",
        "sent_at",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Inquiry | None = None) -> bool:
        return False
