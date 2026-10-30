"""게시 모드 저장(#128, 설계 §5·§9). 각 단계 결과를 MCP 도구가 부르던 서비스 함수로 직접 저장한다.

주체는 `Actor.EDITOR` — 루틴과 같은 규칙(대표가 덮어쓴 플래그·숨긴 분석·올린 포스트 거부)이 그대로
걸린다. **게시(`live`) 실행의 호출만 저장한다**: `save`가 실행 모드를 직접 보고 그림자 실행이면 아무
것도 하지 않는다. 운영이 그림자 모드인 동안 이 모듈은 수집물·기사·분석·포스트를 바꾸지 않는다.

호출 하나 = 저장 하나(부르는 쪽의 트랜잭션 안 savepoint). 결과는 호출 상태로 남는다.

| 상황 | 상태 |
|---|---|
| 저장함 | `saved`(`article`에 기사, 뺀 수집물은 `output["saved"]`) |
| 수집물이 이미 묶임·버림 | 그 수집물만 빼고 저장. 남은 것이 없으면 `skipped` |
| 분야가 지워짐·대상 기사가 없음 | `failed` |
| 숨긴 분석·추천/게시가 풀린 기사·올린 포스트 | `skipped`(하나라도 저장했으면 `saved`+`error`) |
"""

import logging
from collections.abc import Callable
from datetime import datetime

from django.db import transaction

from articles.models import Article
from collection.models import Item
from editor.models import EditorCall, EditorRun
from services import articles, commentary, posts
from services.articles import Actor, ArticleError
from services.commentary import CommentaryError, PredictionIn
from services.editor_classify import JSON, Decision
from services.posts import PostError

logger = logging.getLogger("editor")

# 같은 수집물을 다른 실행·루틴이 먼저 묶는 경쟁은 다시 걸러 다시 시도한다(수집물 수만큼이면 충분).
_BIND_ERRORS = (articles.ItemAlreadyBundled, articles.ItemNotPending, articles.ItemNotFound)


class _Skip(Exception):
    """재시도하지 않고 건너뛴다(설계 §5: 남은 수집물 없음·대표 규칙 거부)."""


def is_live(run: EditorRun) -> bool:
    return run.mode == EditorRun.Mode.LIVE


# --- JSON 칸 읽기(출력은 검사를 거친 모양이지만 JSON 칸이라 타입을 좁힌다) ---


def _obj(value: object) -> JSON:
    return value if isinstance(value, dict) else {}


def _str(raw: JSON, key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _opt_str(raw: JSON, key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _ints(raw: JSON, key: str) -> list[int]:
    value = raw.get(key)
    return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []


def _strs(raw: JSON, key: str) -> list[str] | None:
    value = raw.get(key)
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else None


def _pending(item_ids: list[int]) -> list[int]:
    """아직 미처리인 수집물만(순서 유지). 묶임·버림·없는 수집물은 뺀다."""
    ok = set(
        Item.objects.filter(pk__in=item_ids, status=Item.Status.PENDING).values_list(
            "pk", flat=True
        )
    )
    return [i for i in item_ids if i in ok]


def _bind_with_retry(item_ids: list[int], bind: Callable[[list[int]], Article]) -> Article:
    """미처리 수집물만 골라 `bind`를 부른다. 그 사이 남이 묶었으면 다시 걸러 다시 부른다."""
    ids = _pending(item_ids)
    for _ in range(len(item_ids) + 1):
        if not ids:
            raise _Skip("묶을 수집물이 남지 않았다(이미 묶임·버림)")
        try:
            with transaction.atomic():
                return bind(ids)
        except _BIND_ERRORS:
            ids = _pending(ids)
    raise _Skip("묶을 수집물이 남지 않았다(경쟁)")


# --- 단계별 저장 ---


def _save_new(call: EditorCall) -> None:
    article = _obj(_obj(call.output).get("article"))
    wanted = _ints(article, "item_ids")

    def bind(ids: list[int]) -> Article:
        return articles.create_article(
            Actor.EDITOR,
            item_ids=ids,
            title=_str(article, "title"),
            summary=_str(article, "summary"),
            category=_str(article, "category"),
            tags=_strs(article, "tags") or [],
            published=article.get("published") is True,
            featured=article.get("featured") is True,
            image_url=_str(article, "image_url"),
            slug=_str(article, "slug") or None,
        )

    created = _bind_with_retry(wanted, bind)
    call.article = created
    _mark_saved(call, created, wanted)


def _save_update(call: EditorCall) -> None:
    changes = _obj(_obj(call.output).get("changes"))
    article_id = changes.get("article_id")
    if not isinstance(article_id, int):
        raise articles.ArticleNotFound("갱신 대상 기사 id가 없다")
    target = articles.get_article(article_id)  # 지워졌으면 ArticleNotFound → 실패
    wanted = _ints(changes, "item_ids")
    image = _opt_str(changes, "image_url")

    def bind(ids: list[int]) -> Article:
        return articles.update_article(
            Actor.EDITOR,
            article_id,
            add_item_ids=ids,
            title=_opt_str(changes, "title"),
            summary=_opt_str(changes, "summary"),
            tags=_strs(changes, "tags"),
            image_url=image if image and not target.image_url else None,  # 빈 이미지만 채운다
        )

    updated = _bind_with_retry(wanted, bind)
    call.article = updated
    _mark_saved(call, updated, wanted)


def _mark_saved(call: EditorCall, article: Article, wanted: list[int]) -> None:
    bound = set(article.article_items.values_list("item_id", flat=True))
    dropped = [i for i in wanted if i not in bound]
    call.output = {
        **_obj(call.output),
        "saved": {"article_id": article.pk, "slug": article.slug, "dropped_item_ids": dropped},
    }
    if dropped:
        call.error = f"이미 묶임·버림이라 뺀 수집물 {dropped}"


def _predictions(raw: JSON) -> list[PredictionIn]:
    value = raw.get("predictions")
    if not isinstance(value, list):
        return []
    return [PredictionIn.model_validate(p) for p in value if isinstance(p, dict)]


def _save_commentary(call: EditorCall) -> None:
    output = _obj(call.output)
    article_id = _obj(output.get("article")).get("article_id")
    if not isinstance(article_id, int):
        raise articles.ArticleNotFound("분석 대상 기사 id가 없다")
    fields, post = _obj(output.get("commentary")), _obj(output.get("post"))
    saved: list[str] = []
    refused: list[str] = []
    try:
        with transaction.atomic():
            commentary.upsert_commentary(
                Actor.EDITOR,
                article_id,
                background=_str(fields, "background"),
                significance=_str(fields, "significance"),
                outlook_bull=_str(fields, "outlook_bull"),
                outlook_bear=_str(fields, "outlook_bear"),
                opinion=_str(fields, "opinion"),
                predictions=_predictions(fields),
                watchpoints=_strs(fields, "watchpoints"),
            )
        saved.append("commentary")
    except (commentary.CommentaryNotAllowed, commentary.CommentaryHidden) as error:
        refused.append(f"분석: {error}")
    try:
        with transaction.atomic():
            posts.upsert_post_draft(
                Actor.EDITOR, article_id, _str(post, "channel"), _str(post, "text")
            )
        saved.append("post")
    except (posts.ArticleNotPostable, posts.PostAlreadyPosted) as error:
        refused.append(f"포스트: {error}")
    if not saved:
        raise _Skip("; ".join(refused))
    call.article_id = article_id
    call.output = {**output, "saved": {"article_id": article_id, "parts": saved}}
    call.error = "; ".join(refused)


_SAVERS: dict[str, Callable[[EditorCall], None]] = {
    EditorCall.Stage.WRITE_NEW: _save_new,
    EditorCall.Stage.WRITE_UPDATE: _save_update,
    EditorCall.Stage.COMMENTARY: _save_commentary,
}


def save(call: EditorCall, now: datetime) -> None:
    """성공한 호출을 저장하고 상태를 `saved`·`skipped`·`failed`로 끝낸다. 게시 실행이 아니면 무시.

    부르는 쪽이 트랜잭션을 열어 두면 그 안에서(savepoint) 처리한다 — 호출 상태와 저장이 함께 남는다.
    """
    if not is_live(call.run) or call.status != EditorCall.Status.SUCCEEDED:
        return
    saver = _SAVERS.get(call.stage)
    if saver is None:
        return
    try:
        with transaction.atomic():
            saver(call)
    except _Skip as skip:
        call.status, call.error = EditorCall.Status.SKIPPED, str(skip)
    except (ArticleError, CommentaryError, PostError) as error:
        call.status, call.error = EditorCall.Status.FAILED, f"저장 실패: {error}"
        logger.warning("게시 모드 저장 실패 %s: %s", call.key, error)
    else:
        call.status = EditorCall.Status.SAVED
    call.finished_at = now
    call.save()


def discard(run: EditorRun, decision: Decision) -> int:
    """분류의 버림을 저장한다(게시 실행만). 이미 묶였거나 버려졌거나 없는 수집물은 빼고 센다."""
    if not is_live(run):
        return 0
    count = 0
    for entry in decision["discards"]:
        try:
            with transaction.atomic():
                item = Item.objects.select_for_update().filter(pk=entry["item_id"]).first()
                if item is None or item.status != Item.Status.PENDING:
                    continue
                articles.discard_item(Actor.EDITOR, item.pk)
        except ArticleError as error:
            logger.info("버림 건너뜀 %s: %s", entry["item_id"], error)
            continue
        count += 1
    return count


def tally(run: EditorRun) -> None:
    """실행 건수(`finish_editor_run`과 같은 뜻): 저장한 새 기사·갱신 수. 버림은 `discard`가."""
    saved = run.calls.filter(status=EditorCall.Status.SAVED)
    run.created = saved.filter(stage=EditorCall.Stage.WRITE_NEW).count()
    run.updated = saved.filter(stage=EditorCall.Stage.WRITE_UPDATE).count()


def note(run: EditorRun) -> str:
    """실행 메모 한 조각(게시 실행만): 저장 건수와 건너뜀·저장 실패."""
    if not is_live(run):
        return ""
    calls = run.calls.exclude(stage=EditorCall.Stage.CLASSIFY)
    skipped = calls.filter(status=EditorCall.Status.SKIPPED).exclude(
        error__startswith="슬롯당 분석 상한"
    )
    parts = f"저장: 새 기사 {run.created} · 갱신 {run.updated} · 버림 {run.discarded}"
    extra = []
    if skipped.exists():
        extra.append(f"건너뜀 {skipped.count()}")
    failed = calls.filter(status=EditorCall.Status.FAILED, error__startswith="저장 실패").count()
    if failed:
        extra.append(f"저장 실패 {failed}")
    return parts + (f"({', '.join(extra)})" if extra else "")
