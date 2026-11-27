"""admin 액션 권한(#212): 조회 권한만 있는 staff는 액션을 보지도 실행하지도 못한다."""

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from articles.models import Prediction
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


def test_every_admin_action_declares_permissions() -> None:
    """`allowed_permissions`가 없는 액션은 Django가 조회 권한만으로 실행시킨다."""
    missing = [
        f"{type(model_admin).__qualname__}.{action.name}"
        for model_admin in admin.site._registry.values()
        for action in model_admin._get_base_actions()  # type: ignore[attr-defined]
        if action.name != "delete_selected"
        and not getattr(action.func, "allowed_permissions", None)
    ]
    assert missing == []
