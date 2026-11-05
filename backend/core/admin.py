"""admin 「운영 설정」(#164): 등록부 순서로 한 쪽에 모아 보이고 칸마다 저장·지우기·(키) 연결 시험.

대표(superuser)만 본다. 비밀 값은 다시 렌더하지 않는다(설정됨 · 끝 4자리 · 출처 · 변경 시각·변경자).
"""

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse

from adapters.anthropic import check_key
from core.models import RuntimeSetting, RuntimeSettingChange
from services import runtime_settings

HISTORY_LIMIT = 30


def _is_owner(request: HttpRequest) -> bool:
    user = request.user
    return bool(user.is_active and user.is_staff and user.is_superuser)


@admin.register(RuntimeSetting)
class RuntimeSettingAdmin(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
    def has_module_permission(self, request: HttpRequest) -> bool:
        return _is_owner(request)

    def has_view_permission(self, request: HttpRequest, obj: RuntimeSetting | None = None) -> bool:
        return _is_owner(request)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: RuntimeSetting | None = None
    ) -> bool:
        return False

    def has_delete_permission(
        self, request: HttpRequest, obj: RuntimeSetting | None = None
    ) -> bool:
        return False

    def get_urls(self) -> list[URLPattern]:
        # 목록 자리를 「운영 설정」 쪽으로 바꾼다(admin 첫 화면 링크가 그대로 여기로 온다).
        return [
            path(
                "",
                self.admin_site.admin_view(self.settings_view),
                name="core_runtimesetting_changelist",
            )
        ]

    def settings_view(self, request: HttpRequest) -> HttpResponse:
        if not _is_owner(request):
            raise PermissionDenied
        if request.method == "POST":
            self._handle(request)
            return HttpResponseRedirect(reverse("admin:core_runtimesetting_changelist"))
        rows = runtime_settings.rows()
        groups: dict[str, list[runtime_settings.Row]] = {}
        for row in rows:
            groups.setdefault(row.group, []).append(row)
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "운영 설정",
            "groups": groups,
            "encryption_ready": runtime_settings.encryption_ready(),
            "history": RuntimeSettingChange.objects.all()[:HISTORY_LIMIT],
        }
        return TemplateResponse(request, "admin/core/runtime_settings.html", context)

    def _handle(self, request: HttpRequest) -> None:
        name = request.POST.get("name", "")
        op = request.POST.get("op", "")
        raw = request.POST.get("value", "")
        actor = request.user.get_username()
        spec = runtime_settings.REGISTRY.get(name)
        if spec is None:
            messages.error(request, "등록부에 없는 설정")
            return
        if op == "test":
            if not spec.secret:
                messages.error(request, "연결 시험은 키에만 있다")
                return
            key = raw.strip() or str(spec.get())
            result = check_key(key)
            level = messages.SUCCESS if result.ok else messages.ERROR
            messages.add_message(request, level, f"{name} 연결 시험: {result.message}")
            return
        try:
            if op == "clear":
                if runtime_settings.clear(name, actor=actor):
                    messages.success(request, f"{name}: DB 값을 지웠다(.env 값으로 돌아감)")
                else:
                    messages.info(request, f"{name}: DB 값이 없다")
                return
            if op != "save":
                messages.error(request, "알 수 없는 동작")
                return
            if spec.secret and not raw.strip():
                messages.info(request, f"{name}: 빈 입력 — 기존 값 유지")
                return
            runtime_settings.save(name, raw, actor=actor)
            messages.success(request, f"{name}: 저장했다(재시작 없이 반영)")
        except runtime_settings.RuntimeSettingError as error:
            messages.error(request, str(error))
