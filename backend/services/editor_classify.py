"""1단계 분류(설계 §2): 입력 조립, 출력 스키마, 서버 검증.

구조화 출력(`output_config.format`)이 스키마 모양과 분야 `enum`을 보장한다. 이 파일의 검증은
스키마가 보장하지 못하는 것(입력에 없는 id, 중복 id, 빠진 id, 추천·쓰기 상한)만 본다.
검증은 고친 결과와 고친 까닭(`problems`)을 돌려줄 뿐 예외를 던지지 않는다.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict

from django.conf import settings
from django.utils import timezone

from services import articles, collection, editor_reserve

EXCERPT_CHARS = 300
REASON_CHARS = 80
NOTE_CHARS = 200
RECENT_DAYS = 3

JSON = dict[str, object]


class CategoryInput(TypedDict):
    slug: str
    name: str


class RecentArticleInput(TypedDict):
    id: int
    title: str
    category: str
    published: bool
    featured: bool
    has_commentary: bool


class ItemInput(TypedDict):
    id: int
    source: str
    published_at: str | None
    title: str
    excerpt: str
    has_image: bool


class ClassifyInput(TypedDict):
    today: str
    categories: list[CategoryInput]
    recent_articles: list[RecentArticleInput]
    items: list[ItemInput]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_input(now: datetime, *, max_items: int | None = None) -> ClassifyInput:
    limit = settings.EDITOR_MAX_ITEMS if max_items is None else max_items
    reserved = editor_reserve.reserved_item_ids(now)  # 게시 모드 진행 중 실행이 잡은 수집물(§5)
    pending = collection.list_pending_items(page=1, page_size=limit, exclude_ids=reserved).items
    return {
        "today": timezone.localtime(now).date().isoformat(),
        "categories": [{"slug": c.slug, "name": c.name} for c in articles.list_categories()],
        "recent_articles": [
            {
                "id": a.pk,
                "title": a.title,
                "category": a.category.slug,
                "published": a.published,
                "featured": a.featured,
                "has_commentary": hasattr(a, "commentary"),
            }
            for a in articles.recent_articles(days=RECENT_DAYS, now=now)
        ],
        "items": [
            {
                "id": item.pk,
                "source": item.source.name,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "title": item.title,
                "excerpt": _clip(item.excerpt, EXCERPT_CHARS),
                "has_image": bool(item.image_url),
            }
            for item in pending
        ],
    }


def _object(properties: dict[str, JSON], required: list[str] | None = None) -> JSON:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required if required is not None else list(properties),
        "properties": properties,
    }


def _ids() -> JSON:
    return {"type": "array", "items": {"type": "integer"}}


def output_schema(category_slugs: list[str]) -> JSON:
    """분야 slug는 실행마다 `enum`으로 넣는다. 구조화 출력 제약상 길이·수치 제약은 두지 않는다."""
    text: JSON = {"type": "string"}
    item_reason = _object({"item_id": {"type": "integer"}, "reason": text})
    return _object(
        {
            "new_articles": {
                "type": "array",
                "items": _object(
                    {
                        "item_ids": _ids(),
                        "category": {"type": "string", "enum": list(category_slugs)},
                        "published": {"type": "boolean"},
                        "featured": {"type": "boolean"},
                        "reason": text,
                    }
                ),
            },
            "updates": {
                "type": "array",
                "items": _object(
                    {"article_id": {"type": "integer"}, "item_ids": _ids(), "reason": text}
                ),
            },
            "discards": {"type": "array", "items": item_reason},
            "holds": {"type": "array", "items": item_reason},
            "note": text,
        }
    )


def user_message(data: ClassifyInput) -> str:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return (
        f"이번 실행의 분류 입력이다. 규칙대로 분류해 JSON 하나로 답하라.\n<data>\n{body}\n</data>"
    )


# --- 검증 ---


class NewArticle(TypedDict):
    item_ids: list[int]
    category: str
    published: bool
    featured: bool
    reason: str


class Update(TypedDict):
    article_id: int
    item_ids: list[int]
    reason: str


class ItemReason(TypedDict):
    item_id: int
    reason: str


class Decision(TypedDict):
    new_articles: list[NewArticle]
    updates: list[Update]
    discards: list[ItemReason]
    holds: list[ItemReason]
    note: str


class InvalidOutput(ValueError):
    """스키마 모양이 아니다(구조화 출력이면 생기지 않아야 한다 — 재요청 대상)."""


@dataclass
class _Checker:
    known: set[int]
    seen: set[int] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)

    def take(self, ids: object, where: str) -> list[int]:
        if not isinstance(ids, list):
            raise InvalidOutput(f"{where}: item_ids가 배열이 아니다")
        kept: list[int] = []
        for raw in ids:
            if not isinstance(raw, int) or isinstance(raw, bool):
                raise InvalidOutput(f"{where}: 수집물 id가 정수가 아니다")
            if raw not in self.known:
                self.problems.append(f"{where}: 입력에 없는 수집물 {raw} 버림")
            elif raw in self.seen:
                self.problems.append(f"{where}: 이미 판단한 수집물 {raw} 중복 무시")
            else:
                self.seen.add(raw)
                kept.append(raw)
        return kept


def _str(value: object, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidOutput("문자열 칸이 문자열이 아니다")
    return value.strip()[:limit]


def _list(output: JSON, key: str) -> list[JSON]:
    value = output.get(key)
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise InvalidOutput(f"{key}가 객체 배열이 아니다")
    return value


@dataclass(frozen=True)
class Validated:
    decision: Decision
    problems: list[str]


def _note(model_note: str, kept_new: list[NewArticle]) -> str:
    """모델이 쓴 note에서 추천 언급을 걷어내고, 검증을 거친 실제 추천 수를 서버가 덧붙인다.

    note의 「추천 N건」은 모델의 자유 서술이라 필드(`featured`)와 어긋날 수 있다(#137).
    """
    parts = re.split(r"(?<=[.。!?])\s+|\n+", model_note)
    kept = " ".join(p.strip() for p in parts if p.strip() and "추천" not in p)
    featured = sum(1 for a in kept_new if a["featured"])
    suffix = f"추천 {featured}건"
    return f"{kept[: NOTE_CHARS - len(suffix) - 1]} {suffix}".strip()


def validate(output: object, data: ClassifyInput) -> Validated:
    if not isinstance(output, dict):
        raise InvalidOutput("최상위가 객체가 아니다")
    check = _Checker(known={i["id"] for i in data["items"]})
    recent = {a["id"] for a in data["recent_articles"]}
    holds: list[ItemReason] = []

    new_articles: list[NewArticle] = []
    for n, entry in enumerate(_list(output, "new_articles")):
        ids = check.take(entry.get("item_ids"), f"새 기사 {n + 1}")
        if not ids:
            check.problems.append(f"새 기사 {n + 1}: 남은 수집물이 없어 뺌")
            continue
        published, featured = bool(entry.get("published")), bool(entry.get("featured"))
        if featured and not published:
            check.problems.append(f"새 기사 {n + 1}: 비게시라 추천을 끔")
            featured = False
        new_articles.append(
            {
                "item_ids": ids,
                "category": _str(entry.get("category"), 60),
                "published": published,
                "featured": featured,
                "reason": _str(entry.get("reason", ""), REASON_CHARS),
            }
        )

    updates: list[Update] = []
    for n, entry in enumerate(_list(output, "updates")):
        ids = check.take(entry.get("item_ids"), f"갱신 {n + 1}")
        article_id = entry.get("article_id")
        if article_id not in recent:
            check.problems.append(f"갱신 {n + 1}: 최근 기사가 아닌 {article_id} — 보류로 돌림")
            holds.extend({"item_id": i, "reason": "갱신 대상 기사를 모름"} for i in ids)
            continue
        if ids:
            assert isinstance(article_id, int)
            reason = _str(entry.get("reason", ""), REASON_CHARS)
            updates.append({"article_id": article_id, "item_ids": ids, "reason": reason})

    def item_reasons(key: str, label: str) -> list[ItemReason]:
        found: list[ItemReason] = []
        for entry in _list(output, key):
            for item_id in check.take([entry.get("item_id")], label):
                found.append(
                    {"item_id": item_id, "reason": _str(entry.get("reason", ""), REASON_CHARS)}
                )
        return found

    discards = item_reasons("discards", "버림")
    holds = item_reasons("holds", "보류") + holds

    limit = settings.EDITOR_MAX_WRITES
    kept_new = new_articles[:limit]
    kept_updates = updates[: max(0, limit - len(kept_new))]
    overflow_ids = [i for a in new_articles[len(kept_new) :] for i in a["item_ids"]]
    overflow_ids += [i for u in updates[len(kept_updates) :] for i in u["item_ids"]]
    holds.extend({"item_id": i, "reason": "쓰기 상한 초과"} for i in overflow_ids)
    if overflow_ids:
        check.problems.append(f"쓰기 상한 {limit}건 초과 — 넘친 것은 보류")

    featured_left = settings.EDITOR_MAX_FEATURED
    for article in kept_new:
        if not article["featured"]:
            continue
        if featured_left > 0:
            featured_left -= 1
        else:
            article["featured"] = False
            check.problems.append(f"추천 상한 {settings.EDITOR_MAX_FEATURED}개 초과 — 추천을 끔")

    for item in data["items"]:
        if item["id"] not in check.seen:
            holds.append({"item_id": item["id"], "reason": "판단 없음"})
    missing = [i["id"] for i in data["items"] if i["id"] not in check.seen]
    if missing:
        check.problems.append(f"판단이 빠진 수집물 {len(missing)}건 — 보류로 봄")

    return Validated(
        decision={
            "new_articles": kept_new,
            "updates": kept_updates,
            "discards": discards,
            "holds": holds,
            "note": _note(_str(output.get("note", ""), 1000), kept_new),
        },
        problems=check.problems,
    )
