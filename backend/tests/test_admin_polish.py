"""admin·관리 도구 개선 묶음(#238)."""

from typing import Any

import pytest
from bs4 import BeautifulSoup
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from articles.models import Article, Category, Commentary, Prediction, Tag
from mcpserver.tools import TOOLS
from services import articles as svc
from services.articles import Actor
from tests.test_admin_action_permissions import make_target
from tests.test_articles import make_article, make_items
from tests.test_commentary_admin import change_form_data, prediction, save, written

pytestmark = pytest.mark.django_db

ADMIN = "/" + settings.ADMIN_PATH


def messages_of(html: str) -> str:
    box = BeautifulSoup(html, "html.parser").select_one("ul.messagelist")
    return box.get_text(" ", strip=True) if box else ""


@pytest.mark.parametrize(
    ("app", "model", "action", "message"),
    [
        ("collection", "source", "disable_sources", "소스 1건을 껐습니다."),
        ("articles", "prediction", "mark_hit", "예측 1건을 「적중」 상태로 바꿨습니다."),
        ("articles", "prediction", "mark_pending", "예측 1건을 「미평가」 상태로 바꿨습니다."),
    ],
)
def test_action_reports_completion(
    admin_client: Client, app: str, model: str, action: str, message: str
) -> None:
    pk = make_target(action)

    response = admin_client.post(
        reverse(f"admin:{app}_{model}_changelist"),
        {"action": action, "_selected_action": [pk]},
        follow=True,
    )

    assert message in messages_of(response.content.decode())


def settings_page(client: Client) -> BeautifulSoup:
    client.force_login(get_user_model().objects.create_superuser("owner", password=None))
    page = client.get(reverse("admin:core_runtimesetting_changelist"))
    return BeautifulSoup(page.content, "html.parser")


def test_runtime_setting_controls_are_named(client: Client) -> None:
    rows = settings_page(client).select("tr[data-setting]")

    assert rows
    for row in rows:
        for control in row.select("select, input:not([type=hidden])"):
            assert control.get("aria-label") == row["data-setting"]


def test_runtime_setting_select_shows_value_outside_choices(client: Client, settings: Any) -> None:
    settings.EDITOR_PIPELINE_MODE = "bogus"
    page = settings_page(client)
    name = "EDITOR_PIPELINE_MODE"

    first = page.select_one(f'tr[data-setting="{name}"] option')
    assert first is not None and first.has_attr("selected") and first["value"] == "bogus"
    assert first.get_text() == "(지금 값: bogus — 목록 밖)"
    assert "목록 밖" not in str(page.select_one('tr[data-setting="COLLECT_AI_FILTER_MODE"]'))

    url = reverse("admin:core_runtimesetting_changelist")
    response = client.post(url, {"name": name, "op": "save", "value": "bogus"}, follow=True)
    assert "가능한 값" in messages_of(response.content.decode())


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [
        (f"{ADMIN}articles/article/", f"{ADMIN}articles/article/"),
        ("https://evil.example/x", ADMIN),
        ("//evil.example/x", ADMIN),
        ("", ADMIN),
    ],
)
def test_logged_in_staff_follows_only_safe_next(
    admin_client: Client, next_url: str, expected: str
) -> None:
    response = admin_client.get(f"{ADMIN}login/", {"next": next_url})

    assert response.status_code == 302 and response["Location"] == expected


def test_commentary_errors_are_attached_to_their_fields(admin_client: Client) -> None:
    article = written(("옛 문장", "3m"))

    status, html = save(
        admin_client, article, significance="가" * 901, watchpoints='["a","b","c","d"]'
    )

    assert status == 200
    page = BeautifulSoup(html, "html.parser")
    assert "의미는 1~900자" in page.select_one(".field-significance .errorlist").get_text()  # type: ignore[union-attr]
    assert "3개까지" in page.select_one(".field-watchpoints .errorlist").get_text()  # type: ignore[union-attr]
    assert page.select_one(".inline-related .errorlist.nonfield") is None


@pytest.mark.parametrize("edit", [{}, {"opinion": "고친 의견"}], ids=["untouched", "other-field"])
def test_untouched_legacy_prediction_is_not_checked(
    admin_client: Client, edit: dict[str, Any]
) -> None:
    """브라우저는 여러 문단 textarea를 CRLF로 보낸다. 그것만으로는 바뀐 것이 아니다."""
    article = written(("옛 문장", "3m"))
    legacy = [prediction("2099년까지 내린다", "1m")]
    Commentary.objects.update(background="첫 문단\n\n둘째 문단", predictions=legacy)

    status, _ = save(admin_client, article, background="첫 문단\r\n\r\n둘째 문단", **edit)

    assert status == 302
    saved = Commentary.objects.get()
    assert saved.background == "첫 문단\n\n둘째 문단" and saved.predictions == legacy
    assert Prediction.objects.get().claim == "옛 문장"  # 예측을 안 고치면 동기화도 없다


def test_article_added_in_admin_is_owner_override(admin_client: Client) -> None:
    category = svc.add_category("경제")
    url = reverse("admin:articles_article_add")
    data = {
        "title": "대표가 쓴 기사",
        "summary": "요약",
        "slug": "",
        "category": category.pk,
        "image_url": "",
        "published_at_0": "2026-01-01",
        "published_at_1": "00:00:00",
        "article_items-TOTAL_FORMS": "0",
        "article_items-INITIAL_FORMS": "0",
        "commentary-TOTAL_FORMS": "0",
        "commentary-INITIAL_FORMS": "0",
    }

    assert admin_client.post(url, data).status_code == 302
    article = Article.objects.get()
    assert article.title_overridden and article.summary_overridden
    svc.update_article(Actor.EDITOR, article.pk, title="편집자 제목", summary="편집자 요약")
    article.refresh_from_db()
    assert (article.title, article.summary) == ("대표가 쓴 기사", "요약")


@pytest.mark.parametrize(
    ("slug", "message"), [("ab", "3~60자"), ("a" * 61, "3~60자"), ("old-one", "slug")]
)
def test_admin_article_slug_uses_service_check(
    admin_client: Client, slug: str, message: str
) -> None:
    first = make_article(make_items(1), slug="old-one")
    svc.record_article_slug_change(first, "new-one")
    first.save()
    second = Article.objects.create(title="t", slug="second", category=first.category)
    data = change_form_data(admin_client, second) | {"slug": slug}

    response = admin_client.post(reverse("admin:articles_article_change", args=[second.pk]), data)

    assert response.status_code == 200
    error = BeautifulSoup(response.content, "html.parser").select_one(".field-slug .errorlist")
    assert error is not None and message in error.get_text()
    second.refresh_from_db()
    assert second.slug == "second"


def test_admin_category_slug_never_takes_an_old_alias(admin_client: Client) -> None:
    svc.add_category("가", slug="economy")
    svc.update_category("economy", new_slug="money")
    url = reverse("admin:articles_category_add")

    taken = admin_client.post(url, {"name": "나", "slug": "economy", "order": 1})
    assert taken.status_code == 200
    page = BeautifulSoup(taken.content, "html.parser")
    assert "이미 쓰는 slug" in page.select_one(".field-slug .errorlist").get_text()  # type: ignore[union-attr]

    assert admin_client.post(url, {"name": "Economy", "slug": "", "order": 2}).status_code == 302
    assert Category.objects.get(name="Economy").slug.startswith("economy-")


@pytest.mark.parametrize("bad", ["가" * 51, "  "], ids=["too-long", "blank"])
def test_tag_name_length_is_checked_by_service(bad: str) -> None:
    article = make_article(make_items(1))

    with pytest.raises(svc.InvalidTag):
        svc.update_article(Actor.EDITOR, article.pk, title="바뀌면 안 됨", tags=["정상", bad])

    article.refresh_from_db()
    assert article.title != "바뀌면 안 됨" and not Tag.objects.exists()
    svc.update_article(Actor.EDITOR, article.pk, tags=["가" * 50])
    assert Tag.objects.get().name == "가" * 50


def test_admin_rejects_long_tag_name(admin_client: Client) -> None:
    response = admin_client.post(reverse("admin:articles_tag_add"), {"name": "가" * 51})

    assert response.status_code == 200 and not Tag.objects.exists()


def test_update_category_description_matches_behavior() -> None:
    description = str(TOOLS["update_category"].definition()["description"])

    assert "new_slug" in description and "바뀌지 않는다" not in description
