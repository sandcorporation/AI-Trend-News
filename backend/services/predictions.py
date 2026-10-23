"""전망 기록표(#52): 분석의 예측 문장을 `Prediction` 행으로 모아 대표가 결과를 매기고 적중률을 낸다.

- 동기화: 분석을 upsert할 때 `predictions` JSON과 행을 맞춘다. 평가된 행은 그대로 두고 미평가
  행만 교체한다(같은 claim 문자열이면 같은 행).
- 평가는 대표만 한다(admin·관리자 API·MCP `judge_prediction`). 편집자 경로는 없다.
- 적중률은 평가된 것 중 적중 비율이고 「판단 불가」는 분모에서 뺀다.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from articles.models import Commentary, Prediction

HORIZON_MONTHS = {"1m": 1, "3m": 3, "6m": 6, "12m": 12}
MIN_PUBLIC_SAMPLE = 5  # 사이드바 적중률 블록은 평가 5건 이상일 때만
RECENT_DAYS = 90  # 「3개월」 필터: 기한이 최근 90일 안인 예측
Verdict = Prediction.Verdict


class PredictionError(Exception):
    """전망 기록표 규칙 위반의 공통 부모."""


class PredictionNotFound(PredictionError):
    pass


class InvalidVerdict(PredictionError):
    pass


def today_of(now: datetime) -> date:
    """서비스 시간대(KST)의 오늘."""
    return timezone.localtime(now).date()


def due_date_for(created: date, horizon: str) -> date:
    """분석 작성일에 기간(개월)을 더한다. 말일이 없는 달은 그 달 말일로."""
    months = HORIZON_MONTHS[horizon]
    index = created.month - 1 + months
    year, month = created.year + index // 12, index % 12 + 1
    day = min(created.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@transaction.atomic
def sync_predictions(commentary: Commentary) -> None:
    """분석의 `predictions` JSON에 행을 맞춘다. 평가된 행은 유지하고 미평가 행만 교체한다."""
    base = timezone.localtime(commentary.created_at).date()
    wanted = {p["claim"]: p for p in commentary.predictions}
    existing = {row.claim: row for row in commentary.prediction_rows.select_for_update()}
    for claim, row in existing.items():
        if row.verdict != Verdict.PENDING:
            continue
        if claim not in wanted:
            row.delete()
            continue
        p = wanted[claim]
        row.horizon, row.basis = p["horizon"], p["basis"]
        row.due_date = due_date_for(base, p["horizon"])
        row.save()
    for claim, p in wanted.items():
        if claim in existing:
            continue
        Prediction.objects.create(
            commentary=commentary,
            claim=claim,
            horizon=p["horizon"],
            basis=p["basis"],
            due_date=due_date_for(base, p["horizon"]),
        )


def judge_prediction(
    prediction_id: int, verdict: str, note: str = "", *, now: datetime
) -> Prediction:
    """대표가 결과를 매긴다. `pending`으로 되돌리면 평가 시각·메모를 비운다."""
    if verdict not in Verdict.values:
        raise InvalidVerdict(f"결과는 {', '.join(Verdict.values)} 중 하나여야 합니다: {verdict}")
    note = note.strip()
    if len(note) > 300:
        raise InvalidVerdict(f"평가 메모는 300자까지입니다(지금 {len(note)}자).")
    try:
        row = Prediction.objects.get(pk=prediction_id)
    except Prediction.DoesNotExist:
        raise PredictionNotFound(f"예측을 찾을 수 없습니다: {prediction_id}") from None
    row.verdict = verdict
    if verdict == Verdict.PENDING:
        row.verdict_note, row.judged_at = "", None
    else:
        row.verdict_note, row.judged_at = note, now
    row.save()
    return row


@dataclass(frozen=True)
class HitRate:
    judged: int  # 적중 + 빗나감 (판단 불가 제외)
    hits: int
    undecidable: int

    @property
    def percent(self) -> int | None:
        return round(self.hits * 100 / self.judged) if self.judged else None


def _public() -> QuerySet[Prediction]:
    return Prediction.objects.filter(
        commentary__hidden=False, commentary__article__published=True
    ).select_related("commentary__article")


def _window(qs: QuerySet[Prediction], recent: bool, today: date) -> QuerySet[Prediction]:
    return qs.filter(due_date__gte=today - timedelta(days=RECENT_DAYS)) if recent else qs


def public_predictions(*, recent: bool, today: date) -> list[Prediction]:
    """공개 목록: 기한 빠른 순 대신 최근 기한이 위로."""
    return list(_window(_public(), recent, today).order_by("-due_date", "-id"))


def hit_rate(*, recent: bool, today: date) -> HitRate:
    qs = _window(_public(), recent, today)
    hits = qs.filter(verdict=Verdict.HIT).count()
    misses = qs.filter(verdict=Verdict.MISS).count()
    undecidable = qs.filter(verdict=Verdict.UNDECIDABLE).count()
    return HitRate(judged=hits + misses, hits=hits, undecidable=undecidable)


def sidebar_rate(today: date) -> HitRate | None:
    """사이드바용: 평가된 것(적중+빗나감)이 5건 미만이면 None."""
    rate = hit_rate(recent=False, today=today)
    return rate if rate.judged >= MIN_PUBLIC_SAMPLE else None


def overdue_count(today: date) -> int:
    """기한이 지났는데 미평가인 예측 수(경보용)."""
    return Prediction.objects.filter(verdict=Verdict.PENDING, due_date__lt=today).count()
