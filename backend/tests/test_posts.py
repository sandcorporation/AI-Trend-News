"""포스트 초안(#14): MCP 편집자 도구, 서비스 규칙, admin 목록·올림 표시."""

import pytest
from django.test import Client
from django.urls import reverse

from articles.models import Article
from mcpserver.jsontypes import JSON
from posts.models import Post
from services import articles as article_service
from services import posts as svc
from services.articles import Actor
from tests.conftest import Signer
from tests.test_mcp import McpClient, make_items


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="editor"))


def featured_article(mcp: McpClient, **flags: bool) -> dict[str, JSON]:
    category = article_service.add_category("IT").slug
    item = make_items(1)[0]
    return mcp.ok(
        "create_article",
        {
            "item_ids": [item.pk],
            "title": "금리 동결",
            "summary": "요약",
            "category": category,
            "slug": "rates-frozen",
            **({"published": True, "featured": True} | flags),
        },
    )


def draft_args(article: dict[str, JSON], text: str = "") -> dict[str, JSON]:
    return {
        "article_id": article["id"],
        "channel": "kakao",
        "text": text or f"한국은행이 금리를 동결했다.\n{article['url']}",
    }


@pytest.mark.django_db
def test_draft_is_created_then_updated_in_place_and_listed(
    mcp: McpClient, admin_client: Client
) -> None:
    article = featured_article(mcp)
    assert str(article["url"]).endswith(f"/a/{article['slug']}")
    first = mcp.ok("upsert_post_draft", draft_args(article))
    assert first["status"] == "draft" and first["channel"] == "kakao"
    again = mcp.ok("upsert_post_draft", draft_args(article, f"고친 문구\n{article['url']}"))
    assert again["id"] == first["id"] and str(again["text"]).startswith("고친 문구")
    assert Post.objects.count() == 1

    page = admin_client.get(reverse("admin:posts_post_changelist"))
    assert page.status_code == 200
    assert "금리 동결" in page.text and "카카오톡" in page.text and "초안" in page.text


@pytest.mark.django_db
@pytest.mark.parametrize("flags", [{"featured": False}, {"published": False}])
def test_draft_rejected_for_non_featured_or_unpublished(
    mcp: McpClient, flags: dict[str, bool]
) -> None:
    article = featured_article(mcp, **flags)
    assert "추천이면서 게시된" in mcp.fails("upsert_post_draft", draft_args(article))
    assert not Post.objects.exists()


@pytest.mark.django_db
def test_draft_text_rules(mcp: McpClient) -> None:
    article = featured_article(mcp)
    assert "링크" in mcp.fails("upsert_post_draft", draft_args(article, "링크 없는 문구"))
    long_text = "가" * 480 + f" {article['url']}"
    assert "500" in mcp.fails("upsert_post_draft", draft_args(article, long_text))
    bad = draft_args(article) | {"channel": "x"}
    assert "모르는 채널" in mcp.fails("upsert_post_draft", bad)


@pytest.mark.django_db
def test_marking_posted_records_time_and_locks_editor(mcp: McpClient, admin_client: Client) -> None:
    article = featured_article(mcp)
    post_id = mcp.ok("upsert_post_draft", draft_args(article))["id"]

    detail = admin_client.get(reverse("admin:posts_post_change", args=[post_id]))
    assert detail.status_code == 200 and 'id="post-copy-button"' in detail.text
    assert "한국은행이 금리를 동결했다." in detail.text

    done = admin_client.post(
        reverse("admin:posts_post_changelist"),
        {"action": "mark_posted", "_selected_action": [post_id]},
    )
    assert done.status_code == 302
    post = Post.objects.get()
    assert post.status == Post.Status.POSTED and post.posted_at is not None
    stamp = post.posted_at

    assert "이미 올린" in mcp.fails(
        "upsert_post_draft", draft_args(article, f"다시\n{article['url']}")
    )
    post.refresh_from_db()
    assert post.text.startswith("한국은행") and post.posted_at == stamp
    svc.mark_post_posted(Actor.OWNER, post.pk)
    post.refresh_from_db()
    assert post.posted_at == stamp  # 다시 눌러도 시각 유지


@pytest.mark.django_db
def test_editor_cannot_mark_posted(mcp: McpClient) -> None:
    article = featured_article(mcp)
    post_id = mcp.ok("upsert_post_draft", draft_args(article))["id"]
    assert isinstance(post_id, int)
    with pytest.raises(svc.OwnerOnly):
        svc.mark_post_posted(Actor.EDITOR, post_id)
    assert Article.objects.count() == 1
