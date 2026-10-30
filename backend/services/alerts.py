"""경보 점검(#10): 편집자 미실행과 소스 연속 실패를 Discord로 알린다.

같은 조건으로 되풀이해 보내지 않는다(전망 평가 대기만 주 1회 다시) — `Alert`에 종류·대상별
활성 상태를 두고, 조건이 처음 참이 될 때 한 번 보내고, 풀리면 활성을 끄고 「복구」를 한 번
보낸다. 전송에 실패하면 상태를 바꾸지 않아 다음 점검이 다시 시도한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings

from adapters.discord import Notifier
from collection.models import Source
from editor.models import ABANDON_AFTER, Alert, EditorCall, EditorRun
from services import editor_budget, predictions
from services.editor_runs import counted_runs, last_finished_run

logger = logging.getLogger("alerts")

SOURCE_FAILURE_THRESHOLD = 3
Kind = Alert.Kind
REMIND_AFTER = timedelta(days=7)  # 평가 대기 경보는 조건이 이어지면 주 1회 다시 보낸다


@dataclass(frozen=True)
class Condition:
    kind: Alert.Kind
    target: str
    problem: str  # 경보 메시지


def _editor_condition(now: datetime) -> Condition | None:
    last = last_finished_run()
    abandoned = [
        run
        for run in counted_runs().filter(finished_at__isnull=True)
        if (last is None or run.started_at > last.finished_at)  # type: ignore[operator]
        and run.state(now) == EditorRun.State.ABANDONED
    ]
    hours = int(ABANDON_AFTER.total_seconds() // 3600)
    if last is None:
        why = "편집자 실행이 한 번도 끝난 적이 없습니다."
    elif now - last.finished_at > ABANDON_AFTER:  # type: ignore[operator]
        why = (
            f"마지막 편집자 실행이 {last.finished_at:%Y-%m-%d %H:%M} UTC에 끝난 뒤 "
            f"{hours}시간이 지났습니다."
        )
    elif abandoned:
        why = f"종료 보고 없이 {hours}시간이 지난 편집자 실행이 있습니다."
    else:
        return None
    return Condition(
        Kind.EDITOR_MISSING,
        "",
        f"[경보] 편집자 미실행: {why}",
    )


def _source_conditions() -> list[Condition]:
    failing = Source.objects.filter(consecutive_failures__gte=SOURCE_FAILURE_THRESHOLD)
    return [
        Condition(
            Kind.SOURCE_FAILING,
            str(source.pk),
            f"[경보] 소스 연속 실패: {source.name} — {source.consecutive_failures}회 연속 "
            f"({source.last_result or '결과 없음'})",
        )
        for source in failing
    ]


def _predictions_condition(now: datetime) -> Condition | None:
    due = predictions.overdue_count(predictions.today_of(now))
    if not due:
        return None
    return Condition(
        Kind.PREDICTIONS_DUE,
        "",
        f"[경보] 전망 평가 대기 {due}건: 기한이 지난 예측을 admin 「전망 예측」에서 평가하세요.",
    )


def _budget_condition(now: datetime) -> Condition | None:
    """이번 달 예산으로 건너뛴 실행이 있으면 켠다. 대상은 달(KST) — 달이 바뀌면 풀려 복구 한 번."""
    if not editor_budget.skipped_this_month(now):
        return None
    return Condition(
        Kind.EDITOR_BUDGET,
        editor_budget.month_key(now),
        f"[경보] 편집 예산 초과: 이번 달 합계 US${editor_budget.month_cost(now):.2f} / 상한 "
        f"US${settings.EDITOR_MONTHLY_BUDGET_USD} — {editor_budget.RESUME_NOTE}.",
    )


BATCH_STALE_WINDOW = timedelta(hours=24)  # 만료 처리 뒤 이만큼 경보를 켜 둔다


def _batch_stale_condition(now: datetime) -> Condition | None:
    """24시간 넘어 만료로 끝낸 배치 호출이 최근 하루 안에 있으면 켠다(풀리면 복구 한 번)."""
    expired = EditorCall.objects.filter(
        via_batch=True,
        status=EditorCall.Status.EXPIRED,
        finished_at__gte=now - BATCH_STALE_WINDOW,
    ).count()
    if not expired:
        return None
    return Condition(
        Kind.EDITOR_BATCH_STALE,
        "",
        f"[경보] 편집 배치 지연: 배치 호출 {expired}건이 24시간 넘게 끝나지 않아 "
        "만료로 끝냈습니다. 해당 수집물은 미처리로 남아 다음 슬롯이 다시 봅니다"
        "(EDITOR_USE_BATCH=false로 동기 전환 가능).",
    )


def check_alerts(notifier: Notifier, now: datetime) -> list[str]:
    """점검하고 보낸 메시지들을 돌려준다."""
    conditions: dict[tuple[str, str], Condition] = {}
    editor = _editor_condition(now)
    if editor:
        conditions[(editor.kind, editor.target)] = editor
    due = _predictions_condition(now)
    if due:
        conditions[(due.kind, due.target)] = due
    budget = _budget_condition(now)
    if budget:
        conditions[(budget.kind, budget.target)] = budget
    stale = _batch_stale_condition(now)
    if stale:
        conditions[(stale.kind, stale.target)] = stale
    for condition in _source_conditions():
        conditions[(condition.kind, condition.target)] = condition

    sent: list[str] = []

    def deliver(alert: Alert, message: str, *, active: bool) -> None:
        if not notifier.send(message):
            return
        alert.active, alert.last_sent_at = active, now
        alert.save()
        sent.append(message)

    for (kind, target), condition in conditions.items():
        alert, _ = Alert.objects.get_or_create(kind=kind, target=target)
        if not alert.active or (
            kind == Kind.PREDICTIONS_DUE
            and alert.last_sent_at is not None
            and now - alert.last_sent_at >= REMIND_AFTER
        ):
            deliver(alert, condition.problem, active=True)

    for alert in Alert.objects.filter(active=True):
        if (alert.kind, alert.target) in conditions:
            continue
        deliver(alert, _recovery_message(alert), active=False)
    return sent


def _recovery_message(alert: Alert) -> str:
    if alert.kind == Kind.SOURCE_FAILING:
        source = Source.objects.filter(pk=int(alert.target)).first()
        name = source.name if source else f"#{alert.target}(삭제됨)"
        return f"[복구] 소스 수집이 다시 성공했습니다: {name}"
    if alert.kind == Kind.PREDICTIONS_DUE:
        return "[복구] 평가 대기 중인 전망 예측이 없습니다."
    if alert.kind == Kind.EDITOR_BUDGET:
        return f"[복구] 편집 예산 경보를 해제합니다({alert.target} 월 한도 기간 종료)."
    if alert.kind == Kind.EDITOR_BATCH_STALE:
        return "[복구] 편집 배치 지연 경보를 해제합니다(최근 24시간 만료 호출 없음)."
    return "[복구] 편집자가 다시 실행을 마쳤습니다."
