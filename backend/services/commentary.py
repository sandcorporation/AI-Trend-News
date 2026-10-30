"""AI 분석(논평) 규칙(#51). 편집자는 추천이면서 게시된 기사에만 쓰고, 숨긴 분석은 못 덮어쓴다.

추천이 풀리거나 비게시가 되어도 분석은 지우지 않는다. 공개 화면은 `public_commentary`만 쓴다.
`predictions`는 `{claim, horizon, basis}`로 저장하고, 저장할 때마다 `services/predictions.py`가
`Prediction` 행으로 동기화한다(평가된 행은 유지). `watchpoints`(지켜볼 신호)는 문자열 0~3개(#60).
"""

from typing import TypedDict

from django.db import transaction
from pydantic import BaseModel

from articles.models import Article, Commentary
from services import articles
from services import predictions as predictions_svc
from services.articles import Actor

MAX_TEXT_LENGTH = 900  # 배경·의미·전망 각각. 문단은 빈 줄(\n\n)로 나눈다(#60)
MAX_OPINION_LENGTH = 240
MAX_CLAIM_LENGTH = 120
MAX_BASIS_LENGTH = 300
MAX_PREDICTIONS = 3
MAX_WATCHPOINTS = 3
MAX_WATCHPOINT_LENGTH = 160
HORIZONS = {"1m": "1개월", "3m": "3개월", "6m": "6개월", "12m": "12개월"}


class CommentaryError(Exception):
    """분석 규칙 위반의 공통 부모."""


class CommentaryNotAllowed(CommentaryError):
    pass


class CommentaryHidden(CommentaryError):
    pass


class InvalidCommentary(CommentaryError):
    pass


class PredictionIn(BaseModel):
    """편집자가 넘기는 예측 문장 하나. 길이·기간 검증은 서비스가 한다."""

    claim: str
    horizon: str
    basis: str = ""


class PredictionData(TypedDict):
    claim: str
    horizon: str
    basis: str


def _clean(label: str, value: str, limit: int) -> str:
    value = value.replace("\r\n", "\n").strip()
    if not value or len(value) > limit:
        raise InvalidCommentary(f"{label}은(는) 1~{limit}자여야 합니다(지금 {len(value)}자).")
    return value


def _clean_predictions(predictions: list[PredictionIn]) -> list[PredictionData]:
    if len(predictions) > MAX_PREDICTIONS:
        raise InvalidCommentary(
            f"예측 문장은 {MAX_PREDICTIONS}개까지입니다(지금 {len(predictions)}개)."
        )
    cleaned: list[PredictionData] = []
    for p in predictions:
        if p.horizon not in HORIZONS:
            raise InvalidCommentary(
                f"예측 기간은 {', '.join(HORIZONS)} 중 하나여야 합니다: {p.horizon}"
            )
        cleaned.append(
            {
                "claim": _clean("예측 문장", p.claim, MAX_CLAIM_LENGTH),
                "horizon": p.horizon,
                "basis": _clean("예측 근거", p.basis, MAX_BASIS_LENGTH),
            }
        )
    return cleaned


def _clean_watchpoints(watchpoints: list[str]) -> list[str]:
    if len(watchpoints) > MAX_WATCHPOINTS:
        raise InvalidCommentary(
            f"지켜볼 신호는 {MAX_WATCHPOINTS}개까지입니다(지금 {len(watchpoints)}개)."
        )
    return [_clean("지켜볼 신호", w, MAX_WATCHPOINT_LENGTH) for w in watchpoints]


class CommentaryFields(TypedDict):
    background: str
    significance: str
    outlook_bull: str
    outlook_bear: str
    opinion: str
    predictions: list[PredictionData]
    watchpoints: list[str]


def clean_fields(
    *,
    background: str,
    significance: str,
    outlook_bull: str,
    outlook_bear: str,
    opinion: str,
    predictions: list[PredictionIn],
    watchpoints: list[str] | None = None,
) -> CommentaryFields:
    """저장 없이 길이·개수만 검사하고 다듬은 값을 돌려준다. 어기면 `InvalidCommentary`.

    `upsert_commentary`와 편집 파이프라인 3단계(`services/editor_commentary.py`)가 함께 쓴다.
    """
    return {
        "background": _clean("배경", background, MAX_TEXT_LENGTH),
        "significance": _clean("의미", significance, MAX_TEXT_LENGTH),
        "outlook_bull": _clean("낙관 관점", outlook_bull, MAX_TEXT_LENGTH),
        "outlook_bear": _clean("회의 관점", outlook_bear, MAX_TEXT_LENGTH),
        "opinion": _clean("의견", opinion, MAX_OPINION_LENGTH),
        "predictions": _clean_predictions(predictions),
        "watchpoints": _clean_watchpoints(watchpoints or []),
    }


@transaction.atomic
def upsert_commentary(
    actor: Actor,
    article_id: int,
    *,
    background: str,
    significance: str,
    outlook_bull: str,
    outlook_bear: str,
    opinion: str,
    predictions: list[PredictionIn],
    watchpoints: list[str] | None = None,
) -> Commentary:
    """기사당 분석 하나를 만들거나 갱신한다. 편집자는 추천이면서 게시된 기사만, 숨긴 분석은 불가."""
    article = articles.get_article(article_id)
    if actor is Actor.EDITOR and not (article.featured and article.published):
        raise CommentaryNotAllowed(
            f"추천이면서 게시된 기사에만 분석을 쓸 수 있습니다: {article_id} "
            f"(게시 {article.published}, 추천 {article.featured})"
        )
    fields = clean_fields(
        background=background,
        significance=significance,
        outlook_bull=outlook_bull,
        outlook_bear=outlook_bear,
        opinion=opinion,
        predictions=predictions,
        watchpoints=watchpoints,
    )
    existing = Commentary.objects.select_for_update().filter(article=article).first()
    if existing is None:
        created = Commentary.objects.create(article=article, **fields)
        predictions_svc.sync_predictions(created)
        return created
    if existing.hidden and actor is Actor.EDITOR:
        raise CommentaryHidden(
            f"기사 {article_id}의 분석은 대표가 숨겼으므로 편집자가 덮어쓸 수 없습니다."
        )
    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save()
    predictions_svc.sync_predictions(existing)
    return existing


def public_commentary(article: Article) -> Commentary | None:
    """공개 화면용: 숨기지 않았고 기사가 게시 상태일 때만."""
    if not article.published:
        return None
    try:
        found = article.commentary  # 공개 조회가 select_related로 이미 읽어 둔다
    except Commentary.DoesNotExist:
        return None
    return None if found.hidden else found
