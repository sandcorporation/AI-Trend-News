"""#191 데이터 마이그레이션: 미평가 예측의 기한을 문장 날짜로 당긴다."""

import importlib
from datetime import UTC, date, datetime

import pytest
from django.apps import apps

from articles.models import Article, Category, Commentary, Prediction

pytestmark = pytest.mark.django_db

mig = importlib.import_module("articles.migrations.0013_prediction_claim_due")


def test_backfill_pulls_due_and_marks_owner() -> None:
    cat = Category.objects.create(name="c", slug="c")
    article = Article.objects.create(title="a", slug="aaa-1", category=cat)
    commentary = Commentary.objects.create(article=article)
    Commentary.objects.filter(pk=commentary.pk).update(
        created_at=datetime(2026, 10, 25, 3, tzinfo=UTC)
    )
    due = date(2026, 11, 25)

    def row(claim: str, verdict: str = "pending") -> int:
        made = Prediction.objects.create(
            commentary=commentary, claim=claim, horizon="1m", due_date=due, verdict=verdict
        )
        return int(made.pk)

    early = row("2026년 11월 4일까지 공개한다")
    late = row("2026년 11월 말까지 시작한다")
    plain = row("1개월 안에 공지한다")
    judged = row("11월 3일까지 발표한다", "hit")

    mig.forwards(apps, None)  # 시험: 지금 앱 레지스트리로 부름

    assert Prediction.objects.get(pk=early).due_date == date(2026, 11, 4)
    assert Prediction.objects.get(pk=late).due_date == due  # 늦은 날짜로는 미루지 않는다
    assert Prediction.objects.get(pk=plain).due_date == due
    assert Prediction.objects.get(pk=judged).due_date == due  # 평가된 행은 기한 그대로
