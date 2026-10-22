"""소스 페치 어댑터: 주소를 받아 응답 본문 바이트를 돌려준다."""

import urllib.request
from typing import Protocol

USER_AGENT = "AI-Trend-News-Crawler/1.0 (+https://news.sandcorp.co.kr)"
TIMEOUT_SECONDS = 15


class FetchError(Exception):
    """페치 실패(네트워크·HTTP 오류). 메시지가 소스의 마지막 수집 결과에 기록된다."""


class Fetcher(Protocol):
    def fetch(self, url: str) -> bytes: ...


class HttpFetcher:
    """실제 HTTP GET(시간 제한, User-Agent)."""

    def fetch(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
                body: bytes = response.read()
                return body
        except OSError as exc:  # URLError·HTTPError·시간 초과는 모두 OSError
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc


_fetcher: Fetcher = HttpFetcher()


def get_fetcher() -> Fetcher:
    return _fetcher


def set_fetcher(fetcher: Fetcher) -> Fetcher:
    """등록 지점. 이전 페처를 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _fetcher
    previous, _fetcher = _fetcher, fetcher
    return previous
