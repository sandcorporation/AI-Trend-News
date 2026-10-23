from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError, connection

pytestmark = pytest.mark.django_db


def test_returns_once_the_database_answers() -> None:
    out = StringIO()

    call_command("wait_for_db", timeout=1, stdout=out)

    assert "DB 접속됨 (1회째)" in out.getvalue()


def test_retries_then_gives_up_after_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def refuse() -> None:
        calls.append(1)
        raise OperationalError("refused")

    monkeypatch.setattr(connection, "ensure_connection", refuse)

    with pytest.raises(CommandError, match="DB에 접속하지 못했다"):
        call_command("wait_for_db", timeout=0.05, interval=0.01)
    assert len(calls) >= 2
