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
            items.append(
                RawItem(
                    url=link,
                    title=_text(node, r["title"]) or link,
                    excerpt=_text(node, r["excerpt"]) if "excerpt" in r else "",
                    published_at=_parse_time(raw_time),
                )
            )
        if not items:
            raise FetchError(f"항목 {len(nodes)}개에서 링크를 찾지 못했습니다: {r['link']}")
        return items


# 이름→구현 사전.
ADAPTERS: dict[str, SourceAdapter] = {"rss": RssAdapter(), "html": HtmlAdapter()}
