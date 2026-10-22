"""sandcorp-auth 액세스 토큰 검증(리소스 서버 규칙 T1~T11, S1).

규칙 원본: sandcorp-auth `docs/resource-server-token-validation.md` 3·5절.
참조 구현(`backend/tests/reference_validator.py`)과 같은 판정을 낸다
(`tests/test_token_validation.py`).
Django 요청·응답은 모른다. HTTP 쪽은 `core/auth.py`가 맡는다.
"""

from dataclasses import dataclass
from typing import Literal

import jwt

from adapters.clock import Clock
from adapters.jwks import ALGORITHM, KeySource

ISSUER = "https://auth.sandcorp.co.kr"
EXP_LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["iss", "sub", "aud", "exp", "iat", "jti", "client_id", "scope"]

Scope = Literal["admin", "editor", "login"]
SCOPES: tuple[Scope, ...] = ("admin", "editor", "login")


class TokenRejected(Exception):
    """401 `error="invalid_token"`. 메시지(거부 이유)는 로그에만 남긴다."""


class InsufficientScope(Exception):
    """403 `error="insufficient_scope"`."""

    def __init__(self, required_scope: str) -> None:
        super().__init__(required_scope)
        self.required_scope = required_scope


@dataclass(frozen=True)
class AccessToken:
    sub: str
    client_id: str
    scopes: frozenset[str]
    jti: str
    iat: int
    exp: int


def validate_access_token(
    token: str,
    *,
    keys: KeySource,
    issuer: str,
    audience: str,
    owner_sub: str,
    required_scope: str,
    clock: Clock,
) -> AccessToken:
    """검증된 토큰을 돌려주거나 TokenRejected(401) / InsufficientScope(403)를 던진다."""
    try:
        header = jwt.get_unverified_header(token)  # T1
    except jwt.InvalidTokenError as exc:
        raise TokenRejected("malformed token") from exc
    kid = header.get("kid")
    if header.get("alg") != ALGORITHM or not isinstance(kid, str):  # T2, T3
        raise TokenRejected("alg must be EdDSA and kid must be present")
    key = keys.key_for(kid)  # T4
    if key is None:
        raise TokenRejected("unknown kid")
    try:
        claims: dict[str, object] = jwt.decode(  # T5, T6
            token,
            key,
            algorithms=[ALGORITHM],
            # aud·iss·exp는 아래에서 직접 본다: PyJWT는 배열 aud를 통과시키고 시계를 못 받는다.
            options={
                "require": REQUIRED_CLAIMS,
                "verify_aud": False,
                "verify_iss": False,
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise TokenRejected("bad signature or missing claim") from exc

    sub, client_id, jti, scope = (claims[n] for n in ("sub", "client_id", "jti", "scope"))
    iat, exp = claims["iat"], claims["exp"]
    if not (  # T7 (bool은 int의 하위 타입이라 따로 막는다)
        isinstance(sub, str)
        and isinstance(client_id, str)
        and isinstance(jti, str)
        and isinstance(scope, str)
        and isinstance(iat, int)
        and isinstance(exp, int)
        and not isinstance(iat, bool)
        and not isinstance(exp, bool)
    ):
        raise TokenRejected("claim has the wrong type")
    if claims["iss"] != issuer:  # T8
        raise TokenRejected("wrong issuer")
    if claims["aud"] != audience:  # T9: 문자열 완전 일치만. 배열은 여기서 다르다
        raise TokenRejected("wrong audience")
    if clock.now().timestamp() > exp + EXP_LEEWAY_SECONDS:  # T10
        raise TokenRejected("expired")
    if sub != owner_sub:  # T11
        raise TokenRejected("not the owner")

    scopes = frozenset(scope.split(" ")) - {""}
    if required_scope not in scopes:  # S1: 원소 비교(부분 문자열 아님)
        raise InsufficientScope(required_scope)
    return AccessToken(sub=sub, client_id=client_id, scopes=scopes, jti=jti, iat=iat, exp=exp)


def www_authenticate(
    site_origin: str,
    *,
    error: Literal["invalid_token", "insufficient_scope"] | None = None,
    scope: str | None = None,
) -> str:
    """`WWW-Authenticate` 값(5절): 토큰 없음 / invalid_token(401), insufficient_scope(403)."""
    if error == "insufficient_scope":
        return f'Bearer error="insufficient_scope", scope="{scope}"'
    value = f'Bearer resource_metadata="{site_origin}/.well-known/oauth-protected-resource"'
    if error == "invalid_token":
        value += ', error="invalid_token"'
    return value
