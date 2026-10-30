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
from articles.models import Article
from collection.models import Item, Source
from editor.models import Alert, EditorCall, EditorRun
from services import articles as svc
from services import editor_prompts
from services.alerts import check_alerts
from services.editor_classify import ClassifyInput, output_schema, validate
from services.editor_runs import start_run
from services.llm_pricing import UnknownModel, cost_usd
from services.retention import prune_items

pytestmark = pytest.mark.django_db

KST = timezone.get_fixed_timezone(9 * 60)
SHADOW = {"EDITOR_PIPELINE_MODE": "shadow", "SANDCORP_NEWS_CLAUDE_KEY": "test-key"}


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
    fake_llm.queue(answer(items))

    message = run()

    editor_run = EditorRun.objects.get()
    assert (editor_run.mode, editor_run.slot, editor_run.status) == (
        "shadow",
        "2026-10-30T06",
        "finished",
    )
    call = EditorCall.objects.get()
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
    assert call.cost_usd > 0 and editor_run.cost_usd == call.cost_usd
    assert call.cache_creation_input_tokens > 0  # 첫 요청이 시스템 프롬프트 캐시를 쓴다
    assert re.fullmatch(r"[0-9a-f]{12}", call.prompt_version)
    # 그림자: 수집물·기사는 그대로
    assert set(Item.objects.values_list("status", flat=True)) == {Item.Status.PENDING}
    assert not Article.objects.exists()
    assert "새 기사 1" in editor_run.note and "그림자" in message
    # 입력 요약에 본문을 남기지 않는다
    assert "발췌" not in json.dumps(call.input, ensure_ascii=False)


def test_request_shape_schema_enum_data_block_and_cache(
    clock: FixedClock, shadow: None, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    fake_llm.queue(answer(items))
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
    fake_llm.queue(answer(items), answer(items))
    run()
    clock.advance(6 * 3600)  # 12:25 슬롯, 분야를 하나 더해도
    svc.add_category("우주항공기술")
    run()

    first, second = fake_llm.requests
    assert first["system"] == second["system"]
    assert "우주항공기술" not in first["system"][0]["text"]
    assert EditorCall.objects.filter(cache_read_input_tokens__gt=0).count() == 1


def test_compare_model_records_second_call_with_same_input(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(2)
    fake_llm.queue(answer(items), answer(items, new_articles=[]))
    with override_settings(**SHADOW, EDITOR_MODEL_CLASSIFY_COMPARE="claude-haiku-5-5"):
        run()

    calls = list(EditorCall.objects.order_by("id"))
    assert [c.model for c in calls] == ["claude-sonnet-5-5", "claude-haiku-5-5"]
    assert calls[0].key.endswith("-classify") and calls[1].key.endswith("-classify-compare")
    assert calls[0].input == calls[1].input
    assert fake_llm.requests[0]["messages"] == fake_llm.requests[1]["messages"]
    editor_run = EditorRun.objects.get()
    assert editor_run.cost_usd == calls[0].cost_usd + calls[1].cost_usd
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
    fake_llm.queue(answer(items))
    run()
    clock.advance(3600)  # 07:25, 같은 슬롯
    assert "이미 열림" in run()
    assert EditorRun.objects.count() == 1 and len(fake_llm.requests) == 1

    # 놓친 슬롯: 12시 슬롯을 14:25에 따라잡는다
    set_clock(FixedClock(datetime(2026, 10, 30, 14, 25, tzinfo=KST).astimezone(UTC)))
    fake_llm.queue(answer(items))
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
    fake_llm.queue(Truncated(), answer(items))
    run()
    call = EditorCall.objects.get()
    assert (call.status, call.attempt) == ("succeeded", 1)
    assert len(fake_llm.requests) == 2
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
    fake_llm.queue(answer(items))
    run()
    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)

    listing = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/")
    assert listing.status_code == 200
    call = EditorCall.objects.get()
    assert listing.context["month_cost"] == call.cost_usd
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
    fake_llm.queue(answer(items))
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("18")):
        run()
    assert EditorRun.objects.get().status == EditorRun.Status.FINISHED
    assert len(fake_llm.requests) == 1


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
    fake_llm.queue(answer(items))
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
