"""sandcorp-auth 인가 서버 어댑터: 메타데이터(RFC 8414)와 토큰 엔드포인트.

이 사이트는 sandcorp-auth의 기밀 클라이언트다(관리 화면 로그인). 실제 구현은 HTTP,
시험은 `tests/conftest.py`의 `fake_auth_server`를 등록 지점에 끼운다.
"""

import base64
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

TIMEOUT_SECONDS = 5


class AuthServerError(Exception):
    """인가 서버 호출 실패(네트워크, 2xx 아님, 응답 모양 틀림)."""


@dataclass(frozen=True)
class AuthEndpoints:
    authorization_endpoint: str
    token_endpoint: str


class AuthServer(Protocol):
    def endpoints(self) -> AuthEndpoints: ...

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        """인가 코드를 액세스 토큰으로 바꾼다. 실패하면 AuthServerError."""
        ...


def _read_json(request: urllib.request.Request | str) -> dict[str, object]:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            body = json.load(response)
    except (OSError, ValueError) as exc:  # URLError·HTTPError·시간 초과·JSON 깨짐
        raise AuthServerError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(body, dict):
        raise AuthServerError("response is not a JSON object")
    return body


class HttpAuthServer:
    """설정값 `AUTH_ISSUER`·`AUTH_CLIENT_ID`·`AUTH_CLIENT_SECRET`을 쓰는 실제 구현."""

    def __init__(self) -> None:
        self._endpoints: AuthEndpoints | None = None

    def endpoints(self) -> AuthEndpoints:
        if self._endpoints is None:
            meta = _read_json(f"{settings.AUTH_ISSUER}/.well-known/oauth-authorization-server")
            authorize, token = meta.get("authorization_endpoint"), meta.get("token_endpoint")
            if not isinstance(authorize, str) or not isinstance(token, str):
                raise AuthServerError("metadata lacks authorization/token endpoint")
            self._endpoints = AuthEndpoints(authorize, token)
        return self._endpoints

    def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        form = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            }
        ).encode()
        # client_secret_basic: 각 값을 form-urlencode한 뒤 base64(id:secret) (RFC 6749 2.3.1)
        quote = urllib.parse.quote_plus
        pair = f"{quote(settings.AUTH_CLIENT_ID)}:{quote(settings.AUTH_CLIENT_SECRET)}"
        request = urllib.request.Request(
            self.endpoints().token_endpoint,
            data=form,
            method="POST",
            headers={
                "Authorization": "Basic " + base64.b64encode(pair.encode()).decode(),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        token = _read_json(request).get("access_token")
        if not isinstance(token, str):
            raise AuthServerError("token response lacks access_token")
        return token


_auth_server: AuthServer = HttpAuthServer()


def get_auth_server() -> AuthServer:
    return _auth_server


def set_auth_server(server: AuthServer) -> AuthServer:
    """등록 지점. 이전 것을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _auth_server
    previous, _auth_server = _auth_server, server
    return previous
