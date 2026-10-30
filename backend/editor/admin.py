import json

from django.contrib import admin
from django.db.models import Model
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html

from editor.models import Alert, EditorCall, EditorRun
from services.editor_pipeline import month_cost


def _pretty(value: object) -> str:
    return format_html(
        '<pre style="white-space:pre-wrap;max-height:30rem;overflow:auto">{}</pre>',
        json.dumps(value, ensure_ascii=False, indent=2),
    )


class EditorCallInline(admin.StackedInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """실행의 LLM 호출. 모두 읽기 전용(입출력 JSON은 30일 뒤 비워진다)."""

    model = EditorCall
    extra = 0
    can_delete = False
    fields = (
        ("stage", "model", "effort", "status", "attempt"),
        ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"),
        ("cost_usd", "stop_reason", "prompt_version", "key"),
        "error",
        "input_json",
        "output_json",
    )
    readonly_fields = (
        "stage",
        "model",
        "effort",
        "status",
        "attempt",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cost_usd",
        "stop_reason",
        "prompt_version",
        "key",
        "error",
        "input_json",
        "output_json",
    )

    @admin.display(description="입력 요약")
    def input_json(self, obj: EditorCall) -> str:
        return _pretty(obj.input)

    @admin.display(description="출력")
    def output_json(self, obj: EditorCall) -> str:
        return _pretty(obj.output)

    def has_add_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


@admin.register(EditorRun)
class EditorRunAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """읽기 전용: 루틴 실행은 편집자가 MCP로, 파이프라인 실행은 `run_editor`가 만든다.

    목록 머리에 이번 달 편집 호출 추정 비용 합계를 보인다.
    """

    change_list_template = "admin/editor/editorrun/change_list.html"
    list_display = (
        "id",
        "mode",
        "slot",
        "started_at",
        "finished_at",
        "run_state",
        "status",
        "cost_usd",
        "created",
        "updated",
        "discarded",
    )
    list_filter = ("mode", "status", "finished_at")
    fields = (
        ("mode", "slot", "status", "cost_usd"),
        ("started_at", "finished_at"),
        ("created", "updated", "discarded"),
        "note",
    )
    readonly_fields = (
        "mode",
        "slot",
        "status",
        "cost_usd",
        "started_at",
        "finished_at",
        "created",
        "updated",
        "discarded",
        "note",
    )
    inlines = (EditorCallInline,)

    def changelist_view(
        self, request: HttpRequest, extra_context: dict[str, object] | None = None
    ) -> TemplateResponse:
        now = timezone.now()  # created_at(auto_now_add)과 같은 시계
        context = {
            **(extra_context or {}),
            "month_label": f"{timezone.localtime(now):%Y-%m}",
            "month_cost": month_cost(now),
        }
        return super().changelist_view(request, context)  # type: ignore[return-value]

    @admin.display(description="상태")
    def run_state(self, obj: EditorRun) -> str:
        return str(obj.state(timezone.now()).label)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: EditorRun | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: EditorRun | None = None) -> bool:
        return False


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    """읽기 전용: 경보 상태는 점검 명령이 관리한다."""

    list_display = ("kind", "target", "active", "last_sent_at")
    list_filter = ("kind", "active")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Alert | None = None) -> bool:
        return False
