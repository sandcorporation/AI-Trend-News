"""편집자 파이프라인 4(#126): 작성 단계 Message Batches 경로. 가짜 LLM·고정 시계·가짜 대기만."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from sandcorp_authkit.testing import FixedClock

from adapters.anthropic import BatchErrored, BatchExpired, FakeLlm, LlmError, Refusal, Truncated
from adapters.clock import set_clock
from adapters.discord import FakeNotifier
from adapters.fetch import set_fetcher
from collection.models import Item
from editor.models import Alert, EditorCall, EditorRun
from services import articles as svc
from services import editor_batch
from services.alerts import check_alerts
from services.llm_pricing import cost_usd
from tests.test_editor_pipeline import (
    Pages,
    answer,
    categories,
    existing_article,
    make_items,
    written,
)

pytestmark = pytest.mark.django_db

KST = timezone.get_fixed_timezone(9 * 60)
BATCH = {"EDITOR_PIPELINE_MODE": "shadow", "SANDCORP_NEWS_CLAUDE_KEY": "test-key"}  # 배치가 기본


@pytest.fixture
def clock() -> Iterator[FixedClock]:
    fixed = FixedClock(datetime(2026, 10, 30, 6, 25, tzinfo=KST).astimezone(UTC))
    previous = set_clock(fixed)
    yield fixed
    set_clock(previous)


@pytest.fixture(autouse=True)
def pages() -> Iterator[Pages]:
    fetcher = Pages()
    previous = set_fetcher(fetcher)
    yield fetcher
    set_fetcher(previous)


@pytest.fixture(autouse=True)
def batch_mode() -> Iterator[None]:
    with override_settings(**BATCH):
        yield


def run() -> str:
    out = StringIO()
    call_command("run_editor", stdout=out)
    return out.getvalue()


def two_new(slug: str, items: list[Item]) -> dict[str, object]:
    entry = {"category": slug, "published": True, "featured": False, "reason": "발표"}
    return answer(
        items,
        new_articles=[{"item_ids": [i.pk], **entry} for i in items],
        discards=[],
    )


def setup_two(fake_llm: FakeLlm) -> tuple[list[Item], str]:
    categories()
    items = make_items(2)
    return items, svc.list_categories()[0].slug


def write_calls() -> list[EditorCall]:
    return list(EditorCall.objects.filter(stage__startswith="write").order_by("id"))


# --- 제출 → 같은 주기 안 완료 → 저장 ---


def test_submit_wait_collect_in_same_cycle(
    clock: FixedClock, fake_llm: FakeLlm, fake_sleep: list[float]
) -> None:
    categories()
    old_item, *items = make_items(4)
    article = existing_article(old_item)
    slug = svc.list_categories()[0].slug
    entry = {"category": slug, "published": True, "featured": False, "reason": "발표"}
    decision = answer(
        items,
        new_articles=[{"item_ids": [i.pk], **entry} for i in items[:2]],
        updates=[{"article_id": article.pk, "item_ids": [items[2].pk], "reason": "후속"}],
        discards=[],
    )
    update_reply: dict[str, object] = {
        "title": None, "summary": "후속을 더했다.", "tags": None, "reason": "후속",
    }  # fmt: skip
    fake_llm.queue(
        decision,
        written(title="첫째 기사", slug="first-article"),
        written(title="둘째 기사", slug="second-article"),
        update_reply,
    )

    out = run()

    run_ = EditorRun.objects.get()
    assert run_.status == EditorRun.Status.FINISHED and run_.finished_at is not None
    assert "배치 회수 끝" in out
    # 작성 3건이 요청 한 묶음으로 나갔고, 시계로만 기다렸다(30초 간격).
    assert len(fake_llm.batch_requests) == 1
    assert [r["custom_id"] for r in fake_llm.batch_requests[0]] == [
        f"r{run_.pk}-write-1", f"r{run_.pk}-write-2", f"r{run_.pk}-update-1",
    ]  # fmt: skip
    assert fake_sleep == [30, 30]
    calls = write_calls()
    assert [(c.status, c.via_batch, c.attempt) for c in calls] == [("succeeded", True, 0)] * 3
    assert {c.batch_id for c in calls} == {"msgbatch_fake1"}
    # 결과는 거꾸로 섞여 왔지만 custom_id로 맞아 제 자리에 들어갔다.
    assert calls[0].output is not None and calls[0].output["article"]["title"] == "첫째 기사"
    assert calls[1].output is not None and calls[1].output["article"]["title"] == "둘째 기사"
    assert calls[2].output is not None and calls[2].output["changes"]["article_id"] == article.pk
    assert run_.note.count("작성 3건") == 1
    # 그림자: 수집물·기사는 그대로
    assert Item.objects.filter(pk__in=[i.pk for i in items], status="pending").count() == 3


def test_batch_cost_is_half_of_sync_price(clock: FixedClock, fake_llm: FakeLlm) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    call = write_calls()[0]
    assert call.cost_usd > 0
    from anthropic.types import Usage

    usage = Usage(
        input_tokens=call.input_tokens,
        output_tokens=call.output_tokens,
        cache_creation_input_tokens=call.cache_creation_input_tokens,
        cache_read_input_tokens=call.cache_read_input_tokens,
    )
    assert call.cost_usd == cost_usd(call.model, usage, batch=True)
    assert call.cost_usd * 2 == pytest.approx(cost_usd(call.model, usage), abs=Decimal("0.000002"))
    run_ = EditorRun.objects.get()
    assert run_.cost_usd == sum((c.cost_usd for c in EditorCall.objects.all()), Decimal(0))


# --- 같은 주기 안 미완료 → 다음 주기 회수 ---


def test_unfinished_batch_is_collected_by_next_cycle(
    clock: FixedClock, fake_llm: FakeLlm, fake_sleep: list[float]
) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.batch_polls_until_ended = None
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))

    run()

    run_ = EditorRun.objects.get()
    assert run_.status == EditorRun.Status.RUNNING and run_.finished_at is None
    assert [c.status for c in write_calls()] == ["submitted", "submitted"]
    assert sum(fake_sleep) <= 900 and fake_sleep  # 15분 안에서만 기다렸다

    clock.advance(3600)  # 다음 매시 주기(07:25, 같은 슬롯 안)
    fake_llm.batch_polls_until_ended = 0
    out = run()

    assert "이미 열림" in out and "배치 회수 끝" in out
    run_.refresh_from_db()
    assert run_.status == EditorRun.Status.FINISHED
    assert [c.status for c in write_calls()] == ["succeeded", "succeeded"]
    assert len(fake_llm.batch_requests) == 1  # 다시 제출하지 않았다
    assert EditorRun.objects.count() == 1


# --- 재제출 1회 ---


def test_validation_failure_is_resubmitted_once_with_error_note(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    slug = svc.list_categories()[0].slug
    fake_llm.queue(
        two_new(slug, items), written(title="가" * 60), written(title="고친 제목", slug="fixed")
    )

    run()

    (call,) = write_calls()
    assert (call.status, call.attempt) == ("succeeded", 1)
    assert call.output is not None and call.output["article"]["title"] == "고친 제목"
    assert len(fake_llm.batch_requests) == 2
    retry_text = json.dumps(fake_llm.batch_requests[1][0]["params"]["messages"], ensure_ascii=False)
    assert "제목은 1~40자" in retry_text
    assert call.batch_id == "msgbatch_fake2"
    # 두 번 모두 배치 단가로 합산
    assert call.cost_usd > 0 and call.input_tokens > 0


@pytest.mark.parametrize(
    ("reply", "status"), [(BatchErrored(), "failed"), (BatchExpired(), "expired")]
)
def test_errored_or_expired_resubmits_once_then_ends(
    clock: FixedClock, fake_llm: FakeLlm, reply: BatchErrored | BatchExpired, status: str
) -> None:
    categories()
    items = make_items(1)
    slug = svc.list_categories()[0].slug
    fake_llm.queue(two_new(slug, items), reply, reply)

    run()

    (call,) = write_calls()
    assert (call.status, call.attempt) == (status, 1)
    assert len(fake_llm.batch_requests) == 2  # 재제출은 한 번뿐
    run_ = EditorRun.objects.get()
    assert run_.status == EditorRun.Status.FINISHED and "실패 호출 1" in run_.note


def test_errored_then_success_on_resubmit(clock: FixedClock, fake_llm: FakeLlm) -> None:
    categories()
    items = make_items(1)
    slug = svc.list_categories()[0].slug
    fake_llm.queue(two_new(slug, items), BatchErrored(), written())
    run()
    (call,) = write_calls()
    assert (call.status, call.attempt, call.error) == ("succeeded", 1, "")


def test_refusal_and_truncation_rules(clock: FixedClock, fake_llm: FakeLlm) -> None:
    categories()
    items = make_items(2)
    slug = svc.list_categories()[0].slug
    # 첫째는 refusal(재제출 없음), 둘째는 잘림(재제출 1회, 다시 잘리면 실패)
    fake_llm.queue(two_new(slug, items), Refusal(), Truncated(), Truncated())
    run()
    first, second = write_calls()
    assert (first.status, first.attempt, first.stop_reason) == ("failed", 0, "refusal")
    assert (second.status, second.attempt) == ("failed", 1)
    assert len(fake_llm.batch_requests) == 2 and len(fake_llm.batch_requests[1]) == 1


# --- 만료와 경보 ---


def test_batch_older_than_24h_expires_calls_and_alerts_once(
    clock: FixedClock, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.batch_polls_until_ended = None
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    run_ = EditorRun.objects.get()

    later = clock.now() + timedelta(hours=24, minutes=1)
    assert editor_batch.advance(run_, fake_llm, later)  # 모두 끝 상태

    assert [c.status for c in write_calls()] == ["expired", "expired"]
    sent = check_alerts(fake_discord, later)
    assert [m for m in sent if "편집 배치 지연" in m] and len(sent) >= 1
    assert check_alerts(fake_discord, later) == []  # 같은 조건은 한 번만
    recovered = check_alerts(fake_discord, later + timedelta(hours=25))
    assert any("[복구] 편집 배치 지연" in m for m in recovered)
    assert Alert.objects.get(kind=Alert.Kind.EDITOR_BATCH_STALE).active is False
    # 미처리 수집물은 그대로라 다음 슬롯이 다시 본다(그림자라 어차피 바뀌지 않는다)
    assert Item.objects.filter(status="pending").count() == 2


def test_unfinished_batch_within_24h_is_not_expired(clock: FixedClock, fake_llm: FakeLlm) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.batch_polls_until_ended = None
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    run_ = EditorRun.objects.get()
    assert not editor_batch.advance(run_, fake_llm, clock.now() + timedelta(hours=23))
    assert [c.status for c in write_calls()] == ["submitted", "submitted"]


# --- 중복·죽은 뒤 재실행 ---


def test_collecting_twice_does_not_duplicate_or_double_cost(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    run_ = EditorRun.objects.get()
    before = [(c.pk, c.status, c.cost_usd, c.input_tokens) for c in write_calls()]
    cost = run_.cost_usd

    # 같은 결과를 다시 받아도(복구·중복 호출) 처리된 호출은 건드리지 않는다.
    batch_id = write_calls()[0].batch_id
    for result in fake_llm.batch_results(batch_id):
        editor_batch._apply(batch_id, result, clock.now())
    assert editor_batch.advance(run_, fake_llm, clock.now())
    run()

    assert [(c.pk, c.status, c.cost_usd, c.input_tokens) for c in write_calls()] == before
    run_.refresh_from_db()
    assert run_.cost_usd == cost and EditorRun.objects.count() == 1
    assert len(fake_llm.batch_requests) == 1


def test_stale_result_for_old_batch_is_ignored_after_resubmit(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    categories()
    items = make_items(1)
    slug = svc.list_categories()[0].slug
    fake_llm.queue(two_new(slug, items), BatchErrored(), written())
    run()
    (call,) = write_calls()
    assert call.batch_id == "msgbatch_fake2"
    old = list(fake_llm.batch_results("msgbatch_fake1"))
    editor_batch._apply("msgbatch_fake1", old[0], clock.now())  # 옛 배치 결과: 지금 batch_id와 다름
    call.refresh_from_db()
    assert (call.status, call.attempt) == ("succeeded", 1)


def test_died_after_submit_resumes_without_duplicates(clock: FixedClock, fake_llm: FakeLlm) -> None:
    """제출은 됐는데 회수 전에 죽은 경우: 다음 실행이 같은 배치를 회수한다."""
    items, slug = setup_two(fake_llm)
    fake_llm.batch_polls_until_ended = None
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    run()
    assert [c.status for c in write_calls()] == ["succeeded", "succeeded"]
    assert EditorCall.objects.count() == 3 and len(fake_llm.batch_requests) == 1


def test_submit_failure_keeps_calls_queued_and_next_cycle_submits(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    fake_llm.submit_error = LlmError("API 오류 529: OverloadedError", status_code=529)
    run()
    assert [c.status for c in write_calls()] == ["queued", "queued"]
    assert EditorRun.objects.get().status == EditorRun.Status.RUNNING

    fake_llm.submit_error = None
    clock.advance(3600)
    run()
    assert [c.status for c in write_calls()] == ["succeeded", "succeeded"]
    assert EditorCall.objects.count() == 3


def test_spend_limit_on_submit_closes_run_as_budget(clock: FixedClock, fake_llm: FakeLlm) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.queue(two_new(slug, items))
    fake_llm.submit_error = LlmError("API 오류 400: BadRequestError", status_code=400)
    run()
    run_ = EditorRun.objects.get()
    assert run_.status == EditorRun.Status.SKIPPED_BUDGET
    assert [c.status for c in write_calls()] == ["failed", "failed"]


def test_budget_gate_blocks_batch_submission(clock: FixedClock, fake_llm: FakeLlm) -> None:
    items, slug = setup_two(fake_llm)
    past = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-29T18")
    EditorCall.objects.create(
        run=past, stage="classify", key="old", model="claude-sonnet-5-5", cost_usd=Decimal("17.95")
    )
    fake_llm.queue(two_new(slug, items))
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("18")):
        run()
    new = EditorRun.objects.exclude(pk=past.pk).get()
    assert new.status == EditorRun.Status.SKIPPED_BUDGET
    assert not fake_llm.batch_requests and not write_calls()


# --- 스위치와 운영(키 없음) ---


def test_use_batch_false_goes_sync(clock: FixedClock, fake_llm: FakeLlm) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    with override_settings(EDITOR_USE_BATCH=False):
        run()
    assert not fake_llm.batch_requests
    assert [(c.via_batch, c.status) for c in write_calls()] == [(False, "succeeded")] * 2
    assert EditorRun.objects.get().status == EditorRun.Status.FINISHED


def test_switching_to_sync_still_collects_inflight_batches(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    items, slug = setup_two(fake_llm)
    fake_llm.batch_polls_until_ended = None
    fake_llm.queue(two_new(slug, items), written(), written(slug="second-model"))
    run()
    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    with override_settings(EDITOR_USE_BATCH=False):
        run()
    assert [c.status for c in write_calls()] == ["succeeded", "succeeded"]


def test_default_is_batch_with_fifteen_minute_wait() -> None:
    from django.conf import settings

    assert settings.EDITOR_USE_BATCH is True and settings.EDITOR_BATCH_WAIT_SECONDS == 900


def test_production_without_key_skips_quietly_even_with_batch_default(
    clock: FixedClock, fake_llm: FakeLlm, fake_discord: FakeNotifier
) -> None:
    """운영에는 키가 없다: 켜 두어도(shadow) 실행·호출·경보 없이 건너뛴다. 배치 경로도 마찬가지."""
    make_items(2)
    with override_settings(EDITOR_PIPELINE_MODE="shadow", SANDCORP_NEWS_CLAUDE_KEY=""):
        out = run()
    assert "키 없음" in out
    assert not EditorRun.objects.exists() and not EditorCall.objects.exists()
    assert not fake_llm.requests and not fake_llm.batch_requests
    assert check_alerts(fake_discord, clock.now()) == [] or not any(
        "편집 배치" in m or "예산" in m for m in fake_discord.sent
    )
