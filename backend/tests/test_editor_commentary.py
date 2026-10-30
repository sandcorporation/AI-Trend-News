"""편집자 파이프라인 5(#127): 3단계 분석·포스트 초안(그림자). 가짜 LLM·고정 시계·가짜 페처만."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, override_settings
from sandcorp_authkit.testing import FixedClock

from adapters.anthropic import FakeLlm, Refusal
from adapters.clock import set_clock
from adapters.fetch import set_fetcher
from articles.models import Article, Commentary
from collection.models import Item, Source
from editor.models import EditorCall, EditorRun
from posts.models import Post
from services import articles as svc
from services import commentary as commentary_svc
from services import editor_commentary
from services.commentary import PredictionIn
from services.editor_compare import compare
from services.editor_runs import start_run
from services.posts import article_url, with_article_link
from tests.test_editor_pipeline import (
    KST,
    SHADOW,
    Pages,
    answer,
    categories,
    data_block,
    make_items,
    page,
    run,
    written,
)

pytestmark = pytest.mark.django_db

SYNC = {**SHADOW, "EDITOR_MAX_COMMENTARY": 4}
BATCH = {**SYNC, "EDITOR_USE_BATCH": True}


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
def sync() -> Iterator[None]:
    with override_settings(**SYNC):
        yield


@pytest.fixture
def batch(fake_sleep: list[float]) -> Iterator[None]:
    with override_settings(**BATCH):
        yield


def analysed(**override: object) -> dict[str, object]:
    """3단계 규칙에 맞는 응답(링크 없는 포스트 문장)."""
    reply: dict[str, object] = {
        "background": (
            "보도에 따르면 회사는 지난달 앞선 모델을 냈다.\n\n경쟁사도 비슷한 발표를 했다."
        ),
        "significance": "개발자는 더 싼 값에 같은 성능을 쓴다.",
        "outlook_bull": "가격이 내려가면 쓰임이 늘어난다. 공급이 막히면 이 전제는 깨진다.",
        "outlook_bear": "성능 차이가 작으면 갈아탈 까닭이 없다. 벤치마크가 크게 앞서면 깨진다.",
        "watchpoints": ["다음 분기 실적에서 추론 매출을 따로 밝히는지"],
        "opinion": "가격 경쟁은 이용자에게 좋다. 다만 성능 주장은 검증이 먼저다.",
        "predictions": [
            {"claim": "경쟁사가 3개월 안에 값을 내린다", "horizon": "3m", "basis": "전례"}
        ],
        "post_text": "오픈AI가 새 모델을 공개했다. 값은 내리고 성능은 올렸다.",
    }
    reply.update(override)
    return reply


def featured_article(item: Item, *, featured: bool = True, slug: str = "old-article") -> Article:
    return svc.create_article(
        svc.Actor.EDITOR,
        item_ids=[item.pk],
        title="기존 추천 기사",
        summary="기존 요약.",
        category=svc.list_categories()[0].slug,
        tags=["OpenAI", "모델"],
        published=True,
        featured=featured,
        slug=slug,
    )


def more_items(n: int) -> list[Item]:
    """`make_items`와 겹치지 않는 URL의 수집물(관련 기사 재료)."""
    source = Source.objects.create(name="AP", url="https://example.org/feed")
    return [
        Item.objects.create(
            source=source,
            url=f"https://example.org/{i}",
            normalized_url=f"https://example.org/{i}",
            title=f"다른 {i}",
            excerpt="발췌",
        )  # fmt: skip
        for i in range(n)
    ]


def update_reply() -> dict[str, object]:
    return {"title": None, "summary": "후속 사실을 더했다.", "tags": None, "reason": "후속"}


def commentary_calls() -> list[EditorCall]:
    return list(EditorCall.objects.filter(stage="commentary").order_by("id"))


def new_entry(item: Item, *, featured: bool, published: bool = True) -> dict[str, object]:
    return {
        "item_ids": [item.pk],
        "category": svc.list_categories()[0].slug,
        "published": published,
        "featured": featured,
        "reason": "발표",
    }


# --- 호출 대상 ---


def test_targets_by_recommendation_and_commentary_state(
    clock: FixedClock, sync: None, fake_llm: FakeLlm
) -> None:
    """추천+게시 새 기사, 분석 없는 추천·게시 갱신만 부른다. 숨긴·있는 분석, 비추천은 빼고
    작성 단계가 비게시로 내린 새 기사도 뺀다. 그림자: 기사·분석·포스트를 하나도 만들지 않는다."""
    categories()
    plain_old, hidden_old, shown_old, unfeatured_old, *items = make_items(11)
    bare = featured_article(plain_old)
    hidden = featured_article(hidden_old, slug="hidden-one")
    shown = featured_article(shown_old, slug="shown-one")
    unfeatured = featured_article(unfeatured_old, featured=False, slug="plain-one")
    for target in (hidden, shown):
        commentary_svc.upsert_commentary(
            svc.Actor.EDITOR, target.pk, background="배경", significance="의미",
            outlook_bull="낙관", outlook_bear="회의", opinion="의견", predictions=[],
        )  # fmt: skip
    Commentary.objects.filter(article=hidden).update(hidden=True)
    a, b, c, *rest = items
    decision = answer(
        items,
        new_articles=[
            new_entry(a, featured=True),  # 대상
            new_entry(b, featured=False),  # 추천 아님
            new_entry(c, featured=True),  # 작성이 비게시로 내림
        ],
        updates=[  # bare만 대상: 숨긴 분석·있는 분석·추천 아님은 뺀다
            {"article_id": t.pk, "item_ids": [i.pk], "reason": "후속"}
            for t, i in zip((bare, hidden, shown, unfeatured), rest, strict=True)
        ],
        discards=[],
    )
    fake_llm.queue(
        decision,
        written(),
        written(slug="second-one"),
        written(slug="third-one", published=False, unpublish_reason="루머"),
        *[update_reply() for _ in range(4)],
        analysed(),
        analysed(post_text="기존 기사에 후속이 붙었다."),
    )

    run()

    calls = commentary_calls()
    editor_run = EditorRun.objects.get()
    assert [c.key for c in calls] == [
        f"r{editor_run.pk}-commentary-1",
        f"r{editor_run.pk}-commentary-2",
    ]
    assert [(c.status, c.article_id) for c in calls] == [
        ("succeeded", None),
        ("succeeded", bare.pk),
    ]
    assert calls[0].item_ids == [a.pk]
    assert calls[1].item_ids == [rest[0].pk, plain_old.pk]  # 새 수집물 → 이미 묶인 수집물
    assert len(fake_llm.requests) == 10  # 분류 1 + 작성 7 + 분석 2
    assert editor_run.status == EditorRun.Status.FINISHED
    assert "분석·포스트 2건(실패 0)" in editor_run.note
    assert editor_run.cost_usd == sum((c.cost_usd for c in EditorCall.objects.all()), Decimal(0))
    # 그림자: 분석·포스트·기사는 그대로
    assert Commentary.objects.count() == 2
    assert not Post.objects.exists()
    assert Article.objects.count() == 4
    assert Item.objects.filter(pk__in=[i.pk for i in items], status="pending").count() == len(items)


def test_post_link_is_added_by_server_with_virtual_or_real_slug(
    clock: FixedClock, sync: None, fake_llm: FakeLlm
) -> None:
    categories()
    old, a, b = make_items(3)
    article = featured_article(old)
    decision = answer(
        [a, b],
        new_articles=[new_entry(a, featured=True)],
        updates=[{"article_id": article.pk, "item_ids": [b.pk], "reason": "후속"}],
        discards=[],
    )
    fake_llm.queue(decision, written(slug="virtual-slug"), update_reply(), analysed(), analysed())

    run()

    first, second = commentary_calls()
    assert first.output is not None and second.output is not None
    assert first.output["post"] == {
        "channel": "kakao",
        "text": "오픈AI가 새 모델을 공개했다. 값은 내리고 성능은 올렸다.\n"
        + article_url("virtual-slug"),
    }
    assert second.output["post"]["text"].endswith(f"\n{settings.SITE_URL}/a/old-article")
    assert first.output["article"]["slug"] == "virtual-slug"
    assert first.output["commentary"]["predictions"][0]["horizon"] == "3m"


def test_with_article_link_appends_last_line() -> None:
    assert (
        with_article_link("  한 문장이다.  ", "abc") == f"한 문장이다.\n{settings.SITE_URL}/a/abc"
    )


# --- 요청 모양·원문 상한·관련 기사 ---


def test_request_uses_commentary_prompt_model_and_caps_text_items_related(
    clock: FixedClock, sync: None, fake_llm: FakeLlm, pages: Pages
) -> None:
    categories()
    items = make_items(4)
    for item in items:
        pages.pages[item.url] = page("가" * 20_000)
    slug = svc.list_categories()[0].slug
    other = svc.list_categories()[1].slug
    recent: list[Article] = []
    extra = iter(more_items(14))
    for n in range(12):
        source = next(extra)
        recent.append(
            svc.create_article(
                svc.Actor.EDITOR, item_ids=[source.pk], title=f"최근 {n}", summary=f"요약 {n}.",
                category=slug, tags=["가", "나"], published=True, slug=f"recent-{n}",
            )
        )  # fmt: skip
    # 14일보다 오래된 기사, 다른 분야, 비게시는 빠진다
    Article.objects.filter(pk=recent[0].pk).update(published_at=clock.now() - timedelta(days=15))
    for title, category, published in (("다른 분야", other, True), ("비게시", slug, False)):
        svc.create_article(
            svc.Actor.EDITOR, item_ids=[next(extra).pk], title=title, summary="요약.",
            category=category, tags=["가", "나"], published=published,
        )  # fmt: skip
    decision = answer(
        items,
        new_articles=[{**new_entry(items[0], featured=True), "item_ids": [i.pk for i in items]}],
        discards=[],
    )
    fake_llm.queue(decision, written(), analysed())

    run()

    request = json.loads(json.dumps(fake_llm.requests[2]))
    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"]["effort"] == "high"
    assert "이번 단계: 분석·포스트 초안" in request["system"][0]["text"]
    assert "## AI 분석" in request["system"][0]["text"]
    assert "## 포스트 초안" in request["system"][0]["text"]
    assert "## 중복 판단" not in request["system"][0]["text"]
    assert request["system"][0]["cache_control"] == {"type": "ephemeral"}
    schema = request["output_config"]["format"]["schema"]
    assert set(schema["required"]) == {
        "background", "significance", "outlook_bull", "outlook_bear",
        "watchpoints", "opinion", "predictions", "post_text",
    }  # fmt: skip
    data = data_block(fake_llm.requests[2])
    assert data["article"] == {
        "title": "오픈AI, 새 모델 공개",
        "summary": "오픈AI가 새 모델을 공개했다. 성능이 올랐다.",
        "category": {"slug": slug, "name": "AI"},
    }
    assert isinstance(data["items"], list)
    texts = [len(i["text"]) for i in data["items"]]
    assert texts == [8000, 8000, 8000]  # 각 8,000자, 최대 3건
    related = data["related"]
    assert isinstance(related, list) and len(related) == 10
    titles = {r["title"] for r in related}
    assert "최근 0" not in titles and "다른 분야" not in titles and "비게시" not in titles
    # 같은 실행에서 작성이 받은 원문은 다시 받지 않는다(작성 4,000자 → 분석 8,000자)
    assert len(pages.requested) == len(set(pages.requested))
    call = commentary_calls()[0]
    assert call.input["related"] == 10
    assert [i["text_chars"] for i in call.input["items"]] == [8000, 8000, 8000]
    assert "가가가" not in json.dumps(call.input, ensure_ascii=False)  # 원문은 저장하지 않는다


# --- 검증 실패 재요청 ---


def test_validation_failure_retries_once_with_error_note(
    clock: FixedClock, sync: None, fake_llm: FakeLlm
) -> None:
    categories()
    (a,) = make_items(1)
    decision = answer([a], new_articles=[new_entry(a, featured=True)], discards=[])
    fake_llm.queue(decision, written(), analysed(opinion="의" * 300), analysed())

    run()

    (call,) = commentary_calls()
    assert (call.status, call.attempt) == ("succeeded", 1)
    retry = fake_llm.requests[3]["messages"][0]["content"]
    assert isinstance(retry, str) and "의견은(는) 1~240자" in retry


@pytest.mark.parametrize(
    "bad",
    [
        {"post_text": "링크를 넣었다. https://example.com/a"},
        {"predictions": [{"claim": "가", "horizon": "3m", "basis": "나"}] * 4},
        {"watchpoints": ["가", "나", "다", "라"]},
        {"post_text": "가" * 260},
    ],
)
def test_rule_violation_twice_fails(
    clock: FixedClock, sync: None, fake_llm: FakeLlm, bad: dict[str, object]
) -> None:
    categories()
    (a,) = make_items(1)
    decision = answer([a], new_articles=[new_entry(a, featured=True)], discards=[])
    fake_llm.queue(decision, written(), analysed(**bad), analysed(**bad))

    run()

    (call,) = commentary_calls()
    assert (call.status, call.attempt) == ("failed", 1)
    assert "실패 1" in EditorRun.objects.get().note


def test_refusal_fails_without_retry(clock: FixedClock, sync: None, fake_llm: FakeLlm) -> None:
    categories()
    (a,) = make_items(1)
    decision = answer([a], new_articles=[new_entry(a, featured=True)], discards=[])
    fake_llm.queue(decision, written(), Refusal())

    run()

    (call,) = commentary_calls()
    assert (call.status, call.attempt) == ("failed", 0)


# --- 상한·끔·예산 ---


def test_cap_per_slot_skips_overflow(clock: FixedClock, sync: None, fake_llm: FakeLlm) -> None:
    categories()
    items = make_items(3)
    decision = answer(items, new_articles=[new_entry(i, featured=True) for i in items], discards=[])
    with override_settings(EDITOR_MAX_COMMENTARY=2):
        fake_llm.queue(
            decision, written(slug="one-a"), written(slug="two-b"), written(slug="three-c"),
            analysed(), analysed(),
        )  # fmt: skip
        run()

    calls = commentary_calls()
    assert [c.status for c in calls] == ["succeeded", "succeeded", "skipped"]
    assert calls[2].error == "슬롯당 분석 상한 2건 초과" and calls[2].cost_usd == 0
    assert "상한 건너뜀 1" in EditorRun.objects.get().note


def test_zero_cap_turns_stage_off(clock: FixedClock, sync: None, fake_llm: FakeLlm) -> None:
    categories()
    (a,) = make_items(1)
    decision = answer([a], new_articles=[new_entry(a, featured=True)], discards=[])
    with override_settings(EDITOR_MAX_COMMENTARY=0):
        fake_llm.queue(decision, written())
        run()
    assert not commentary_calls()
    assert EditorRun.objects.get().status == EditorRun.Status.FINISHED


def _run_with_written_new(featured: bool = True) -> EditorRun:
    categories()
    (a,) = make_items(1)
    editor_run = EditorRun.objects.create(mode=EditorRun.Mode.SHADOW, slot="2026-10-30T06")
    EditorCall.objects.create(
        run=editor_run,
        stage=EditorCall.Stage.WRITE_NEW,
        key=f"r{editor_run.pk}-write-1",
        model="claude-sonnet-5-5",
        status=EditorCall.Status.SUCCEEDED,
        item_ids=[a.pk],
        output={
            "article": {
                "category": svc.list_categories()[0].slug, "item_ids": [a.pk], "title": "제목",
                "summary": "요약.", "slug": "virtual", "published": True, "featured": featured,
            }
        },
    )  # fmt: skip
    return editor_run


def test_budget_gate_before_commentary_skips_run(clock: FixedClock) -> None:
    editor_run = _run_with_written_new()
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("0.05"), EDITOR_MAX_COMMENTARY=4):
        result = editor_commentary.enqueue(editor_run, clock.now(), via_batch=True)
    assert result is None
    editor_run.refresh_from_db()
    assert editor_run.status == EditorRun.Status.SKIPPED_BUDGET
    assert "월 예산 초과" in editor_run.note
    assert not commentary_calls()


def test_budget_estimate_for_commentary_is_opus_batch_price(clock: FixedClock) -> None:
    editor_run = _run_with_written_new()
    with override_settings(EDITOR_MONTHLY_BUDGET_USD=Decimal("0.10"), EDITOR_MAX_COMMENTARY=4):
        calls = editor_commentary.enqueue(editor_run, clock.now(), via_batch=True)
    assert calls is not None and [c.status for c in calls] == ["queued"]
    assert calls[0].model == "claude-opus-5-5" and calls[0].via_batch


def test_unfeatured_new_article_makes_no_call(clock: FixedClock) -> None:
    editor_run = _run_with_written_new(featured=False)
    with override_settings(EDITOR_MAX_COMMENTARY=4):
        assert editor_commentary.enqueue(editor_run, clock.now(), via_batch=True) == []


# --- 배치: 2단계 → 3단계 순서 ---


def test_batch_runs_commentary_after_all_writes_end(
    clock: FixedClock, batch: None, fake_llm: FakeLlm
) -> None:
    categories()
    a, b = make_items(2)
    decision = answer(
        [a, b],
        new_articles=[new_entry(a, featured=True), new_entry(b, featured=False)],
        discards=[],
    )
    fake_llm.queue(
        decision, written(), written(slug="second-one"),
        analysed(opinion="의" * 300), analysed(),  # 분석: 검증 실패 → 다음 배치로 재제출
    )  # fmt: skip

    run()

    editor_run = EditorRun.objects.get()
    ids = [[r["custom_id"] for r in batch_] for batch_ in fake_llm.batch_requests]
    pk = editor_run.pk
    assert ids == [
        [f"r{pk}-write-1", f"r{pk}-write-2"],
        [f"r{pk}-commentary-1"],
        [f"r{pk}-commentary-1"],
    ]
    retry = fake_llm.batch_requests[2][0]["params"]["messages"][0]["content"]
    assert isinstance(retry, str) and "의견은(는) 1~240자" in retry
    (call,) = commentary_calls()
    assert (call.status, call.attempt, call.via_batch) == ("succeeded", 1, True)
    assert call.output is not None and call.output["post"]["text"].endswith("/a/openai-new-model")
    assert call.cost_usd > 0
    assert editor_run.status == EditorRun.Status.FINISHED
    assert "분석·포스트 1건(실패 0)" in editor_run.note
    assert editor_run.cost_usd == sum((c.cost_usd for c in EditorCall.objects.all()), Decimal(0))


def test_batch_commentary_waits_for_unfinished_writes(
    clock: FixedClock, batch: None, fake_llm: FakeLlm
) -> None:
    categories()
    (a,) = make_items(1)
    decision = answer([a], new_articles=[new_entry(a, featured=True)], discards=[])
    fake_llm.batch_polls_until_ended = None  # 작성 배치가 끝나지 않는다
    fake_llm.queue(decision, written())

    run()

    assert len(fake_llm.batch_requests) == 1
    assert not commentary_calls()
    assert EditorRun.objects.get().status == EditorRun.Status.RUNNING


# --- 비교 화면 ---


def test_shadow_compare_shows_commentary_side_by_side(
    clock: FixedClock, sync: None, fake_llm: FakeLlm, client: Client
) -> None:
    categories()
    old, a = make_items(2)
    article = featured_article(old)
    decision = answer(
        [a],
        new_articles=[],
        updates=[{"article_id": article.pk, "item_ids": [a.pk], "reason": "후속"}],
        discards=[],
    )
    fake_llm.queue(decision, update_reply(), analysed())
    run()
    # 루틴이 15분 뒤 같은 기사에 분석·포스트를 썼다
    commentary_svc.upsert_commentary(
        svc.Actor.EDITOR, article.pk, background="루틴 배경", significance="루틴 의미",
        outlook_bull="루틴 낙관", outlook_bear="루틴 회의", opinion="루틴 의견",
        predictions=[PredictionIn(claim="루틴 예측", horizon="6m", basis="근거")],
    )  # fmt: skip
    Post.objects.create(article=article, channel="kakao", text="루틴 포스트")
    editor_run = EditorRun.objects.get()

    (pair,) = compare(editor_run).commentaries
    assert pair.kind == "갱신" and pair.routine_article is not None
    assert pair.routine_article["id"] == article.pk
    by_label = {s.label: s for s in pair.sections}
    assert by_label["배경"].routine == "루틴 배경"
    assert by_label["배경"].pipeline.startswith("보도에 따르면")
    assert by_label["예측"].routine == "루틴 예측 (6m) — 근거"
    assert by_label["포스트 초안"].routine == "루틴 포스트"
    assert by_label["포스트 초안"].pipeline.endswith("/a/old-article")

    owner = get_user_model().objects.create_superuser("owner", password=None)
    client.force_login(owner)
    html = client.get(f"/{settings.ADMIN_PATH}editor/editorrun/{editor_run.pk}/shadow/")
    text = html.content.decode()
    assert html.status_code == 200
    assert "분석 대조" in text and "루틴 배경" in text and "보도에 따르면" in text
    assert f'data-commentary="r{editor_run.pk}-commentary-1"' in text


def test_compare_without_commentary_calls_is_empty() -> None:
    routine = start_run()
    assert compare(routine).commentaries == []


def test_editor_probe_commentary_prints_and_saves_nothing(fake_llm: FakeLlm) -> None:
    categories()
    (item,) = make_items(1)
    article = featured_article(item)
    fake_llm.queue(analysed())
    out = StringIO()
    call_command("editor_probe", "--stage", "commentary", "--article", str(article.pk), stdout=out)
    text = out.getvalue()
    assert "단계 commentary / 모델 claude-opus-5-5 / effort high" in text
    assert "결과 succeeded" in text and "추정 US$" in text
    assert not EditorCall.objects.exists() and not EditorRun.objects.exists()
    assert not Commentary.objects.exists() and not Post.objects.exists()
