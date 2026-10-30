"""3단계 분석·포스트 초안(#127, 설계 §2): 호출 대상 고르기, 입력 조립, 출력 스키마, 서버 검사.

대상(`targets`)은 같은 실행의 2단계 작성 결과에서 고른다.
- 새 기사: 작성 출력의 최종 `published`·`featured`가 모두 참인 가상 기사(그림자에서는
  아직 id가 없다). 게시 모드(#128)는 저장된 기사(`EditorCall.article`)의 지금 상태로 고른다.
- 갱신: 대상 기사가 지금 추천·게시이고 `Commentary` 행이 아예 없는 기사. 분석이 있으면(숨긴
  분석 포함) 부르지 않는다 — 숨긴 분석은 편집자가 덮어쓰지 못하고(`CommentaryHidden`), 있는 분석은
  다시 쓰지 않는 것이 지금 규칙이다.
슬롯당 호출은 `EDITOR_MAX_COMMENTARY`까지(새 기사 → 갱신 순, 0이면 3단계를 끈다). 넘는 대상은
`skipped` 호출로 남긴다.

검사는 `services/commentary.py::clean_fields`(저장 없이 길이·개수만)를 그대로 쓰고, 포스트 문구는
모델이 링크 없이 쓰면 서버가 `posts.with_article_link`로 마지막 줄에 기사 링크를 붙인다.
원문은 저장하지 않는다 — `EditorCall.input`에는 계획(`plan`)과 길이·실패 까닭만 남긴다.
"""

import json
from datetime import datetime, timedelta
from typing import Literal, TypedDict

from django.conf import settings

from adapters.anthropic import LlmRequest
from articles.models import Article, ArticleItem, Category, Commentary
from collection.models import Item
from editor.models import EditorCall, EditorRun
from services import editor_budget, editor_prompts
from services.commentary import HORIZONS, InvalidCommentary, PredictionIn, clean_fields
from services.editor_classify import JSON, CategoryInput, InvalidOutput
from services.editor_write import EXCERPT_CHARS, RunReader, sentence_count
from services.posts import with_article_link

COMMENTARY_TEXT_CHARS = 8000
TEXTS_PER_COMMENTARY = 3
RELATED_DAYS = 14
RELATED_MAX = 10
COMMENTARY_MAX_TOKENS = 16_000  # thinking 포함. SDK의 비스트리밍 상한(약 21K) 안
POST_CHANNEL = "kakao"
POST_MAX_CHARS = 300  # 링크 포함(editor.md 「포스트 초안」)
POST_SENTENCES = 3


class Plan(TypedDict):
    """`EditorCall.input["plan"]`: 분석할 기사. 새 기사는 작성 출력의 가상 기사(id 없음)."""

    kind: Literal["new", "update"]
    write_key: str  # 이 대상을 낳은 작성 호출
    article_id: int | None
    category: str
    title: str
    summary: str
    slug: str  # 포스트 링크용. 그림자 새 기사는 작성 단계가 정한 가상 slug
    item_ids: list[int]


# --- 대상 ---


def _object(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _ints(value: object) -> list[int]:
    return [v for v in value if isinstance(v, int)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def targets(run: EditorRun) -> list[Plan]:
    """이 실행의 성공한 작성 호출에서 분석 대상을 고른다(상한 적용 전, 새 기사 → 갱신 순)."""
    done = (EditorCall.Status.SUCCEEDED, EditorCall.Status.SAVED)
    calls = list(
        run.calls.filter(
            stage__in=(EditorCall.Stage.WRITE_NEW, EditorCall.Stage.WRITE_UPDATE), status__in=done
        ).order_by("id")
    )
    found: list[Plan] = []
    for call in calls:
        if call.stage != EditorCall.Stage.WRITE_NEW:
            continue
        if call.article_id is not None:  # 게시 모드(#128): 저장된 기사의 지금 상태로 고른다
            saved = (
                Article.objects.select_related("category")
                .filter(pk=call.article_id, published=True, featured=True)
                .exclude(pk__in=Commentary.objects.values("article_id"))
                .first()
            )
            if saved is not None:
                found.append(
                    {
                        "kind": "new",
                        "write_key": call.key,
                        "article_id": saved.pk,
                        "category": saved.category.slug,
                        "title": saved.title,
                        "summary": saved.summary,
                        "slug": saved.slug,
                        "item_ids": list(
                            ArticleItem.objects.filter(article=saved)
                            .order_by("id")
                            .values_list("item_id", flat=True)
                        ),
                    }
                )
            continue
        article = _object(_object(call.output).get("article"))
        if article.get("published") is True and article.get("featured") is True:
            found.append(
                {
                    "kind": "new",
                    "write_key": call.key,
                    "article_id": None,
                    "category": _text(article.get("category")),
                    "title": _text(article.get("title")),
                    "summary": _text(article.get("summary")),
                    "slug": _text(article.get("slug")),
                    "item_ids": _ints(article.get("item_ids")),
                }
            )

    updates = [c for c in calls if c.stage == EditorCall.Stage.WRITE_UPDATE and c.article_id]
    ids = {c.article_id for c in updates if c.article_id is not None}
    eligible = Article.objects.select_related("category").in_bulk(
        Article.objects.filter(pk__in=ids, published=True, featured=True)
        .exclude(pk__in=Commentary.objects.values("article_id"))  # 숨긴 분석도 여기서 빠진다
        .values_list("pk", flat=True)
    )
    seen: set[int] = set()
    for call in updates:
        article_ = eligible.get(call.article_id or 0)
        if article_ is None or article_.pk in seen:
            continue
        seen.add(article_.pk)
        changes = _object(_object(call.output).get("changes"))
        bundled = list(
            ArticleItem.objects.filter(article=article_)
            .order_by("id")
            .values_list("item_id", flat=True)
        )
        new_ids = _ints(changes.get("item_ids")) or list(call.item_ids)
        found.append(
            {
                "kind": "update",
                "write_key": call.key,
                "article_id": article_.pk,
                "category": article_.category.slug,
                "title": _text(changes.get("title")) or article_.title,
                "summary": _text(changes.get("summary")) or article_.summary,
                "slug": article_.slug,
                "item_ids": new_ids + [i for i in bundled if i not in new_ids],
            }
        )
    return found


# --- 입력 ---


class CommentaryItem(TypedDict):
    id: int
    source: str
    url: str
    published_at: str | None
    title: str
    excerpt: str
    text: str
    reason: str


class RelatedArticle(TypedDict):
    title: str
    summary: str
    published_at: str


class TargetArticle(TypedDict):
    title: str
    summary: str
    category: CategoryInput


class CommentaryInput(TypedDict):
    article: TargetArticle
    items: list[CommentaryItem]
    related: list[RelatedArticle]


def _items(item_ids: list[int], reader: RunReader) -> list[CommentaryItem]:
    by_id = Item.objects.select_related("source").in_bulk(item_ids)
    ordered = [by_id[i] for i in item_ids if i in by_id][:TEXTS_PER_COMMENTARY]
    found: list[CommentaryItem] = []
    for item in ordered:
        fetched = reader.read(item.url, chars=COMMENTARY_TEXT_CHARS)
        excerpt = item.excerpt
        found.append(
            {
                "id": item.pk,
                "source": item.source.name,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "title": item.title,
                "excerpt": excerpt if len(excerpt) <= EXCERPT_CHARS else excerpt[:EXCERPT_CHARS],
                "text": fetched["text"],
                "reason": fetched["reason"],
            }
        )
    return found


def related(category: str, exclude_id: int | None, now: datetime) -> list[RelatedArticle]:
    """같은 분야 최근 14일 게시 기사 제목·요약 최대 10건(최신순)."""
    query = Article.objects.filter(
        category__slug=category,
        published=True,
        published_at__gte=now - timedelta(days=RELATED_DAYS),
    )
    if exclude_id is not None:
        query = query.exclude(pk=exclude_id)
    return [
        {"title": a.title, "summary": a.summary, "published_at": a.published_at.date().isoformat()}
        for a in query.order_by("-published_at", "-id")[:RELATED_MAX]
    ]


def prepare(plan: Plan, reader: RunReader, now: datetime) -> CommentaryInput:
    name = Category.objects.filter(slug=plan["category"]).values_list("name", flat=True).first()
    return {
        "article": {
            "title": plan["title"],
            "summary": plan["summary"],
            "category": {"slug": plan["category"], "name": name or plan["category"]},
        },
        "items": _items(plan["item_ids"], reader),
        "related": related(plan["category"], plan["article_id"], now),
    }


def input_summary(data: CommentaryInput) -> JSON:
    """`EditorCall.input`: id·원문 길이·실패 까닭·관련 기사 수만(본문은 남기지 않는다)."""
    return {
        "items": [
            {"id": i["id"], "text_chars": len(i["text"]), "reason": i["reason"]}
            for i in data["items"]
        ],
        "related": len(data["related"]),
    }


# --- 스키마·요청 ---

_TEXT: JSON = {"type": "string"}

SCHEMA: JSON = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "background",
        "significance",
        "outlook_bull",
        "outlook_bear",
        "watchpoints",
        "opinion",
        "predictions",
        "post_text",
    ],  # fmt: skip
    "properties": {
        "background": _TEXT,
        "significance": _TEXT,
        "outlook_bull": _TEXT,
        "outlook_bear": _TEXT,
        "watchpoints": {"type": "array", "items": _TEXT},
        "opinion": _TEXT,
        "predictions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "horizon", "basis"],
                "properties": {
                    "claim": _TEXT,
                    "horizon": {"type": "string", "enum": list(HORIZONS)},
                    "basis": _TEXT,
                },
            },
        },
        "post_text": _TEXT,
    },
}


def user_message(data: CommentaryInput) -> str:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        "이번 기사의 분석 입력이다. 규칙대로 분석과 포스트 문구를 JSON 하나로 답하라.\n"
        f"<data>\n{body}\n</data>"
    )


def request(data: CommentaryInput, model: str, effort: str, *, retry_note: str = "") -> LlmRequest:
    text = user_message(data)
    if retry_note:
        text += f"\n\n앞선 답이 규칙을 어겼다({retry_note}). 고쳐서 정해진 JSON 하나로 다시 답하라."
    return {
        "model": model,
        "max_tokens": COMMENTARY_MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": editor_prompts.system_prompt("commentary"),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": text}],
        "output_config": {
            "format": {"type": "json_schema", "schema": SCHEMA},
            "effort": editor_prompts.parse_effort(effort),
        },
    }


# --- 검사 ---


def _str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise InvalidOutput(f"{key}가 문자열이 아니다")
    return value


def _strings(raw: dict[str, object], key: str) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidOutput(f"{key}가 문자열 목록이 아니다")
    return list(value)


def _predictions(raw: dict[str, object]) -> list[PredictionIn]:
    value = raw.get("predictions")
    if not isinstance(value, list):
        raise InvalidOutput("predictions가 목록이 아니다")
    found: list[PredictionIn] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise InvalidOutput("predictions 항목이 object가 아니다")
        found.append(
            PredictionIn(
                claim=_str(entry, "claim"),
                horizon=_str(entry, "horizon"),
                basis=_str(entry, "basis"),
            )
        )
    return found


def post_text(text: str, slug: str) -> str:
    """모델 문구를 검사하고 링크 줄을 붙인다. 링크를 스스로 넣었거나 길이·문장 수를 어기면 오류."""
    text = text.strip()
    if "http://" in text or "https://" in text:
        raise InvalidOutput("post_text에 링크를 넣지 않는다(서버가 마지막 줄에 붙인다)")
    count = sentence_count(text)
    if not 1 <= count <= POST_SENTENCES:
        raise InvalidOutput(f"post_text는 1~{POST_SENTENCES}문장이어야 한다(지금 {count}문장)")
    final = with_article_link(text, slug)
    if len(final) > POST_MAX_CHARS:
        room = POST_MAX_CHARS - (len(final) - len(text))
        raise InvalidOutput(f"post_text는 {room}자 이내여야 한다(지금 {len(text)}자)")
    return final


def check(parsed: object, plan: Plan) -> JSON:
    """출력 검사. 규칙 위반은 `InvalidOutput`(호출 쪽이 오류 문구를 붙여 한 번 재요청)."""
    if not isinstance(parsed, dict):
        raise InvalidOutput("JSON object가 아니다")
    raw: dict[str, object] = parsed
    try:
        fields = clean_fields(
            background=_str(raw, "background"),
            significance=_str(raw, "significance"),
            outlook_bull=_str(raw, "outlook_bull"),
            outlook_bear=_str(raw, "outlook_bear"),
            opinion=_str(raw, "opinion"),
            predictions=_predictions(raw),
            watchpoints=_strings(raw, "watchpoints"),
        )
    except InvalidCommentary as error:
        raise InvalidOutput(str(error)) from None
    post = post_text(_str(raw, "post_text"), plan["slug"])
    return {
        "response": raw,
        "commentary": json.loads(json.dumps(fields)),
        "post": {"channel": POST_CHANNEL, "text": post},
        "article": {
            "kind": plan["kind"],
            "article_id": plan["article_id"],
            "title": plan["title"],
            "slug": plan["slug"],
        },
    }


def cap(found: list[Plan]) -> tuple[list[Plan], list[Plan]]:
    """(부를 대상, 상한으로 건너뛸 대상). 상한은 `EDITOR_MAX_COMMENTARY`."""
    limit = max(0, settings.EDITOR_MAX_COMMENTARY)
    return found[:limit], found[limit:]


# --- 호출 만들기(동기·배치 공통) ---


def enqueue(run: EditorRun, now: datetime, *, via_batch: bool) -> list[EditorCall] | None:
    """대상을 골라 `queued` 호출을 만든다(상한 밖 대상은 `skipped`). 앞에서 예산을 확인한다.

    예산을 넘으면 실행을 건너뜀으로 닫고(`gate_stage`) None. 대상이 없으면 빈 목록.
    """
    if settings.EDITOR_MAX_COMMENTARY <= 0:  # 0이면 3단계를 끈다(호출 행도 만들지 않는다)
        return []
    go, over = cap(targets(run))
    if not go and not over:
        return []
    if go and not editor_budget.gate_stage(run, EditorCall.Stage.COMMENTARY, len(go), now):
        return None
    model, effort = settings.EDITOR_MODEL_COMMENTARY, settings.EDITOR_EFFORT_COMMENTARY
    version = editor_prompts.prompt_version(editor_prompts.system_prompt("commentary"))
    created: list[EditorCall] = []
    for n, plan in enumerate(go + over, start=1):
        skipped = n > len(go)
        call, _ = EditorCall.objects.get_or_create(
            key=f"r{run.pk}-commentary-{n}",
            defaults={
                "run": run,
                "stage": EditorCall.Stage.COMMENTARY,
                "model": model,
                "effort": effort,
                "via_batch": via_batch,
                "prompt_version": version,
                "status": EditorCall.Status.SKIPPED if skipped else EditorCall.Status.QUEUED,
                "error": (
                    f"슬롯당 분석 상한 {settings.EDITOR_MAX_COMMENTARY}건 초과" if skipped else ""
                ),
                "finished_at": now if skipped else None,
                "item_ids": plan["item_ids"],
                "article_id": plan["article_id"],
                "input": {"plan": dict(plan)},
            },
        )
        if call.status == EditorCall.Status.QUEUED:
            created.append(call)
    return created


def plan_of(call: EditorCall) -> Plan:
    plan: Plan = call.input["plan"]
    return plan


def note(run: EditorRun) -> str:
    """실행 메모 한 조각: 분석·포스트 건수(실패·상한 건너뜀). 3단계가 없었으면 빈 값."""
    statuses = list(
        run.calls.filter(stage=EditorCall.Stage.COMMENTARY).values_list("status", flat=True)
    )
    if not statuses:
        return ""
    done = sum(s in (EditorCall.Status.SUCCEEDED, EditorCall.Status.SAVED) for s in statuses)
    failed = sum(s in (EditorCall.Status.FAILED, EditorCall.Status.EXPIRED) for s in statuses)
    skipped = statuses.count(EditorCall.Status.SKIPPED)
    extra = f", 상한 건너뜀 {skipped}" if skipped else ""
    return f"분석·포스트 {done}건(실패 {failed}{extra})"
