"""Shared fixtures for backend HTTP-boundary tests.

Static fixture files (Vite manifest, later RSS/HTML samples) live in tests/fixtures/.
Fake adapters get registered here as they appear.
"""

import os
import resource
import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from django.test import override_settings
from sandcorp_authkit.testing import FakeJwks, FakeTokenEndpoint, FixedClock

from adapters.anthropic import FakeLlm, set_llm
from adapters.clock import set_clock
from adapters.discord import FakeNotifier, set_notifier
from adapters.fetch import set_fetcher
from adapters.portfolio import FakeGateway, set_gateway
from core import auth_adapter
from core.vite import _read_manifest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def vite_manifest() -> Iterator[None]:
    """Serve the shell against a fixed Vite manifest so tests never need a frontend build."""
    _read_manifest.cache_clear()
    with override_settings(
        VITE_DEV_SERVER_URL="", VITE_MANIFEST_PATH=FIXTURES / "vite-manifest.json"
    ):
        yield
    _read_manifest.cache_clear()


class FakeFetcher:
    """고정 응답을 URL별로 돌려주는 페처. 값이 예외면 던진다. 요청 기록을 남긴다."""

    def __init__(self, responses: dict[str, bytes | Exception] | None = None) -> None:
        self.responses: dict[str, bytes | Exception] = responses or {}
        self.requested: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.requested.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def rss_bytes() -> bytes:
    return (FIXTURES / "news-feed.xml").read_bytes()


@pytest.fixture
def fake_fetcher() -> Iterator[FakeFetcher]:
    """등록 지점에 가짜 페처를 끼운다(실제 네트워크 호출 금지)."""
    fetcher = FakeFetcher()
    previous = set_fetcher(fetcher)
    yield fetcher
    set_fetcher(previous)


@pytest.fixture
def html_bytes() -> bytes:
    return (FIXTURES / "news-list.html").read_bytes()


# --- sandcorp-auth (토큰 검증·admin 로그인): 가짜는 `sandcorp_authkit.testing`, 네트워크 없음 ---

NEWS_RESOURCE = "https://news.sandcorp.co.kr/mcp"
OWNER = "owner"  # config.test_settings의 AUTH_OWNER_SUB


@dataclass
class Signer:
    """시험 키로 이 사이트(대표, 뉴스 aud, admin editor) 기본 클레임의 액세스 토큰을 발급한다."""

    jwks: FakeJwks
    clock: FixedClock

    def token(self, **overrides: object) -> str:
        """기본 클레임에 덮어쓴다. 값이 None이면 클레임을 뺀다."""
        claims: dict[str, object] = {
            "sub": OWNER,
            "scope": "admin editor",
            "client_id": "client-123",
            **overrides,
        }
        return self.jwks.issue_access_token(self.clock, audience=NEWS_RESOURCE, **claims)  # type: ignore[arg-type]  # claim 이름이 키워드로 온다


@pytest.fixture
def frozen_clock() -> Iterator[FixedClock]:
    """저장소 시계 등록 지점(토큰 만료·JWKS 캐시가 읽는다)에 고정 시계를 끼운다."""
    clock = FixedClock()
    previous = set_clock(clock)
    yield clock
    set_clock(previous)


@pytest.fixture(autouse=True)
def fake_jwks(frozen_clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> FakeJwks:
    """JWKS 캐시를 `진짜 JwksCache(가짜 JWKS 엔드포인트, 고정 시계)`로 바꾼다."""
    fake = FakeJwks()
    cache = fake.cache(frozen_clock)
    monkeypatch.setattr(auth_adapter, "jwks_cache", lambda: cache)
    return fake


@pytest.fixture
def token_signer(fake_jwks: FakeJwks, frozen_clock: FixedClock) -> Signer:
    return Signer(fake_jwks, frozen_clock)


@pytest.fixture
def fake_auth_server(
    fake_jwks: FakeJwks, frozen_clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> FakeTokenEndpoint:
    """가짜 sandcorp-auth `/token`. admin 로그인 시험이 요청한다(이 저장소 클라이언트 값)."""
    server = FakeTokenEndpoint(
        fake_jwks, frozen_clock, client_id="news-client", client_secret="news-secret"
    )
    monkeypatch.setattr(auth_adapter, "token_transport", lambda: server)
    return server


@pytest.fixture(autouse=True)
def fake_discord() -> Iterator[FakeNotifier]:
    """경보는 실제 웹훅으로 나가지 않는다. 보낸 메시지는 `.sent`에 남는다."""
    notifier = FakeNotifier()
    previous = set_notifier(notifier)
    yield notifier
    set_notifier(previous)


@pytest.fixture(autouse=True)
def fake_llm() -> Iterator[FakeLlm]:
    """LLM은 실제 API로 나가지 않는다. 응답은 `.queue(...)`로 넣고 보낸 요청은 `.requests`."""
    llm = FakeLlm()
    previous = set_llm(llm)
    yield llm
    set_llm(previous)


@pytest.fixture(autouse=True)
def fake_portfolio() -> Iterator[FakeGateway]:
    """문의는 실제 portfolio로 나가지 않는다(설정은 채워진 것으로 본다). 보낸 본문은 `.sent`."""
    gateway = FakeGateway()
    previous = set_gateway(gateway)
    with override_settings(
        PORTFOLIO_INQUIRY_URL="https://portfolio.test/api/inquiries",
        PORTFOLIO_INQUIRY_SECRET="test-secret",
    ):
        yield gateway
    set_gateway(previous)


# --- 시험 하나당 본문 CPU 한도 (이슈 #102, 스킬 test-speed-budget) ---
# 본문(call 단계)의 CPU = 이 프로세스 + 자식. 넘고 통과한 시험은 실패로 바꾼다(준비·정리 제외).
# 시험마다 CPU 초를 junit 속성 `cpu_seconds`로 남긴다(`pytest --junitxml=…`).
_CPU_LIMIT_SECONDS = float(os.environ.get("NEWS_TEST_CPU_LIMIT", "1.0"))  # 0이면 끔


def _cpu_seconds() -> float:
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None]:
    started = _cpu_seconds()
    try:
        return (yield)
    finally:
        item._cpu_seconds_spent = _cpu_seconds() - started  # type: ignore[attr-defined]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    spent: float | None = getattr(item, "_cpu_seconds_spent", None)
    if report.when == "call" and spent is not None:
        # junitxml은 teardown 보고서의 user_properties를 쓴다 — item과 report 양쪽에 넣어야 남는다.
        item.user_properties.append(("cpu_seconds", round(spent, 3)))
        report.user_properties.append(("cpu_seconds", round(spent, 3)))
        if _CPU_LIMIT_SECONDS > 0 and spent > _CPU_LIMIT_SECONDS and report.passed:
            report.outcome = "failed"
            report.longrepr = (
                f"시험 본문이 CPU {spent:.2f}초를 썼다 — 한도 {_CPU_LIMIT_SECONDS:.2f}초"
                " (NEWS_TEST_CPU_LIMIT). 원인을 찾아 줄인다; 한도를 늘리지 않는다."
            )
    return report


@pytest.fixture(scope="session", autouse=True)
def _load_urlconf_before_any_test() -> None:
    """URLconf는 첫 요청에서야 읽힌다 — 처음 보낸 시험 하나가 혼자 CPU를 내지 않게 미리 읽는다."""
    from django.urls import get_resolver

    assert get_resolver().url_patterns
