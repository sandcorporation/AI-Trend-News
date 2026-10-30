"""편집자 파이프라인 실행(ADR 0006, 설계 §4·§5·§9). `run_editor` 관리 명령이 매 주기 부른다.

그림자 모드: 편집 시각 슬롯이면 `EditorRun`(mode=shadow)을 열고 1단계 분류를 동기로 한 번
부르고(비교 모델이 설정돼 있으면 같은 입력으로 한 번 더, #123), 분류가 정한 새 기사·갱신마다
2단계 작성을 동기로 한 번씩 부른다(#125, 앞에 예산 확인 `gate_stage`). 결과·토큰·비용은
`EditorCall`에만 남기고 끝낸다 — 수집물·기사는 하나도 바꾸지 않는다. 실행 메모는 서버가 만든다.
"""

import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from functools import partial

from anthropic.types import Message
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from adapters.anthropic import LlmClient, LlmError, LlmRequest, get_llm
from adapters.clock import get_clock, sleep
from adapters.fetch import get_fetcher
from articles.models import Article, Category
from editor.models import EditorCall, EditorRun
from services import (
    editor_batch,
    editor_budget,
    editor_classify,
    editor_prompts,
    editor_write,
    llm_pricing,
)
from services.editor_classify import JSON, ClassifyInput, Decision, InvalidOutput
from services.editor_prompts import parse_effort

logger = logging.getLogger("editor")

CATCH_UP = timedelta(hours=3)  # 슬롯 주기를 놓쳤을 때 따라잡는 한계
CLASSIFY_MAX_TOKENS = 16_000  # thinking 포함. SDK의 비스트리밍 상한(약 21K) 안


def due_slot(now: datetime) -> str | None:
    """지금 열어야 할 슬롯(KST `YYYY-MM-DDTHH`). 슬롯 시각부터 3시간 안이면 그 슬롯이다."""
    local = timezone.localtime(now)
    candidates: list[datetime] = []
    for days_ago in (0, 1):
        day = local.date() - timedelta(days=days_ago)
        for hour in settings.EDITOR_RUN_HOURS:
            start = timezone.make_aware(datetime.combine(day, time(hour)))
            if start <= local < start + CATCH_UP:
                candidates.append(start)
    if not candidates:
        return None
    return max(candidates).strftime("%Y-%m-%dT%H")


def classify_request(
    data: ClassifyInput, model: str, effort: str, *, retry_note: str = ""
) -> LlmRequest:
    prompt = editor_prompts.system_prompt("classify")
    schema = editor_classify.output_schema([c["slug"] for c in data["categories"]])
    text = editor_classify.user_message(data)
    if retry_note:
        text += f"\n\n앞선 답이 쓸 수 없었다({retry_note}). 정해진 JSON 하나로 끝까지 답하라."
    return {
        "model": model,
        "max_tokens": CLASSIFY_MAX_TOKENS,
        "system": [{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": text}],
        "output_config": {
            "format": {"type": "json_schema", "schema": schema},
            "effort": parse_effort(effort),
        },
    }


@dataclass
class Outcome:
    """분류 호출 한 번(재요청 포함)의 결과. `editor_probe`도 이 모양을 출력한다."""

    status: str  # EditorCall.Status 값
    attempt: int = 0
    stop_reason: str = ""
    error: str = ""
    limit_status: int | None = None  # Console 지출 한도 거절로 보이는 HTTP 상태
    output: dict[str, object] | None = None
    problems: list[str] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    decision: Decision | None = None  # 분류가 성공했을 때 검증한 판단

    def add_usage(self, model: str, message: Message) -> None:
        usage = message.usage
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens or 0
        self.cache_read_input_tokens += usage.cache_read_input_tokens or 0
        self.cost_usd += llm_pricing.cost_usd(model, usage)


def _first_text(message: Message) -> str:
    for block in message.content:
        if block.type == "text":
            return block.text
    return ""


Accept = Callable[[object], tuple[JSON, list[str]]]


def call_json(
    llm: LlmClient, model: str, request_for: Callable[[str], LlmRequest], accept: Accept
) -> Outcome:
    """구조화 출력 호출 하나(설계 §2 공통·§5). `request_for(retry_note)`로 요청을 만들고 `accept`가
    (저장할 출력, 고친 까닭)을 돌려준다. max_tokens·JSON 파싱·`InvalidOutput`은 오류 문구를 붙여
    한 번 재요청, refusal·API 오류는 바로 실패."""
    outcome = Outcome(status=EditorCall.Status.FAILED)
    retry_note = ""
    for attempt in (0, 1):
        outcome.attempt = attempt
        try:
            message = llm.create(request_for(retry_note))
        except LlmError as error:
            outcome.error = str(error)
            if error.spend_limit_rejected:
                outcome.limit_status = error.status_code
            return outcome
        outcome.add_usage(model, message)
        outcome.stop_reason = message.stop_reason or ""
        if message.stop_reason == "refusal":
            outcome.error = "모델이 응답을 거절했다(refusal)"
            return outcome
        if message.stop_reason == "max_tokens":
            retry_note = "max_tokens에 걸려 JSON이 잘렸다"
            outcome.error = retry_note
            continue
        try:
            output, problems = accept(json.loads(_first_text(message)))
        except (json.JSONDecodeError, InvalidOutput) as error:
            retry_note = f"JSON이 아니거나 규칙에 맞지 않는다: {error}"[:300]
            outcome.error = retry_note
            continue
        outcome.status = EditorCall.Status.SUCCEEDED
        outcome.error = ""
        outcome.output = output
        outcome.problems = problems
        return outcome
    return outcome


def classify(llm: LlmClient, data: ClassifyInput, model: str, effort: str) -> Outcome:
    """분류를 부른다. max_tokens·JSON 파싱·스키마 모양 실패는 한 번 재요청, refusal은 바로 실패."""

    def accept(parsed: object) -> tuple[JSON, list[str]]:
        validated = editor_classify.validate(parsed, data)
        outcome_decision.append(validated.decision)
        output: JSON = {"response": parsed, "decision": dict(validated.decision)}
        if validated.problems:
            output["problems"] = list(validated.problems)
        return output, validated.problems

    outcome_decision: list[Decision] = []
    outcome = call_json(
        llm, model, lambda note: classify_request(data, model, effort, retry_note=note), accept
    )
    if outcome.status == EditorCall.Status.SUCCEEDED and outcome_decision:
        outcome.decision = outcome_decision[-1]
    return outcome


def input_summary(data: ClassifyInput) -> dict[str, object]:
    """`EditorCall.input`: id·건수만(제목·발췌 본문은 남기지 않는다)."""
    return {
        "today": data["today"],
        "categories": [c["slug"] for c in data["categories"]],
        "article_ids": [a["id"] for a in data["recent_articles"]],
        "item_ids": [i["id"] for i in data["items"]],
    }


def _record(
    run: EditorRun,
    *,
    key: str,
    stage: str,
    model: str,
    effort: str,
    item_ids: list[int],
    summary: JSON,
    outcome: Outcome,
    now: datetime,
    article_id: int | None = None,
) -> EditorCall:
    prompt_stage: editor_prompts.Stage = (
        "classify" if stage == EditorCall.Stage.CLASSIFY else "write"
    )
    return EditorCall.objects.create(
        run=run,
        stage=stage,
        key=key,
        model=model,
        effort=effort,
        prompt_version=editor_prompts.prompt_version(editor_prompts.system_prompt(prompt_stage)),
        status=outcome.status,
        attempt=outcome.attempt,
        item_ids=item_ids,
        article_id=article_id,
        input=summary,
        output=outcome.output,
        stop_reason=outcome.stop_reason,
        error=outcome.error,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cache_creation_input_tokens=outcome.cache_creation_input_tokens,
        cache_read_input_tokens=outcome.cache_read_input_tokens,
        cost_usd=outcome.cost_usd,
        finished_at=now,
    )


def _classify_note(outcome: Outcome) -> str:
    if outcome.decision is None:
        return f"그림자 분류 실패: {outcome.error}"
    d = outcome.decision
    counts = (
        f"새 기사 {len(d['new_articles'])} · 갱신 {len(d['updates'])} · "
        f"버림 {len(d['discards'])} · 보류 {len(d['holds'])}"
    )
    fixed = f" / 검증 고침 {len(outcome.problems or [])}건" if outcome.problems else ""
    return f"그림자 분류: {counts}{fixed}"


# --- 2단계 작성 ---


def write(llm: LlmClient, prepared: editor_write.Prepared, model: str, effort: str) -> Outcome:
    """작성 호출 하나(재요청 규칙은 `call_json`). `editor_probe`도 부른다."""

    def accept(parsed: object) -> tuple[JSON, list[str]]:
        checked = editor_write.check(parsed, prepared)
        return checked["output"], checked["problems"]

    return call_json(
        llm,
        model,
        lambda note: editor_write.request(prepared["data"], model, effort, retry_note=note),
        accept,
    )


@dataclass
class WriteStage:
    """작성 단계의 결과 요약(실행 메모용)."""

    calls: int = 0
    failed: int = 0
    unpublished: list[str] = field(default_factory=list)  # 「제목 — 까닭」
    stopped: bool = False  # 예산·지출 한도로 실행을 닫았다
    queued: bool = False  # 배치 경로: 호출을 만들어 두었다(제출·회수는 `editor_batch`)


def _write_one(
    run: EditorRun,
    llm: LlmClient,
    *,
    stage: str,
    key: str,
    prepare: Callable[[], editor_write.Prepared],
    item_ids: list[int],
    article_id: int | None,
    now: datetime,
) -> Outcome:
    model, effort = settings.EDITOR_MODEL_WRITE, settings.EDITOR_EFFORT_WRITE
    try:
        prepared = prepare()
    except Article.DoesNotExist:
        outcome = Outcome(status=EditorCall.Status.FAILED, error="갱신 대상 기사가 없다")
        _record(
            run, key=key, stage=stage, model=model, effort=effort, item_ids=item_ids,
            summary={}, outcome=outcome, now=now,
        )  # fmt: skip
        return outcome

    outcome = write(llm, prepared, model, effort)
    _record(
        run,
        key=key,
        stage=stage,
        model=model,
        effort=effort,
        item_ids=item_ids,
        summary=editor_write.input_summary(prepared["data"]),
        outcome=outcome,
        now=now,
        article_id=article_id,
    )
    return outcome


def write_stage(
    run: EditorRun, llm: LlmClient, decision: Decision, data: ClassifyInput, now: datetime
) -> WriteStage:
    """분류의 새 기사·갱신마다 작성 호출 한 번(동기). 앞에서 예산을 확인한다(#124 `gate_stage`).

    호출마다 독립이다 — 하나가 실패해도 나머지는 계속한다. Console 지출 한도 거절이면 멈춘다.
    """
    result = WriteStage()
    new, updates = decision["new_articles"], decision["updates"]
    if not new and not updates:
        return result
    if not editor_budget.gate_stage(run, EditorCall.Stage.WRITE_NEW, len(new) + len(updates), now):
        result.stopped = True
        return result
    if settings.EDITOR_USE_BATCH:
        result.calls = editor_batch.enqueue_writes(run, decision)
        result.queued = True
        return result
    reader = editor_write.RunReader(get_fetcher())
    names = {c["slug"]: c["name"] for c in data["categories"]}

    jobs: list[tuple[str, str, Callable[[], editor_write.Prepared], list[int], int | None]] = []
    for n, entry in enumerate(new, start=1):
        jobs.append(
            (
                EditorCall.Stage.WRITE_NEW,
                f"r{run.pk}-write-{n}",
                partial(editor_write.prepare_new, entry, names, reader),
                entry["item_ids"],
                None,
            )
        )
    for n, update in enumerate(updates, start=1):
        jobs.append(
            (
                EditorCall.Stage.WRITE_UPDATE,
                f"r{run.pk}-update-{n}",
                partial(editor_write.prepare_update, update, reader),
                update["item_ids"],
                update["article_id"],
            )
        )

    for stage, key, prepare, item_ids, article_id in jobs:
        outcome = _write_one(
            run, llm, stage=stage, key=key, prepare=prepare, item_ids=item_ids,
            article_id=article_id, now=now,
        )  # fmt: skip
        result.calls += 1
        run.cost_usd += outcome.cost_usd
        if outcome.limit_status is not None:
            editor_budget.mark_limit_rejected(run, outcome.limit_status, now)
            result.stopped = True
            return result
        if outcome.status != EditorCall.Status.SUCCEEDED or outcome.output is None:
            result.failed += 1
            continue
        article = outcome.output.get("article")
        if isinstance(article, dict) and not article.get("published"):
            classify_reason = next(
                (a["reason"] for a in new if a["item_ids"] == item_ids and not a["published"]), ""
            )
            why = article.get("unpublish_reason") or classify_reason or "까닭 없음"
            result.unpublished.append(f"{article.get('title')} — {why}")
    return result


def _run_note(
    outcome: Outcome,
    categories: list[tuple[str, str]],
    article_category: dict[int, str],
    writes: WriteStage,
    failed_calls: int,
) -> str:
    """실행 종료 메모(서버가 만든다): 분류·작성 건수, 비게시·보류 까닭, 안 쓴 분야, 실패 호출."""
    parts = [_classify_note(outcome)]
    decision = outcome.decision
    if decision is None:
        return parts[0]
    if writes.calls:
        parts.append(f"작성 {writes.calls}건(실패 {writes.failed})")
    if writes.unpublished:
        parts.append(f"비게시 {len(writes.unpublished)}: " + "; ".join(writes.unpublished[:5]))
    hold_reasons = Counter(h["reason"] or "까닭 없음" for h in decision["holds"])
    if hold_reasons:
        parts.append("보류 까닭: " + ", ".join(f"{r} {n}" for r, n in hold_reasons.most_common(3)))
    used = {a["category"] for a in decision["new_articles"]}
    used |= {article_category[u["article_id"]] for u in decision["updates"]}
    unused = [name for slug, name in categories if slug not in used]
    if unused:
        parts.append("안 쓴 분야: " + ", ".join(unused))
    if failed_calls:
        parts.append(f"실패 호출 {failed_calls}")
    return " / ".join(parts)


def finish_batch_run(run: EditorRun, now: datetime) -> str:
    """배치 호출이 모두 끝난 실행을 닫는다. 메모는 분류 호출 출력과 작성 호출에서 서버가 만든다."""
    classify_call = run.calls.filter(key=f"r{run.pk}-classify").first()
    decision: Decision | None = None
    problems: list[str] | None = None
    if classify_call is not None and isinstance(classify_call.output, dict):
        raw = classify_call.output.get("decision")
        if isinstance(raw, dict):
            decision = json.loads(json.dumps(raw))  # JSON 칸의 dict를 Decision으로
        raw_problems = classify_call.output.get("problems")
        problems = list(raw_problems) if isinstance(raw_problems, list) else None
    main = Outcome(status=EditorCall.Status.SUCCEEDED, decision=decision, problems=problems)
    writes = WriteStage(queued=True)
    unpublished: list[str] = []
    failed_writes = 0
    for call in run.calls.filter(stage__in=editor_batch.WRITE_STAGES).order_by("id"):
        writes.calls += 1
        if call.status != EditorCall.Status.SUCCEEDED or not isinstance(call.output, dict):
            failed_writes += 1
            continue
        article = call.output.get("article")
        if isinstance(article, dict) and not article.get("published"):
            reason = ""
            if decision is not None:
                reason = next(
                    (
                        a["reason"]
                        for a in decision["new_articles"]
                        if a["item_ids"] == call.item_ids and not a["published"]
                    ),
                    "",
                )
            why = article.get("unpublish_reason") or reason or "까닭 없음"
            unpublished.append(f"{article.get('title')} — {why}")
    writes.failed, writes.unpublished = failed_writes, unpublished
    categories = list(Category.objects.order_by("order", "id").values_list("slug", "name"))
    update_ids = [u["article_id"] for u in decision["updates"]] if decision else []
    article_category = dict(
        Article.objects.filter(pk__in=update_ids).values_list("pk", "category__slug")
    )
    failed_calls = run.calls.filter(
        status__in=[EditorCall.Status.FAILED, EditorCall.Status.EXPIRED]
    ).count()
    run.cost_usd = run.calls.aggregate(total=Sum("cost_usd"))["total"] or Decimal(0)
    run.status = EditorRun.Status.FINISHED if decision is not None else EditorRun.Status.FAILED
    run.finished_at = now
    run.note = _run_note(main, categories, article_category, writes, failed_calls)
    compare = run.calls.filter(key=f"r{run.pk}-classify-compare").first()
    if compare is not None:
        compare_decision = (
            compare.output.get("decision") if isinstance(compare.output, dict) else None
        )
        compare_outcome = Outcome(
            status=compare.status,
            decision=json.loads(json.dumps(compare_decision)) if compare_decision else None,
            error=compare.error,
        )
        run.note += (
            f" / 비교 {compare.model}: {_classify_note(compare_outcome).removeprefix('그림자 ')}"
        )
    run.save()
    return f"슬롯 {run.slot} 배치 회수 끝 — {run.note} (US${run.cost_usd})"


def _advance_pending(now: datetime) -> list[str]:
    """열려 있는 배치 실행을 한 걸음 민다(끝난 배치 회수·대기 호출 제출). 끝낸 실행의 한 줄들."""
    runs = editor_batch.pending_runs()
    if not runs:
        return []
    llm = get_llm()
    notes: list[str] = []
    for run in runs:
        if editor_batch.advance(run, llm, now) and run.status == EditorRun.Status.RUNNING:
            notes.append(finish_batch_run(run, now))
    return notes


def _settle(now: datetime) -> list[str]:
    """이번 주기 안에서 `EDITOR_BATCH_WAIT_SECONDS`까지 30초 간격으로 배치를 본다. 시간 안에 안
    끝나면 그대로 둔다 — 다음 매시 주기의 `_advance_pending`이 이어 간다."""
    notes = _advance_pending(now)
    deadline = now + timedelta(seconds=settings.EDITOR_BATCH_WAIT_SECONDS)
    steps = settings.EDITOR_BATCH_WAIT_SECONDS // editor_batch.POLL_SECONDS
    for _ in range(steps):
        if (
            not editor_batch.pending_runs()
            or get_clock().now() + timedelta(seconds=editor_batch.POLL_SECONDS) > deadline
        ):
            break
        sleep(editor_batch.POLL_SECONDS)
        notes += _advance_pending(get_clock().now())
    return notes


def run_editor(now: datetime) -> str:
    """한 주기. 무엇을 했는지 한 줄로 돌려준다(관리 명령이 출력한다).

    키 없음·꺼짐이면 아무것도 하지 않는다. 먼저 이어 갈 배치 실행을 한 걸음 밀고(설계 §4-1),
    편집 시각 슬롯이면 새 실행을 열고(2), 진행 중인 배치를 기다린다(3).
    """
    mode = settings.EDITOR_PIPELINE_MODE
    if mode == "off":
        return "편집 파이프라인: 꺼짐(EDITOR_PIPELINE_MODE=off)"
    if mode != "shadow":
        logger.warning("EDITOR_PIPELINE_MODE=%s는 아직 지원하지 않는다 — 건너뜀", mode)
        return f"편집 파이프라인: {mode} 모드는 아직 없음 — 건너뜀"
    if not settings.SANDCORP_NEWS_CLAUDE_KEY:
        logger.warning("SANDCORP_NEWS_CLAUDE_KEY 없음 — 편집 파이프라인을 건너뜀")
        return "편집 파이프라인: 키 없음 — 건너뜀"
    notes = _advance_pending(now)
    message = _open_slot(now)
    notes += _settle(now)
    return message + "".join(f" / {note}" for note in notes)


def _open_slot(now: datetime) -> str:
    slot = due_slot(now)
    if slot is None:
        return "편집 파이프라인: 편집 시각 아님"
    if EditorRun.objects.filter(slot=slot).exists():
        return f"편집 파이프라인: 슬롯 {slot}은 이미 열림"

    effort = settings.EDITOR_EFFORT_CLASSIFY
    models = [settings.EDITOR_MODEL_CLASSIFY]
    if settings.EDITOR_MODEL_CLASSIFY_COMPARE:
        models.append(settings.EDITOR_MODEL_CLASSIFY_COMPARE)
    for model in [*models, settings.EDITOR_MODEL_WRITE]:
        llm_pricing.check_model(model)
    parse_effort(effort)
    parse_effort(settings.EDITOR_EFFORT_WRITE)

    try:
        with transaction.atomic():
            run = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot=slot)
    except IntegrityError:
        return f"편집 파이프라인: 슬롯 {slot}은 이미 열림"

    if not editor_budget.gate_new_run(run, len(models), now):
        logger.warning("편집 예산 초과 — 슬롯 %s 건너뜀: %s", slot, run.note)
        return f"편집 파이프라인: 슬롯 {slot} {run.note}"

    data = editor_classify.build_input(now)
    if not data["items"]:
        run.status, run.finished_at, run.note = EditorRun.Status.FINISHED, now, "미처리 수집물 없음"
        run.save()
        return f"편집 파이프라인: 슬롯 {slot} — 미처리 수집물 없음"

    llm = get_llm()
    outcomes: list[Outcome] = []
    for n, model in enumerate(models):
        outcome = classify(llm, data, model, effort)
        key = f"r{run.pk}-classify" if n == 0 else f"r{run.pk}-classify-compare"
        _record(
            run, key=key, stage=EditorCall.Stage.CLASSIFY, model=model, effort=effort,
            item_ids=[i["id"] for i in data["items"]], summary=input_summary(data),
            outcome=outcome, now=now,
        )  # fmt: skip
        outcomes.append(outcome)

    main = outcomes[0]
    run.cost_usd = sum((o.cost_usd for o in outcomes), Decimal(0))
    limited = next((o for o in outcomes if o.limit_status is not None), None)
    if limited is not None:
        editor_budget.mark_limit_rejected(run, limited.limit_status, now)
        logger.warning("Console 지출 한도 거절 — 슬롯 %s: %s", slot, run.note)
        return f"편집 파이프라인: 슬롯 {slot} {run.note}"

    writes = WriteStage()
    if main.decision is not None:
        writes = write_stage(run, llm, main.decision, data, now)
        if writes.stopped:
            logger.warning("편집 파이프라인 작성 단계 중단 — 슬롯 %s: %s", slot, run.note)
            return f"편집 파이프라인: 슬롯 {slot} {run.note} (US${run.cost_usd})"
        if writes.queued:  # 실행은 열어 둔다 — 제출·회수는 `_settle`·다음 주기
            run.save()
            return (
                f"편집 파이프라인: 슬롯 {slot} 분류 끝, 작성 {writes.calls}건 배치 제출 예정 "
                f"(US${run.cost_usd})"
            )

    run.status = EditorRun.Status.FINISHED if main.decision is not None else EditorRun.Status.FAILED
    run.finished_at = now
    failed_calls = run.calls.filter(status=EditorCall.Status.FAILED).count()
    run.note = _run_note(
        main,
        [(c["slug"], c["name"]) for c in data["categories"]],
        {a["id"]: a["category"] for a in data["recent_articles"]},
        writes,
        failed_calls,
    )
    if len(outcomes) > 1:
        run.note += f" / 비교 {models[1]}: {_classify_note(outcomes[1]).removeprefix('그림자 ')}"
    run.save()
    return (
        f"편집 파이프라인: 슬롯 {slot} {run.get_status_display()} — {run.note} (US${run.cost_usd})"
    )
