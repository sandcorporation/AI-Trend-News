"""편집자 파이프라인 1단계(#123): `call_command("run_editor")` + 고정 시계 + 가짜 LLM."""

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from anthropic.types import Usage
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.utils import timezone
from sandcorp_authkit.testing import FixedClock

from adapters.anthropic import (
    FakeLlm,
    LlmError,
    LlmNotConfigured,
    Refusal,
    Truncated,
    get_llm,
    set_llm,
)
from adapters.clock import set_clock
from adapters.discord import FakeNotifier
from adapters.fetch import FetchError, set_fetcher
from articles.models import Article
from collection.models import Item, Source
from editor.models import Alert, EditorCall, EditorRun
from services import articles as svc
from services import editor_budget, editor_prompts, editor_write
from services.alerts import check_alerts
from services.editor_classify import ClassifyInput, output_schema, validate
from services.editor_compare import compare
from services.editor_runs import start_run
from services.editor_write import FETCHES_PER_RUN, RunReader
from services.llm_pricing import UnknownModel, cost_usd
from services.retention import prune_items

pytestmark = pytest.mark.django_db

KST = timezone.get_fixed_timezone(9 * 60)
SHADOW = {
    "EDITOR_PIPELINE_MODE": "shadow",
    "SANDCORP_NEWS_CLAUDE_KEY": "test-key",
    "EDITOR_USE_BATCH": False,  # 동기 경로(#125). 배치는 test_editor_batch.py
}


@pytest.fixture
def clock() -> Iterator[FixedClock]:
    """2026-10-30 06:25 KST(편집 시각 06시 슬롯의 첫 주기)."""
    fixed = FixedClock(datetime(2026, 10, 30, 6, 25, tzinfo=KST).astimezone(UTC))
    previous = set_clock(fixed)
    yield fixed
    set_clock(previous)


@pytest.fixture
def shadow() -> Iterator[None]:
    with override_settings(**SHADOW):
        yield


class Pages:
    """URL별 원문 HTML. 등록하지 않은 URL은 `FetchError`(시험에서 실제 네트워크 금지)."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes | Exception] = {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.requested.append(url)
        page = self.pages.get(url, FetchError("HTTP 404"))
        if isinstance(page, Exception):
            raise page
        return page


@pytest.fixture(autouse=True)
def pages() -> Iterator[Pages]:
    fetcher = Pages()
    previous = set_fetcher(fetcher)
    yield fetcher
    set_fetcher(previous)


def written(**override: object) -> dict[str, object]:
    """작성 단계(새 기사)의 규칙에 맞는 응답."""
    reply: dict[str, object] = {
        "title": "오픈AI, 새 모델 공개",
        "slug": "openai-new-model",
        "summary": "오픈AI가 새 모델을 공개했다. 성능이 올랐다.",
        "tags": ["OpenAI", "모델"],
        "published": True,
        "unpublish_reason": "",
    }
    reply.update(override)
    return reply


def make_items(n: int) -> list[Item]:
    source = Source.objects.create(name="Reuters", url="https://example.com/feed")
    return [
        Item.objects.create(
            source=source,
            url=f"https://example.com/{i}",
            normalized_url=f"https://example.com/{i}",
            title=f"제목 {i}",
            excerpt="발췌 " * 200,
        )
        for i in range(n)
    ]


def categories() -> None:
    svc.add_category("AI")
    svc.add_category("IT")


def answer(items: list[Item], **override: object) -> dict[str, object]:
    """첫 수집물은 새 기사, 나머지는 버림."""
    slug = svc.list_categories()[0].slug
    reply: dict[str, object] = {
        "new_articles": [
            {
                "item_ids": [items[0].pk],
                "category": slug,
                "published": True,
                "featured": True,
                "reason": "중요한 발표",
            }
        ],
        "updates": [],
        "discards": [{"item_id": i.pk, "reason": "홍보"} for i in items[1:]],
        "holds": [],
        "note": "",
    }
    reply.update(override)
    return reply


def run(out: StringIO | None = None) -> str:
    buffer = out or StringIO()
    call_command("run_editor", stdout=buffer)
    return buffer.getvalue()


# --- run_editor 관통 ---


def test_shadow_slot_records_one_call_and_changes_nothing(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(3)
    fake_llm.queue(answer(items), written())

    message = run()

    editor_run = EditorRun.objects.get()
    assert (editor_run.mode, editor_run.slot, editor_run.status) == (
        "shadow",
        "2026-10-30T06",
        "finished",
    )
    call = EditorCall.objects.get(stage="classify")
    assert (call.stage, call.status, call.model, call.effort) == (
        "classify",
        "succeeded",
        "claude-sonnet-5-5",
        "medium",
    )
    assert call.item_ids == [i.pk for i in items] or sorted(call.item_ids) == sorted(
        i.pk for i in items
    )
    assert call.output is not None
    decision = call.output["decision"]
    assert [a["item_ids"] for a in decision["new_articles"]] == [[items[0].pk]]
    assert len(decision["discards"]) == 2
    write = EditorCall.objects.get(stage="write_new")
    assert call.cost_usd > 0 and editor_run.cost_usd == call.cost_usd + write.cost_usd
    assert call.cache_creation_input_tokens > 0  # 첫 요청이 시스템 프롬프트 캐시를 쓴다
    assert re.fullmatch(r"[0-9a-f]{12}", call.prompt_version)
    # 그림자: 수집물·기사는 그대로
    assert set(Item.objects.values_list("status", flat=True)) == {Item.Status.PENDING}
    assert not Article.objects.exists()
    assert "새 기사 1" in editor_run.note and "그림자" in message
    # 입력 요약에 본문을 남기지 않는다
    assert "발췌 발췌" not in json.dumps(call.input, ensure_ascii=False)


def test_request_shape_schema_enum_data_block_and_cache(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    fake_llm.queue(answer(items), written())
    run()

    request = json.loads(json.dumps(fake_llm.requests[0]))  # 깊은 색인을 위해 평범한 JSON으로
    schema = request["output_config"]["format"]["schema"]
    category_enum = schema["properties"]["new_articles"]["items"]["properties"]["category"]["enum"]
    assert category_enum == [c.slug for c in svc.list_categories()]
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    user = request["messages"][0]["content"]
    assert isinstance(user, str)
    data = json.loads(user.split("<data>\n", 1)[1].split("\n</data>", 1)[0])
    assert [i["id"] for i in data["items"]] == [i.pk for i in reversed(items)]  # 최신순
    assert all(len(i["excerpt"]) <= 300 for i in data["items"])
    assert data["today"] == "2026-10-30"


def test_system_prompt_is_same_bytes_across_runs(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    fake_llm.queue(answer(items), written(), answer(items), written())
    run()
    clock.advance(6 * 3600)  # 12:25 슬롯, 분야를 하나 더해도
    svc.add_category("우주항공기술")
    run()

    first, second = fake_llm.requests[0], fake_llm.requests[2]
    assert first["system"] == second["system"]
    assert "우주항공기술" not in first["system"][0]["text"]
    classify = EditorCall.objects.filter(stage="classify")
    assert classify.filter(cache_read_input_tokens__gt=0).count() == 1


def test_compare_model_records_second_call_with_same_input(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    fake_llm.queue(answer(items), answer(items, new_articles=[]), written())
    with override_settings(**SHADOW, EDITOR_MODEL_CLASSIFY_COMPARE="claude-haiku-5-5"):
        run()

    calls = list(EditorCall.objects.filter(stage="classify").order_by("id"))
    assert [c.model for c in calls] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert calls[0].key.endswith("-classify") and calls[1].key.endswith("-classify-compare")
    assert calls[0].input == calls[1].input
    assert fake_llm.requests[0]["messages"] == fake_llm.requests[1]["messages"]
    editor_run = EditorRun.objects.get()
    write = EditorCall.objects.get(stage="write_new")  # 작성은 주 분류의 판단으로만
    assert editor_run.cost_usd == calls[0].cost_usd + calls[1].cost_usd + write.cost_usd
    assert "비교 claude-haiku-5-5" in editor_run.note


@pytest.mark.parametrize(
    ("hour", "minute"),
    [(5, 25), (9, 25), (10, 0), (23, 25)],
)
def test_not_a_slot_does_nothing(hour: int, minute: int, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    make_items(1)
    set_clock(FixedClock(datetime(2026, 10, 30, hour, minute, tzinfo=KST).astimezone(UTC)))
    assert "편집 시각 아님" in run()
    assert not EditorRun.objects.exists() and not fake_llm.requests


def test_same_slot_runs_once_and_catches_up_within_three_hours(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    fake_llm.queue(answer(items), written())
    run()
    clock.advance(3600)  # 07:25, 같은 슬롯
    assert "이미 열림" in run()
    assert EditorRun.objects.count() == 1 and len(fake_llm.requests) == 2

    # 놓친 슬롯: 12시 슬롯을 14:25에 따라잡는다
    set_clock(FixedClock(datetime(2026, 10, 30, 14, 25, tzinfo=KST).astimezone(UTC)))
    fake_llm.queue(answer(items), written())
    run()
    assert EditorRun.objects.filter(slot="2026-10-30T12").exists()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"EDITOR_PIPELINE_MODE": "off", "SANDCORP_NEWS_CLAUDE_KEY": "test-key"}, "꺼짐"),
        ({"EDITOR_PIPELINE_MODE": "shadow", "SANDCORP_NEWS_CLAUDE_KEY": ""}, "키 없음"),
        ({"EDITOR_PIPELINE_MODE": "live", "SANDCORP_NEWS_CLAUDE_KEY": "test-key"}, "아직 없음"),
    ],
)
def test_off_missing_key_or_live_skip_quietly(
    overrides: dict[str, str], expected: str, clock: FixedClock, fake_llm: FakeLlm
) -> None:
    """운영에는 키가 없다 — 스케줄러 주기에서 아무것도 쓰지 않고 실패하지도 않는다."""
    categories()
    make_items(1)
    with override_settings(**overrides):
        assert expected in run()
    assert not EditorRun.objects.exists() and not fake_llm.requests
    assert not Alert.objects.exists()


def test_default_settings_are_off(clock: FixedClock, fake_llm: FakeLlm) -> None:
    assert settings.EDITOR_PIPELINE_MODE == "off" and settings.SANDCORP_NEWS_CLAUDE_KEY == ""
    assert "꺼짐" in run()


def test_unknown_model_does_not_open_run(clock: FixedClock, fake_llm: FakeLlm) -> None:
    make_items(1)
    with (
        override_settings(**SHADOW, EDITOR_MODEL_CLASSIFY="claude-unknown-9"),
        pytest.raises(CommandError, match="가격표"),
    ):
        run()
    assert not EditorRun.objects.exists() and not fake_llm.requests


def test_no_pending_items_finishes_without_call(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    run()
    editor_run = EditorRun.objects.get()
    assert editor_run.status == "finished" and editor_run.note == "미처리 수집물 없음"
    assert not fake_llm.requests


def test_refusal_fails_without_retry(clock: FixedClock, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    make_items(1)
    fake_llm.queue(Refusal())
    run()
    call = EditorCall.objects.get()
    assert (call.status, call.stop_reason, call.attempt) == ("failed", "refusal", 0)
    assert EditorRun.objects.get().status == "failed"
    assert len(fake_llm.requests) == 1
    assert set(Item.objects.values_list("status", flat=True)) == {Item.Status.PENDING}


def test_max_tokens_retries_once_then_succeeds(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    fake_llm.queue(Truncated(), answer(items), written())
    run()
    call = EditorCall.objects.get(stage="classify")
    assert (call.status, call.attempt) == ("succeeded", 1)
    assert len(fake_llm.requests) == 3
    retry_text = fake_llm.requests[1]["messages"][0]["content"]
    assert isinstance(retry_text, str) and "max_tokens" in retry_text
    assert call.cache_read_input_tokens > 0  # 두 요청의 토큰을 합친다


def test_max_tokens_twice_fails(clock: FixedClock, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    make_items(1)
    fake_llm.queue(Truncated(), Truncated())
    run()
    assert EditorCall.objects.get().status == "failed"
    assert EditorRun.objects.get().status == "failed"


def test_api_error_fails_run(clock: FixedClock, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    make_items(1)
    fake_llm.queue(LlmError("API 오류 429: RateLimitError"))
    run()
    call = EditorCall.objects.get()
    assert call.status == "failed" and "429" in call.error
    assert EditorRun.objects.get().status == "failed"


# --- 가짜의 조건부 동작 ---


def test_fake_without_output_format_returns_prose(fake_llm: FakeLlm) -> None:
    fake_llm.queue({"a": 1})
    message = fake_llm.create(
        {
            "model": "claude-sonnet-5-5",
            "max_tokens": 10,
            "system": [{"type": "text", "text": "짧다"}],
            "messages": [{"role": "user", "content": "x"}],
        }
    )
    text = message.content[0].text  # type: ignore[union-attr]
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    assert message.usage.cache_creation_input_tokens == 0  # cache_control 없음


def test_get_llm_without_fake_raises() -> None:
    previous = set_llm(None)
    try:
        with pytest.raises(LlmNotConfigured):
            get_llm()
        with override_settings(LLM_FORBID_REAL=False), pytest.raises(LlmNotConfigured):
            get_llm()  # 키도 비었다
    finally:
        set_llm(previous)


# --- 검증 규칙 ---


def data_with(item_ids: list[int], article_ids: list[int] = ()) -> ClassifyInput:  # type: ignore[assignment]
    return {
        "today": "2026-10-30",
        "categories": [{"slug": "ai", "name": "AI"}],
        "recent_articles": [
            {
                "id": a,
                "title": "t",
                "category": "ai",
                "published": True,
                "featured": False,
                "has_commentary": False,
            }
            for a in article_ids
        ],
        "items": [
            {
                "id": i,
                "source": "s",
                "published_at": None,
                "title": "t",
                "excerpt": "",
                "has_image": False,
            }
            for i in item_ids
        ],
    }


def new(ids: list[int], *, published: bool = True, featured: bool = False) -> dict[str, object]:
    return {
        "item_ids": ids,
        "category": "ai",
        "published": published,
        "featured": featured,
        "reason": "r",
    }


def output(**parts: object) -> dict[str, object]:
    base: dict[str, object] = {
        "new_articles": [],
        "updates": [],
        "discards": [],
        "holds": [],
        "note": "",
    }
    base.update(parts)
    return base


def test_validate_unknown_duplicate_and_missing_ids() -> None:
    result = validate(
        output(
            new_articles=[new([1, 99])],
            discards=[{"item_id": 1, "reason": "중복"}, {"item_id": 2, "reason": "x" * 300}],
        ),
        data_with([1, 2, 3]),
    )
    d = result.decision
    assert d["new_articles"][0]["item_ids"] == [1]
    assert d["discards"] == [{"item_id": 2, "reason": "x" * 80}]
    assert d["holds"] == [{"item_id": 3, "reason": "판단 없음"}]
    assert any("99" in p for p in result.problems) and any("중복" in p for p in result.problems)


def test_validate_update_to_unknown_article_holds_items() -> None:
    result = validate(
        output(
            updates=[
                {"article_id": 5, "item_ids": [1], "reason": "r"},
                {"article_id": 7, "item_ids": [2], "reason": "r"},
            ]
        ),
        data_with([1, 2], article_ids=[7]),
    )
    assert result.decision["updates"] == [{"article_id": 7, "item_ids": [2], "reason": "r"}]
    assert result.decision["holds"] == [{"item_id": 1, "reason": "갱신 대상 기사를 모름"}]


@override_settings(EDITOR_MAX_FEATURED=2)
def test_validate_featured_requires_published_and_is_capped() -> None:
    result = validate(
        output(
            new_articles=[
                new([1], published=False, featured=True),
                new([2], featured=True),
                new([3], featured=True),
                new([4], featured=True),
            ]
        ),
        data_with([1, 2, 3, 4]),
    )
    assert [a["featured"] for a in result.decision["new_articles"]] == [False, True, True, False]


@override_settings(EDITOR_MAX_WRITES=2)
def test_validate_write_cap_holds_overflow() -> None:
    result = validate(
        output(
            new_articles=[new([1]), new([2])],
            updates=[{"article_id": 7, "item_ids": [3], "reason": "r"}],
        ),
        data_with([1, 2, 3], article_ids=[7]),
    )
    assert len(result.decision["new_articles"]) == 2 and result.decision["updates"] == []
    assert result.decision["holds"] == [{"item_id": 3, "reason": "쓰기 상한 초과"}]


# --- 스키마·프롬프트·비용 ---

FORBIDDEN = {"minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "$ref"}


def walk(node: object) -> Iterator[dict[str, object]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_schema_respects_structured_output_limits() -> None:
    schema = output_schema(["ai", "it"])
    nodes = list(walk(schema))
    objects = [n for n in nodes if n.get("type") == "object"]
    assert len(objects) == 5
    assert all(n["additionalProperties"] is False for n in objects)
    for n in objects:
        properties = n["properties"]
        assert isinstance(properties, dict) and n["required"] == list(properties)
    assert not any(FORBIDDEN & set(n) for n in nodes)


def test_every_editor_md_section_belongs_to_a_stage() -> None:
    text = (editor_prompts.automation_dir() / "editor.md").read_text(encoding="utf-8")
    _, sections = editor_prompts.parse_editor_md(text)
    used = set(editor_prompts.COMMON_SECTIONS) | set(editor_prompts.ROUTINE_ONLY_SECTIONS)
    for titles in editor_prompts.STAGE_SECTIONS.values():
        used |= set(titles)
    assert set(sections) <= used, f"STAGE_SECTIONS에 없는 절: {set(sections) - used}"
    assert used - set(editor_prompts.ROUTINE_ONLY_SECTIONS) <= set(sections)


def test_classify_prompt_has_chosen_sections_only() -> None:
    prompt = editor_prompts.system_prompt("classify")
    assert "## 중복 판단" in prompt and "## 하지 말 것" in prompt and "# 이번 단계: 분류" in prompt
    assert "## 실행 절차" not in prompt and "## AI 분석" not in prompt
    assert "MCP 도구뿐이다" not in prompt  # 서두 둘째 문단(루틴 전용)


def usage(i: int, o: int, created: int = 0, read: int = 0) -> Usage:
    return Usage(
        input_tokens=i,
        output_tokens=o,
        cache_creation_input_tokens=created,
        cache_read_input_tokens=read,
    )


def test_cost_formula() -> None:
    u = usage(1000, 400, created=2000, read=3000)
    # (1000×2 + 2000×2×1.25 + 3000×0.20 + 400×10) / 10⁶
    assert cost_usd("claude-sonnet-5-5", u) == Decimal("0.011600")
    assert cost_usd("claude-sonnet-5-5", u, batch=True) == Decimal("0.005800")
    assert cost_usd("claude-opus-5-5", usage(1000, 1000)) == Decimal("0.024000")
    assert cost_usd("claude-haiku-5-5", usage(100_000, 1000)) == Decimal("0.010500")
    # 100K 초과면 그 호출 전체가 긴 문맥 단가($0.50/$2.50)
    assert cost_usd("claude-haiku-5-5", usage(120_000, 1000)) == Decimal("0.062500")
    with pytest.raises(UnknownModel):
        cost_usd("gpt-9", u)


# --- 경보·정리·admin·MCP·확인 명령 ---


def test_shadow_runs_do_not_silence_editor_missing_alert(fake_discord: FakeNotifier) -> None:
    now = timezone.now()
    EditorRun.objects.create(
        mode="shadow", slot="2026-10-30T06", status="finished", finished_at=now
    )
    sent = check_alerts(fake_discord, now)
    assert any("편집자 미실행" in m for m in sent)

    routine = start_run()
    EditorRun.objects.filter(pk=routine.pk).update(finished_at=now)
    assert routine.mode == "routine"
    assert any("[복구]" in m for m in check_alerts(fake_discord, now))


def test_prune_clears_old_call_payloads_but_keeps_cost() -> None:
    editor_run = EditorRun.objects.create(mode="shadow", slot="2026-09-01T06")
    old = EditorCall.objects.create(
        run=editor_run,
        stage="classify",
        key="r1-classify",
        model="m",
        input={"item_ids": [1]},
        output={"decision": {}},
        input_tokens=10,
        cost_usd=Decimal("0.5"),
    )
    fresh = EditorCall.objects.create(
        run=editor_run,
        stage="classify",
        key="r1-classify-compare",
        model="m",
        input={"item_ids": [1]},
        output={"decision": {}},
    )
    EditorCall.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=31))

    assert prune_items().calls_cleared == 1
    old.refresh_from_db()
    fresh.refresh_from_db()
    assert (old.input, old.output, old.input_tokens, old.cost_usd) == ({}, None, 10, Decimal("0.5"))
    assert fresh.output == {"decision": {}}
    assert prune_items().calls_cleared == 0


def test_admin_shows_month_total_and_calls(
    client: Client, clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    fake_llm.queue(answer(items), written())
    run()
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)

    listing = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/")
    assert listing.status_code == 200
    call = EditorCall.objects.get(stage="classify")
    assert listing.context["month_cost"] == EditorRun.objects.get().cost_usd
    assert "편집 호출 추정 비용 합계" in listing.content.decode()

    detail = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/{call.run_id}/change/")
    html = detail.content.decode()
    assert detail.status_code == 200 and call.key in html and "new_articles" in html


def test_editor_probe_prints_and_saves_nothing(fake_llm: FakeLlm) -> None:
    categories()
    items = make_items(3)
    fake_llm.queue(answer(items))
    out = StringIO()
    with override_settings(SANDCORP_NEWS_CLAUDE_KEY="test-key"):
        call_command("editor_probe", "--items", "2", stdout=out)
    text = out.getvalue()
    assert "결과 succeeded" in text and "추정 US$" in text
    assert not EditorRun.objects.exists() and not EditorCall.objects.exists()
    data = fake_llm.requests[0]["messages"][0]["content"]
    assert isinstance(data, str) and data.count('"source"') == 2


def test_mcp_runs_stay_routine() -> None:
    assert start_run().mode == EditorRun.Mode.ROUTINE


# --- 월 예산 상한(#124) ---


def budget_msgs(notifier: FakeNotifier) -> list[str]:
    return [m for m in notifier.sent if "예산" in m]


def fixed_cost_call(run: EditorRun, cost: str, stage: str = "classify", n: int = 0) -> EditorCall:
    return EditorCall.objects.create(
        run=run, stage=stage, key=f"fixed-{stage}-{cost}-{n}", cost_usd=Decimal(cost)
    )


def test_under_budget_runs_normally(clock: FixedClock, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    items = make_items(1)
    fake_llm.queue(answer(items), written())
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("18")):
        run()
    assert EditorRun.objects.get().status == EditorRun.Status.FINISHED
    assert len(fake_llm.requests) == 2


def test_over_budget_skips_run_alerts_once_and_recovers_next_month(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    categories()
    make_items(2)
    past = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-29T18")
    fixed_cost_call(past, "17.95")  # 17.95 + 예상 0.104 > 18

    out = run()
    skipped = EditorRun.objects.get(slot="2026-10-30T06")
    assert skipped.status == EditorRun.Status.SKIPPED_BUDGET and skipped.finished_at is not None
    assert "17.95" in skipped.note and "상한 US$18" in skipped.note
    assert "월 한도 도달, 다음 달 1일 재개" in skipped.note and "건너뜀" in out
    assert not fake_llm.requests  # 호출하지 않았다
    assert Item.objects.filter(status=Item.Status.PENDING).count() == 2  # 수집물은 그대로

    check_alerts(fake_discord, clock.now())
    assert len(budget_msgs(fake_discord)) == 1
    assert "월 한도 도달, 다음 달 1일 재개" in budget_msgs(fake_discord)[0]

    clock.advance(6 * 3600)  # 다음 슬롯(12시)도 건너뛴다 — 재발송은 없다
    run()
    assert EditorRun.objects.filter(status=EditorRun.Status.SKIPPED_BUDGET).count() == 2
    check_alerts(fake_discord, clock.now())
    assert len(budget_msgs(fake_discord)) == 1

    clock.advance(2 * 86400)  # 11월 1일 이후 — 합계가 0으로 돌아가 복구 한 번
    check_alerts(fake_discord, clock.now())
    assert len(budget_msgs(fake_discord)) == 2 and "복구" in budget_msgs(fake_discord)[1]
    check_alerts(fake_discord, clock.now())
    assert len(budget_msgs(fake_discord)) == 2
    assert not Alert.objects.get(kind=Alert.Kind.EDITOR_BUDGET).active


def test_next_month_runs_again_after_budget_reset(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    past = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-29T18")
    fixed_cost_call(past, "20", "commentary")  # 분류 예상액은 표 값 그대로
    clock.advance((2 * 24 + 6) * 3600)  # 11월 1일 12:25 KST
    fake_llm.queue(answer(items), written())
    run()
    assert EditorRun.objects.latest("pk").status == EditorRun.Status.FINISHED


def test_estimate_uses_seven_day_average_else_table(clock: FixedClock) -> None:
    from services import editor_budget

    now = clock.now()
    assert editor_budget.call_estimate("classify", now) == Decimal("0.104")
    past = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-29T18")
    fixed_cost_call(past, "0.20", n=1)
    fixed_cost_call(past, "0.40", n=2)
    old = fixed_cost_call(past, "9", n=3)
    EditorCall.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=8))
    assert editor_budget.call_estimate("classify", now) == Decimal("0.30")


def test_stage_gate_skips_run_when_estimate_exceeds(clock: FixedClock) -> None:
    from services import editor_budget

    now = clock.now()
    run_ = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-30T06")
    fixed_cost_call(run_, "17.9")
    assert editor_budget.gate_stage(run_, "write_new", 2, now)  # 17.9 + 0.027
    assert not editor_budget.gate_stage(run_, "commentary", 3, now)  # 17.9 + 0.1395 > 18
    run_.refresh_from_db()
    assert run_.status == EditorRun.Status.SKIPPED_BUDGET and "상한" in run_.note


@pytest.mark.parametrize("status", [400, 403])
def test_console_limit_rejection_is_handled_like_budget(
    status: int, clock: FixedClock, shadow: None, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    categories()
    make_items(1)
    fake_llm.queue(LlmError("API 오류", status_code=status))
    run()
    skipped = EditorRun.objects.get()
    assert skipped.status == EditorRun.Status.SKIPPED_BUDGET
    assert f"HTTP {status}" in skipped.note and "다음 달 1일 재개" in skipped.note
    check_alerts(fake_discord, clock.now())
    assert len(budget_msgs(fake_discord)) == 1


def test_server_error_is_not_a_budget_event(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    make_items(1)
    fake_llm.queue(LlmError("API 오류", status_code=500))
    run()
    assert EditorRun.objects.get().status == EditorRun.Status.FAILED


def test_no_key_in_production_skips_quietly_without_run_or_alert(
    clock: FixedClock, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    """운영에는 키가 없다 — 켜 두어도(shadow) 기록 없이 건너뛰고 경보도 없다."""
    make_items(1)
    with override_settings(EDITOR_PIPELINE_MODE="shadow", SANDCORP_NEWS_CLAUDE_KEY=""):
        out = run()
    assert "키 없음" in out and not EditorRun.objects.exists() and not fake_llm.requests
    check_alerts(fake_discord, clock.now())
    assert not any("예산" in m for m in fake_discord.sent)


def test_admin_header_shows_budget(client: Client, clock: FixedClock) -> None:
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("18")):
        html = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/").content.decode()
    assert "월 상한" in html and "US$18" in html


# --- 2단계 기사 작성(#125) ---


def page(text: str) -> bytes:
    return f"<html><body><article><p>{text}</p></article></body></html>".encode()


def data_block(request: object) -> dict[str, object]:
    plain = json.loads(json.dumps(request))
    user = plain["messages"][0]["content"]
    parsed: dict[str, object] = json.loads(user.split("<data>\n", 1)[1].split("\n</data>", 1)[0])
    return parsed


def existing_article(item: Item, image_url: str = "") -> Article:
    slug = svc.list_categories()[0].slug
    return svc.create_article(
        svc.Actor.EDITOR,
        item_ids=[item.pk],
        title="기존 기사",
        summary="기존 요약.",
        category=slug,
        tags=["OpenAI", "모델"],
        published=True,
        image_url=image_url,
        slug="old-article",
    )


def test_shadow_slot_calls_classify_once_and_write_per_article_and_changes_nothing(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, pages: Pages
) -> None:
    categories()
    old_item, *items = make_items(4)
    article = existing_article(old_item)
    slug = svc.list_categories()[0].slug
    decision = answer(
        items,
        new_articles=[
            {"item_ids": [items[0].pk], "category": slug, "published": True,
             "featured": True, "reason": "발표"},
            {"item_ids": [items[1].pk], "category": slug, "published": True,
             "featured": False, "reason": "출시"},
        ],
        updates=[{"article_id": article.pk, "item_ids": [items[2].pk], "reason": "후속"}],
        discards=[],
    )  # fmt: skip
    update_reply: dict[str, object] = {
        "title": None,
        "summary": "기존 요약에 후속 사실을 더했다.",
        "tags": None,
        "reason": "후속 사실",
    }
    fake_llm.queue(decision, written(), written(slug="second-model"), update_reply)

    run()

    calls = list(EditorCall.objects.order_by("id"))
    assert [(c.stage, c.status) for c in calls] == [
        ("classify", "succeeded"),
        ("write_new", "succeeded"),
        ("write_new", "succeeded"),
        ("write_update", "succeeded"),
    ]
    editor_run = EditorRun.objects.get()
    assert [c.key for c in calls[1:]] == [
        f"r{editor_run.pk}-write-1",
        f"r{editor_run.pk}-write-2",
        f"r{editor_run.pk}-update-1",
    ]
    assert calls[1].item_ids == [items[0].pk] and calls[3].article_id == article.pk
    assert calls[3].output is not None
    assert calls[3].output["changes"]["summary"] == "기존 요약에 후속 사실을 더했다."
    assert calls[3].output["changes"]["title"] is None
    assert editor_run.cost_usd == sum((c.cost_usd for c in calls), Decimal(0))
    # 작성 요청: write 프롬프트(캐시), 새 기사 스키마, 분류 결정이 입력에
    write_request = json.loads(json.dumps(fake_llm.requests[1]))
    assert "이번 단계: 기사 작성" in write_request["system"][0]["text"]
    assert write_request["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert write_request["output_config"]["effort"] == "low"
    schema = write_request["output_config"]["format"]["schema"]
    assert set(schema["required"]) == {
        "title", "slug", "summary", "tags", "published", "unpublish_reason"
    }  # fmt: skip
    assert "image" not in json.dumps(schema)  # 모델 출력에 URL 칸이 없다
    data = data_block(fake_llm.requests[1])
    assert data["decided"] == {"published": True, "featured": True}
    update_data = data_block(fake_llm.requests[3])
    assert update_data["kind"] == "update" and update_data["article"] == {
        "id": article.pk, "title": "기존 기사", "summary": "기존 요약.", "tags": ["OpenAI", "모델"]
    }  # fmt: skip
    # 그림자: 수집물·기사는 그대로
    assert set(
        Item.objects.filter(pk__in=[i.pk for i in items]).values_list("status", flat=True)
    ) == {Item.Status.PENDING}
    assert list(Article.objects.values_list("pk", flat=True)) == [article.pk]
    article.refresh_from_db()
    assert article.summary == "기존 요약."
    assert "작성 3건(실패 0)" in editor_run.note


def test_write_input_caps_text_per_item_and_items_per_article(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, pages: Pages
) -> None:
    categories()
    items = make_items(5)
    long_text = "가" * 9000
    for item in items:
        pages.pages[item.url] = page(long_text)
    slug = svc.list_categories()[0].slug
    bundle = [i.pk for i in items[:4]]
    fake_llm.queue(
        answer(
            items,
            new_articles=[{"item_ids": bundle, "category": slug, "published": True,
                           "featured": False, "reason": "같은 뉴스"}],
            discards=[{"item_id": items[4].pk, "reason": "홍보"}],
        ),
        written(),
    )  # fmt: skip

    run()

    data = data_block(fake_llm.requests[1])
    sent = data["items"]
    assert isinstance(sent, list)
    assert [i["id"] for i in sent] == bundle  # 분류가 묶은 순서
    assert [len(i["text"]) for i in sent] == [4000, 4000, 4000, 0]
    assert "3건 초과" in sent[3]["reason"] and sent[3]["excerpt"]
    assert pages.requested == [i.url for i in items[:3]]  # 넷째는 받지도 않는다
    call = EditorCall.objects.get(stage="write_new")
    assert call.input["items"][0] == {"id": bundle[0], "text_chars": 4000, "reason": ""}
    assert "가가가" not in json.dumps(call.input, ensure_ascii=False)  # 원문은 저장하지 않는다
    assert "발췌 발췌" not in json.dumps(call.input, ensure_ascii=False)


def test_fetch_failure_falls_back_to_excerpt(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, pages: Pages
) -> None:
    categories()
    items = make_items(1)
    pages.pages[items[0].url] = FetchError("직접 403, 대체 503")
    fake_llm.queue(answer(items), written())

    run()

    sent = data_block(fake_llm.requests[1])["items"]
    assert isinstance(sent, list)
    assert sent[0]["text"] == "" and "직접 403" in sent[0]["reason"]
    assert sent[0]["excerpt"].startswith("발췌")
    call = EditorCall.objects.get(stage="write_new")
    assert call.status == "succeeded"
    assert call.input["items"][0]["text_chars"] == 0


def test_run_reader_caches_urls_and_caps_fetches() -> None:
    fetcher = Pages()
    fetcher.pages = {f"https://e.com/{n}": page("본문 " * 100) for n in range(3)}
    reader = RunReader(fetcher, limit=2)
    first = reader.read("https://e.com/0")
    assert reader.read("https://e.com/0") == first and first["text"].startswith("본문")
    reader.read("https://e.com/1")
    capped = reader.read("https://e.com/2")
    assert capped["text"] == "" and "페치 상한 2회" in capped["reason"]
    assert fetcher.requested == ["https://e.com/0", "https://e.com/1"]
    assert FETCHES_PER_RUN == 60


def test_image_is_first_bundled_image_and_update_fills_only_empty(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    old_with, old_without, *items = make_items(6)
    Item.objects.filter(pk=items[1].pk).update(image_url="https://img.example.com/b.jpg")
    Item.objects.filter(pk=items[2].pk).update(image_url="https://img.example.com/c.jpg")
    Item.objects.filter(pk=items[3].pk).update(image_url="https://img.example.com/d.jpg")
    with_image = existing_article(old_with, image_url="https://img.example.com/old.jpg")
    without_image = svc.create_article(
        svc.Actor.EDITOR, item_ids=[old_without.pk], title="이미지 없는 기사", summary="요약.",
        category=svc.list_categories()[0].slug, tags=["a", "b"], slug="no-image",
    )  # fmt: skip
    slug = svc.list_categories()[0].slug
    fake_llm.queue(
        answer(
            items,
            new_articles=[{"item_ids": [items[0].pk, items[1].pk, items[2].pk], "category": slug,
                           "published": True, "featured": False, "reason": "발표"}],
            updates=[
                {"article_id": with_image.pk, "item_ids": [items[3].pk], "reason": "후속"},
                {"article_id": without_image.pk, "item_ids": [], "reason": "빈 묶음"},
            ],
            discards=[],
        ),
        written(),
        {"title": None, "summary": None, "tags": None, "reason": "그대로"},
    )  # fmt: skip

    run()

    new = EditorCall.objects.get(stage="write_new")
    assert new.output is not None
    assert new.output["article"]["image_url"] == "https://img.example.com/b.jpg"
    update = EditorCall.objects.get(stage="write_update")
    assert update.output is not None
    assert update.output["changes"]["image_url"] is None  # 기사에 이미지가 이미 있다
    filled = editor_write.prepare_update(
        {"article_id": without_image.pk, "item_ids": [items[3].pk], "reason": ""},
        RunReader(Pages()),
    )
    assert filled["image_url"] == "https://img.example.com/d.jpg"


@pytest.mark.parametrize(
    ("classified", "wrote", "expected"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_published_is_classify_and_write(
    classified: bool,
    wrote: bool,
    expected: bool,
    clock: FixedClock,
    shadow: None,
    fake_llm: FakeLlm,
) -> None:
    categories()
    items = make_items(1)
    slug = svc.list_categories()[0].slug
    fake_llm.queue(
        answer(
            items,
            new_articles=[{"item_ids": [items[0].pk], "category": slug, "published": classified,
                           "featured": classified, "reason": "단독 보도"}],
        ),
        written(published=wrote, unpublish_reason="" if wrote else "출처 불분명"),
    )  # fmt: skip

    run()

    call = EditorCall.objects.get(stage="write_new")
    assert call.output is not None
    article = call.output["article"]
    assert (article["published"], article["featured"]) == (expected, expected)
    note = EditorRun.objects.get().note
    if not expected:
        assert "비게시 1: 오픈AI, 새 모델 공개 — " in note
        assert ("출처 불분명" if classified else "단독 보도") in note
    if classified and not wrote:
        assert any("비게시로 내림" in p for p in call.output["problems"])


def test_rule_violation_retries_once_with_error_then_fails(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    too_long = "가" * 41
    fake_llm.queue(answer(items), written(title=too_long), written(tags=["하나"]))

    run()

    call = EditorCall.objects.get(stage="write_new")
    assert (call.status, call.attempt) == ("failed", 1)
    assert len(fake_llm.requests) == 3
    retry = fake_llm.requests[2]["messages"][0]["content"]
    assert isinstance(retry, str) and "제목은 1~40자" in retry and "41자" in retry
    assert "태그는 2~5개" in call.error
    editor_run = EditorRun.objects.get()
    assert editor_run.status == "finished"  # 작성 호출 하나의 실패는 실행 실패가 아니다
    assert "작성 1건(실패 1)" in editor_run.note and "실패 호출 1" in editor_run.note


def test_rule_violation_then_good_answer_succeeds(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    four_sentences = "하나다. 둘이다. 셋이다. 넷이다."
    fake_llm.queue(answer(items), written(summary=four_sentences), written())
    run()
    call = EditorCall.objects.get(stage="write_new")
    assert (call.status, call.attempt) == ("succeeded", 1)
    retry = fake_llm.requests[2]["messages"][0]["content"]
    assert isinstance(retry, str) and "요약은 1~3문장" in retry


def test_invalid_slug_is_replaced_by_make_slug_and_recorded(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    old, *items = make_items(2)
    existing_article(old)  # slug old-article
    fake_llm.queue(answer(items), written(title="OpenAI GPT launch", slug="오픈AI 모델"))
    run()
    call = EditorCall.objects.get(stage="write_new")
    assert call.output is not None
    assert call.output["article"]["slug"] == "openai-gpt-launch"
    assert any("slug 형식 오류" in p for p in call.output["problems"])
    assert call.attempt == 0  # slug는 재요청 사유가 아니다


def test_duplicate_slug_gets_suffix(clock: FixedClock, shadow: None, fake_llm: FakeLlm) -> None:
    categories()
    old, *items = make_items(2)
    existing_article(old)
    fake_llm.queue(answer(items), written(slug="old-article"))
    run()
    call = EditorCall.objects.get(stage="write_new")
    assert call.output is not None and call.output["article"]["slug"] == "old-article-2"


def test_write_api_error_fails_only_that_call(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    slug = svc.list_categories()[0].slug
    entry = {"category": slug, "published": True, "featured": False, "reason": "발표"}
    fake_llm.queue(
        answer(
            items,
            new_articles=[{"item_ids": [i.pk], **entry} for i in items],
            discards=[],
        ),
        LlmError("API 오류 529: OverloadedError", status_code=529),
        written(),
    )
    run()
    statuses = list(
        EditorCall.objects.filter(stage="write_new").order_by("id").values_list("status", flat=True)
    )
    assert statuses == ["failed", "succeeded"]
    assert EditorRun.objects.get().status == "finished"
    assert set(Item.objects.values_list("status", flat=True)) == {Item.Status.PENDING}


def test_spend_limit_during_write_stops_and_marks_budget(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    categories()
    items = make_items(2)
    slug = svc.list_categories()[0].slug
    entry = {"category": slug, "published": True, "featured": False, "reason": "발표"}
    fake_llm.queue(
        answer(items, new_articles=[{"item_ids": [i.pk], **entry} for i in items], discards=[]),
        LlmError("spend limit", status_code=402),
    )
    run()
    editor_run = EditorRun.objects.get()
    assert editor_run.status == EditorRun.Status.SKIPPED_BUDGET
    assert "HTTP 402" in editor_run.note
    assert EditorCall.objects.filter(stage="write_new").count() == 1  # 둘째는 부르지 않는다
    assert len(fake_llm.requests) == 2
    assert any("편집 예산" in m for m in check_alerts(fake_discord, timezone.now()))


def test_budget_gate_before_write_stage_skips_writes(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm, monkeypatch: pytest.MonkeyPatch
) -> None:
    categories()
    items = make_items(1)
    fake_llm.queue(answer(items))  # 작성 응답은 넣지 않는다 — 부르면 시험이 깨진다
    # 분류(표 값 0.104)는 상한 안, 작성 예상액(기록 없으면 표 값)은 상한을 넘는다
    monkeypatch.setitem(editor_budget.DEFAULT_CALL_COST, EditorCall.Stage.WRITE_NEW, Decimal(100))
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("18")):
        run()
    editor_run = EditorRun.objects.get()
    assert editor_run.status == EditorRun.Status.SKIPPED_BUDGET
    assert "월 예산 초과" in editor_run.note
    assert list(EditorCall.objects.values_list("stage", flat=True)) == ["classify"]


def test_no_key_never_reaches_write_stage(clock: FixedClock, fake_llm: FakeLlm) -> None:
    categories()
    make_items(2)
    with override_settings(EDITOR_PIPELINE_MODE="shadow", SANDCORP_NEWS_CLAUDE_KEY=""):
        assert "키 없음" in run()
    assert not EditorRun.objects.exists() and not fake_llm.requests


def test_unknown_write_model_does_not_open_run(clock: FixedClock, fake_llm: FakeLlm) -> None:
    categories()
    make_items(1)
    with (
        override_settings(**SHADOW, EDITOR_MODEL_WRITE="claude-unknown-9"),
        pytest.raises((UnknownModel, CommandError)),
    ):
        run()
    assert not EditorRun.objects.exists()


def test_run_note_lists_holds_unused_categories_and_failures(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(3)
    fake_llm.queue(
        answer(
            items,
            discards=[{"item_id": items[1].pk, "reason": "홍보"}],
            holds=[{"item_id": items[2].pk, "reason": "출처 확인 필요"}],
        ),
        written(),
    )
    run()
    note = EditorRun.objects.get().note
    assert "보류 까닭: 출처 확인 필요 1" in note
    assert "안 쓴 분야: IT" in note
    assert "실패 호출" not in note


# --- 그림자 비교(#125) ---


def test_shadow_compare_aggregates_against_routine_results(
    client: Client, clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    old, a, b, c, d, e = make_items(6)
    article = existing_article(old)
    slug = svc.list_categories()[0].slug
    entry = {"category": slug, "published": True, "featured": False, "reason": "발표"}
    fake_llm.queue(
        answer(
            [a],
            new_articles=[{"item_ids": [a.pk], **entry}, {"item_ids": [b.pk], **entry}],
            updates=[{"article_id": article.pk, "item_ids": [c.pk], "reason": "후속"}],
            discards=[{"item_id": d.pk, "reason": "홍보"}],
            holds=[{"item_id": e.pk, "reason": "애매"}],
        ),
        written(title="파이프라인 제목 A"),
        written(title="파이프라인 제목 B"),
        {"title": None, "summary": None, "tags": None, "reason": "그대로"},
    )
    run()
    editor_run = EditorRun.objects.get()

    # 15분 뒤 루틴: a는 새 기사(일치), b는 버림(갈림), c는 다른 새 기사(갈림),
    # d는 버림(일치), e는 그대로 미처리(일치)
    routine_a = svc.create_article(
        svc.Actor.EDITOR, item_ids=[a.pk], title="루틴 제목 A", summary="루틴 요약 A.",
        category=slug, tags=["x", "y"], published=True, slug="routine-a",
    )  # fmt: skip
    svc.discard_item(svc.Actor.EDITOR, b.pk)
    routine_c = svc.create_article(
        svc.Actor.EDITOR, item_ids=[c.pk], title="루틴 제목 C", summary="루틴 요약 C.",
        category=slug, tags=["x", "y"], published=True, slug="routine-c",
    )  # fmt: skip
    svc.discard_item(svc.Actor.EDITOR, d.pk)

    result = compare(editor_run)
    by_item = {r.item_id: r for r in result.rows}
    assert [by_item[i.pk].match for i in (a, b, c, d, e)] == [True, False, False, True, True]
    assert (by_item[c.pk].pipeline, by_item[c.pk].actual) == ("update", "new")
    assert by_item[c.pk].pipeline_target == "기존 기사"
    assert result.match_rate == 60.0
    assert result.pipeline_only == 1  # B
    assert [r["id"] for r in result.routine_only] == [routine_c.pk]
    assert result.pairs[0].title == "파이프라인 제목 A"
    assert [r["title"] for r in result.pairs[0].routine] == ["루틴 제목 A"]
    assert result.pairs[0].routine[0]["summary"] == "루틴 요약 A."
    assert result.cost_usd == editor_run.cost_usd and result.calls == 4
    assert routine_a.pk != routine_c.pk

    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)
    detail = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/{editor_run.pk}/change/")
    assert "그림자 비교 보기" in detail.content.decode()
    url = f"/{settings.ADMIN_PATH}editor/editorrun/{editor_run.pk}/shadow/"
    full = client.get(url)
    html = full.content.decode()
    assert full.status_code == 200
    assert 'data-stat="match-rate">60.0%' in html and 'data-stat="pipeline-only">1' in html
    assert 'data-stat="routine-only">1' in html
    assert "파이프라인 제목 A" in html and "루틴 요약 A." in html
    diff = client.get(url + "?diff=1").content.decode()
    assert f'data-item="{b.pk}"' in diff and f'data-item="{a.pk}"' not in diff


def test_shadow_compare_requires_login_and_shadow_run(client: Client) -> None:
    routine = start_run()
    url = f"/{settings.ADMIN_PATH}editor/editorrun/{routine.pk}/shadow/"
    assert client.get(url).status_code == 302  # 로그인 화면으로
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)
    assert client.get(url).status_code == 404  # 루틴 실행에는 비교가 없다
