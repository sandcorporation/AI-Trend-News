"""편집자 월 예산 상한(#124, 설계 §6).

이번 달(KST 달력 월) `EditorCall.cost_usd` 합 + 다음 단계 예상액이 `EDITOR_MONTHLY_BUDGET_USD`를
넘으면 그 단계를 시작하지 않는다. 확인 지점은 새 실행 전(`gate_new_run`), 2단계·3단계 제출 전
(`gate_stage`, 티켓 3·5가 부른다) 셋이다. 건너뛰면 `EditorRun.status=skipped_budget`이 되고
경보(`Alert.Kind.EDITOR_BUDGET`)는 `check_alerts`가 같은 달 한 번만 보낸다.
Console이 지출 한도로 요청을 거절(HTTP 4xx)할 때도 같은 상태·경보로 처리한다(`mark_limit_rejected`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg, Sum
from django.utils import timezone

from editor.models import EditorCall, EditorRun

# 기록이 없을 때 쓰는 호출당 예상액(US$, 설계 §7 표 ÷ 호출 수). 1·2단계는 Sonnet, 3단계는 Opus
# (대표 결정 2026-10-30, $0.372 ÷ 4), 2·3단계는 배치.
DEFAULT_CALL_COST: dict[str, Decimal] = {
    EditorCall.Stage.CLASSIFY: Decimal("0.104"),
    EditorCall.Stage.WRITE_NEW: Decimal("0.0135"),
    EditorCall.Stage.WRITE_UPDATE: Decimal("0.0135"),
    EditorCall.Stage.COMMENTARY: Decimal("0.093"),
}
AVERAGE_WINDOW = timedelta(days=7)
RESUME_NOTE = "월 한도 도달, 다음 달 1일 재개"


def month_start(now: datetime) -> datetime:
    local = timezone.localtime(now)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def month_key(now: datetime) -> str:
    return f"{timezone.localtime(now):%Y-%m}"


def month_cost(now: datetime) -> Decimal:
    """이번 달(KST 달력 월) 편집 호출 추정 비용 합계."""
    value = EditorCall.objects.filter(created_at__gte=month_start(now)).aggregate(
        total=Sum("cost_usd")
    )["total"]
    return value if value is not None else Decimal(0)


def call_estimate(stage: str, now: datetime) -> Decimal:
    """호출 한 번의 예상액: 최근 7일 같은 단계 호출 평균(비용이 기록된 것만), 없으면 표 값."""
    average = EditorCall.objects.filter(
        stage=stage, created_at__gte=now - AVERAGE_WINDOW, cost_usd__gt=0
    ).aggregate(avg=Avg("cost_usd"))["avg"]
    return Decimal(average) if average is not None else DEFAULT_CALL_COST[stage]


@dataclass(frozen=True)
class BudgetCheck:
    allowed: bool
    month_total: Decimal
    estimate: Decimal
    budget: Decimal

    @property
    def note(self) -> str:
        return (
            f"월 예산 초과로 건너뜀: 이번 달 합계 US${self.month_total:.4f} + 예상 "
            f"US${self.estimate:.4f} > 상한 US${self.budget} ({RESUME_NOTE})"
        )


def check(stage: str, calls: int, now: datetime) -> BudgetCheck:
    total = month_cost(now)
    estimate = call_estimate(stage, now) * calls
    budget = Decimal(settings.EDITOR_MONTHLY_BUDGET_USD)
    return BudgetCheck(total + estimate <= budget, total, estimate, budget)


def skip_run(run: EditorRun, result: BudgetCheck | str, now: datetime) -> None:
    """실행을 예산 초과 건너뜀으로 닫는다. 메모에 합계·상한(또는 거절 까닭)을 남긴다."""
    run.status = EditorRun.Status.SKIPPED_BUDGET
    run.finished_at = now
    run.note = result if isinstance(result, str) else result.note
    run.save()


def gate_new_run(run: EditorRun, classify_calls: int, now: datetime) -> bool:
    """새 실행 전 확인. 넘으면 실행을 건너뜀으로 닫고 False."""
    result = check(EditorCall.Stage.CLASSIFY, classify_calls, now)
    if not result.allowed:
        skip_run(run, result, now)
    return result.allowed


def gate_stage(run: EditorRun, stage: str, calls: int, now: datetime) -> bool:
    """2단계·3단계 제출 전 확인(티켓 3·5가 부른다). 넘으면 실행을 건너뜀으로 닫고 False."""
    result = check(stage, calls, now)
    if not result.allowed:
        skip_run(run, result, now)
    return result.allowed


def mark_limit_rejected(run: EditorRun, status_code: int | None, now: datetime) -> None:
    """Console이 지출 한도로 거절했다 — 같은 상태·경보로 처리한다."""
    skip_run(run, f"Console 지출 한도 거절(HTTP {status_code}) ({RESUME_NOTE})", now)


def skipped_this_month(now: datetime) -> bool:
    return EditorRun.objects.filter(
        status=EditorRun.Status.SKIPPED_BUDGET, started_at__gte=month_start(now)
    ).exists()
