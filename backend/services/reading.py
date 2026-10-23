"""원문 읽기(#60): 편집자가 분석을 쓰기 전에 수집물 원문 본문을 평문으로 읽는다.

원문은 페치 어댑터로 한 번 받아(앞 1MB) 본문만 뽑아 돌려주고 저장하지 않는다(핫링크·저작권 정책).
본문 후보는 `<article>` → `<main>`/`role=main` → `<p>` 텍스트가 가장 많이 모인 블록 순이다.
어떤 실패든 오류가 아니라 빈 `text`와 `reason`으로 돌려준다(편집자는 발췌로 대신 쓴다).
"""

import re
from typing import TypedDict

from bs4 import BeautifulSoup, Tag

from adapters.fetch import Fetcher, FetchError, get_fetcher
from collection.models import Item
from services.articles import ItemNotFound

MAX_BYTES = 1_000_000  # 원문은 앞 1MB만 본다
MAX_TEXT_LENGTH = 8000  # 편집자에게 넘기는 본문 상한(넘으면 자른다)
MIN_TEXT_LENGTH = 80  # 이보다 짧으면 본문을 못 찾은 것으로 본다

NOISE_TAGS = (
    "script",
    "style",
    "noscript",
    "template",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "button",
    "iframe",
    "svg",
    "figure",
    "select",
)
BLOCK_TAGS = ("p", "h1", "h2", "h3", "h4", "li", "blockquote", "pre")
_SPACES = re.compile(r"\s+")


class ReadResult(TypedDict):
    item_id: int
    url: str
    title: str
    text: str
    truncated: bool
    reason: str


def _squash(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def _text_length(node: Tag) -> int:
    return sum(len(_squash(p.get_text(" "))) for p in node.find_all("p"))


def _best_container(soup: BeautifulSoup) -> Tag | None:
    """본문을 담은 요소. 의미 태그가 있으면 그중 문단이 가장 많은 것, 없으면 문단 밀도."""
    for selector in ("article", "main, [role=main]"):
        found = [node for node in soup.select(selector) if _text_length(node) > 0]
        if found:
            return max(found, key=_text_length)
    scores: dict[int, tuple[Tag, int]] = {}
    for p in soup.find_all("p"):
        parent = p.parent
        if not isinstance(parent, Tag):
            continue
        node, score = scores.get(id(parent), (parent, 0))
        scores[id(parent)] = (node, score + len(_squash(p.get_text(" "))))
    if scores:
        return max(scores.values(), key=lambda pair: pair[1])[0]
    return soup.body


def extract_text(html: bytes) -> str:
    """HTML 바이트에서 본문 평문을 뽑는다. 문단은 빈 줄(`\\n\\n`)로 나눈다."""
    soup = BeautifulSoup(html, "html.parser")
    for noise in soup.find_all(NOISE_TAGS):
        noise.decompose()
    container = _best_container(soup)
    if container is None:
        return ""
    blocks = [
        _squash(block.get_text(" "))
        for block in container.find_all(BLOCK_TAGS)
        # 목록 안의 문단처럼 겹친 블록은 바깥 것 하나만 센다
        if not block.find_parent(BLOCK_TAGS)
    ]
    paragraphs = [b for b in blocks if b]
    if not paragraphs:
        paragraphs = [_squash(container.get_text(" "))]
    return "\n\n".join(p for p in paragraphs if p)


def read_item(item_id: int, fetcher: Fetcher | None = None) -> ReadResult:
    """수집물 원문을 받아 본문을 최대 8000자 평문으로 돌려준다. 저장하지 않는다."""
    try:
        item = Item.objects.get(pk=item_id)
    except Item.DoesNotExist:
        raise ItemNotFound(f"없는 수집물입니다: {item_id}") from None
    result: ReadResult = {
        "item_id": item.pk,
        "url": item.url,
        "title": item.title,
        "text": "",
        "truncated": False,
        "reason": "",
    }
    try:
        html = (fetcher or get_fetcher()).fetch(item.url)[:MAX_BYTES]
    except FetchError as exc:
        return result | {"reason": f"원문을 받지 못했습니다: {exc}"}
    try:
        text = extract_text(html)
    except Exception as exc:  # noqa: BLE001  # 깨진 HTML도 오류가 아니라 빈 본문으로
        return result | {"reason": f"본문을 해석하지 못했습니다: {type(exc).__name__}"}
    if len(text) < MIN_TEXT_LENGTH:
        return result | {"reason": "본문을 찾지 못했습니다."}
    if len(text) > MAX_TEXT_LENGTH:
        return result | {"text": text[:MAX_TEXT_LENGTH], "truncated": True}
    return result | {"text": text}
