"""예측 문장 속 날짜(#191): 날짜 추출·기한 당김·날짜가 기한보다 늦으면 거절."""

from datetime import date

import pytest

from articles.models import Prediction
from services import claim_dates, editor_commentary
from services import predictions as svc
from services.commentary import InvalidCommentary
from services.editor_classify import InvalidOutput
from tests.test_predictions import featured_article_id, write

pytestmark = pytest.mark.django_db

C = date(2026, 10, 25)


# --- 날짜 ---


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("2026년 11월 4일까지 공개한다", date(2026, 11, 4)),
        ("11월 3일 중간선거 전까지 발표한다", date(2026, 11, 3)),
        ("2026년 11월 말까지 시작한다", date(2026, 11, 30)),
        ("11월까지 낸다", date(2026, 11, 30)),
        ("2월 중순까지 출시", date(2027, 2, 20)),
        ("10월 초에 발표", date(2027, 10, 10)),  # 작성일(10/25) 이후 처음 오는 10월 초
        ("4분기 안에 낸다", date(2026, 12, 31)),
        ("2027년까지 상장한다", date(2027, 12, 31)),
        ("연말까지 늘린다", date(2026, 12, 31)),
        ("2026-12-01 발표", date(2026, 12, 1)),
        ("11월 3일과 11월 20일 사이에", date(2026, 11, 20)),  # 가장 늦은 날짜
        ("1개월 안에 공지한다", None),
        ("11월 중간선거 결과가 나온다", None),
        ("올해 매출이 는다", None),
    ],
)
def test_claim_deadline_forms(claim: str, expected: date | None) -> None:
    assert claim_dates.deadline(claim, C) == expected


def test_sync_pulls_due_date_to_stated_date() -> None:
    write(featured_article_id(), ("11월 30일까지 출시한다", "6m"), ("출시한다", "1m"))
    rows = {r.claim: r for r in Prediction.objects.select_related("commentary")}
    created = rows["출시한다"].commentary.created_at
    base = svc.today_of(created)
    assert rows["출시한다"].due_date == svc.due_date_for(base, "1m")
    stated = claim_dates.deadline("11월 30일까지 출시한다", base)
    assert stated is not None
    assert rows["11월 30일까지 출시한다"].due_date == min(stated, svc.due_date_for(base, "6m"))


def test_upsert_rejects_date_after_due() -> None:
    article_id = featured_article_id()
    with pytest.raises(InvalidCommentary, match="기한"):
        write(article_id, ("2099년 1월 1일까지 출시한다", "1m"))


def test_pipeline_check_rejects_date_after_due() -> None:
    plan: editor_commentary.Plan = {
        "kind": "new",
        "write_key": "r1-write-1",
        "article_id": None,
        "title": "t",
        "summary": "s",
        "category": "ai",
        "slug": "slug-a",
        "item_ids": [],
    }
    raw = {
        "background": "b",
        "significance": "s",
        "outlook_bull": "u",
        "outlook_bear": "d",
        "opinion": "o",
        "watchpoints": [],
        "predictions": [{"claim": "2099년 1월 1일까지 낸다", "horizon": "1m", "basis": "근거"}],
        "post_text": "한 문장이다.",
    }
    with pytest.raises(InvalidOutput, match="기한"):
        editor_commentary.check(raw, plan)
