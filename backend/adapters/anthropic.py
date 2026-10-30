"""LLM 어댑터(ADR 0006): Anthropic Messages API 호출은 이 파일에서만 한다.

이 티켓(#123)은 동기 `create`만 둔다(배치는 #126). 키는 `settings.SANDCORP_NEWS_CLAUDE_KEY`를
명시적으로 넘긴다 — SDK가 스스로 읽는 `ANTHROPIC_API_KEY`에는 기대지 않는다.

시험은 autouse 픽스처 `fake_llm`이 `FakeLlm`을 꽂는다. 가짜가 꽂혀 있지 않은데 키가 비었거나
`LLM_FORBID_REAL`(시험 설정)이면 `get_llm()`은 실제 클라이언트를 만들지 않고 `LlmNotConfigured`.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import NotRequired, Protocol, TypedDict

import anthropic
from anthropic.types import (
    Message,
    MessageParam,
    OutputConfigParam,
    TextBlock,
    TextBlockParam,
    ThinkingConfigParam,
    Usage,
)
from django.conf import settings


class LlmRequest(TypedDict):
    """`messages.create`에 넘기는 칸 중 이 저장소가 쓰는 것만(값 타입은 SDK 것)."""

    model: str
    max_tokens: int
    system: list[TextBlockParam]
    messages: list[MessageParam]
    output_config: NotRequired[OutputConfigParam]
    thinking: NotRequired[ThinkingConfigParam]


class LlmError(Exception):
    """API 호출 실패(429·5xx·연결 실패 등, SDK 재시도 뒤). 메시지에 키·본문을 넣지 않는다."""


class LlmNotConfigured(LlmError):
    """키가 없거나(또는 시험 중) 실제 클라이언트를 만들 수 없다."""


class LlmClient(Protocol):
    def create(self, request: LlmRequest) -> Message: ...


class AnthropicClient:
    """실제 구현. SDK 기본 재시도(2회)를 그대로 쓴다."""

    def __init__(self, api_key: str, timeout: float = 600.0) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    def create(self, request: LlmRequest) -> Message:
        try:
            return self._client.messages.create(**request)
        except anthropic.APIStatusError as error:
            raise LlmError(f"API 오류 {error.status_code}: {type(error).__name__}") from None
        except anthropic.APIError as error:
            raise LlmError(f"API 연결 실패: {type(error).__name__}") from None


# --- 가짜 ---


@dataclass(frozen=True)
class Refusal:
    """`stop_reason: refusal` 응답."""


@dataclass(frozen=True)
class Truncated:
    """`stop_reason: max_tokens` — JSON이 중간에 잘린 응답."""


Reply = dict[str, object] | Refusal | Truncated | LlmError

# 실제 서비스의 최소 캐시 길이(토큰). 접두어가 이보다 짧으면 캐시가 써지지도 읽히지도 않는다.
MIN_CACHE_TOKENS = 512


def _tokens(text: str) -> int:
    """가짜의 토큰 어림(2자 ≈ 1토큰, 설계 §7). 실제 값과 맞을 필요는 없고 결정적이기만 하면 된다."""
    return max(1, len(text) // 2)


class FakeLlm:
    """시험용 가짜. 실제 서비스의 조건부 동작을 흉내 낸다(설계 §8).

    - 응답은 `queue()`로 넣은 순서대로 쓴다. 비었으면 시험이 무엇을 기대하는지 모르므로 실패한다.
    - 요청에 `output_config.format`이 없으면 dict 응답이어도 JSON이 아닌 문장을 돌려준다.
    - 시스템 블록에 `cache_control`이 붙어 있고 길이가 최소 이상일 때만 캐시 토큰이 생긴다 —
      같은 모델·같은 접두어의 첫 요청은 `cache_creation_input_tokens`, 다음부터 `cache_read`.
    - `requests`에 보낸 요청을 남긴다.
    """

    def __init__(self) -> None:
        self.replies: list[Reply] = []
        self.requests: list[LlmRequest] = []
        self._cached: set[tuple[str, str]] = set()

    def queue(self, *replies: Reply) -> None:
        self.replies.extend(replies)

    def create(self, request: LlmRequest) -> Message:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("FakeLlm: 준비된 응답이 없다 — 시험에서 fake_llm.queue(...)")
        reply = self.replies.pop(0)
        if isinstance(reply, LlmError):
            raise reply
        usage = self._usage(request)
        if isinstance(reply, Refusal):
            return self._message(request, "", "refusal", usage)
        if isinstance(reply, Truncated):
            usage.output_tokens = request["max_tokens"]
            return self._message(request, '{"new_articles": [{"item_ids": [1', "max_tokens", usage)
        has_format = "format" in request.get("output_config", {})
        text = json.dumps(reply, ensure_ascii=False) if has_format else "네, 분류해 보겠습니다."
        usage.output_tokens = _tokens(text)
        return self._message(request, text, "end_turn", usage)

    def _usage(self, request: LlmRequest) -> Usage:
        cached_prefix = ""
        rest = ""
        for block in request["system"]:
            if "cache_control" in block:
                cached_prefix = rest + block["text"]
                rest = ""
            else:
                rest += block["text"]
        rest += "".join(_message_text(m) for m in request["messages"])
        creation = read = 0
        if cached_prefix and _tokens(cached_prefix) >= MIN_CACHE_TOKENS:
            key = (request["model"], hashlib.sha256(cached_prefix.encode()).hexdigest())
            if key in self._cached:
                read = _tokens(cached_prefix)
            else:
                creation = _tokens(cached_prefix)
                self._cached.add(key)
        else:
            rest = cached_prefix + rest
        return Usage(
            input_tokens=_tokens(rest),
            output_tokens=0,
            cache_creation_input_tokens=creation,
            cache_read_input_tokens=read,
        )

    @staticmethod
    def _message(request: LlmRequest, text: str, stop: str, usage: Usage) -> Message:
        content = [TextBlock(type="text", text=text, citations=None)] if text else []
        return Message.model_construct(
            id="msg_fake",
            type="message",
            role="assistant",
            model=request["model"],
            content=content,
            stop_reason=stop,
            stop_sequence=None,
            usage=usage,
        )


def _message_text(message: MessageParam) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    parts: Iterable[object] = content
    return "".join(str(part.get("text", "")) if isinstance(part, dict) else "" for part in parts)


# --- 등록 지점 ---

_llm: LlmClient | None = None


def get_llm() -> LlmClient:
    if _llm is not None:
        return _llm
    if getattr(settings, "LLM_FORBID_REAL", False):
        raise LlmNotConfigured("시험 중에는 실제 LLM 클라이언트를 만들지 않는다(fake_llm을 쓴다)")
    key = settings.SANDCORP_NEWS_CLAUDE_KEY
    if not key:
        raise LlmNotConfigured("SANDCORP_NEWS_CLAUDE_KEY가 비어 있다")
    return AnthropicClient(key)


def set_llm(client: LlmClient | None) -> LlmClient | None:
    """등록 지점. 이전 값을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _llm
    previous, _llm = _llm, client
    return previous
