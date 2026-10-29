"""MCP over JSON-RPC: one message in, at most one message out. No Django, no HTTP here.

Copied from the portfolio repo (sandcorporation/portfolio, PR #28); keep the two in step.

Supported: initialize, ping, tools/list, tools/call, and notifications (ignored).
The server is stateless: no sessions, no server-to-client requests, no resources/prompts.
"""

import json
import logging

from pydantic import ValidationError

from core.auth_adapter import AccessToken
from mcpserver.jsontypes import JSON
from mcpserver.tools import ToolError, visible_tool, visible_tools

# Newest first. initialize answers the client's version when we know it, else the newest.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO: dict[str, JSON] = {"name": "sandcorp-news", "version": "0.1.0"}
INSTRUCTIONS = (
    "Foresight(포사이트, https://news.sandcorp.co.kr) 편집 도구. 한 번의 편집 실행은 "
    "start_editor_run으로 시작하고 finish_editor_run으로 건수를 보고하며 끝낸다. "
    "미처리 수집물(list_pending_items)을 읽어 기사로 묶거나(create_article·update_article) "
    "버린다(discard_item). 분야는 slug로 지정한다."
)

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

type RequestId = str | int
type Message = dict[str, JSON]

logger = logging.getLogger(__name__)


class _Fail(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def error(request_id: RequestId | None, code: int, message: str) -> Message:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def dispatch(message: object, token: AccessToken) -> Message | None:
    """Answer a request; return None for a notification or a client response."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return error(None, INVALID_REQUEST, "expected a JSON-RPC 2.0 object")
    method = message.get("method")
    if "id" not in message:
        if method is None and ("result" in message or "error" in message):
            return None  # a response to a server request; this server sends none
        if isinstance(method, str):
            return None  # notification (e.g. notifications/initialized): nothing to answer
        return error(None, INVALID_REQUEST, "missing method")
    request_id = message["id"]
    if isinstance(request_id, bool) or not isinstance(request_id, str | int):
        return error(None, INVALID_REQUEST, "id must be a string or an integer")
    if not isinstance(method, str):
        return error(request_id, INVALID_REQUEST, "missing method")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return error(request_id, INVALID_PARAMS, "params must be an object")
    try:
        result = _call(method, params, token)
    except _Fail as fail:
        return error(request_id, fail.code, fail.message)
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _call(method: str, params: dict[str, JSON], token: AccessToken) -> dict[str, JSON]:
    match method:
        case "initialize":
            return _initialize(params)
        case "ping":
            return {}
        case "tools/list":
            return {"tools": [registered.definition() for registered in visible_tools(token)]}
        case "tools/call":
            return _call_tool(params, token)
        case _:
            raise _Fail(METHOD_NOT_FOUND, f"method not found: {method}")


def _initialize(params: dict[str, JSON]) -> dict[str, JSON]:
    requested = params.get("protocolVersion")
    version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def _call_tool(params: dict[str, JSON], token: AccessToken) -> dict[str, JSON]:
    name = params.get("name")
    arguments = params.get("arguments", {})
    registered = visible_tool(name, token) if isinstance(name, str) else None
    if registered is None:  # 없는 도구와 토큰 스코프로 안 보이는 도구를 구별하지 않는다
        raise _Fail(INVALID_PARAMS, f"unknown tool: {name}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _Fail(INVALID_PARAMS, "arguments must be an object")
    try:
        result = registered.call(token, arguments)
    except ValidationError as exc:
        return _tool_error(f"invalid arguments: {exc.errors(include_url=False)}")
    except ToolError as exc:
        return _tool_error(str(exc))
    except Exception:
        logger.exception("MCP tool %s failed", name)
        raise _Fail(INTERNAL_ERROR, "internal error") from None
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": False,
    }


def _tool_error(text: str) -> dict[str, JSON]:
    return {"content": [{"type": "text", "text": text}], "isError": True}
