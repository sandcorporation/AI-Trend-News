"""경보 점검(#10): 편집자 미실행과 소스 연속 실패를 Discord로 알린다.

같은 조건으로 되풀이해 보내지 않는다 — `Alert`에 종류·대상별 활성 상태를 두고, 조건이 처음
참이 될 때 한 번 보내고, 풀리면 활성을 끄고 「복구」를 한 번 보낸다. 전송에 실패하면 상태를
바꾸지 않아 다음 점검이 다시 시도한다.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from adapters.discord import Notifier
from collection.models import Source
from editor.models import ABANDON_AFTER, Alert, EditorRun
from services.editor_runs import last_finished_run

logger = logging.getLogger("alerts")

SOURCE_FAILURE_THRESHOLD = 3
Kind = Alert.Kind


@dataclass(frozen=True)
class Condition:
    kind: Alert.Kind
    target: str
    problem: str  # 경보 메시지


def _editor_condition(now: datetime) -> Condition | None:
    last = last_finished_run()
    abandoned = [
        run
        for run in EditorRun.objects.filter(finished_at__isnull=True)
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


def check_alerts(notifier: Notifier, now: datetime) -> list[str]:
    """점검하고 보낸 메시지들을 돌려준다."""
    conditions: dict[tuple[str, str], Condition] = {}
    editor = _editor_condition(now)
    if editor:
        conditions[(editor.kind, editor.target)] = editor
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
        if not alert.active:
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
    return "[복구] 편집자가 다시 실행을 마쳤습니다."
