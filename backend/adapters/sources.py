"""소스 어댑터: 소스 하나를 수집해 원재료 목록으로 만든다. 소스 레코드의 adapter_name이 키다."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import Protocol
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup, Tag

from adapters.fetch import Fetcher, FetchError
from services.html_rules import validate_html_rules


@dataclass(frozen=True)
class RawItem:
    url: str
    title: str
    excerpt: str
    published_at: datetime | None
    image_url: str = ""


class SourceAdapter(Protocol):
    def collect(self, url: str, fetcher: Fetcher, rules: Mapping[str, str]) -> list[RawItem]: ...


def _to_datetime(value: struct_time | None) -> datetime | None:
    if value is None:
        return None
    return datetime(*value[:6], tzinfo=UTC)


def plain_text(markup: str) -> str:
    """HTML 태그를 벗기고 엔티티를 풀고 연속 공백·줄바꿈을 한 칸으로 줄인다."""
    text = BeautifulSoup(markup, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text).strip()


def clean_image_url(base: str, value: object) -> str:
    """이미지 주소를 base 기준 절대 http(s) 주소로. 쓸 수 없으면 빈 문자열."""
    raw = str(value or "").strip()
    if not raw or raw.startswith("data:"):
        return ""
    absolute = urljoin(base, raw)
    if not absolute.startswith(("http://", "https://")) or len(absolute) > 2000:
        return ""
    return absolute


def _is_image(entry: Mapping[str, object]) -> bool:
    mime = str(entry.get("type") or "")
    return mime.startswith("image/") or (not mime and entry.get("medium") in (None, "image"))


def _first_img_src(markup: str) -> str:
    img = BeautifulSoup(markup, "html.parser").find("img")
    return str(img.get("src", "")) if isinstance(img, Tag) else ""


def _rss_image(entry: Mapping[str, object], link: str) -> str:
    """enclosure(image/*) → media:content → media:thumbnail → 본문 첫 img 순."""
    candidates: list[object] = []
    for enclosure in entry.get("enclosures") or []:  # type: ignore[attr-defined]  # feedparser 사전
        if str(enclosure.get("type") or "").startswith("image/"):
            candidates.append(enclosure.get("href"))
    for media in entry.get("media_content") or []:  # type: ignore[attr-defined]
        if _is_image(media):
            candidates.append(media.get("url"))
    for thumb in entry.get("media_thumbnail") or []:  # type: ignore[attr-defined]
        candidates.append(thumb.get("url"))
    html_parts = [str(entry.get("summary") or "")]
    for content in entry.get("content") or []:  # type: ignore[attr-defined]
        html_parts.append(str(content.get("value") or ""))
    candidates.extend(_first_img_src(part) for part in html_parts if part)
    for candidate in candidates:
        found = clean_image_url(link, candidate)
        if found:
            return found
    return ""


class RssAdapter:
    def collect(self, url: str, fetcher: Fetcher, rules: Mapping[str, str]) -> list[RawItem]:
        parsed = feedparser.parse(fetcher.fetch(url))
        if parsed.bozo and not parsed.entries:
            raise FetchError(f"RSS 파싱 실패: {parsed.bozo_exception}")
        items: list[RawItem] = []
        for entry in parsed.entries:
            link = str(entry.get("link", "")).strip()
            if not link:
                continue
            items.append(
                RawItem(
                    url=link,
                    title=str(entry.get("title", "")).strip() or link,
                    excerpt=plain_text(str(entry.get("summary", ""))),
                    published_at=_to_datetime(entry.get("published_parsed")),
                    image_url=_rss_image(entry, link),
                )
            )
        return items


def _parse_time(text: str) -> datetime | None:
    text = text.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _text(node: Tag, selector: str) -> str:
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else ""


class HtmlAdapter:
    """목록 페이지를 선택자 규칙(item/link/title/published/excerpt)으로 파싱한다."""

    def collect(self, url: str, fetcher: Fetcher, rules: Mapping[str, str]) -> list[RawItem]:
        r = validate_html_rules(rules)
        soup = BeautifulSoup(fetcher.fetch(url), "html.parser")
        nodes = soup.select(r["item"])
        if not nodes:
            raise FetchError(f"선택자가 항목을 찾지 못했습니다: {r['item']}")
        items: list[RawItem] = []
        for node in nodes:
            link_node = (
                node if node.name == "a" and r["link"] == "." else node.select_one(r["link"])
            )
            href = str(link_node.get("href", "")).strip() if link_node else ""
            if not href:
                continue
            link = urljoin(url, href)
            time_node = node.select_one(r["published"])
            raw_time = ""
            if time_node:
                raw_time = str(time_node.get("datetime") or time_node.get_text(strip=True))
            image = ""
            if "image" in r:
                image_node = node.select_one(r["image"])
                if image_node:
                    image = clean_image_url(url, image_node.get("content") or image_node.get("src"))
            items.append(
                RawItem(
                    url=link,
                    title=_text(node, r["title"]) or link,
                    excerpt=_text(node, r["excerpt"]) if "excerpt" in r else "",
                    published_at=_parse_time(raw_time),
                    image_url=image,
                )
            )
        if not items:
            raise FetchError(f"항목 {len(nodes)}개에서 링크를 찾지 못했습니다: {r['link']}")
        return items


# 이름→구현 사전.
ADAPTERS: dict[str, SourceAdapter] = {"rss": RssAdapter(), "html": HtmlAdapter()}


def find_og_image(page: bytes, page_url: str) -> str:
    """원문 HTML의 og:image(없으면 twitter:image). 없으면 빈 문자열."""
    soup = BeautifulSoup(page, "html.parser")
    for attr, name in (
        ("property", "og:image"),
        ("name", "twitter:image"),
        ("property", "twitter:image"),
    ):
        meta = soup.find("meta", attrs={attr: name})
        if isinstance(meta, Tag):
            found = clean_image_url(page_url, meta.get("content"))
            if found:
                return found
    return ""
