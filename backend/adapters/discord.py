"""알림 어댑터: Discord 웹훅으로 운영 경보를 보낸다. 실패해도 호출 쪽을 실패시키지 않는다."""

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Protocol

from django.conf import settings

logger = logging.getLogger("alerts")

TIMEOUT_SECONDS = 10
MAX_CONTENT = 1900  # Discord 메시지 한도(2000자) 안쪽

PostJson = Callable[[str, bytes], None]


class Notifier(Protocol):
    def send(self, message: str) -> bool:
        """메시지를 보낸다. 보냈거나(또는 보낼 곳이 없어 로그로 갈음) 하면 True, 실패하면 False.
        예외를 던지지 않는다."""
        ...


def _post_json(url: str, body: bytes) -> None:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "AI-Trend-News-Alerts/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):  # noqa: S310
        pass


class DiscordNotifier:
    """실제 구현. 웹훅 URL이 없으면 보내지 않고 로그만 남긴다."""

    def __init__(self, webhook_url: str, post: PostJson = _post_json) -> None:
        self._url = webhook_url
        self._post = post

    def send(self, message: str) -> bool:
        if not self._url:
            logger.warning("DISCORD_WEBHOOK_URL 없음 — 경보를 보내지 않고 기록만: %s", message)
            return True
        body = json.dumps({"content": message[:MAX_CONTENT]}).encode()
        try:
            self._post(self._url, body)
        except Exception:  # noqa: BLE001 - 경보 전송 실패가 점검 명령을 죽이면 안 된다
            logger.exception("Discord 전송 실패")
            return False
        return True


class FakeNotifier:
    """시험용: 보낸 메시지를 남기고, `fail`이면 전송 실패를 흉내 낸다."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, message: str) -> bool:
        if self.fail:
            return False
        self.sent.append(message)
        return True


_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    return _notifier if _notifier is not None else DiscordNotifier(settings.DISCORD_WEBHOOK_URL)


def set_notifier(notifier: Notifier | None) -> Notifier | None:
    """등록 지점. 이전 값을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _notifier
    previous, _notifier = _notifier, notifier
    return previous
