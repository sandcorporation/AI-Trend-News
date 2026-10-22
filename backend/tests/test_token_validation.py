"""토큰 검증 규칙: sandcorp-auth 참조 검증기(`backend/tests/test_reference_validator.py`)의
16개 사례를 그대로 옮겨 같은 판정이 나오는지 본다. 고정 시계, 로컬 Ed25519 키, DB·네트워크 없음."""

import base64

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from adapters.jwks import JwksCache, JwkSet
from services.tokens import (
    ISSUER,
    AccessToken,
    InsufficientScope,
    TokenRejected,
    validate_access_token,
    www_authenticate,
)
from tests.conftest import (
    NEWS_RESOURCE,
    OWNER,
    FakeJwksEndpoint,
    FrozenClock,
    Signer,
    public_jwk,
)

NEWS = NEWS_RESOURCE
PORTFOLIO = "https://sandcorp.co.kr/mcp"


@pytest.fixture
def cache(fake_jwks: FakeJwksEndpoint, frozen_clock: FrozenClock) -> JwksCache:
    return JwksCache(fake_jwks, frozen_clock)


def validate(
    token: str, cache: JwksCache, clock: FrozenClock, *, audience: str = NEWS, scope: str = "admin"
) -> AccessToken:
    return validate_access_token(
        token,
        keys=cache,
        issuer=ISSUER,
        audience=audience,
        owner_sub=OWNER,
        required_scope=scope,
        clock=clock,
    )


def test_valid_token_passes(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    token = validate(token_signer.token(), cache, frozen_clock, scope="editor")

    assert token.sub == OWNER
    assert token.client_id == "client-123"
    assert token.scopes == frozenset({"admin", "editor"})
    assert token.jti == "jti-1"


def test_other_audience_is_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    for aud in (PORTFOLIO, [NEWS], NEWS + "/", NEWS.upper()):
        with pytest.raises(TokenRejected):
            validate(token_signer.token(aud=aud), cache, frozen_clock)


def test_other_issuer_is_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    for iss in ("https://evil.example", ISSUER + "/"):
        with pytest.raises(TokenRejected):
            validate(token_signer.token(iss=iss), cache, frozen_clock)


def test_expiry_allows_30_seconds_of_leeway_then_rejects(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    token = token_signer.token()

    frozen_clock.advance(600 + 30)
    validate(token, cache, frozen_clock)

    frozen_clock.advance(1)
    with pytest.raises(TokenRejected):
        validate(token, cache, frozen_clock)


def test_token_is_rejected_eleven_minutes_later(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    token = token_signer.token()
    frozen_clock.advance(11 * 60)

    with pytest.raises(TokenRejected):
        validate(token, cache, frozen_clock)


def test_signature_by_another_key_with_the_same_kid_is_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    impostor = Signer(Ed25519PrivateKey.generate(), token_signer.kid, frozen_clock)

    with pytest.raises(TokenRejected):
        validate(impostor.token(), cache, frozen_clock)


def test_hs256_signed_with_the_public_key_is_rejected(
    token_signer: Signer, fake_jwks: FakeJwksEndpoint, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    secret = fake_jwks.keys[0]["x"]
    forged = jwt.encode(
        jwt.decode(token_signer.token(), options={"verify_signature": False}),
        secret,
        algorithm="HS256",
        headers={"kid": token_signer.kid},
    )

    with pytest.raises(TokenRejected):
        validate(forged, cache, frozen_clock)


def test_alg_none_is_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    header, payload, _ = token_signer.token().split(".")
    none_header = base64.urlsafe_b64encode(b'{"alg":"none","kid":"key-a"}').rstrip(b"=").decode()
    assert header != none_header

    with pytest.raises(TokenRejected):
        validate(f"{none_header}.{payload}.", cache, frozen_clock)


def test_missing_required_claims_are_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    for claim in ("iss", "sub", "aud", "client_id", "scope", "iat", "exp", "jti"):
        with pytest.raises(TokenRejected):
            validate(token_signer.token(**{claim: None}), cache, frozen_clock)


def test_subject_other_than_the_owner_is_rejected(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    for sub in ("someone-else", "Owner", " owner"):
        with pytest.raises(TokenRejected):
            validate(token_signer.token(sub=sub), cache, frozen_clock)


def test_missing_scope_is_forbidden_not_unauthorized(
    token_signer: Signer, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    with pytest.raises(InsufficientScope) as raised:
        validate(token_signer.token(scope="editor"), cache, frozen_clock, scope="admin")

    assert raised.value.required_scope == "admin"
    with pytest.raises(InsufficientScope):
        validate(token_signer.token(scope="administrator"), cache, frozen_clock, scope="admin")


def test_unknown_kid_refetches_jwks_once_then_passes(
    token_signer: Signer, fake_jwks: FakeJwksEndpoint, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    validate(token_signer.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 1

    rotated = Signer(Ed25519PrivateKey.generate(), "key-b", frozen_clock)
    fake_jwks.keys.append(public_jwk(rotated.private_key, rotated.kid))

    validate(rotated.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 2


def test_unknown_kid_refetch_is_limited_to_once_per_60_seconds(
    token_signer: Signer, fake_jwks: FakeJwksEndpoint, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    validate(token_signer.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 1

    stranger = Signer(Ed25519PrivateKey.generate(), "key-x", frozen_clock)
    for _ in range(3):
        with pytest.raises(TokenRejected):
            validate(stranger.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 2

    frozen_clock.advance(59)
    with pytest.raises(TokenRejected):
        validate(stranger.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 2

    frozen_clock.advance(1)
    with pytest.raises(TokenRejected):
        validate(stranger.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 3


def test_jwks_is_refetched_after_the_10_minute_ttl(
    token_signer: Signer, fake_jwks: FakeJwksEndpoint, cache: JwksCache, frozen_clock: FrozenClock
) -> None:
    validate(token_signer.token(), cache, frozen_clock)
    frozen_clock.advance(599)
    validate(token_signer.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 1

    frozen_clock.advance(1)
    validate(token_signer.token(), cache, frozen_clock)
    assert fake_jwks.fetches == 2


def test_failed_jwks_fetch_keeps_the_cached_keys(
    token_signer: Signer, fake_jwks: FakeJwksEndpoint, frozen_clock: FrozenClock
) -> None:
    calls = 0

    def flaky() -> JwkSet:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("auth server down")
        return fake_jwks()

    cache = JwksCache(flaky, frozen_clock)
    validate(token_signer.token(), cache, frozen_clock)
    frozen_clock.advance(600)

    validate(token_signer.token(), cache, frozen_clock)
    assert calls == 2
    # J4: 실패 뒤 60초 동안은 다시 조회하지 않는다.
    frozen_clock.advance(59)
    validate(token_signer.token(), cache, frozen_clock)
    assert calls == 2


def test_www_authenticate_header_values() -> None:
    meta = "https://news.sandcorp.co.kr/.well-known/oauth-protected-resource"

    assert www_authenticate("https://news.sandcorp.co.kr") == f'Bearer resource_metadata="{meta}"'
    assert www_authenticate("https://news.sandcorp.co.kr", error="invalid_token") == (
        f'Bearer resource_metadata="{meta}", error="invalid_token"'
    )
    assert www_authenticate(
        "https://news.sandcorp.co.kr", error="insufficient_scope", scope="admin"
    ) == ('Bearer error="insufficient_scope", scope="admin"')
