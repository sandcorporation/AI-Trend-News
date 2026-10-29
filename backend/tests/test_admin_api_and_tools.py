"""관리 기능(#9): 관리자 API와 MCP 관리 도구가 같은 서비스 동작을 확인한다.

소스 등록·플래그 덮어쓰기는 양쪽 다, 나머지는 한쪽에서. 편집자 토큰은 도구를 못 보고 API는 403.
"""

import json

import pytest
from django.test import Client
from django.urls import reverse

from articles.models import Article, ArticleSlugAlias, Category
from collection.models import Item, Source
from mcpserver.jsontypes import JSON
from posts.models import Post
from services.articles import Actor, FlagOverridden, create_article, set_flags
from services.collection import collect_all
from tests.conftest import FakeFetcher, Signer
from tests.test_collection import HTML_RULES, HTML_URL
from tests.test_mcp import McpClient

pytestmark = pytest.mark.django_db

FEED = "https://feeds.example.com/new.xml"


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="admin editor"))


@pytest.fixture
def api(client: Client, token_signer: Signer) -> Api:
    return Api(client, token_signer.token(scope="admin"))


class Api:
    def __init__(self, client: Client, token: str) -> None:
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}"}

    def call(self, method: str, path: str, body: JSON = None) -> tuple[int, JSON]:
        response = getattr(self.client, method)(
            f"/api/admin{path}",
            json.dumps(body) if body is not None else None,
            content_type="application/json",
            headers=self.headers,
        )
        return response.status_code, (response.json() if response.content else None)


def obj(value: JSON) -> dict[str, JSON]:
    assert isinstance(value, dict)
    return value


def rows(value: JSON) -> list[dict[str, JSON]]:
    assert isinstance(value, list)
    return [obj(v) for v in value]


def make_article(published: bool = True) -> Article:
    category = Category.objects.create(name="모델", slug="models")
    item = Item.objects.create(
        source=Source.objects.create(name="s", url=FEED),
        url="https://x.example.com/a",
        normalized_url="https://x.example.com/a",
        title="원문",
    )
    return create_article(
        Actor.EDITOR,
        item_ids=[item.pk],
        title="제목",
        summary="요약",
        category=category.slug,
        published=published,
    )


# --- 소스 등록: 양쪽 다 ---


def test_mcp_registered_source_is_collected_next_run(
    mcp: McpClient, fake_fetcher: FakeFetcher, rss_bytes: bytes
) -> None:
    created = mcp.ok("create_source", {"name": "새 피드", "kind": "rss", "url": FEED})
    assert created["enabled"] is True and created["last_collected_at"] is None
    fake_fetcher.responses[FEED] = rss_bytes

    collect_all()

    assert Item.objects.filter(source_id=int(str(created["id"]))).count() == 5
    assert rows(mcp.ok("list_sources", {})["sources"])[0]["last_succeeded"] is True


def test_api_registered_source_is_collected_next_run(
    api: Api, fake_fetcher: FakeFetcher, html_bytes: bytes
) -> None:
    status, body = api.call(
        "post",
        "/sources",
        {"name": "HTML 소스", "kind": "html", "url": HTML_URL, "html_rules": dict(HTML_RULES)},
    )
    created = obj(body)
    assert status == 201 and created["kind"] == "html"
    assert Source.objects.get(pk=int(str(created["id"]))).adapter_name == "html"
    fake_fetcher.responses[HTML_URL] = html_bytes

    collect_all()

    assert Item.objects.count() == 6


def test_bad_html_rules_rejected_by_both(mcp: McpClient, api: Api) -> None:
    payload: dict[str, JSON] = {
        "name": "깨진",
        "kind": "html",
        "url": HTML_URL,
        "html_rules": {"item": "li"},
    }
    message = mcp.fails("create_source", payload)
    assert "html_rules" in message and "필수 키가 없습니다: link, title, published" in message

    status, body = api.call("post", "/sources", payload)
    assert status == 400 and "필수 키가 없습니다" in str(obj(body)["detail"])
    assert not Source.objects.filter(name="깨진").exists()


def test_disabled_source_is_not_collected(
    mcp: McpClient, api: Api, fake_fetcher: FakeFetcher, rss_bytes: bytes
) -> None:
    source = Source.objects.create(name="a", url=FEED)
    fake_fetcher.responses[FEED] = rss_bytes
    assert mcp.ok("set_source_enabled", {"id": source.pk, "enabled": False})["enabled"] is False
    collect_all()
    assert Item.objects.count() == 0

    status, body = api.call("put", f"/sources/{source.pk}/enabled", {"enabled": True})
    assert status == 200 and obj(body)["enabled"] is True
    collect_all()
    assert Item.objects.count() == 5


def test_update_source_revalidates_whole_result(mcp: McpClient, api: Api) -> None:
    source = Source.objects.create(name="a", url=FEED)
    message = mcp.fails("update_source", {"id": source.pk, "kind": "html"})
    assert "필수 키가 없습니다" in message
    assert Source.objects.get(pk=source.pk).kind == "rss"

    changed = mcp.ok(
        "update_source",
        {"id": source.pk, "kind": "html", "html_rules": dict(HTML_RULES), "name": "b"},
    )
    assert changed["kind"] == "html" and changed["name"] == "b"
    status, body = api.call("patch", f"/sources/{source.pk}", {"kind": "rss"})
    assert status == 200 and obj(body)["kind"] == "rss"
    assert Source.objects.get(pk=source.pk).adapter_name == "rss"

    assert api.call("patch", "/sources/999", {"name": "x"})[0] == 404
    assert "없는 소스" in mcp.fails("set_source_enabled", {"id": 999, "enabled": True})


# --- 플래그 덮어쓰기: 양쪽 다 ---


def test_mcp_override_blocks_editor_from_turning_publishing_back_on(
    client: Client, token_signer: Signer, mcp: McpClient
) -> None:
    article = make_article(published=True)
    result = mcp.ok("set_article_flags_as_owner", {"id": article.pk, "published": False})
    assert result["published"] is False and result["published_overridden"] is True
    assert result["featured_overridden"] is False

    editor = McpClient(client, token_signer.token(scope="editor"))
    message = editor.fails("set_article_flags", {"id": article.pk, "published": True})
    assert "대표가 정했" in message
    article.refresh_from_db()
    assert article.published is False


def test_api_override_blocks_editor(api: Api) -> None:
    article = make_article(published=False)
    status, body = api.call("put", f"/articles/{article.pk}/flags", {"featured": True})
    flags = obj(body)
    assert status == 200 and flags["featured"] is True and flags["featured_overridden"] is True
    with pytest.raises(FlagOverridden):
        set_flags(Actor.EDITOR, article.pk, featured=False)
    assert api.call("put", "/articles/999/flags", {"featured": True})[0] == 404


def test_owner_edits_title_and_summary(mcp: McpClient, api: Api) -> None:
    article = make_article()
    result = mcp.ok("update_article_as_owner", {"id": article.pk, "title": "새 제목"})
    assert result["title"] == "새 제목" and result["summary"] == "요약"
    status, body = api.call("patch", f"/articles/{article.pk}", {"summary": "새 요약"})
    edited = obj(body)
    assert status == 200 and edited["title"] == "새 제목" and edited["summary"] == "새 요약"
    assert edited["slug"] == article.slug


# --- 분야 ---


def test_category_update_reorder_delete_via_mcp(mcp: McpClient) -> None:
    a = Category.objects.create(name="가", slug="a", order=0)
    Category.objects.create(name="나", slug="b", order=1)
    assert mcp.ok("update_category", {"slug": "a", "name": "가가"})["slug"] == "a"
    assert "이미 있는 분야" in mcp.fails("update_category", {"slug": "a", "name": "나"})

    ordered = rows(mcp.ok("reorder_categories", {"slugs": ["b", "a"]})["categories"])
    assert [c["slug"] for c in ordered] == ["b", "a"]
    assert "빠진 slug: b" in mcp.fails("reorder_categories", {"slugs": ["a"]})

    assert mcp.ok("delete_category", {"slug": "b"}) == {"deleted": "b", "deleted_articles": 0}
    assert list(Category.objects.values_list("slug", flat=True)) == [a.slug]


def test_category_with_articles_is_refused_without_confirm(mcp: McpClient, api: Api) -> None:
    make_article()
    message = mcp.fails("delete_category", {"slug": "models"})
    assert "기사 1건" in message and "confirm=true" in message
    status, body = api.call("delete", "/categories/models")
    assert status == 400 and "confirm=true" in str(obj(body)["detail"])
    assert Category.objects.filter(slug="models").exists() and Article.objects.count() == 1


def _delete_with_confirm_scene() -> tuple[Article, Article, Item]:
    article = make_article()
    ArticleSlugAlias.objects.create(slug="old-one", article=article)
    Post.objects.create(article=article, channel=Post.Channel.KAKAO, text="문구")
    mine = Item.objects.get(article_link__article=article)
    other_cat = Category.objects.create(name="다른", slug="other")
    keep_item = Item.objects.create(
        source=mine.source, url="https://x.example.com/k", normalized_url="https://x.example.com/k"
    )
    keep = create_article(
        Actor.EDITOR, item_ids=[keep_item.pk], title="남길", summary="s", category=other_cat.slug
    )
    return article, keep, mine


def _assert_cascaded(keep: Article, mine: Item) -> None:
    assert not Category.objects.filter(slug="models").exists()
    assert list(Article.objects.values_list("pk", flat=True)) == [keep.pk]
    assert not Post.objects.exists() and not ArticleSlugAlias.objects.exists()
    mine.refresh_from_db()
    assert mine.status == Item.Status.DISCARDED
    assert Item.objects.get(article_link__article=keep).status == Item.Status.BUNDLED


def test_mcp_confirm_deletes_articles_and_discards_items(mcp: McpClient) -> None:
    _, keep, mine = _delete_with_confirm_scene()
    result = mcp.ok("delete_category", {"slug": "models", "confirm": True})
    assert result == {"deleted": "models", "deleted_articles": 1}
    _assert_cascaded(keep, mine)


def test_api_confirm_deletes_articles_and_discards_items(api: Api) -> None:
    _, keep, mine = _delete_with_confirm_scene()
    status, body = api.call("delete", "/categories/models?confirm=true")
    assert status == 200 and body == {"deleted": "models", "deleted_articles": 1}
    _assert_cascaded(keep, mine)


def test_category_without_articles_is_deleted_without_confirm(api: Api) -> None:
    Category.objects.create(name="빈", slug="empty")
    status, body = api.call("delete", "/categories/empty")
    assert status == 200 and body == {"deleted": "empty", "deleted_articles": 0}


def test_admin_delete_goes_through_service(admin_client: Client) -> None:
    _, keep, mine = _delete_with_confirm_scene()
    category = Category.objects.get(slug="models")
    url = reverse("admin:articles_category_delete", args=[category.pk])
    assert admin_client.post(url, {"post": "yes"}).status_code == 302
    _assert_cascaded(keep, mine)


def test_category_api(api: Api) -> None:
    Category.objects.create(name="가", slug="a", order=0)
    Category.objects.create(name="나", slug="b", order=1)
    status, body = api.call("put", "/categories/order", {"slugs": ["b", "a"]})
    assert status == 200 and [c["slug"] for c in rows(body)] == ["b", "a"]
    status, body = api.call("patch", "/categories/a", {"name": "새"})
    assert status == 200 and obj(body)["name"] == "새"
    assert api.call("delete", "/categories/a")[0] == 200
    assert api.call("delete", "/categories/a")[0] == 404


def test_category_new_slug_via_api_and_mcp(api: Api, mcp: McpClient) -> None:
    Category.objects.create(name="경제", slug="category-0ac600", order=0)
    Category.objects.create(name="정치", slug="politics", order=1)
    status, body = api.call("patch", "/categories/category-0ac600", {"new_slug": "economy"})
    assert status == 200 and obj(body)["slug"] == "economy"
    assert mcp.ok("update_category", {"slug": "economy", "new_slug": "econ"})["slug"] == "econ"
    assert "이미 쓰는 slug" in mcp.fails(
        "update_category", {"slug": "econ", "new_slug": "politics"}
    )
    assert "이미 쓰는 slug" in mcp.fails(
        "update_category", {"slug": "politics", "new_slug": "category-0ac600"}
    )
    assert "slug는" in mcp.fails("update_category", {"slug": "econ", "new_slug": "경제"})
    assert api.call("patch", "/categories/econ", {"new_slug": "Bad_Slug"})[0] == 400


# --- 거부 ---


def test_editor_token_cannot_see_or_call_admin_tools(client: Client, token_signer: Signer) -> None:
    editor = McpClient(client, token_signer.token(scope="editor"))
    source = Source.objects.create(name="a", url=FEED)
    reply = editor.request(
        "tools/call",
        {"name": "set_source_enabled", "arguments": {"id": source.pk, "enabled": False}},
    )
    assert reply["error"] == {"code": -32602, "message": "unknown tool: set_source_enabled"}
    source.refresh_from_db()
    assert source.enabled is True


def test_admin_api_requires_admin_scope(client: Client, token_signer: Signer) -> None:
    Source.objects.create(name="a", url=FEED)
    editor = Api(client, token_signer.token(scope="editor"))
    assert editor.call("get", "/sources")[0] == 403
    assert editor.call("post", "/sources", {"name": "n", "kind": "rss", "url": FEED})[0] == 403
    assert Client().get("/api/admin/sources").status_code == 401
