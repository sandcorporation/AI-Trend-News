"""/mcp over Streamable HTTP (JSON responses): initialize -> tools/list -> tools/call.

편집자 도구는 `editor` 스코프 토큰으로, 주체는 편집자다. 서비스 오류는 tools/call 결과의
`isError: true`로 돌아온다(프로토콜 오류가 아님).
"""

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from articles.models import Article
from collection.models import Item, Source
from editor.models import EditorRun
from mcpserver import tools
from mcpserver.jsontypes import JSON
from services import articles as article_service
from services import editor_runs
from services.tokens import AccessToken
from tests.conftest import Signer

type Message = dict[str, JSON]

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse

META = "https://news.sandcorp.co.kr/.well-known/oauth-protected-resource"
EDITOR_TOOLS = {
    "upsert_commentary",
    "upsert_post_draft",
    "whoami",
    "list_pending_items",
    "list_recent_articles",
    "create_article",
    "update_article",
    "set_article_flags",
    "discard_item",
    "list_categories",
    "start_editor_run",
    "finish_editor_run",
}
ADMIN_TOOLS = {
    "add_category",
    "list_sources",
    "create_source",
    "update_source",
    "set_source_enabled",
    "update_category",
    "delete_category",
    "reorder_categories",
    "set_article_flags_as_owner",
    "update_article_as_owner",
    "judge_prediction",
}


class McpClient:
    """Speaks JSON-RPC to /mcp with an owner token, the way a Claude connector does."""

    def __init__(self, client: Client, token: str) -> None:
        self.client = client
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        self.next_id = 0

    def send(self, body: object) -> "_MonkeyPatchedWSGIResponse":
        return self.client.post(
            "/mcp", json.dumps(body), content_type="application/json", headers=self.headers
        )

    def request(self, method: str, params: Message | None = None) -> Message:
        self.next_id += 1
        message: Message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self.send(message)
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        answer: Message = response.json()
        assert answer["id"] == self.next_id
        return answer

    def call_tool(self, name: str, arguments: Message) -> Message:
        answer = self.request("tools/call", {"name": name, "arguments": arguments})
        result = answer["result"]
        assert isinstance(result, dict)
        return result

    def ok(self, name: str, arguments: Message) -> dict[str, JSON]:
        """도구를 불러 성공을 확인하고 structuredContent를 돌려준다."""
        result = self.call_tool(name, arguments)
        assert result["isError"] is False, result["content"]
        content = result["structuredContent"]
        assert isinstance(content, dict)
        return content

    def fails(self, name: str, arguments: Message) -> str:
        """도구를 불러 isError를 확인하고 메시지를 돌려준다."""
        result = self.call_tool(name, arguments)
        assert result["isError"] is True, result
        assert "structuredContent" not in result
        content = result["content"]
        assert isinstance(content, list) and isinstance(content[0], dict)
        return str(content[0]["text"])

    def tool_names(self) -> set[str]:
        listed = self.request("tools/list")["result"]
        assert isinstance(listed, dict) and isinstance(listed["tools"], list)
        return {str(t["name"]) for t in listed["tools"] if isinstance(t, dict)}


@pytest.fixture
def mcp(client: Client, token_signer: Signer) -> McpClient:
    return McpClient(client, token_signer.token(scope="editor"))


def test_initialize_list_and_whoami(mcp: McpClient) -> None:
    init = mcp.request(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "claude-ai", "version": "1"},
        },
    )["result"]
    assert isinstance(init, dict)
    assert init["protocolVersion"] == "2025-06-18"
    assert init["capabilities"] == {"tools": {"listChanged": False}}
    assert init["serverInfo"] == {"name": "sandcorp-news", "version": "0.1.0"}
    assert mcp.send({"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202

    listed = mcp.request("tools/list")["result"]
    assert isinstance(listed, dict) and isinstance(listed["tools"], list)
    by_name = {str(t["name"]): t for t in listed["tools"] if isinstance(t, dict)}
    assert set(by_name) == EDITOR_TOOLS
    for definition in by_name.values():
        schema = definition["inputSchema"]
        assert isinstance(schema, dict) and schema["type"] == "object"
        assert schema.get("additionalProperties") is False
        assert definition["description"]
    annotations = by_name["create_article"]["annotations"]
    assert annotations == {"readOnlyHint": False}

    whoami = mcp.call_tool("whoami", {})
    assert whoami["isError"] is False
    assert whoami["structuredContent"] == {
        "sub": "owner",
        "client_id": "client-123",
        "scopes": ["editor"],
        "expires_at": whoami["structuredContent"]["expires_at"],  # type: ignore[index, call-overload]
    }
    content = whoami["content"]
    assert isinstance(content, list) and isinstance(content[0], dict)
    assert json.loads(str(content[0]["text"])) == whoami["structuredContent"]
    assert mcp.request("ping")["result"] == {}


def test_initialize_falls_back_to_newest_known_version(mcp: McpClient) -> None:
    result = mcp.request("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert isinstance(result, dict)
    assert result["protocolVersion"] == "2025-11-25"


def test_without_token_401_points_at_metadata(client: Client) -> None:
    response = client.post("/mcp", "{}", content_type="application/json")
    assert response.status_code == 401
    assert response["WWW-Authenticate"] == f'Bearer resource_metadata="{META}"'
    assert client.get("/.well-known/oauth-protected-resource/mcp").json()["resource"] == (
        "https://news.sandcorp.co.kr/mcp"
    )


@pytest.mark.parametrize("scope", ["login", "admin"])
def test_token_without_editor_scope_is_403(
    client: Client, token_signer: Signer, scope: str
) -> None:
    response = McpClient(client, token_signer.token(scope=scope)).send(
        {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    )
    assert response.status_code == 403
    assert response["WWW-Authenticate"] == 'Bearer error="insufficient_scope", scope="editor"'


@pytest.fixture
def admin_probe_tool() -> Iterator[None]:
    """관리 스코프 도구 하나를 잠시 등록한다(#9가 실제 도구를 더한다)."""

    @tools.tool("admin_probe", "probe", "관리 시험용", tools.NoInput, scope="admin")
    def probe(token: AccessToken, args: tools.NoInput) -> tools.ToolResult:
        return {"ok": True}

    yield
    del tools.TOOLS["admin_probe"]


@pytest.mark.usefixtures("admin_probe_tool")
def test_admin_tools_are_hidden_from_editor_tokens(
    mcp: McpClient, client: Client, token_signer: Signer
) -> None:
    assert mcp.tool_names() == EDITOR_TOOLS
    hidden = mcp.request("tools/call", {"name": "admin_probe", "arguments": {}})["error"]
    assert hidden == {"code": -32602, "message": "unknown tool: admin_probe"}

    admin = McpClient(client, token_signer.token(scope="admin editor"))
    assert admin.tool_names() == EDITOR_TOOLS | ADMIN_TOOLS | {"admin_probe"}
    assert admin.ok("admin_probe", {}) == {"ok": True}


def test_protocol_errors(mcp: McpClient) -> None:
    assert mcp.request("nope")["error"] == {"code": -32601, "message": "method not found: nope"}
    response = mcp.client.post(
        "/mcp", "{not json", content_type="application/json", headers=mcp.headers
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32700
    assert mcp.send([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).status_code == 400
    assert "invalid arguments" in mcp.fails("whoami", {"extra": 1})


def test_get_is_405_but_still_needs_a_token(client: Client, mcp: McpClient) -> None:
    assert client.get("/mcp").status_code == 401
    response = client.get("/mcp", headers=mcp.headers)
    assert response.status_code == 405
    assert response["Allow"] == "POST"


def test_unsupported_protocol_version_header_is_400(mcp: McpClient) -> None:
    mcp.headers["MCP-Protocol-Version"] = "2024-01-01"
    assert mcp.send({"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 400


# --- DB가 필요한 시나리오 ---


def make_items(n: int) -> list[Item]:
    source = Source.objects.create(name="Reuters", url="https://example.com/feed")
    return [
        Item.objects.create(
            source=source,
            url=f"https://example.com/{i}",
            normalized_url=f"https://example.com/{i}",
            title=f"rate decision {i}",
            excerpt=f"excerpt {i}",
        )
        for i in range(n)
    ]


@pytest.mark.django_db
def test_list_pending_items_clips_excerpt_and_caps_page_size(mcp: McpClient) -> None:
    item = make_items(1)[0]
    item.excerpt = "가" * 301
    item.save()
    listed = mcp.ok("list_pending_items", {})["items"]
    assert isinstance(listed, list)
    first = listed[0]
    assert isinstance(first, dict)
    assert first["excerpt"] == "가" * 300 + "…"
    item.refresh_from_db()
    assert len(item.excerpt) == 301
    assert "page_size" in mcp.fails("list_pending_items", {"page_size": 51})


@pytest.mark.django_db
def test_editor_run_scenario(mcp: McpClient, admin_client: Client) -> None:
    items = make_items(4)
    run_id = mcp.ok("start_editor_run", {})["run_id"]
    assert isinstance(run_id, int)

    page = mcp.ok("list_pending_items", {"page": 1, "page_size": 3})
    assert page["total"] == 4
    listed = page["items"]
    assert isinstance(listed, list) and len(listed) == 3
    ids = [i["id"] for i in listed if isinstance(i, dict)]
    assert ids == [items[3].pk, items[2].pk, items[1].pk]  # 최신순
    assert listed[0] == {
        "id": items[3].pk,
        "source": "Reuters",
        "collected_at": items[3].collected_at.isoformat(),
        "published_at": None,
        "title": "rate decision 3",
        "excerpt": "excerpt 3",
        "url": "https://example.com/3",
        "image_url": "",
    }

    category = article_service.add_category("Economy").slug
    assert mcp.ok("list_categories", {}) == {"categories": [{"name": "Economy", "slug": category}]}
    assert "없는 분야입니다: nope" in mcp.fails(
        "create_article",
        {
            "item_ids": [items[0].pk],
            "title": "x",
            "summary": "y",
            "category": "nope",
            "slug": "x-news",
        },
    )

    article = mcp.ok(
        "create_article",
        {
            "item_ids": [items[0].pk, items[1].pk],
            "title": "Rates held",
            "summary": "요약",
            "category": category,
            "slug": "rates-held",
            "tags": ["금리"],
            "published": True,
        },
    )
    article_id = article["id"]
    assert article["published"] is True and article["slug"] == "rates-held"

    again = mcp.fails(
        "create_article",
        {
            "item_ids": [items[1].pk],
            "title": "dup",
            "summary": "",
            "category": category,
            "slug": "dup-news",
        },
    )
    assert f"수집물 {items[1].pk}" in again and f"기사 {article_id}" in again
    assert "Rates held" in again and "rates-held" in again
    assert Article.objects.count() == 1

    # 대표가 admin에서 게시를 끈다 → 편집자는 다시 켤 수 없다.
    data = {
        "title": "Rates held",
        "summary": "요약",
        "category": Article.objects.get().category.pk,
        "image_url": "",
        "published_at_0": "2026-01-01",
        "published_at_1": "00:00:00",
        "featured": "",
        "article_items-TOTAL_FORMS": "0",
        "article_items-INITIAL_FORMS": "0",
        "commentary-TOTAL_FORMS": "0",
        "commentary-INITIAL_FORMS": "0",
    }
    url = reverse("admin:articles_article_change", args=[article_id])
    assert admin_client.post(url, data).status_code == 302
    refused = mcp.fails("set_article_flags", {"id": article_id, "published": True})
    assert f"기사 {article_id}" in refused and "대표" in refused
    assert Article.objects.get().published is False

    updated = mcp.ok(
        "update_article", {"id": article_id, "add_item_ids": [items[2].pk], "summary": "새 요약"}
    )
    bundled = updated["items"]
    assert isinstance(bundled, list)
    assert {i["url"] for i in bundled if isinstance(i, dict)} == {
        f"https://example.com/{n}" for n in range(3)
    }
    assert mcp.ok("discard_item", {"item_id": items[3].pk}) == {
        "id": items[3].pk,
        "status": "discarded",
    }
    assert mcp.ok("list_pending_items", {})["total"] == 0

    recent = mcp.ok("list_recent_articles", {"days": 1})["articles"]
    assert isinstance(recent, list) and len(recent) == 1
    assert isinstance(recent[0], dict) and recent[0]["published"] is False

    finished = mcp.ok(
        "finish_editor_run",
        {"run_id": run_id, "created": 1, "updated": 1, "discarded": 1, "note": "금리 1건"},
    )
    assert finished["created"] == 1 and finished["finished_at"]
    run = EditorRun.objects.get()
    assert (run.created, run.updated, run.discarded, run.note) == (1, 1, 1, "금리 1건")
    assert "이미" in mcp.fails(
        "finish_editor_run", {"run_id": run_id, "created": 0, "updated": 0, "discarded": 0}
    )


@pytest.mark.django_db
def test_recent_articles_respects_days(mcp: McpClient) -> None:
    category = article_service.add_category("IT").slug
    items = make_items(2)
    for item in items:
        mcp.ok(
            "create_article",
            {
                "item_ids": [item.pk],
                "title": item.title,
                "summary": "",
                "category": category,
                "slug": f"rate-decision-{item.pk}",
            },
        )
    Article.objects.filter(title="rate decision 0").update(
        created_at=timezone.now() - timedelta(days=3)
    )
    recent = mcp.ok("list_recent_articles", {"days": 2})["articles"]
    assert isinstance(recent, list)
    titles = [a["title"] for a in recent if isinstance(a, dict)]
    assert titles == ["rate decision 1"]
    assert "invalid arguments" in mcp.fails("list_recent_articles", {"days": 0})


@pytest.mark.django_db
def test_unfinished_run_counts_as_missed_after_24_hours(admin_client: Client) -> None:
    run = editor_runs.start_run()
    assert run.state(run.started_at + timedelta(hours=23)) is EditorRun.State.RUNNING
    assert run.state(run.started_at + timedelta(hours=24)) is EditorRun.State.ABANDONED
    assert editor_runs.last_finished_run() is None
    editor_runs.finish_run(run.pk, created=0, updated=0, discarded=0)
    run.refresh_from_db()
    assert run.state(run.started_at + timedelta(days=9)) is EditorRun.State.FINISHED
    assert editor_runs.last_finished_run() == run

    changelist = admin_client.get(reverse("admin:editor_editorrun_changelist"))
    assert changelist.status_code == 200
    assert "종료" in changelist.content.decode()
    assert admin_client.get(reverse("admin:editor_editorrun_add")).status_code == 403
