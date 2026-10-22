from django.contrib import admin
from django.http import HttpRequest
from django.utils import timezone

from editor.models import EditorRun


@admin.register(EditorRun)
class EditorRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """읽기 전용: 실행은 편집자가 MCP로만 만든다."""

    list_display = (
        "id",
        "started_at",
        "finished_at",
        "run_state",
        "created",
        "updated",
        "discarded",
    )
    list_filter = ("finished_at",)
    fields = ("started_at", "finished_at", "created", "updated", "discarded", "note")
    readonly_fields = fields

    @admin.display(description="상태")
    def run_state(self, obj: EditorRun) -> str:
        return str(obj.state(timezone.now()).label)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: EditorRun | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: EditorRun | None = None) -> bool:
        return False
