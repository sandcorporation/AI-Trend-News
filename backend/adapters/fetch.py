"""소스 페치 어댑터: 주소를 받아 응답 본문 바이트를 돌려준다."""

import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

logger = logging.getLogger(__name__)

USER_AGENT = "Foresight-Crawler/1.0 (+https://news.sandcorp.co.kr)"
TIMEOUT_SECONDS = 15
BLOCK_STATUSES = frozenset({403, 429, 503})
CHALLENGE_SNIFF_BYTES = 4096


class FetchError(Exception):
    """페치 실패(네트워크·HTTP 오류). 메시지가 소스의 마지막 수집 결과에 기록된다."""


class Fetcher(Protocol):
    def fetch(self, url: str, deadline_seconds: float | None = None) -> bytes: ...


class _Blocked(Exception):
    """직접 GET이 봇 차단으로 보이는 응답(챌린지 페이지)을 받았다."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_challenge(headers: dict[str, str], body: bytes) -> bool:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    return b"<title>just a moment" in body[:CHALLENGE_SNIFF_BYTES].lower()


def _timeout(deadline: float | None) -> float:
    """다음 GET에 줄 시간. 마감(`time.monotonic` 기준)이 있으면 남은 시간을 넘지 않는다."""
    if deadline is None:
        return TIMEOUT_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("마감 시간 초과")
    return min(TIMEOUT_SECONDS, remaining)


def _get(url: str, timeout: float, extra_headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, **(extra_headers or {})}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        body: bytes = response.read()
        headers = {k.lower(): v for k, v in response.headers.items()}
    if _is_challenge(headers, body):
        raise _Blocked("챌린지 페이지")
    return body


class HttpFetcher:
    """실제 HTTP GET. 봇 차단이면 설정된 대체 경로로 한 번 더 받는다(ADR 0005)."""

    def fetch(self, url: str, deadline_seconds: float | None = None) -> bytes:
        """`deadline_seconds`를 주면 직접·대체를 합친 전체 시간이 그 안에 끝난다."""
        from services import runtime_settings  # 순환 import 피함(services → adapters)

        if deadline_seconds is None:
            return self._fetch(url, runtime_settings.FETCH_FALLBACK_URL.get, None)

        # urlopen의 timeout은 소켓 동작 하나의 한도라, 조금씩 오는 응답은 전체 시간이 끝없이 는다.
        # 그래서 다른 스레드에서 받고 마감까지만 기다린다. 설정(DB)은 이 스레드에서 읽어 둔다.
        template = runtime_settings.FETCH_FALLBACK_URL.get()
        deadline = time.monotonic() + deadline_seconds
        outcome: list[bytes | Exception] = []

        def run() -> None:
            try:
                outcome.append(self._fetch(url, lambda: template, deadline))
            except Exception as exc:  # noqa: BLE001  # 부른 스레드에서 다시 던진다
                outcome.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(deadline_seconds)
        if not outcome:
            raise FetchError(f"시간 초과: {deadline_seconds:g}초 안에 받지 못했습니다")
        result = outcome[0]
        if isinstance(result, Exception):
            raise result
        return result

    def _fetch(self, url: str, fallback: Callable[[], str], deadline: float | None) -> bytes:
        try:
            return _get(url, _timeout(deadline))
        except _Blocked as blocked:
            direct = blocked.reason
        except urllib.error.HTTPError as exc:
            if exc.code not in BLOCK_STATUSES:
                raise FetchError(f"{type(exc).__name__}: {exc}") from exc
            direct = f"HTTP {exc.code}"
        except OSError as exc:  # URLError·시간 초과는 모두 OSError
            raise FetchError(f"{type(exc).__name__}: {exc}") from exc

        template = fallback()
        if not template:
            raise FetchError(f"직접 {direct}")
        try:
            body = _get(
                template.replace("{url}", url), _timeout(deadline), {"X-Return-Format": "html"}
            )
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
