"""#191 데이터 마이그레이션: 미평가 예측의 기한을 문장 날짜로 당긴다."""

from datetime import UTC, date, datetime

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

pytestmark = pytest.mark.django_db(transaction=True)


def test_backfill_pulls_due_and_marks_owner() -> None:
    executor = MigrationExecutor(connection)
    before = [("articles", "0012_commentary_watchpoints")]
    executor.migrate(before)
    apps = executor.loader.project_state(before).apps
    Category, Article = (
        apps.get_model("articles", "Category"),
        apps.get_model("articles", "Article"),
    )
    Commentary = apps.get_model("articles", "Commentary")
    Prediction = apps.get_model("articles", "Prediction")
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

    executor = MigrationExecutor(connection)
    after = [("articles", "0013_prediction_claim_due")]
    executor.migrate(after)
    new = executor.loader.project_state(after).apps.get_model("articles", "Prediction")
    assert new.objects.get(pk=early).due_date == date(2026, 11, 4)
    assert new.objects.get(pk=late).due_date == due  # 늦은 날짜로는 미루지 않는다
    assert new.objects.get(pk=plain).due_date == due
    assert new.objects.get(pk=judged).due_date == due  # 평가된 행은 기한 그대로

    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
