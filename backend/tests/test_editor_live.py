"""편집자 파이프라인 6(#128): 게시 모드 — 서비스 저장·수집물 예약·경보.

가짜 LLM·고정 시계·가짜 페처만 쓴다.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from django.test import override_settings
from sandcorp_authkit.testing import FixedClock

from adapters.anthropic import FakeLlm
from adapters.clock import set_clock
from adapters.discord import FakeNotifier
from adapters.fetch import set_fetcher
from articles.models import Article, Commentary
from collection.models import Item
from editor.models import Alert, EditorCall, EditorRun
from posts.models import Post
from services import articles as svc
from services import commentary as commentary_svc
from services import editor_commentary, editor_save
from services.alerts import check_alerts
from services.editor_classify import build_input
from services.editor_reserve import reserved_item_ids
from services.posts import article_url
from services.retention import prune_items
from tests.test_editor_commentary import analysed, featured_article, more_items
from tests.test_editor_pipeline import KST, SHADOW, Pages, answer, categories, make_items, run
from tests.test_editor_pipeline import written as write_reply

pytestmark = pytest.mark.django_db

LIVE_SYNC = {**SHADOW, "EDITOR_PIPELINE_MODE": "live", "EDITOR_MAX_COMMENTARY": 4}
LIVE_BATCH = {**LIVE_SYNC, "EDITOR_USE_BATCH": True}
SHADOW_BATCH = {**LIVE_BATCH, "EDITOR_PIPELINE_MODE": "shadow"}
KEEP: dict[str, object] = {"title": None, "summary": None, "tags": None, "reason": "추가 보도"}


@pytest.fixture
def clock() -> Iterator[FixedClock]:
    fixed = FixedClock(datetime(2026, 10, 30, 6, 25, tzinfo=KST).astimezone(UTC))
    previous = set_clock(fixed)
    yield fixed
    set_clock(previous)


@pytest.fixture(autouse=True)
def pages() -> Iterator[Pages]:
    fetcher = Pages()
    previous = set_fetcher(fetcher)
    yield fetcher
    set_fetcher(previous)


@pytest.fixture
def live_sync() -> Iterator[None]:
    with override_settings(**LIVE_SYNC):
        yield


@pytest.fixture
def live_batch(fake_sleep: list[float]) -> Iterator[None]:
    with override_settings(**LIVE_BATCH):
        yield


def new_entry(*items: Item, featured: bool = True) -> dict[str, object]:
    return {
        "item_ids": [i.pk for i in items],
        "category": svc.list_categories()[0].slug,
        "published": True,
        "featured": featured,
        "reason": "발표",
    }


def full_slot(fake_llm: FakeLlm) -> tuple[Item, Item, Item, Article]:
    """새 기사(a, 추천) · 기존 추천 기사 갱신(b) · 버림(c). 분석은 새 기사 → 갱신 기사 순."""
    categories()
    a, b, c = make_items(3)
    (old_item,) = more_items(1)
    existing = featured_article(old_item)
    decision = answer(
        [a, b, c],
        new_articles=[new_entry(a)],
        updates=[{"article_id": existing.pk, "item_ids": [b.pk], "reason": "후속"}],
        discards=[{"item_id": c.pk, "reason": "홍보"}],
    )
    fake_llm.queue(decision, write_reply(), KEEP, analysed(), analysed(opinion="갱신 의견."))
    return a, b, c, existing


def assert_full_slot_saved(a: Item, b: Item, c: Item, existing: Article) -> EditorRun:
    new = Article.objects.get(slug="openai-new-model")
    assert (new.published, new.featured, new.title) == (True, True, "오픈AI, 새 모델 공개")
    assert list(new.article_items.values_list("item_id", flat=True)) == [a.pk]
    assert b.pk in set(existing.article_items.values_list("item_id", flat=True))
    statuses = {i.pk: i.status for i in Item.objects.filter(pk__in=[a.pk, b.pk, c.pk])}
    assert statuses == {a.pk: "bundled", b.pk: "bundled", c.pk: "discarded"}
    for article in (new, existing):
        assert Commentary.objects.filter(article=article).exists()
        post = Post.objects.get(article=article)
        assert post.channel == "kakao" and post.text.endswith(article_url(article.slug))
    assert Commentary.objects.get(article=existing).opinion == "갱신 의견."
    editor_run = EditorRun.objects.get()
    assert editor_run.mode == EditorRun.Mode.LIVE
    assert editor_run.status == EditorRun.Status.FINISHED
    assert (editor_run.created, editor_run.updated, editor_run.discarded) == (1, 1, 1)
    assert "저장: 새 기사 1 · 갱신 1 · 버림 1" in editor_run.note
    assert editor_run.note.startswith("분류:")
    calls = EditorCall.objects.exclude(stage="classify")
    assert set(calls.values_list("status", flat=True)) == {"saved"}
    assert calls.get(stage="write_new").article == new
    return editor_run


# --- 한 슬롯 관통 ---


def test_live_sync_slot_creates_updates_discards_and_writes_commentary_posts(
    clock: FixedClock, live_sync: None, fake_llm: FakeLlm
) -> None:
    a, b, c, existing = full_slot(fake_llm)

    output = run()

    assert "종료" in output
    assert_full_slot_saved(a, b, c, existing)
    assert not reserved_item_ids(clock.now())


def test_live_batch_slot_saves_when_results_arrive(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    a, b, c, existing = full_slot(fake_llm)

    run()

    assert_full_slot_saved(a, b, c, existing)
    commentary = EditorCall.objects.filter(stage="commentary")
    assert all(call.via_batch for call in commentary)


def test_rerun_after_live_slot_makes_no_duplicates(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    full_slot(fake_llm)
    run()
    counts = (Article.objects.count(), Commentary.objects.count(), Post.objects.count())

    clock.advance(3600)  # 같은 슬롯 안의 다음 주기: 이미 열린 슬롯은 다시 열지 않는다
    assert "이미 열림" in run()
    for call in EditorCall.objects.exclude(stage="classify"):
        editor_save.save(call, clock.now())  # 저장된 호출은 다시 저장하지 않는다

    assert (Article.objects.count(), Commentary.objects.count(), Post.objects.count()) == counts
    assert EditorRun.objects.count() == 1


# --- 그림자는 절대 저장하지 않는다 ---


def test_shadow_slot_with_same_answers_changes_nothing(
    clock: FixedClock, fake_llm: FakeLlm
) -> None:
    with override_settings(**{**LIVE_SYNC, "EDITOR_PIPELINE_MODE": "shadow"}):
        a, b, c, existing = full_slot(fake_llm)
        run()

    assert list(Article.objects.values_list("pk", flat=True)) == [existing.pk]
    assert set(Item.objects.filter(pk__in=[a.pk, b.pk, c.pk]).values_list("status", flat=True)) == {
        "pending"
    }
    assert not Commentary.objects.exists() and not Post.objects.exists()
    editor_run = EditorRun.objects.get()
    assert editor_run.mode == EditorRun.Mode.SHADOW
    assert (editor_run.created, editor_run.updated, editor_run.discarded) == (0, 0, 0)
    assert "저장:" not in editor_run.note
    assert "saved" not in set(EditorCall.objects.values_list("status", flat=True))
    for call in EditorCall.objects.all():
        editor_save.save(call, clock.now())  # 직접 불러도 그림자 실행이면 아무것도 안 한다
    assert Article.objects.count() == 1 and not Post.objects.exists()


def test_shadow_run_switched_to_live_midway_still_saves_nothing(
    clock: FixedClock, fake_llm: FakeLlm, fake_sleep: list[float]
) -> None:
    """그림자로 연 실행은 도중에 모드를 live로 바꿔도 그림자로 끝난다(실행 모드가 정한다)."""
    fake_llm.batch_polls_until_ended = None  # 첫 주기에는 배치가 끝나지 않는다
    with override_settings(**SHADOW_BATCH):
        a, b, c, existing = full_slot(fake_llm)
        run()
    assert EditorCall.objects.filter(status="submitted").count() == 2

    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    with override_settings(**LIVE_BATCH):
        run()

    assert Article.objects.count() == 1 and not Commentary.objects.exists()
    assert Item.objects.get(pk=c.pk).status == "pending"
    editor_run = EditorRun.objects.get()
    assert (editor_run.mode, editor_run.status) == ("shadow", "finished")
    assert "saved" not in set(EditorCall.objects.values_list("status", flat=True))


# --- 예약 ---


def test_reserved_items_leave_next_classify_and_prune_until_call_ends(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    fake_llm.batch_polls_until_ended = None  # 작성 배치가 끝나지 않고 머문다
    categories()
    a, b = make_items(2)
    fake_llm.queue(answer([a, b], new_articles=[new_entry(a)], discards=[]), write_reply())
    run()
    write = EditorCall.objects.get(stage="write_new")
    assert write.status == "submitted"
    assert reserved_item_ids(clock.now()) == {a.pk}

    assert [i["id"] for i in build_input(clock.now())["items"]] == [b.pk]
    Item.objects.update(collected_at=clock.now() - timedelta(hours=30))
    assert prune_items(clock.now).discarded == 1
    assert Item.objects.get(pk=a.pk).status == "pending"  # 예약 중이라 자동 버림에서 빠짐
    assert Item.objects.get(pk=b.pk).status == "discarded"

    write.status = EditorCall.Status.FAILED  # 실패로 끝나면 예약이 풀린다
    write.save()
    assert reserved_item_ids(clock.now()) == set()
    assert prune_items(clock.now).discarded == 1
    assert Item.objects.get(pk=a.pk).status == "discarded"


def test_shadow_run_reserves_nothing(clock: FixedClock, fake_llm: FakeLlm) -> None:
    fake_llm.batch_polls_until_ended = None
    with override_settings(**SHADOW_BATCH):
        categories()
        a, b = make_items(2)
        fake_llm.queue(answer([a, b], new_articles=[new_entry(a)], discards=[]), write_reply())
        run()
    assert EditorCall.objects.get(stage="write_new").status == "submitted"
    assert reserved_item_ids(clock.now()) == set()


def test_reservation_released_after_run_is_too_old(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    """모드를 off로 되돌려 실행이 멈춰도 48시간 뒤에는 예약이 풀린다(루틴이 처리하게)."""
    fake_llm.batch_polls_until_ended = None
    categories()
    a, b = make_items(2)
    fake_llm.queue(answer([a, b], new_articles=[new_entry(a)], discards=[]), write_reply())
    run()
    EditorRun.objects.update(started_at=clock.now() - timedelta(hours=49))
    assert reserved_item_ids(clock.now()) == set()


# --- 저장 중 경쟁·오류(설계 §5) ---


def test_item_bundled_by_routine_meanwhile_is_dropped_and_rest_saved(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    fake_llm.batch_polls_until_ended = None
    categories()
    a, b, c = make_items(3)
    decision = answer([a, b, c], new_articles=[new_entry(a, b, featured=False)], discards=[])
    decision["discards"] = [{"item_id": c.pk, "reason": "홍보"}]
    fake_llm.queue(decision, write_reply())
    run()
    # 루틴(또는 대표)이 그 사이 a를 다른 기사에 묶었다
    other = featured_article(a, featured=False, slug="routine-article")

    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    run()

    call = EditorCall.objects.get(stage="write_new")
    assert call.status == "saved" and call.article is not None
    assert list(call.article.article_items.values_list("item_id", flat=True)) == [b.pk]
    assert call.output is not None and call.output["saved"]["dropped_item_ids"] == [a.pk]
    assert str(a.pk) in call.error
    assert other.article_items.get().item_id == a.pk


def test_all_items_taken_meanwhile_skips_call(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    fake_llm.batch_polls_until_ended = None
    categories()
    a, b = make_items(2)
    fake_llm.queue(answer([a, b], new_articles=[new_entry(a)], discards=[]), write_reply())
    run()
    svc.discard_item(svc.Actor.OWNER, a.pk)  # 대표가 그 사이 버렸다

    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    run()

    call = EditorCall.objects.get(stage="write_new")
    assert call.status == "skipped" and "남지 않았다" in call.error
    assert Article.objects.count() == 0
    editor_run = EditorRun.objects.get()
    assert editor_run.status == "finished" and "건너뜀 1" in editor_run.note


def test_category_deleted_meanwhile_fails_call_and_releases_items(
    clock: FixedClock, live_batch: None, fake_llm: FakeLlm
) -> None:
    fake_llm.batch_polls_until_ended = None
    categories()
    a, b = make_items(2)
    fake_llm.queue(answer([a, b], new_articles=[new_entry(a)], discards=[]), write_reply())
    run()
    svc.delete_category(svc.list_categories()[0].slug)

    fake_llm.batch_polls_until_ended = 0
    clock.advance(3600)
    run()

    call = EditorCall.objects.get(stage="write_new")
    assert call.status == "failed" and call.error.startswith("저장 실패: 없는 분야")
    assert Item.objects.get(pk=a.pk).status == "pending"
    assert reserved_item_ids(clock.now()) == set()
    assert "저장 실패 1" in EditorRun.objects.get().note


def test_discard_skips_items_bundled_before_save(
    clock: FixedClock, live_sync: None, fake_llm: FakeLlm
) -> None:
    categories()
    a, b = make_items(2)
    editor_run = EditorRun.objects.create(mode=EditorRun.Mode.LIVE, slot="2026-10-30T06")
    featured_article(a, slug="taken")  # 분류 뒤 루틴이 먼저 묶었다
    decision = {
        "new_articles": [],
        "updates": [],
        "discards": [{"item_id": a.pk, "reason": "x"}, {"item_id": b.pk, "reason": "y"}],
        "holds": [],
        "note": "",
    }
    assert editor_save.discard(editor_run, decision) == 1  # type: ignore[arg-type]
    assert Item.objects.get(pk=a.pk).status == "bundled"
    assert Item.objects.get(pk=b.pk).status == "discarded"
    shadow_run = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-30T12")
    assert editor_save.discard(shadow_run, decision) == 0  # type: ignore[arg-type]


def _commentary_call(editor_run: EditorRun, article: Article) -> EditorCall:
    plan: editor_commentary.Plan = {
        "kind": "update",
        "write_key": "w",
        "article_id": article.pk,
        "category": article.category.slug,
        "title": article.title,
        "summary": article.summary,
        "slug": article.slug,
        "item_ids": [],
    }
    return EditorCall.objects.create(
        run=editor_run,
        stage=EditorCall.Stage.COMMENTARY,
        key=f"c{article.pk}",
        model="claude-opus-5-5",
        status=EditorCall.Status.SUCCEEDED,
        article=article,
        input={"plan": dict(plan)},
        output=editor_commentary.check(analysed(), plan),
    )


def test_hidden_commentary_and_posted_post_are_skipped(clock: FixedClock) -> None:
    categories()
    (item,) = make_items(1)
    article = featured_article(item)
    hidden = commentary_svc.upsert_commentary(
        svc.Actor.OWNER, article.pk, background="대표 글", significance="의미",
        outlook_bull="낙관", outlook_bear="비관", opinion="의견", predictions=[],
    )  # fmt: skip
    hidden.hidden = True
    hidden.save()
    Post.objects.create(
        article=article, channel="kakao", text="올린 문구", status=Post.Status.POSTED
    )
    editor_run = EditorRun.objects.create(mode=EditorRun.Mode.LIVE, slot="2026-10-30T06")
    call = _commentary_call(editor_run, article)

    editor_save.save(call, clock.now())

    call.refresh_from_db()
    assert call.status == "skipped" and "분석:" in call.error and "포스트:" in call.error
    hidden.refresh_from_db()
    assert hidden.background == "대표 글"
    assert Post.objects.get(article=article).text == "올린 문구"


def test_hidden_commentary_still_saves_post_draft(clock: FixedClock) -> None:
    categories()
    (item,) = make_items(1)
    article = featured_article(item)
    hidden = commentary_svc.upsert_commentary(
        svc.Actor.OWNER, article.pk, background="대표 글", significance="의미",
        outlook_bull="낙관", outlook_bear="비관", opinion="의견", predictions=[],
    )  # fmt: skip
    hidden.hidden = True
    hidden.save()
    editor_run = EditorRun.objects.create(mode=EditorRun.Mode.LIVE, slot="2026-10-30T06")
    call = _commentary_call(editor_run, article)

    editor_save.save(call, clock.now())

    call.refresh_from_db()
    assert call.status == "saved" and call.error.startswith("분석:")
    assert Post.objects.get(article=article).text.endswith(article_url(article.slug))


def test_unfeatured_meanwhile_skips_commentary(clock: FixedClock) -> None:
    categories()
    (item,) = make_items(1)
    article = featured_article(item)
    editor_run = EditorRun.objects.create(mode=EditorRun.Mode.LIVE, slot="2026-10-30T06")
    call = _commentary_call(editor_run, article)
    svc.set_flags(svc.Actor.OWNER, article.pk, featured=False)  # 대표가 추천을 풀었다

    editor_save.save(call, clock.now())

    call.refresh_from_db()
    assert call.status == "skipped"
    assert not Commentary.objects.exists() and not Post.objects.exists()


# --- 경보 ---


def _pipeline_run(slot: str, mode: str, status: str, finished: datetime) -> EditorRun:
    return EditorRun.objects.create(
        mode=mode, slot=slot, status=status, finished_at=finished, note="분류 실패: API 오류"
    )


def test_missing_editor_alert_counts_live_runs_not_shadow(
    clock: FixedClock, fake_discord: FakeNotifier
) -> None:
    now = clock.now()
    _pipeline_run("2026-10-30T06", "shadow", "finished", now - timedelta(hours=1))
    assert any("편집자 미실행" in m for m in check_alerts(fake_discord, now))

    _pipeline_run("2026-10-30T00", "live", "finished", now - timedelta(hours=2))
    sent = check_alerts(fake_discord, now)
    assert any("[복구] 편집자가 다시" in m for m in sent)
    assert not Alert.objects.get(kind=Alert.Kind.EDITOR_MISSING).active


def test_pipeline_failure_alert_after_two_failed_slots_then_recovers(
    clock: FixedClock, fake_discord: FakeNotifier
) -> None:
    now = clock.now()
    _pipeline_run("2026-10-29T12", "live", "finished", now - timedelta(hours=18))
    _pipeline_run("2026-10-29T18", "live", "failed", now - timedelta(hours=12))
    assert not any("파이프라인 실패" in m for m in check_alerts(fake_discord, now))

    EditorRun.objects.create(  # 예산 건너뜀은 세지 않는다
        mode="live", slot="2026-10-29T21", status="skipped_budget", finished_at=now
    )
    _pipeline_run("2026-10-30T06", "shadow", "failed", now)
    sent = check_alerts(fake_discord, now)
    assert [m for m in sent if "파이프라인 실패" in m] == [
        next(m for m in sent if m.startswith("[경보] 편집 파이프라인 실패"))
    ]
    assert not any("파이프라인 실패" in m for m in check_alerts(fake_discord, now))  # 한 번만

    _pipeline_run("2026-10-30T12", "live", "finished", now)
    assert "[복구] 편집 파이프라인 분류가 다시 성공했습니다." in check_alerts(fake_discord, now)
