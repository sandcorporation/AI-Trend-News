"""대표가 고친 제목·요약(#219): 편집자 갱신은 선 칸을 덮지 않고 결과에 알린다."""

import pytest
from django.test import Client
from django.urls import reverse
from sandcorp_authkit.testing import FixedClock

from adapters.anthropic import FakeLlm
from articles.models import Article
from editor.models import EditorCall
from services import articles as svc
from services.articles import Actor
from tests.conftest import Signer
from tests.test_admin_api_and_tools import Api, api, obj
from tests.test_commentary_admin import change_form_data
from tests.test_editor_live import KEEP, clock, full_slot, live_sync, pages
from tests.test_editor_pipeline import run
from tests.test_mcp import McpClient
from tests.test_public_site import make_article

__all__ = ["api", "clock", "live_sync", "pages"]  # 다른 파일의 픽스처를 이 파일에서도 쓴다

pytestmark = pytest.mark.django_db


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="editor"))


def flags(article: Article) -> tuple[bool, bool]:
    article.refresh_from_db()
    return article.title_overridden, article.summary_overridden


def test_new_and_editor_updated_articles_are_not_owner_decided() -> None:
    article = make_article("원래 제목")
    assert flags(article) == (False, False)
    svc.update_article(Actor.EDITOR, article.pk, title="편집자 제목", summary="편집자 요약")
    assert flags(article) == (False, False)
    assert (article.title, article.summary) == ("편집자 제목", "편집자 요약")


def test_owner_change_marks_only_changed_field_and_editor_keeps_it() -> None:
    article = make_article("원래 제목")
    svc.update_article(Actor.OWNER, article.pk, title="대표 제목", summary=article.summary)
    assert flags(article) == (True, False)  # 같은 값은 바꾼 것이 아니다

    updated = svc.update_article(
        Actor.EDITOR, article.pk, title="편집자 제목", summary="편집자 요약"
    )

    assert updated.ignored_fields == ("title",)
    article.refresh_from_db()
    assert (article.title, article.summary) == ("대표 제목", "편집자 요약")
    svc.update_article(Actor.OWNER, article.pk, title="대표 새 제목")  # 대표는 계속 고친다
    article.refresh_from_db()
    assert article.title == "대표 새 제목"


def test_owner_tool_sets_flags_and_editor_tool_reports_ignored_fields(
    mcp: McpClient, client: Client, token_signer: Signer
) -> None:
    article = make_article("원래 제목")
    owner = McpClient(client, token_signer.token(scope="admin editor"))
    result = owner.ok(
        "update_article_as_owner", {"id": article.pk, "title": "대표 제목", "summary": "대표 요약"}
    )
    assert result["title_overridden"] is True and result["summary_overridden"] is True

    updated = mcp.ok(
        "update_article", {"id": article.pk, "title": "x", "summary": "y", "tags": ["새 태그"]}
    )

    assert updated["ignored_fields"] == ["title", "summary"]
    assert updated["title_by_owner"] is True and updated["summary_by_owner"] is True
    assert updated["title"] == "대표 제목"
    article.refresh_from_db()
    assert (article.title, article.summary) == ("대표 제목", "대표 요약")
    assert [t.name for t in article.tags.all()] == ["새 태그"]  # 나머지는 반영
    recent = mcp.ok("list_recent_articles", {"days": 1})["articles"]
    assert isinstance(recent, list) and isinstance(recent[0], dict)
    assert recent[0]["title_by_owner"] is True and recent[0]["summary_by_owner"] is True
    assert mcp.ok("update_article", {"id": article.pk})["ignored_fields"] == []


def test_admin_api_sets_flags(api: Api) -> None:
    article = make_article("원래 제목")
    status, body = api.call("patch", f"/articles/{article.pk}", {"summary": "대표 요약"})
    assert status == 200 and obj(body)["summary_overridden"] is True
    assert flags(article) == (False, True)


def admin_save(client: Client, article: Article, **changes: str) -> None:
    data = change_form_data(client, article) | changes
    response = client.post(reverse("admin:articles_article_change", args=[article.pk]), data)
    assert response.status_code == 302


def test_admin_marks_changed_text_only(admin_client: Client) -> None:
    article = make_article("원래 제목")
    Article.objects.filter(pk=article.pk).update(summary="첫 줄.\n둘째 줄.\n")
    # 브라우저는 textarea 줄바꿈을 CRLF로 보낸다. 손대지 않은 칸은 표시하지 않는다.
    admin_save(admin_client, article, summary="첫 줄.\r\n둘째 줄.\r\n")
    assert flags(article) == (False, False)

    admin_save(admin_client, article, title="대표 제목")
    assert flags(article) == (True, False)

    admin_save(admin_client, article, summary="대표 요약")
    assert flags(article) == (True, True)


def test_pipeline_update_keeps_owner_text_and_records_ignored_fields(
    clock: FixedClock, live_sync: None, fake_llm: FakeLlm
) -> None:
    *_, existing = full_slot(fake_llm)
    svc.update_article(Actor.OWNER, existing.pk, title="대표 제목")
    # full_slot의 갱신 응답(KEEP)을 제목·요약을 바꾸는 응답으로 바꿔 끼운다.
    decision, write_new, _keep, *analyses = list(fake_llm.replies)
    fake_llm.replies.clear()
    rewrite = KEEP | {"title": "모델이 고친 제목", "summary": "모델이 고친 요약."}
    fake_llm.queue(decision, write_new, rewrite, *analyses)

    run()

    existing.refresh_from_db()
    assert (existing.title, existing.summary) == ("대표 제목", "모델이 고친 요약.")
    call = EditorCall.objects.get(stage="write_update")
    assert call.status == EditorCall.Status.SAVED
    assert isinstance(call.output, dict) and call.output["saved"]["ignored_fields"] == ["title"]
    assert "대표가 고친 칸" in call.error
    analysis_input = str(fake_llm.requests[-1])
    assert "대표 제목" in analysis_input and "모델이 고친 제목" not in analysis_input
