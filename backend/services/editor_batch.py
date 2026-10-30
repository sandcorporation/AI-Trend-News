"""2단계 작성의 Message Batches 경로(#126, 설계 §4·§5). 50% 할인 대신 결과가 나중에 온다.

흐름: 분류가 정한 새 기사·갱신마다 `EditorCall`(`via_batch`, `queued`)을 만들고 → `advance`가
한 묶음으로 제출(`submitted`, 24시간 시계는 `submitted_at`부터) → 끝난 배치의 결과를 `custom_id`
(= `EditorCall.key`)로 맞춰 검사하고 `succeeded`로 끝낸다. `run_editor`가 매 주기 앞머리에서
`advance`를 부르고, 같은 주기 안에서 30초 간격으로 기다리며 부르기도 한다(`pending_runs`가 이어 갈
실행을 찾는다). 그림자 모드에서는 저장할 기사가 없으므로 「저장」은 `succeeded`로 기록하는 것이다.

안전 장치:
- 결과 한 건 = 트랜잭션 하나, `select_for_update(skip_locked=True)`로 `submitted` 행만 잡는다 —
  같은 결과를 두 번 받거나 두 프로세스가 동시에 돌아도 한 번만 처리한다. 죽은 뒤 다시 돌아도
  이미 끝난 호출은 건너뛴다.
- `errored`·`expired`·`canceled`·검증 실패·`max_tokens`는 1회 재제출(오류 문구를 덧붙임) 뒤 실패.
  `refusal`은 재제출 없이 실패. 재제출은 원문을 다시 받아(`prepare_plan`) 만든다.
- 24시간 넘게 안 끝난 배치(또는 24시간 넘게 제출 못 한 대기 호출)는 `expired`로 끝낸다 — 경보
  「편집 배치 지연」은 `check_alerts`가 `expired` 호출로 켠다.
- 비용은 `llm_pricing.cost_usd(..., batch=True)`(0.5배)를 호출에 누적한다(재제출 비용도 합산).
"""

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from anthropic.types import Message
from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from adapters.anthropic import BatchRequest, BatchResult, LlmClient, LlmError
from adapters.fetch import get_fetcher
from articles.models import Article, Category
from editor.models import EditorCall, EditorRun
from services import editor_budget, editor_prompts, editor_write, llm_pricing
from services.editor_classify import Decision, InvalidOutput

logger = logging.getLogger("editor")

BATCH_MAX_AGE = timedelta(hours=24)
POLL_SECONDS = 30
MAX_ATTEMPTS = 2  # 처음 + 재제출 1회
TERMINAL = frozenset(
    {
        EditorCall.Status.SUCCEEDED,
        EditorCall.Status.SAVED,
        EditorCall.Status.FAILED,
        EditorCall.Status.EXPIRED,
        EditorCall.Status.SKIPPED,
    }
)
WRITE_STAGES = (EditorCall.Stage.WRITE_NEW, EditorCall.Stage.WRITE_UPDATE)


def enqueue_writes(run: EditorRun, decision: Decision) -> int:
    """분류의 새 기사·갱신마다 `queued` 호출을 만든다(원문 페치는 제출 때). 만든 건수."""
    model, effort = settings.EDITOR_MODEL_WRITE, settings.EDITOR_EFFORT_WRITE
    version = editor_prompts.prompt_version(editor_prompts.system_prompt("write"))
    count = 0
    jobs: list[tuple[str, str, editor_write.Plan, int | None]] = []
    for n, entry in enumerate(decision["new_articles"], start=1):
        jobs.append(
            (EditorCall.Stage.WRITE_NEW, f"r{run.pk}-write-{n}", editor_write.new_plan(entry), None)
        )
    for n, update in enumerate(decision["updates"], start=1):
        plan = editor_write.update_plan(update)
        jobs.append(
            (EditorCall.Stage.WRITE_UPDATE, f"r{run.pk}-update-{n}", plan, update["article_id"])
        )
    for stage, key, plan, article_id in jobs:
        EditorCall.objects.get_or_create(
            key=key,
            defaults={
                "run": run,
                "stage": stage,
                "model": model,
                "effort": effort,
                "via_batch": True,
                "prompt_version": version,
                "status": EditorCall.Status.QUEUED,
                "item_ids": plan["item_ids"],
                "article_id": article_id,
                "input": {"plan": dict(plan)},
            },
        )
        count += 1
    return count


def pending_runs() -> list[EditorRun]:
    """아직 끝나지 않은 배치 호출이 있는 열린 파이프라인 실행(오래된 순)."""
    ids = (
        EditorCall.objects.filter(via_batch=True, run__status=EditorRun.Status.RUNNING)
        .exclude(status__in=TERMINAL)
        .values_list("run_id", flat=True)
        .distinct()
    )
    return list(EditorRun.objects.filter(pk__in=ids).order_by("id"))


def advance(run: EditorRun, llm: LlmClient, now: datetime) -> bool:
    """실행을 한 걸음 민다: 끝난 배치 회수 → 대기 호출 제출. 모든 배치 호출이 끝났으면 True."""
    _collect(run, llm, now)
    _submit_queued(run, llm, now)
    run.refresh_from_db()
    run.cost_usd = run.calls.aggregate(total=Sum("cost_usd"))["total"] or Decimal(0)
    run.save(update_fields=["cost_usd"])
    if run.status != EditorRun.Status.RUNNING:
        return True
    return not run.calls.filter(via_batch=True).exclude(status__in=TERMINAL).exists()


# --- 회수 ---


def _collect(run: EditorRun, llm: LlmClient, now: datetime) -> None:
    batch_ids = list(
        run.calls.filter(status=EditorCall.Status.SUBMITTED)
        .order_by("batch_id")
        .values_list("batch_id", flat=True)
        .distinct()
    )
    for batch_id in batch_ids:
        try:
            state = llm.batch_status(batch_id)
        except LlmError as error:
            logger.warning("배치 상태 조회 실패 %s: %s", batch_id, error)
            continue
        if state != "ended":
            _expire_if_stale(run, batch_id, now)
            continue
        try:
            results = list(llm.batch_results(batch_id))
        except LlmError as error:
            logger.warning("배치 결과 조회 실패 %s: %s", batch_id, error)
            continue
        seen: set[str] = set()
        for result in results:
            seen.add(result.custom_id)
            _apply(batch_id, result, now)
        # 끝났는데 결과가 없는 호출은 errored와 같다.
        for call in run.calls.filter(status=EditorCall.Status.SUBMITTED, batch_id=batch_id):
            if call.key not in seen:
                _apply(batch_id, BatchResult(call.key, "errored", error="배치 결과에 없음"), now)


def _expire_if_stale(run: EditorRun, batch_id: str, now: datetime) -> None:
    stale = run.calls.filter(
        status=EditorCall.Status.SUBMITTED, batch_id=batch_id, submitted_at__lte=now - BATCH_MAX_AGE
    )
    count = stale.update(
        status=EditorCall.Status.EXPIRED,
        error="배치가 24시간 넘게 끝나지 않아 만료로 끝냄",
        finished_at=now,
    )
    if count:
        logger.warning("편집 배치 지연: %s의 호출 %d건을 만료로 끝냄", batch_id, count)


def _first_text(message: Message) -> str:
    for block in message.content:
        if block.type == "text":
            return block.text
    return ""


def _apply(batch_id: str, result: BatchResult, now: datetime) -> None:
    """결과 한 건을 호출에 반영한다(트랜잭션 하나). 이미 처리됐거나 잠겼으면 아무것도 안 한다."""
    with transaction.atomic():
        call = (
            EditorCall.objects.select_for_update(skip_locked=True)
            .filter(key=result.custom_id, status=EditorCall.Status.SUBMITTED, batch_id=batch_id)
            .first()
        )
        if call is None:
            return
        problem = _problem(call, result)
        if problem is None:
            call.status = EditorCall.Status.SUCCEEDED
            call.error = ""
            call.finished_at = now
        elif problem.final or call.attempt + 1 >= MAX_ATTEMPTS:
            call.status = problem.final_status
            call.error = problem.note
            call.finished_at = now
        else:
            call.status = EditorCall.Status.QUEUED  # 다음 `_submit_queued`가 재제출
            call.attempt += 1
            call.error = problem.note
            call.input = {**call.input, "retry_note": problem.note}
            call.batch_id = ""
            call.submitted_at = None
        call.save()


class _Problem:
    def __init__(self, note: str, *, final: bool = False, expired: bool = False) -> None:
        self.note = note
        self.final = final  # 재제출하지 않는다(refusal)
        self.final_status = EditorCall.Status.EXPIRED if expired else EditorCall.Status.FAILED


def _problem(call: EditorCall, result: BatchResult) -> _Problem | None:
    """결과를 호출에 쓰고(토큰·비용·출력), 고쳐야 할 문제가 있으면 돌려준다."""
    if result.outcome != "succeeded" or result.message is None:
        label = {"expired": "배치 요청 만료(expired)", "canceled": "배치 요청 취소(canceled)"}.get(
            result.outcome, result.error or "배치 요청 오류(errored)"
        )
        return _Problem(label, expired=result.outcome == "expired")
    message = result.message
    usage = message.usage
    call.input_tokens += usage.input_tokens
    call.output_tokens += usage.output_tokens
    call.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
    call.cache_read_input_tokens += usage.cache_read_input_tokens or 0
    call.cost_usd += llm_pricing.cost_usd(call.model, usage, batch=True)
    call.stop_reason = message.stop_reason or ""
    if message.stop_reason == "refusal":
        return _Problem("모델이 응답을 거절했다(refusal)", final=True)
    if message.stop_reason == "max_tokens":
        return _Problem("max_tokens에 걸려 JSON이 잘렸다")
    try:
        checked = editor_write.check(
            json.loads(_first_text(message)),
            editor_write.checkable(_plan(call)),
        )
    except (json.JSONDecodeError, InvalidOutput) as error:
        return _Problem(f"JSON이 아니거나 규칙에 맞지 않는다: {error}"[:300])
    call.output = checked["output"]
    return None


def _plan(call: EditorCall) -> editor_write.Plan:
    plan: editor_write.Plan = call.input["plan"]
    return plan


# --- 제출 ---


def _submit_queued(run: EditorRun, llm: LlmClient, now: datetime) -> None:
    if run.status != EditorRun.Status.RUNNING:
        return
    queued = list(run.calls.filter(via_batch=True, status=EditorCall.Status.QUEUED).order_by("id"))
    if not queued:
        return
    fresh: list[EditorCall] = []
    for call in queued:
        if call.created_at <= now - BATCH_MAX_AGE:
            _end(call, EditorCall.Status.EXPIRED, "24시간 넘게 제출하지 못해 만료로 끝냄", now)
        else:
            fresh.append(call)
    retries = [c for c in fresh if c.attempt > 0]
    if retries and not editor_budget.check(EditorCall.Stage.WRITE_NEW, len(retries), now).allowed:
        for call in retries:
            _end(call, EditorCall.Status.FAILED, "월 예산 초과로 재제출하지 않음", now)
        fresh = [c for c in fresh if c.attempt == 0]
    if not fresh:
        return

    names = dict(Category.objects.values_list("slug", "name"))
    reader = editor_write.RunReader(get_fetcher())
    requests: list[BatchRequest] = []
    ready: list[EditorCall] = []
    for call in fresh:
        plan = _plan(call)
        try:
            prepared = editor_write.prepare_plan(plan, names, reader)
        except Article.DoesNotExist:
            _end(call, EditorCall.Status.FAILED, "갱신 대상 기사가 없다", now)
            continue
        plan["image_url"] = prepared["image_url"]
        call.input = {
            **editor_write.input_summary(prepared["data"]),
            "plan": dict(plan),
            **({"retry_note": call.input["retry_note"]} if "retry_note" in call.input else {}),
        }
        call.save(update_fields=["input"])
        requests.append(
            {
                "custom_id": call.key,
                "params": editor_write.request(
                    prepared["data"],
                    call.model,
                    call.effort,
                    retry_note=str(call.input.get("retry_note", "")),
                ),
            }
        )
        ready.append(call)
    if not requests:
        return
    try:
        batch_id = llm.submit_batch(requests)
    except LlmError as error:
        if error.spend_limit_rejected:
            for call in ready:
                _end(call, EditorCall.Status.FAILED, str(error), now)
            editor_budget.mark_limit_rejected(run, error.status_code, now)
        else:  # 일시 오류: 대기로 두고 다음 주기에 다시 제출한다(24시간 안에 못 하면 만료)
            logger.warning("배치 제출 실패(다음 주기에 다시): %s", error)
        return
    for call in ready:
        call.status = EditorCall.Status.SUBMITTED
        call.batch_id = batch_id
        call.submitted_at = now
        call.save(update_fields=["status", "batch_id", "submitted_at"])


def _end(call: EditorCall, status: str, error: str, now: datetime) -> None:
    call.status = status
    call.error = error
    call.finished_at = now
    call.save(update_fields=["status", "error", "finished_at"])
