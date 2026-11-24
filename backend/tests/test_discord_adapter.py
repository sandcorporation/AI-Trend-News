import logging

import pytest

from adapters.discord import DiscordNotifier

DISCORD = "https://discord.test/hook"
NOTICE = "https://notice.test/secret-token"


class Recorder:
    def __init__(self, fail_for: str = "") -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.fail_for = fail_for

    def __call__(self, url: str, body: bytes) -> None:
        self.calls.append((url, body))
        if url == self.fail_for:
            raise RuntimeError(f"boom {url}")


def test_둘째_있으면_두_곳에_같은_본문() -> None:
    rec = Recorder()
    assert DiscordNotifier(DISCORD, NOTICE, post=rec).send("경보") is True
    assert [u for u, _ in rec.calls] == [DISCORD, NOTICE]
    assert rec.calls[0][1] == rec.calls[1][1]


def test_둘째_없으면_discord만() -> None:
    rec = Recorder()
    assert DiscordNotifier(DISCORD, post=rec).send("경보") is True
    assert [u for u, _ in rec.calls] == [DISCORD]


def test_discord_실패에도_둘째_감_반환_false() -> None:
    rec = Recorder(fail_for=DISCORD)
    assert DiscordNotifier(DISCORD, NOTICE, post=rec).send("경보") is False
    assert [u for u, _ in rec.calls] == [DISCORD, NOTICE]


def test_둘째_실패에도_true_예외_없음_로그에_주소_없음(caplog: pytest.LogCaptureFixture) -> None:
    rec = Recorder(fail_for=NOTICE)
    with caplog.at_level(logging.DEBUG, logger="alerts"):
        assert DiscordNotifier(DISCORD, NOTICE, post=rec).send("경보") is True
    assert "둘째 알림 목적지 전송 실패" in caplog.text
    assert "secret-token" not in caplog.text
    assert "notice.test" not in caplog.text
