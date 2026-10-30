"""편집자 파이프라인 실행(ADR 0006, 설계 §4·§5·§9). `run_editor` 관리 명령이 매 주기 부른다.

이 티켓(#123)은 그림자 모드의 1단계(분류)까지만 한다: 편집 시각 슬롯이면 `EditorRun`
(mode=shadow)을 열고 분류를 동기로 한 번 부르고(비교 모델이 설정돼 있으면 같은 입력으로 한 번 더)
결과·토큰·비용을 `EditorCall`에만 남기고 끝낸다. 수집물·기사는 하나도 바꾸지 않는다.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from anthropic.types import Message
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from adapters.anthropic import LlmClient, LlmError, LlmRequest, get_llm
from editor.models import EditorCall, EditorRun
from services import editor_budget, editor_classify, editor_prompts, llm_pricing
from services.editor_classify import ClassifyInput, InvalidOutput

logger = logging.getLogger("editor")

CATCH_UP = timedelta(hours=3)  # 슬롯 주기를 놓쳤을 때 따라잡는 한계
CLASSIFY_MAX_TOKENS = 16_000  # thinking 포함. SDK의 비스트리밍 상한(약 21K) 안


Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS: tuple[Effort, ...] = ("low", "medium", "high", "xhigh", "max")


def parse_effort(value: str) -> Effort:
    if value not in EFFORTS:
        raise ValueError(f"알 수 없는 effort: {value} (가능: {', '.join(EFFORTS)})")
    return value


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


def classify(llm: LlmClient, data: ClassifyInput, model: str, effort: str) -> Outcome:
    """분류를 부른다. max_tokens·JSON 파싱·스키마 모양 실패는 한 번 재요청, refusal은 바로 실패."""
    outcome = Outcome(status=EditorCall.Status.FAILED)
    retry_note = ""
    for attempt in (0, 1):
        outcome.attempt = attempt
        try:
            message = llm.create(classify_request(data, model, effort, retry_note=retry_note))
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
            parsed = json.loads(_first_text(message))
            validated = editor_classify.validate(parsed, data)
        except (json.JSONDecodeError, InvalidOutput) as error:
            retry_note = f"JSON이 아니거나 모양이 틀렸다: {error}"[:200]
            outcome.error = retry_note
            continue
        outcome.status = EditorCall.Status.SUCCEEDED
        outcome.error = ""
        outcome.output = {"response": parsed, "decision": dict(validated.decision)}
        outcome.problems = validated.problems
        if validated.problems:
            outcome.output["problems"] = list(validated.problems)
        return outcome
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
    key: str,
    model: str,
    effort: str,
    data: ClassifyInput,
    outcome: Outcome,
    now: datetime,
) -> EditorCall:
    return EditorCall.objects.create(
        run=run,
        stage=EditorCall.Stage.CLASSIFY,
        key=key,
        model=model,
        effort=effort,
        prompt_version=editor_prompts.prompt_version(editor_prompts.system_prompt("classify")),
        status=outcome.status,
        attempt=outcome.attempt,
        item_ids=[i["id"] for i in data["items"]],
        input=input_summary(data),
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


def _note(outcome: Outcome) -> str:
    if outcome.status != EditorCall.Status.SUCCEEDED or outcome.output is None:
        return f"그림자 분류 실패: {outcome.error}"
    decision = outcome.output["decision"]
    assert isinstance(decision, dict)
    counts = " · ".join(
        f"{label} {len(decision[key])}"
        for key, label in (
            ("new_articles", "새 기사"),
            ("updates", "갱신"),
            ("discards", "버림"),
            ("holds", "보류"),
        )
    )
    fixed = f" / 검증 고침 {len(outcome.problems or [])}건" if outcome.problems else ""
    return f"그림자 분류: {counts}{fixed}"


def run_editor(now: datetime) -> str:
    """한 주기. 무엇을 했는지 한 줄로 돌려준다(관리 명령이 출력한다).

    키 없음·꺼짐·편집 시각 아님·이미 연 슬롯이면 아무것도 쓰지 않는다. 가격표에 없는 모델이면
    실행을 열지 않고 `llm_pricing.UnknownModel`을 던진다.
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
    slot = due_slot(now)
    if slot is None:
        return "편집 파이프라인: 편집 시각 아님"
    if EditorRun.objects.filter(slot=slot).exists():
        return f"편집 파이프라인: 슬롯 {slot}은 이미 열림"

    effort = settings.EDITOR_EFFORT_CLASSIFY
    models = [settings.EDITOR_MODEL_CLASSIFY]
    if settings.EDITOR_MODEL_CLASSIFY_COMPARE:
        models.append(settings.EDITOR_MODEL_CLASSIFY_COMPARE)
    for model in models:
        llm_pricing.check_model(model)
    parse_effort(effort)

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
        _record(run, key, model, effort, data, outcome, now)
        outcomes.append(outcome)

    main = outcomes[0]
    run.cost_usd = sum((o.cost_usd for o in outcomes), Decimal(0))
    limited = next((o for o in outcomes if o.limit_status is not None), None)
    if limited is not None:
        editor_budget.mark_limit_rejected(run, limited.limit_status, now)
        logger.warning("Console 지출 한도 거절 — 슬롯 %s: %s", slot, run.note)
        return f"편집 파이프라인: 슬롯 {slot} {run.note}"
    run.status = (
        EditorRun.Status.FINISHED
        if main.status == EditorCall.Status.SUCCEEDED
        else EditorRun.Status.FAILED
    )
    run.finished_at = now
    run.note = _note(main)
    if len(outcomes) > 1:
        run.note += f" / 비교 {models[1]}: {_note(outcomes[1]).removeprefix('그림자 ')}"
    run.save()
    return (
        f"편집 파이프라인: 슬롯 {slot} {run.get_status_display()} — {run.note} (US${run.cost_usd})"
    )
