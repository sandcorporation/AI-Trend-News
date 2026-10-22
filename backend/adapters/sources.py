"""소스 어댑터: 소스 하나를 수집해 원재료 목록으로 만든다. 소스 레코드의 adapter_name이 키다."""

from dataclasses import dataclass
from datetime import UTC, datetime
from time import struct_time
from typing import Protocol

import feedparser

from adapters.fetch import Fetcher, FetchError


@dataclass(frozen=True)
class RawItem:
    url: str
    title: str
    excerpt: str
    published_at: datetime | None


class SourceAdapter(Protocol):
    def collect(self, url: str, fetcher: Fetcher) -> list[RawItem]: ...


def _to_datetime(value: struct_time | None) -> datetime | None:
    if value is None:
        return None
    return datetime(*value[:6], tzinfo=UTC)


class RssAdapter:
    def collect(self, url: str, fetcher: Fetcher) -> list[RawItem]:
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
                    excerpt=str(entry.get("summary", "")).strip(),
                    published_at=_to_datetime(entry.get("published_parsed")),
                )
            )
        return items


# 이름→구현 사전. HTML 어댑터는 #4가 여기에 더한다.
ADAPTERS: dict[str, SourceAdapter] = {"rss": RssAdapter()}
