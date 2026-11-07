"""오류 수집(GlitchTip, #168): 무엇을 보내고 무엇을 버리는지, DSN 없으면 꺼져 있는지.

SDK를 켜는 시험은 가짜 DSN과 가짜 전송(이벤트 dict를 모음)만 쓴다 — 네트워크로 나가지 않는다.
끝나면 전역 클라이언트를 떼어 다른 시험에 새지 않게 한다.
"""

from collections.abc import Iterator
from io import StringIO

import pytest
import sentry_sdk
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import Http404
from ninja.errors import HttpError
from ninja.errors import ValidationError as NinjaValidationError
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event, Hint

from core.observability import before_send, configure_sentry, init_sentry, sentry_enabled

FAKE_DSN = "https://public@example.invalid/1"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJvd25lciJ9.c2lnbmF0dXJlLXZhbHVl"


def _hint(exc: BaseException) -> Hint:
    return {"exc_info": (type(exc), exc, None)}


def _event() -> Event:
    return {"event_id": "x"}


@pytest.fixture
def captured() -> Iterator[list[Event]]:
    events: list[Event] = []

    class CaptureTransport(Transport):
        def capture_envelope(self, envelope: Envelope) -> None:
            event = envelope.get_event()
            if event is not None:
                events.append(event)

    init_sentry(FAKE_DSN, "a" * 40, transport=CaptureTransport)
    try:
        yield events
    finally:
        sentry_sdk.get_global_scope().set_client(None)


def test_no_dsn_means_no_sdk() -> None:
    assert configure_sentry("") is False
    assert configure_sentry("   ") is False
    assert not sentry_enabled()


@pytest.mark.parametrize(
    "exc",
    [
        HttpError(422, "입력"),
        HttpError(404, "없음"),
        HttpError(403, "권한"),
        Http404(),
        PermissionDenied(),
        SuspiciousOperation(),
        DjangoValidationError("틀림"),
        NinjaValidationError([]),
    ],
)
def test_expected_rejections_are_dropped(exc: BaseException) -> None:
    assert before_send(_event(), _hint(exc)) is None


@pytest.mark.parametrize("exc", [HttpError(500, "서버"), HttpError(503, "잠시"), KeyError("k")])
def test_server_errors_and_unhandled_exceptions_are_sent(exc: BaseException) -> None:
    assert before_send(_event(), _hint(exc)) is not None


def test_manage_shell_errors_are_dropped() -> None:
    event: Event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"abs_path": "/usr/lib/django/core/management/commands/shell.py"}
                        ]
                    }
                }
            ]
        }
    }
    assert before_send(event, _hint(RuntimeError("손으로 친 것"))) is None


def test_request_credentials_and_tokens_are_scrubbed() -> None:
    event: Event = {
        "request": {
            "url": f"https://news.sandcorp.co.kr/auth/callback?t={JWT}",
            "cookies": {"sessionid": "abc"},
            "data": {"message": "문의 본문"},
            "headers": {
                "Cookie": "sessionid=abc",
                "Authorization": f"Bearer {JWT}",
                "Accept": "*/*",
            },
            "query_string": "code=supersecret&page=2",
        },
        "message": f"토큰 {JWT}",
        "exception": {
            "values": [
                {
                    "value": "Bearer abcdefghijklmnop 거절",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"key": "sk-x"}}]},
                }
            ]
        },
    }
    sent = before_send(event, _hint(RuntimeError("x")))
    assert sent is not None
    request = sent["request"]
    assert "cookies" not in request
    assert "data" not in request
    assert request["headers"] == {"Accept": "*/*"}
    assert request["query_string"] == "code=[redacted]&page=2"
    assert JWT not in str(sent)
    assert "abcdefghijklmnop" not in str(sent)
    assert "vars" not in sent["exception"]["values"][0]["stacktrace"]["frames"][0]


def test_unhandled_exception_is_sent_with_release_and_production(captured: list[Event]) -> None:
    try:
        raise RuntimeError("처리 안 됨")
    except RuntimeError as exc:
        sentry_sdk.capture_exception(exc)
    sentry_sdk.capture_exception(HttpError(404, "없음"))

    assert len(captured) == 1
    assert captured[0]["release"] == "a" * 40
    assert captured[0]["environment"] == "production"


def test_glitchtip_test_without_dsn_exits_1() -> None:
    with pytest.raises(CommandError, match="DSN 없음") as caught:
        call_command("glitchtip_test")
    assert caught.value.returncode == 1


def test_glitchtip_test_captures_onboarding_exception(captured: list[Event]) -> None:
    out = StringIO()
    call_command("glitchtip_test", "--message", "연동 시험 foresight 2026-11-07", stdout=out)

    assert len(captured) == 1
    value = captured[0]["exception"]["values"][-1]
    assert value["type"] == "GlitchTipOnboardingTest"
    assert value["value"] == "연동 시험 foresight 2026-11-07"
    assert out.getvalue().startswith("보냄: ")
