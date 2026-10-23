"""전망 기록표(#52): 동기화, 평가(API·MCP), 적중률, 경보."""

from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.test import Client

from adapters.discord import FakeNotifier
from articles.models import Commentary, Prediction
from editor.models import Alert
from mcpserver.jsontypes import JSON
from services import commentary as commentary_svc
from services import predictions as svc
from services.articles import Actor
from services.commentary import PredictionIn
from tests.conftest import FrozenClock, Signer
from tests.test_admin_api_and_tools import Api, obj
from tests.test_mcp import McpClient
from tests.test_public_site import make_article

pytestmark = pytest.mark.django_db


def write(article_id: int, *claims: tuple[str, str], actor: Actor = Actor.EDITOR) -> Commentary:
    return commentary_svc.upsert_commentary(
        actor,
        article_id,
        background="b",
        significance="s",
        outlook_bull="u",
        outlook_bear="d",
        opinion="o",
        predictions=[PredictionIn(claim=c, horizon=h, basis="근거") for c, h in claims],
    )


def featured_article_id() -> int:
    return make_article("기사", featured=True).pk


def judge(row: Prediction, verdict: str, clock: FrozenClock) -> None:
    svc.judge_prediction(row.pk, verdict, now=clock.now())


def test_due_date_adds_months_and_clamps_month_end() -> None:
    assert svc.due_date_for(date(2026, 10, 23), "3m") == date(2027, 1, 23)
    assert svc.due_date_for(date(2026, 8, 31), "1m") == date(2026, 9, 30)
    assert svc.due_date_for(date(2026, 10, 23), "12m") == date(2027, 10, 23)


def test_upsert_creates_rows_with_due_date() -> None:
    write(featured_article_id(), ("금리가 내린다", "6m"), ("환율이 오른다", "1m"))
    rows = {r.claim: r for r in Prediction.objects.all()}
    assert set(rows) == {"금리가 내린다", "환율이 오른다"}
    created = Commentary.objects.get().created_at.date()
    assert rows["환율이 오른다"].due_date == svc.due_date_for(created, "1m")
    assert all(r.verdict == "pending" for r in rows.values())


def test_rewrite_replaces_pending_but_keeps_judged_rows(frozen_clock: FrozenClock) -> None:
    article_id = featured_article_id()
    write(article_id, ("맞춘 예측", "1m"), ("미평가 예측", "3m"), ("그대로", "6m"))
    judge(Prediction.objects.get(claim="맞춘 예측"), "hit", frozen_clock)

    write(article_id, ("새 예측", "3m"), ("그대로", "12m"))

    claims = set(Prediction.objects.values_list("claim", flat=True))
    assert claims == {"맞춘 예측", "새 예측", "그대로"}  # 평가된 행 유지, 미평가 행 교체
    kept = Prediction.objects.get(claim="맞춘 예측")
    assert kept.verdict == "hit" and kept.judged_at is not None
    # 같은 claim의 미평가 행은 같은 행: 기간·기한이 새 값으로
    same = Prediction.objects.get(claim="그대로")
    assert same.horizon == "12m"


def test_same_claim_judged_row_is_not_touched(frozen_clock: FrozenClock) -> None:
    article_id = featured_article_id()
    write(article_id, ("예측", "1m"))
    row = Prediction.objects.get()
    judge(row, "miss", frozen_clock)
    write(article_id, ("예측", "12m"))
    row.refresh_from_db()
    assert (row.horizon, row.verdict, Prediction.objects.count()) == ("1m", "miss", 1)


def test_judge_validation_and_undo(frozen_clock: FrozenClock) -> None:
    write(featured_article_id(), ("예측", "1m"))
    row = Prediction.objects.get()
    with pytest.raises(svc.InvalidVerdict):
        svc.judge_prediction(row.pk, "maybe", now=frozen_clock.now())
    with pytest.raises(svc.PredictionNotFound):
        svc.judge_prediction(999, "hit", now=frozen_clock.now())
    done = svc.judge_prediction(row.pk, "hit", " 메모 ", now=frozen_clock.now())
    assert done.verdict_note == "메모" and done.judged_at == frozen_clock.now()
    undone = svc.judge_prediction(row.pk, "pending", "x", now=frozen_clock.now())
    assert undone.judged_at is None and undone.verdict_note == ""


def make_judged(
    clock: FrozenClock, hits: int, misses: int, undecidable: int = 0, *, due: date | None = None
) -> None:
    article_id = featured_article_id()
    total = hits + misses + undecidable
    write(article_id, *[(f"예측 {i}", "1m") for i in range(min(total, 3))])
    # 3개 한도를 넘는 시험 데이터는 행을 직접 만든다
    commentary = Commentary.objects.get(article_id=article_id)
    for i in range(3, total):
        Prediction.objects.create(
            commentary=commentary, claim=f"예측 {i}", horizon="1m", due_date=date(2026, 1, 1)
        )
    verdicts = ["hit"] * hits + ["miss"] * misses + ["undecidable"] * undecidable
    for row, verdict in zip(commentary.prediction_rows.order_by("id"), verdicts, strict=True):
        judge(row, verdict, clock)
        if due:
            Prediction.objects.filter(pk=row.pk).update(due_date=due)


def test_hit_rate_excludes_undecidable_and_sidebar_needs_five(frozen_clock: FrozenClock) -> None:
    make_judged(frozen_clock, hits=3, misses=1, undecidable=2)
    today = date(2026, 1, 1)
    rate = svc.hit_rate(recent=False, today=today)
    assert (rate.judged, rate.hits, rate.undecidable, rate.percent) == (4, 3, 2, 75)
    assert svc.sidebar_rate(today) is None  # 평가 4건 < 5

    Prediction.objects.filter(verdict="undecidable").update(verdict="miss")
    shown = svc.sidebar_rate(today)
    assert shown is not None and shown.percent == 50  # 판단 불가였던 2건이 빗나감이 되어 6건 중 3건


def test_hit_rate_none_when_nothing_judged() -> None:
    assert svc.hit_rate(recent=False, today=date(2026, 1, 1)).percent is None


def test_recent_filter_and_public_visibility(frozen_clock: FrozenClock) -> None:
    today = date(2026, 1, 1)
    make_judged(frozen_clock, hits=1, misses=0, due=today - timedelta(days=200))
    make_judged(frozen_clock, hits=0, misses=1, due=today - timedelta(days=10))
    assert svc.hit_rate(recent=False, today=today).judged == 2
    recent = svc.hit_rate(recent=True, today=today)
    assert (recent.judged, recent.hits) == (1, 0)
    assert len(svc.public_predictions(recent=True, today=today)) == 1

    Commentary.objects.update(hidden=True)
    assert svc.public_predictions(recent=False, today=today) == []
    Commentary.objects.update(hidden=False)
    from articles.models import Article

    Article.objects.update(published=False)
    assert svc.hit_rate(recent=False, today=today).judged == 0


# --- 평가: 관리자 API·MCP는 대표만 ---


def test_admin_api_judges_and_rejects(client: Client, token_signer: Signer) -> None:
    write(featured_article_id(), ("예측", "1m"))
    row = Prediction.objects.get()
    api = Api(client, token_signer.token(scope="admin"))

    status, body = api.call("patch", f"/predictions/{row.pk}", {"verdict": "hit", "note": "맞음"})
    assert status == 200 and obj(body)["verdict"] == "hit"
    row.refresh_from_db()
    assert row.verdict == "hit" and row.verdict_note == "맞음"
    assert api.call("patch", "/predictions/999", {"verdict": "hit"})[0] == 404
    assert api.call("patch", f"/predictions/{row.pk}", {"verdict": "maybe"})[0] == 422
    editor = Api(client, token_signer.token(scope="editor"))
    assert editor.call("patch", f"/predictions/{row.pk}", {"verdict": "miss"})[0] == 403


def test_mcp_judge_is_admin_only(client: Client, token_signer: Signer) -> None:
    write(featured_article_id(), ("예측", "1m"))
    row = Prediction.objects.get()
    editor = McpClient(client, token_signer.token(scope="editor"))
    assert "judge_prediction" not in editor.tool_names()
    owner = McpClient(client, token_signer.token(scope="admin editor"))
    args: dict[str, JSON] = {"id": row.pk, "verdict": "miss", "note": "사유"}
    assert owner.ok("judge_prediction", args)["verdict"] == "miss"
    assert "찾을 수 없" in owner.fails("judge_prediction", {"id": 999, "verdict": "hit"})


# --- 경보 ---


def make_overdue(clock: FrozenClock, count: int = 1) -> None:
    write(featured_article_id(), *[(f"예측 {i}", "1m") for i in range(count)])
    Prediction.objects.update(due_date=clock.now().date() - timedelta(days=1))


def test_alert_sent_weekly_while_pending_and_recovers(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    make_overdue(frozen_clock, 2)
    call_command("check_alerts")
    due = [m for m in fake_discord.sent if "전망 평가 대기" in m]
    assert len(due) == 1 and "2건" in due[0]

    frozen_clock.advance(6 * 24 * 3600)
    call_command("check_alerts")
    assert len([m for m in fake_discord.sent if "전망 평가 대기" in m]) == 1  # 7일 전엔 안 보낸다

    frozen_clock.advance(1 * 24 * 3600)
    call_command("check_alerts")
    assert len([m for m in fake_discord.sent if "전망 평가 대기" in m]) == 2  # 주 1회 다시

    for row in Prediction.objects.all():
        judge(row, "hit", frozen_clock)
    call_command("check_alerts")
    assert "[복구] 평가 대기" in fake_discord.sent[-1]
    call_command("check_alerts")
    assert not Alert.objects.filter(kind="predictions_due", active=True).exists()
    assert len([m for m in fake_discord.sent if "평가 대기" in m]) == 3


def test_no_alert_before_due_or_when_judged(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    write(featured_article_id(), ("예측", "1m"))
    Prediction.objects.update(due_date=frozen_clock.now().date())  # 기한 당일은 아직
    call_command("check_alerts")
    assert not [m for m in fake_discord.sent if "전망 평가 대기" in m]


# --- 공개 화면 ---


def page(html: str) -> dict[str, object]:
    from tests.test_home_layout import page_data

    return page_data(html)


def test_predictions_page_shows_rate_rows_and_filters(
    client: Client, frozen_clock: FrozenClock
) -> None:
    today = svc.today_of(frozen_clock.now())
    make_judged(frozen_clock, hits=2, misses=1, undecidable=1, due=today - timedelta(days=200))
    make_judged(frozen_clock, hits=0, misses=1, due=today - timedelta(days=5))
    write(featured_article_id(), ("아직 모르는 예측", "6m"))

    response = client.get("/predictions")
    html = response.content.decode()
    assert response.status_code == 200
    # 서버 HTML: 평가 4건(적중 2·빗나감 2) → 50%, 판단 불가 1건 제외
    assert 'data-slot="hit-rate"' in html and "<strong>50%</strong>" in html
    assert "판단 불가 1건 제외" in html and "평가 대기" in html
    data = page(html)
    assert data["kind"] == "predictions"
    assert data["rate"] == {"percent": 50, "judged": 4, "hits": 2, "undecidable": 1}
    rows = data["rows"]
    assert isinstance(rows, list) and len(rows) == 6

    recent = page(client.get("/predictions?period=3m").content.decode())
    assert recent["rate"] == {"percent": 0, "judged": 1, "hits": 0, "undecidable": 0}
    assert client.get("/predictions?period=1y").status_code == 404


def test_predictions_page_hides_hidden_and_empty_state(client: Client) -> None:
    html = client.get("/predictions").content.decode()
    assert "아직 평가된 예측이 없습니다." in html and "표시할 예측이 없습니다." in html
    write(featured_article_id(), ("숨길 예측", "1m"))
    Commentary.objects.update(hidden=True)
    assert "숨길 예측" not in client.get("/predictions").content.decode()


def test_sitemap_lists_predictions(client: Client) -> None:
    assert "/predictions</loc>" in client.get("/sitemap.xml").content.decode()


def test_home_sidebar_rate_block_needs_five_judged(
    client: Client, frozen_clock: FrozenClock
) -> None:
    make_judged(frozen_clock, hits=3, misses=1, undecidable=3)
    html = client.get("/").content.decode()
    assert 'data-slot="prediction-rate"' not in html  # 판단 불가를 빼면 4건
    assert page(html)["sidebar"]["prediction_rate"] is None  # type: ignore[index]

    make_judged(frozen_clock, hits=1, misses=0)
    html = client.get("/").content.decode()
    assert 'data-slot="prediction-rate"' in html and "80%" in html
    assert 'href="/predictions"' in html
    rate = page(html)["sidebar"]["prediction_rate"]  # type: ignore[index]
    assert rate["percent"] == 80 and rate["judged"] == 5


def test_article_page_badges_only_judged(client: Client, frozen_clock: FrozenClock) -> None:
    article_id = featured_article_id()
    write(article_id, ("맞은 예측", "1m"), ("대기 예측", "3m"))
    judge(Prediction.objects.get(claim="맞은 예측"), "hit", frozen_clock)
    slug = Commentary.objects.get().article.slug
    html = client.get(f"/a/{slug}").content.decode()
    assert html.count('data-slot="verdict"') == 1 and "적중" in html
    predictions = page(html)["article"]["commentary"]["predictions"]  # type: ignore[index]
    assert {p["claim"]: p["verdict"] for p in predictions} == {
        "맞은 예측": "hit",
        "대기 예측": "",
    }
