"""portfolio 문의 API 어댑터(ADR 0003). 실제 + 가짜.

계약: `POST {PORTFOLIO_INQUIRY_URL}`, 헤더 `X-Inquiry-Secret`, JSON 본문
`{name, company, contact, message, site, kind}`. 2xx 성공(본문 무시), 4xx 영구 실패,
5xx·연결 실패·타임아웃(5초)은 재시도. 오류 메시지에 문의 내용을 넣지 않는다.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol, TypedDict

from django.conf import settings

TIMEOUT_SECONDS = 5
SITE = "news"


class InquiryPayload(TypedDict):
    name: str
    company: str
    contact: str
    message: str
    site: str
    kind: str


class PermanentError(Exception):
    """4xx: 다시 보내도 소용없다."""


class TransientError(Exception):
    """5xx·연결 실패·타임아웃: 나중에 다시 보낸다."""


class InquiryGateway(Protocol):
    def send(self, payload: InquiryPayload) -> None:
        """보냈으면 돌아오고, 영구 실패면 PermanentError, 재시도할 실패면 TransientError."""
        ...


# (url, secret, body) -> HTTP 상태 코드. 연결 실패·타임아웃은 예외.
Post = Callable[[str, str, bytes], int]


def _post(url: str, secret: str, body: bytes) -> int:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Inquiry-Secret": secret,
            "User-Agent": "Foresight/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310
            return int(response.status)
    except urllib.error.HTTPError as error:
        return error.code


class PortfolioGateway:
    def __init__(self, url: str, secret: str, post: Post = _post) -> None:
        self._url = url
        self._secret = secret
        self._post = post

    def send(self, payload: InquiryPayload) -> None:
        try:
            status = self._post(self._url, self._secret, json.dumps(payload).encode())
        except Exception as error:  # noqa: BLE001 - 어떤 연결 실패도 재시도 대상
            raise TransientError(f"연결 실패({type(error).__name__})") from None
        if 200 <= status < 300:
            return
        if 400 <= status < 500:
            raise PermanentError(f"HTTP {status}")
        raise TransientError(f"HTTP {status}")


class FakeGateway:
    """시험용: 받은 본문을 `.sent`에 남긴다. `error`가 있으면 그것을 던진다."""

    def __init__(self) -> None:
        self.sent: list[InquiryPayload] = []
        self.error: Exception | None = None

    def send(self, payload: InquiryPayload) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(payload)


_gateway: InquiryGateway | None = None


def is_configured() -> bool:
    return bool(settings.PORTFOLIO_INQUIRY_URL and settings.PORTFOLIO_INQUIRY_SECRET)


def get_gateway() -> InquiryGateway:
    if _gateway is not None:
        return _gateway
    return PortfolioGateway(settings.PORTFOLIO_INQUIRY_URL, settings.PORTFOLIO_INQUIRY_SECRET)


def set_gateway(gateway: InquiryGateway | None) -> InquiryGateway | None:
    """등록 지점. 이전 값을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _gateway
    previous, _gateway = _gateway, gateway
    return previous
