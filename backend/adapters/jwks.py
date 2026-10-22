"""sandcorp-auth 공개 키(JWKS) 어댑터.

규칙 원본: sandcorp-auth `docs/resource-server-token-validation.md` 4절(J1~J5).
`KeySource`가 인터페이스이고, 실제 구현은 HTTP로 JWKS를 받아 캐시하는 `JwksCache`다.
시험은 같은 `JwksCache`에 가짜 조회 함수(시험용 키 쌍의 JWK를 돌려줌)를 넣어 등록한다.
"""

import json
import logging
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import NotRequired, Protocol, TypedDict

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings

from adapters.clock import Clock, get_clock

logger = logging.getLogger(__name__)

ALGORITHM = "EdDSA"
JWKS_TTL_SECONDS = 600
JWKS_MIN_REFETCH_SECONDS = 60
JWKS_TIMEOUT_SECONDS = 5


class Jwk(TypedDict):
    kty: str
    crv: str
    x: str
    kid: str
    alg: NotRequired[str]
    use: NotRequired[str]


class JwkSet(TypedDict):
    keys: list[Jwk]


class KeySource(Protocol):
    def key_for(self, kid: str) -> Ed25519PublicKey | None: ...


def jwks_url() -> str:
    return f"{settings.AUTH_ISSUER}/.well-known/jwks.json"


def fetch_jwks_over_http() -> JwkSet:
    """GET <issuer>/.well-known/jwks.json (5초). 2xx가 아니거나 JSON이 깨지면 예외."""
    with urllib.request.urlopen(jwks_url(), timeout=JWKS_TIMEOUT_SECONDS) as response:  # noqa: S310
        body: JwkSet = json.load(response)  # 모양은 _refresh가 키마다 다시 거른다
        return body


class JwksCache:
    """`kid` → 공개 키.

    - J1: 비었거나 마지막 성공 조회에서 600초 지났으면 다시 받는다.
    - J2: 신선한데 모르는 `kid`면 그 자리에서 한 번 더 받되, 60초에 한 번까지만.
    - J3: OKP/Ed25519(+alg EdDSA, use sig)만 쓰고, 성공한 조회는 캐시를 통째로 바꾼다.
    - J4: 조회 실패는 기존 키를 그대로 쓰고 60초 동안 다시 시도하지 않는다.
    """

    def __init__(self, fetch_jwks: Callable[[], JwkSet], clock: Clock) -> None:
        self._fetch = fetch_jwks
        self._clock = clock
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._unknown_kid_refetch_at: float | None = None

    def key_for(self, kid: str) -> Ed25519PublicKey | None:
        now = self._clock.now().timestamp()
        stale = self._fetched_at is None or now - self._fetched_at >= JWKS_TTL_SECONDS
        if stale and not _within(self._failed_at, now):
            self._refresh(now)
        elif kid not in self._keys and not _within(self._unknown_kid_refetch_at, now):
            self._unknown_kid_refetch_at = now
            self._refresh(now)
        return self._keys.get(kid)

    def _refresh(self, now: float) -> None:
        try:
            jwk_set = self._fetch()
        except Exception as exc:  # 네트워크·시간 초과·HTTP·JSON 실패 모두: 가진 키로 버틴다(J4)
            logger.warning("JWKS fetch failed: %s: %s", type(exc).__name__, exc)
            self._failed_at = now
            return
        keys: dict[str, Ed25519PublicKey] = {}
        for jwk in jwk_set.get("keys", []):
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                continue
            if jwk.get("alg", ALGORITHM) != ALGORITHM or jwk.get("use", "sig") != "sig":
                continue
            key = jwt.PyJWK(dict(jwk), algorithm=ALGORITHM).key
            if isinstance(key, Ed25519PublicKey):
                keys[jwk["kid"]] = key
        self._keys = keys
        self._fetched_at = now
        self._failed_at = None


def _within(moment: float | None, now: float) -> bool:
    return moment is not None and now - moment < JWKS_MIN_REFETCH_SECONDS


class _RegisteredClock:
    """기본 캐시가 등록 지점의 시계를 따라가게 한다."""

    def now(self) -> datetime:
        return get_clock().now()


_key_source: KeySource | None = None


def get_key_source() -> KeySource:
    """프로세스당 캐시 하나(J1). 처음 부를 때 만든다."""
    global _key_source
    if _key_source is None:
        _key_source = JwksCache(fetch_jwks_over_http, _RegisteredClock())
    return _key_source


def set_key_source(source: KeySource | None) -> KeySource | None:
    """등록 지점. 이전 것을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _key_source
    previous, _key_source = _key_source, source
    return previous
