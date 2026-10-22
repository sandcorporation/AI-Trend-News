"""경보 점검(#10): 시계를 고정하고 점검 명령을 불러 보낸 메시지를 확인한다."""

import re
from pathlib import Path

import pytest
from django.core.management import call_command

from adapters.discord import DiscordNotifier, FakeNotifier, set_notifier
from collection.models import Source
from editor.models import Alert, EditorRun
from mcpserver import tools
from tests.conftest import FrozenClock

PROMPT = Path(__file__).resolve().parents[2] / "automation" / "editor.md"


def finish_run_at(clock: FrozenClock) -> EditorRun:
    run = EditorRun.objects.create()
    EditorRun.objects.filter(pk=run.pk).update(started_at=clock.now(), finished_at=clock.now())
    return run


@pytest.mark.django_db
def test_editor_missing_alerts_once_then_recovers(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    finish_run_at(frozen_clock)
    frozen_clock.advance(25 * 3600)

    call_command("check_alerts")
    assert len(fake_discord.sent) == 1
    assert "편집자 미실행" in fake_discord.sent[0]

    frozen_clock.advance(3600)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 1  # 같은 조건으로 다시 보내지 않는다

    finish_run_at(frozen_clock)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 2
    assert fake_discord.sent[1].startswith("[복구]")
    call_command("check_alerts")
    assert len(fake_discord.sent) == 2
    assert not Alert.objects.filter(active=True).exists()


@pytest.mark.django_db
def test_recent_run_sends_nothing_and_never_run_alerts(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    finish_run_at(frozen_clock)
    frozen_clock.advance(23 * 3600)
    call_command("check_alerts")
    assert fake_discord.sent == []

    EditorRun.objects.all().delete()
    call_command("check_alerts")
    assert len(fake_discord.sent) == 1 and "한 번도" in fake_discord.sent[0]


@pytest.mark.django_db
def test_abandoned_run_alerts_and_clears_after_new_finish(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    finish_run_at(frozen_clock)
    frozen_clock.advance(3600)
    stuck = EditorRun.objects.create()
    EditorRun.objects.filter(pk=stuck.pk).update(started_at=frozen_clock.now())
    frozen_clock.advance(24 * 3600)  # 미종료 실행은 24시간째, 마지막 종료는 25시간 전
    call_command("check_alerts")
    assert len(fake_discord.sent) == 1

    # 정상 종료가 새로 생기면 복구된다(옛 미종료 실행은 마지막 종료보다 앞이라 더는 세지 않는다).
    frozen_clock.advance(60)
    finish_run_at(frozen_clock)
    call_command("check_alerts")
    assert fake_discord.sent[-1].startswith("[복구]")


@pytest.mark.django_db
def test_source_failures_alert_recover_and_realert(
    frozen_clock: FrozenClock, fake_discord: FakeNotifier
) -> None:
    finish_run_at(frozen_clock)
    source = Source.objects.create(
        name="HN", url="https://example.com/feed", last_result="HTTP 500"
    )

    Source.objects.filter(pk=source.pk).update(consecutive_failures=2)
    call_command("check_alerts")
    assert fake_discord.sent == []

    Source.objects.filter(pk=source.pk).update(consecutive_failures=3)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 1 and "HN" in fake_discord.sent[0]
    Source.objects.filter(pk=source.pk).update(consecutive_failures=4)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 1

    Source.objects.filter(pk=source.pk).update(consecutive_failures=0)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 2 and fake_discord.sent[1].startswith("[복구]")

    Source.objects.filter(pk=source.pk).update(consecutive_failures=3)
    call_command("check_alerts")
    assert len(fake_discord.sent) == 3 and fake_discord.sent[2].startswith("[경보]")


@pytest.mark.django_db
def test_failed_send_does_not_fail_command_and_retries_next_time(
    frozen_clock: FrozenClock,
) -> None:
    broken = FakeNotifier(fail=True)
    previous = set_notifier(broken)
    try:
        call_command("check_alerts")  # 예외 없이 끝난다(편집자 한 번도 안 돔)
        assert not Alert.objects.filter(active=True).exists()
        broken.fail = False
        call_command("check_alerts")
        assert len(broken.sent) == 1
    finally:
        set_notifier(previous)


def test_real_discord_adapter_never_raises_and_skips_without_url() -> None:
    posted: list[tuple[str, bytes]] = []

    def ok(url: str, body: bytes) -> None:
        posted.append((url, body))

    def boom(url: str, body: bytes) -> None:
        raise OSError("network down")

    assert DiscordNotifier("https://discord.test/hook", post=ok).send("안녕") is True
    assert posted and posted[0][0] == "https://discord.test/hook"
    assert DiscordNotifier("https://discord.test/hook", post=boom).send("안녕") is False
    assert DiscordNotifier("", post=boom).send("안녕") is True  # URL 없음: 보내지 않고 로그만


def test_prompt_and_editor_tools_agree() -> None:
    text = PROMPT.read_text(encoding="utf-8")
    mentioned = set(re.findall(r"`([a-z_]+)\(\)`", text))
    registered = set(tools.TOOLS)
    editor_tools = {name for name, t in tools.TOOLS.items() if t.scope == "editor"}

    assert mentioned <= registered, f"등록부에 없는 도구: {mentioned - registered}"
    assert editor_tools <= mentioned, f"프롬프트에 빠진 편집자 도구: {editor_tools - mentioned}"
    admin_tools = {name for name, t in tools.TOOLS.items() if t.scope == "admin"}
    assert not (admin_tools & mentioned)
