import io
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message

import pytest
from django.test import override_settings

from adapters import fetch as fetch_mod
from adapters.fetch import FetchError, HttpFetcher

PROXY = "https://proxy.example/{url}"
URL = "https://blocked.example/a"


class _Resp(io.BytesIO):
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class _Net:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.calls: list[urllib.request.Request] = []
        self.timeouts: list[float] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> _Resp:
        self.calls.append(request)
        self.timeouts.append(timeout)
        result = self.routes[request.full_url]
        if isinstance(result, int):
            raise urllib.error.HTTPError(request.full_url, result, "x", Message(), None)
        assert isinstance(result, _Resp)
        return result


Install = Callable[[dict[str, object]], _Net]


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> Install:
    def install(routes: dict[str, object]) -> _Net:
        n = _Net(routes)
        monkeypatch.setattr(urllib.request, "urlopen", n)
        return n

    return install


PROXY_URL = "https://proxy.example/" + URL


@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_ok_skips_fallback(net: Install) -> None:
    n = net({URL: _Resp(b"direct")})
    assert HttpFetcher().fetch(URL) == b"direct"
    assert len(n.calls) == 1


@pytest.mark.parametrize("code", [403, 429, 503])
@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_blocked_status_uses_fallback(net: Install, code: int) -> None:
    n = net({URL: code, PROXY_URL: _Resp(b"<html>real</html>")})
    assert HttpFetcher().fetch(URL) == b"<html>real</html>"
    assert n.calls[1].full_url == PROXY_URL
    assert n.calls[1].get_header("X-return-format") == "html"
    assert n.calls[1].get_header("User-agent") == n.calls[0].get_header("User-agent")


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(b"x", {"cf-mitigated": "challenge"}),
        _Resp(b"<html><head><title>Just a moment...</title>"),
    ],
)
@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_challenge_200_uses_fallback(net: Install, resp: _Resp) -> None:
    net({URL: resp, PROXY_URL: _Resp(b"real")})
    assert HttpFetcher().fetch(URL) == b"real"


@override_settings(FETCH_FALLBACK_URL="")
def test_disabled_fallback_raises(net: Install) -> None:
    n = net({URL: 403})
    with pytest.raises(FetchError, match="403"):
        HttpFetcher().fetch(URL)
    assert len(n.calls) == 1


@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_fallback_failure_reports_both(net: Install) -> None:
    net({URL: 403, PROXY_URL: 502})
    with pytest.raises(FetchError, match="직접 HTTP 403, 대체 502"):
        HttpFetcher().fetch(URL)


@pytest.mark.parametrize("code", [404, 500])
@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_non_block_errors_skip_fallback(net: Install, code: int) -> None:
    n = net({URL: code})
    with pytest.raises(FetchError):
        HttpFetcher().fetch(URL)
    assert len(n.calls) == 1


@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_without_deadline_each_get_keeps_its_own_timeout(net: Install) -> None:
    n = net({URL: 403, PROXY_URL: _Resp(b"real")})
    HttpFetcher().fetch(URL)
    assert n.timeouts == [15, 15]


@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_deadline_gives_fallback_only_the_remaining_time(
    monkeypatch: pytest.MonkeyPatch, net: Install
) -> None:
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    n = net({URL: 403, PROXY_URL: _Resp(b"real")})

    def slow_direct(request: urllib.request.Request, timeout: float) -> _Resp:
        if request.full_url == URL:
            clock[0] += 14  # 직접 시도가 14초를 썼다
        return n(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", slow_direct)

    assert fetch_mod._timeout(None) == 15
    assert HttpFetcher().fetch(URL, deadline_seconds=20) == b"real"
    assert n.timeouts == [15, 6]


@override_settings(FETCH_FALLBACK_URL=PROXY)
def test_deadline_ends_a_response_that_never_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()

    def hang(request: urllib.request.Request, timeout: float) -> _Resp:
        release.wait(5)
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", hang)
    try:
        with pytest.raises(FetchError, match="시간 초과"):
            HttpFetcher().fetch(URL, deadline_seconds=0.05)
    finally:
        release.set()
