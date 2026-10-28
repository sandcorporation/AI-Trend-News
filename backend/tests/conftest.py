"""Shared fixtures for backend HTTP-boundary tests.

Static fixture files (Vite manifest, later RSS/HTML samples) live in tests/fixtures/.
Fake adapters get registered here as they appear.
"""

import base64
import os
import resource
import time
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.test import override_settings

from adapters.auth_server import AuthEndpoints, AuthServerError, set_auth_server
from adapters.clock import set_clock
from adapters.discord import FakeNotifier, set_notifier
from adapters.fetch import set_fetcher
from adapters.jwks import Jwk, JwksCache, JwkSet, set_key_source
from adapters.portfolio import FakeGateway, set_gateway
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


# --- sandcorp-auth (토큰 검증·admin 로그인). 실제 네트워크는 부르지 않는다. ---

NEWS_RESOURCE = "https://news.sandcorp.co.kr/mcp"
OWNER = "owner"  # config.test_settings의 AUTH_OWNER_SUB


class FrozenClock:
    def __init__(self, at: datetime) -> None:
        self._now = at

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def public_jwk(private_key: Ed25519PrivateKey, kid: str) -> Jwk:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid, "alg": "EdDSA", "use": "sig"}


@dataclass
class FakeJwksEndpoint:
    """시험이 `keys`에 넣은 JWK를 돌려주고 조회 횟수를 센다."""

    keys: list[Jwk] = field(default_factory=list)
    fetches: int = 0

    def __call__(self) -> JwkSet:
        self.fetches += 1
        return {"keys": list(self.keys)}


@dataclass
class Signer:
    """시험용 Ed25519 키로 sandcorp-auth 모양의 액세스 토큰을 서명한다."""

    private_key: Ed25519PrivateKey
    kid: str
    clock: FrozenClock

    def token(self, **overrides: object) -> str:
        """기본 클레임(대표, 뉴스 aud, admin editor)에 덮어쓴다. 값이 None이면 클레임을 뺀다."""
        now = int(self.clock.now().timestamp())
        claims: dict[str, object] = {
            "iss": "https://auth.sandcorp.co.kr",
            "sub": OWNER,
            "aud": NEWS_RESOURCE,
            "client_id": "client-123",
            "scope": "admin editor",
            "iat": now,
            "exp": now + 600,
            "jti": "jti-1",
        }
        for name, value in overrides.items():
            if value is None:
                claims.pop(name, None)
            else:
                claims[name] = value
        return jwt.encode(claims, self.private_key, algorithm="EdDSA", headers={"kid": self.kid})


@pytest.fixture
def frozen_clock() -> Iterator[FrozenClock]:
    """등록 지점에 고정 시계를 끼운다."""
    clock = FrozenClock(datetime(2026, 1, 1, tzinfo=UTC))
    previous = set_clock(clock)
    yield clock
    set_clock(previous)


@pytest.fixture
def token_signer(frozen_clock: FrozenClock) -> Signer:
    return Signer(Ed25519PrivateKey.generate(), "key-a", frozen_clock)


@pytest.fixture(autouse=True)
def fake_jwks(token_signer: Signer, frozen_clock: FrozenClock) -> Iterator[FakeJwksEndpoint]:
    """키 등록 지점에 `JwksCache(가짜 JWKS 조회, 고정 시계)`를 끼운다. 시험 키의 JWK를 돌려준다."""
    endpoint = FakeJwksEndpoint([public_jwk(token_signer.private_key, token_signer.kid)])
    previous = set_key_source(JwksCache(endpoint, frozen_clock))
    yield endpoint
    set_key_source(previous)


FAKE_AUTHORIZE = "https://auth.example.test/authorize"
FAKE_TOKEN = "https://auth.example.test/token"


class FakeAuthServer:
    """메타데이터는 고정 주소, 코드 교환은 `access_token`(또는 예외)을 돌려주고 호출을 남긴다."""

    def __init__(self) -> None:
        self.access_token: str | AuthServerError = AuthServerError("no token configured")
        self.exchanges: list[dict[str, str]] = []

    def endpoints(self) -> AuthEndpoints:
        return AuthEndpoints(FAKE_AUTHORIZE, FAKE_TOKEN)

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        self.exchanges.append(
            {"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier}
        )
        if isinstance(self.access_token, AuthServerError):
            raise self.access_token
        return self.access_token


@pytest.fixture(autouse=True)
def fake_auth_server() -> Iterator[FakeAuthServer]:
    server = FakeAuthServer()
    previous = set_auth_server(server)
    yield server
    set_auth_server(previous)


@pytest.fixture(autouse=True)
def fake_discord() -> Iterator[FakeNotifier]:
    """경보는 실제 웹훅으로 나가지 않는다. 보낸 메시지는 `.sent`에 남는다."""
    notifier = FakeNotifier()
    previous = set_notifier(notifier)
    yield notifier
    set_notifier(previous)


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
