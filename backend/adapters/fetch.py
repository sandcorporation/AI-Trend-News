"""소스 페치 어댑터: 주소를 받아 응답 본문 바이트를 돌려준다."""

import logging
import urllib.error
import urllib.request
from typing import Protocol

from django.conf import settings

logger = logging.getLogger(__name__)

USER_AGENT = "Foresight-Crawler/1.0 (+https://news.sandcorp.co.kr)"
TIMEOUT_SECONDS = 15
BLOCK_STATUSES = frozenset({403, 429, 503})
CHALLENGE_SNIFF_BYTES = 4096


class FetchError(Exception):
    """페치 실패(네트워크·HTTP 오류). 메시지가 소스의 마지막 수집 결과에 기록된다."""


class Fetcher(Protocol):
    def fetch(self, url: str) -> bytes: ...


class _Blocked(Exception):
    """직접 GET이 봇 차단으로 보이는 응답(챌린지 페이지)을 받았다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_challenge(headers: dict[str, str], body: bytes) -> bool:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    return b"<title>just a moment" in body[:CHALLENGE_SNIFF_BYTES].lower()


def _get(url: str, extra_headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(extra_headers or {})}
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
        body: bytes = response.read()
        headers = {k.lower(): v for k, v in response.headers.items()}
    if _is_challenge(headers, body):
        raise _Blocked("챌린지 페이지")
    return body


class HttpFetcher:
    """실제 HTTP GET. 봇 차단이면 설정된 대체 경로로 한 번 더 받는다(ADR 0005)."""

    def fetch(self, url: str) -> bytes:
        try:
            return _get(url)
        except _Blocked as blocked:
            direct = blocked.reason
        except urllib.error.HTTPError as exc:
            if exc.code not in BLOCK_STATUSES:
                raise FetchError(f"{type(exc).__name__}: {exc}") from exc
            direct = f"HTTP {exc.code}"
        except OSError as exc:  # URLError·시간 초과는 모두 OSError
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

        template: str = getattr(settings, "FETCH_FALLBACK_URL", "")
        if not template:
            raise FetchError(f"직접 {direct}")
        try:
            body = _get(template.replace("{url}", url), {"X-Return-Format": "html"})
        except _Blocked as blocked:
            raise FetchError(f"직접 {direct}, 대체 {blocked.reason}") from blocked
        except OSError as exc:
            code = exc.code if isinstance(exc, urllib.error.HTTPError) else type(exc).__name__
            raise FetchError(f"직접 {direct}, 대체 {code}") from exc
        logger.info("fetch fallback used: direct=%s url=%s", direct, url)
        return body


_fetcher: Fetcher = HttpFetcher()


def get_fetcher() -> Fetcher:
    return _fetcher


def set_fetcher(fetcher: Fetcher) -> Fetcher:
    """등록 지점. 이전 페처를 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _fetcher
    previous, _fetcher = _fetcher, fetcher
    return previous
