"""2단계 기사 작성(설계 §2): 입력 조립(원문 페치 포함), 출력 스키마, 서버 검사.

분류가 정한 새 기사·갱신마다 한 번 부른다. 원문은 `services/reading.py`의 추출기로 받아
앞 `WRITE_TEXT_CHARS`자만, 기사당 `TEXTS_PER_ARTICLE`건까지(그 이상은 발췌만) 넣는다. 실행
하나 안에서는 `RunReader`가 같은 URL을 다시 받지 않고 페치 횟수를 `FETCHES_PER_RUN`으로
막는다. 원문은 저장하지 않는다 — `EditorCall.input`에는 길이와 실패 까닭만 남긴다.

서버가 정하는 것: 게시 = 분류 AND 작성(작성은 내릴 수만 있다), 추천은 게시일 때만, 이미지는
묶인 순서대로 처음 있는 `image_url`(모델 출력에 URL 칸이 없다), slug 형식 오류는
`make_slug(title)`로 대신.
제목·태그·요약 규칙을 어기면 `InvalidOutput`을 던져 호출 쪽이 오류 문구를 붙여 한 번 재요청한다.
"""

import json
import re
from typing import Literal, TypedDict

from adapters.anthropic import LlmRequest
from adapters.fetch import Fetcher, FetchError
from articles.models import ARTICLE_SLUG_RE, Article
from collection.models import Item
from services import editor_prompts
from services.articles import make_slug, unique_article_slug
from services.editor_classify import (
    JSON,
    CategoryInput,
    InvalidOutput,
    NewArticle,
    Update,
)
from services.reading import MAX_BYTES, MIN_TEXT_LENGTH, extract_text

WRITE_TEXT_CHARS = 4000
TEXTS_PER_ARTICLE = 3
FETCHES_PER_RUN = 60
EXCERPT_CHARS = 600
WRITE_MAX_TOKENS = 8_000  # thinking 포함. 출력은 짧은 정형 글이다

TITLE_CHARS = 40
TAGS_MIN, TAGS_MAX = 2, 5
SUMMARY_SENTENCES = 3
REASON_CHARS = 80

_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+")


# --- 원문 ---


class Fetched(TypedDict):
    text: str
    reason: str


class RunReader:
    """실행 하나 동안의 원문 읽기. 같은 URL은 다시 받지 않고(메모리만), 페치는 `limit`회까지."""

    def __init__(self, fetcher: Fetcher, limit: int = FETCHES_PER_RUN) -> None:
        self.fetcher = fetcher
        self.limit = limit
        self.fetches = 0
        self._cache: dict[str, Fetched] = {}

    def read(self, url: str) -> Fetched:
        if url in self._cache:
            return self._cache[url]
        if self.fetches >= self.limit:
            return {"text": "", "reason": f"실행당 페치 상한 {self.limit}회 — 발췌로 대신"}
        self.fetches += 1
        self._cache[url] = result = self._fetch(url)
        return result

    def _fetch(self, url: str) -> Fetched:
        try:
            html = self.fetcher.fetch(url)[:MAX_BYTES]
        except FetchError as error:
            return {"text": "", "reason": f"원문을 받지 못함: {error}"[:120]}
        try:
            text = extract_text(html)
        except Exception as error:  # noqa: BLE001  # 깨진 HTML도 오류가 아니라 빈 본문으로
            return {"text": "", "reason": f"본문을 해석하지 못함: {type(error).__name__}"}
        if len(text) < MIN_TEXT_LENGTH:
            return {"text": "", "reason": "본문을 찾지 못함"}
        return {"text": text[:WRITE_TEXT_CHARS], "reason": ""}


# --- 입력 ---


class WriteItem(TypedDict):
    id: int
    source: str
    url: str
    published_at: str | None
    title: str
    excerpt: str
    text: str
    reason: str


class Decided(TypedDict):
    published: bool
    featured: bool


class ExistingArticle(TypedDict):
    id: int
    title: str
    summary: str
    tags: list[str]


class WriteInput(TypedDict):
    kind: Literal["new", "update"]
    category: CategoryInput
    decided: Decided | None
    items: list[WriteItem]
    article: ExistingArticle | None


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _items(item_ids: list[int], reader: RunReader) -> tuple[list[WriteItem], list[Item]]:
    by_id = Item.objects.select_related("source").in_bulk(item_ids)
    ordered = [by_id[i] for i in item_ids if i in by_id]
    found: list[WriteItem] = []
    for n, item in enumerate(ordered):
        fetched: Fetched = (
            reader.read(item.url)
            if n < TEXTS_PER_ARTICLE
            else {"text": "", "reason": f"기사당 원문 {TEXTS_PER_ARTICLE}건 초과 — 발췌만"}
        )
        found.append(
            {
                "id": item.pk,
                "source": item.source.name,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "title": item.title,
                "excerpt": _clip(item.excerpt, EXCERPT_CHARS),
                "text": fetched["text"],
                "reason": fetched["reason"],
            }
        )
    return found, ordered


def first_image(items: list[Item]) -> str:
    """묶인 순서대로 처음 `image_url`이 있는 수집물의 이미지(editor.md의 지금 규칙과 같다)."""
    return next((i.image_url for i in items if i.image_url), "")


class Prepared(TypedDict):
    data: WriteInput
    image_url: str  # 새 기사: 쓸 이미지. 갱신: 기사 이미지가 비었을 때 채울 이미지(아니면 빈 값)


def prepare_new(entry: NewArticle, category_names: dict[str, str], reader: RunReader) -> Prepared:
    items, ordered = _items(entry["item_ids"], reader)
    slug = entry["category"]
    return {
        "data": {
            "kind": "new",
            "category": {"slug": slug, "name": category_names.get(slug, slug)},
            "decided": {"published": entry["published"], "featured": entry["featured"]},
            "items": items,
            "article": None,
        },
        "image_url": first_image(ordered),
    }


def prepare_update(entry: Update, reader: RunReader) -> Prepared:
    article = (
        Article.objects.select_related("category")
        .prefetch_related("tags")
        .get(pk=entry["article_id"])
    )
    items, ordered = _items(entry["item_ids"], reader)
    return {
        "data": {
            "kind": "update",
            "category": {"slug": article.category.slug, "name": article.category.name},
            "decided": None,
            "items": items,
            "article": {
                "id": article.pk,
                "title": article.title,
                "summary": article.summary,
                "tags": [t.name for t in article.tags.all()],
            },
        },
        "image_url": "" if article.image_url else first_image(ordered),
    }


def input_summary(data: WriteInput) -> JSON:
    """`EditorCall.input`: id·원문 길이·실패 까닭만(원문·발췌 본문은 남기지 않는다)."""
    summary: JSON = {
        "kind": data["kind"],
        "category": data["category"]["slug"],
        "items": [
            {"id": i["id"], "text_chars": len(i["text"]), "reason": i["reason"]}
            for i in data["items"]
        ],
    }
    if data["article"] is not None:
        summary["article_id"] = data["article"]["id"]
    if data["decided"] is not None:
        summary["decided"] = dict(data["decided"])
    return summary


# --- 스키마·요청 ---

_TEXT: JSON = {"type": "string"}
_TAGS: JSON = {"type": "array", "items": {"type": "string"}}


def _nullable(schema: JSON) -> JSON:
    return {"anyOf": [schema, {"type": "null"}]}


def _object(properties: dict[str, JSON]) -> JSON:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


NEW_SCHEMA: JSON = _object(
    {
        "title": _TEXT,
        "slug": _TEXT,
        "summary": _TEXT,
        "tags": _TAGS,
        "published": {"type": "boolean"},
        "unpublish_reason": _TEXT,
    }
)
UPDATE_SCHEMA: JSON = _object(
    {
        "title": _nullable(_TEXT),
        "summary": _nullable(_TEXT),
        "tags": _nullable(_TAGS),
        "reason": _TEXT,
    }
)


def user_message(data: WriteInput) -> str:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    what = "새 기사를 써" if data["kind"] == "new" else "기존 기사를 고칠지 정해"
    return (
        f"이번 기사의 작성 입력이다. 규칙대로 {what} JSON 하나로 답하라.\n<data>\n{body}\n</data>"
    )


def request(data: WriteInput, model: str, effort: str, *, retry_note: str = "") -> LlmRequest:
    text = user_message(data)
    if retry_note:
        text += f"\n\n앞선 답이 규칙을 어겼다({retry_note}). 고쳐서 정해진 JSON 하나로 다시 답하라."
    return {
        "model": model,
        "max_tokens": WRITE_MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": editor_prompts.system_prompt("write"),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": text}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": NEW_SCHEMA if data["kind"] == "new" else UPDATE_SCHEMA,
            },
            "effort": editor_prompts.parse_effort(effort),
        },
    }


# --- 검사 ---


def sentence_count(text: str) -> int:
    return len([s for s in _SENTENCE_END.split(text.strip()) if s.strip()])


def _rule_errors(title: str | None, summary: str | None, tags: list[str] | None) -> list[str]:
    errors: list[str] = []
    if title is not None and not 1 <= len(title.strip()) <= TITLE_CHARS:
        errors.append(f"제목은 1~{TITLE_CHARS}자여야 한다(지금 {len(title.strip())}자)")
    if summary is not None:
        count = sentence_count(summary)
        if not 1 <= count <= SUMMARY_SENTENCES:
            errors.append(f"요약은 1~{SUMMARY_SENTENCES}문장이어야 한다(지금 {count}문장)")
    if tags is not None:
        names = [t.strip() for t in tags]
        if not TAGS_MIN <= len(names) <= TAGS_MAX:
            errors.append(f"태그는 {TAGS_MIN}~{TAGS_MAX}개여야 한다(지금 {len(names)}개)")
        if any(not n for n in names) or len(set(names)) != len(names):
            errors.append("태그에 빈 값이나 중복이 있다")
    return errors


def _as_dict(parsed: object) -> dict[str, object]:
    if not isinstance(parsed, dict):
        raise InvalidOutput("JSON object가 아니다")
    return parsed


def _opt_str(raw: dict[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidOutput(f"{key}가 문자열이 아니다")
    return value.strip()


def _str(raw: dict[str, object], key: str) -> str:
    value = _opt_str(raw, key)
    if value is None:
        raise InvalidOutput(f"{key}가 없다")
    return value


def _opt_tags(raw: dict[str, object], key: str) -> list[str] | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise InvalidOutput(f"{key}가 문자열 목록이 아니다")
    return [v.strip() for v in value]


def _bool(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise InvalidOutput(f"{key}가 boolean이 아니다")
    return value


class Checked(TypedDict):
    output: JSON
    problems: list[str]


def check_new(parsed: object, prepared: Prepared) -> Checked:
    """새 기사 출력 검사. 규칙 위반은 `InvalidOutput`(재요청), slug 형식 오류는 대신하고 기록."""
    data, raw = prepared["data"], _as_dict(parsed)
    title, slug, summary = _str(raw, "title"), _str(raw, "slug"), _str(raw, "summary")
    tags = _opt_tags(raw, "tags")
    if tags is None:
        raise InvalidOutput("tags가 없다")
    wants_published = _bool(raw, "published")
    unpublish_reason = _str(raw, "unpublish_reason")[:REASON_CHARS]
    errors = _rule_errors(title, summary, tags)
    if errors:
        raise InvalidOutput("; ".join(errors))

    problems: list[str] = []
    if 3 <= len(slug) <= 60 and ARTICLE_SLUG_RE.fullmatch(slug):
        final_slug = unique_article_slug(slug)
    else:
        final_slug = make_slug(title, Article, "news")
        problems.append(f"slug 형식 오류 {slug!r} — {final_slug}로 대신")

    decided = data["decided"]
    assert decided is not None
    published = decided["published"] and wants_published
    if decided["published"] and not wants_published:
        problems.append(f"작성 단계가 비게시로 내림: {unpublish_reason or '까닭 없음'}")
    featured = decided["featured"] and published
    article: JSON = {
        "category": data["category"]["slug"],
        "item_ids": [i["id"] for i in data["items"]],
        "title": title,
        "slug": final_slug,
        "summary": summary,
        "tags": tags,
        "published": published,
        "featured": featured,
        "image_url": prepared["image_url"],
        "unpublish_reason": "" if published else unpublish_reason,
    }
    output: JSON = {"response": raw, "article": article}
    if problems:
        output["problems"] = problems
    return {"output": output, "problems": problems}


def check_update(parsed: object, prepared: Prepared) -> Checked:
    """갱신 출력 검사. 바꾸지 않는 칸은 `null`. 이미지는 기사 이미지가 비었을 때만 채운다."""
    data, raw = prepared["data"], _as_dict(parsed)
    title_s, summary_s = _opt_str(raw, "title"), _opt_str(raw, "summary")
    tags_l = _opt_tags(raw, "tags")
    reason = _str(raw, "reason")[:REASON_CHARS]
    errors = _rule_errors(title_s, summary_s, tags_l)
    if errors:
        raise InvalidOutput("; ".join(errors))
    assert data["article"] is not None
    changes: JSON = {
        "article_id": data["article"]["id"],
        "item_ids": [i["id"] for i in data["items"]],
        "title": title_s,
        "summary": summary_s,
        "tags": tags_l,
        "image_url": prepared["image_url"] or None,
        "reason": reason,
    }
    return {"output": {"response": raw, "changes": changes}, "problems": []}


def check(parsed: object, prepared: Prepared) -> Checked:
    if prepared["data"]["kind"] == "new":
        return check_new(parsed, prepared)
    return check_update(parsed, prepared)
