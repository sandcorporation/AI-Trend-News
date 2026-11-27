"""admin 액션 권한(#212): 조회 권한만 있는 staff는 액션을 보지도 실행하지도 못한다."""

import pytest
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.admin.options import ActionLocation
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from articles.models import Article, ArticleItem, Prediction
from collection.models import Item, Source
from tests.test_collection import make_source
from tests.test_predictions import featured_article_id, write

pytestmark = pytest.mark.django_db

CASES = [
    ("collection", "source", "disable_sources"),
    ("collection", "item", "revive_filtered"),
    ("articles", "prediction", "mark_hit"),
    ("articles", "prediction", "mark_miss"),
    ("articles", "prediction", "mark_undecidable"),
    ("articles", "prediction", "mark_pending"),
]


def make_target(action: str) -> int:
    if action == "disable_sources":
        return make_source().pk
    if action == "revive_filtered":
        url = "https://t.example.com/1"
        return Item.objects.create(
            source=make_source(),
            url=url,
            normalized_url=url,
            title="t",
            status=Item.Status.FILTERED,
            filter_reason="include_any 없음",
        ).pk
    write(featured_article_id(), ("금리가 내린다", "6m"))
    row = Prediction.objects.get()
    if action == "mark_pending":
        Prediction.objects.filter(pk=row.pk).update(verdict="hit", judged_at=timezone.now())
    return row.pk


def state(action: str, pk: int) -> object:
    if action == "disable_sources":
        return Source.objects.get(pk=pk).enabled
    if action == "revive_filtered":
        return Item.objects.get(pk=pk).status
    return Prediction.objects.get(pk=pk).verdict


def staff_client(client: Client, app: str, *codenames: str) -> Client:
    staff = get_user_model().objects.create_user("staff", password=None, is_staff=True)
    staff.user_permissions.set(
        Permission.objects.filter(content_type__app_label=app, codename__in=codenames)
    )
    client.force_login(staff)
    return client


@pytest.mark.parametrize(("app", "model", "action"), CASES)
def test_view_only_staff_cannot_see_or_run(
    client: Client, app: str, model: str, action: str
) -> None:
    pk = make_target(action)
    before = state(action, pk)
    staff = staff_client(client, app, f"view_{model}")
    url = reverse(f"admin:{app}_{model}_changelist")

    page = staff.get(url)
    assert page.status_code == 200 and f'value="{action}"' not in page.text
    staff.post(url, {"action": action, "_selected_action": [pk]})
    assert state(action, pk) == before


@pytest.mark.parametrize(("app", "model", "action"), CASES)
def test_change_staff_can_see_and_run(client: Client, app: str, model: str, action: str) -> None:
    pk = make_target(action)
    before = state(action, pk)
    staff = staff_client(client, app, f"view_{model}", f"change_{model}")
    url = reverse(f"admin:{app}_{model}_changelist")

    assert f'value="{action}"' in staff.get(url).text
    staff.post(url, {"action": action, "_selected_action": [pk]})
    assert state(action, pk) != before


def actions_without_permissions(site: AdminSite) -> list[str]:
    """목록·변경 폼 액션과 인라인에 단 액션 가운데 `allowed_permissions`가 없는 것."""
    missing = []
    for model_admin in site._registry.values():
        found: list[tuple[type, str, object]] = [
            (type(model_admin), action.name, action.func)
            for location in ActionLocation
            for action in model_admin._get_base_actions(location)  # type: ignore[attr-defined]
        ]
        for inline in model_admin.inlines:  # Django는 인라인 `actions`를 모른다 — 선언을 직접 본다
            for entry in getattr(inline, "actions", None) or []:
                func = entry if callable(entry) else getattr(inline, entry)
                found.append((inline, getattr(func, "__name__", str(entry)), func))
        missing += [
            f"{owner.__qualname__}.{name}"
            for owner, name, func in found
            if name != "delete_selected" and not getattr(func, "allowed_permissions", None)
        ]
    return sorted(set(missing))


def test_every_admin_action_declares_permissions() -> None:
    """`allowed_permissions`가 없는 액션은 Django가 조회 권한만으로 실행시킨다."""
    assert actions_without_permissions(admin.site) == []


def test_action_sweep_covers_change_form_and_inline_actions() -> None:
    @admin.action(location=ActionLocation.CHANGE_FORM)
    def on_form(model_admin: object, request: object, queryset: object) -> None: ...

    @admin.action
    def on_inline(model_admin: object, request: object, queryset: object) -> None: ...

    class Inline(admin.TabularInline):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
        model = ArticleItem
        actions = [on_inline]

    class Parent(admin.ModelAdmin):  # type: ignore[type-arg]  # 런타임에 제네릭 아님
        actions = [on_form]
        inlines = [Inline]

    site = AdminSite(name="sweep")
    site.register(Article, Parent)

    assert {name.rsplit(".", 1)[1] for name in actions_without_permissions(site)} == {
        "on_form",
        "on_inline",
    }
