import threading

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.utils import OperationalError
from django.test import Client
from sandcorp_authkit.testing import FixedClock

from services import heartbeat


def _refuse_db(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise OperationalError("connection refused")

    monkeypatch.setattr(connection, "cursor", refuse)


@pytest.mark.django_db
def test_health_ok_when_db_and_scheduler_alive(client: Client, frozen_clock: FixedClock) -> None:
    heartbeat.beat("scheduler")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_health_503_when_scheduler_never_beat(client: Client, frozen_clock: FixedClock) -> None:
    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "failed": ["scheduler"]}


@pytest.mark.django_db
def test_health_503_when_scheduler_beat_is_stale(client: Client, frozen_clock: FixedClock) -> None:
    heartbeat.beat("scheduler")
    frozen_clock.advance(heartbeat.STALE_AFTER_SECONDS + 1)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "failed": ["scheduler"]}


@pytest.mark.django_db
def test_health_still_ok_just_inside_the_stale_limit(
    client: Client, frozen_clock: FixedClock
) -> None:
    heartbeat.beat("scheduler")
    frozen_clock.advance(heartbeat.STALE_AFTER_SECONDS)

    assert client.get("/api/health").status_code == 200


@pytest.mark.django_db
def test_health_503_lists_db_when_database_is_down(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_db(monkeypatch)

    response = client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert body == {"status": "error", "failed": ["db"]}
    assert "connection refused" not in response.content.decode()


@pytest.mark.django_db
def test_ready_scope_checks_only_the_database(client: Client, frozen_clock: FixedClock) -> None:
    response = client.get("/api/health?scope=ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_scope_503_when_database_is_down(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    _refuse_db(monkeypatch)

    response = client.get("/api/health?scope=ready")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "failed": ["db"]}


@pytest.mark.django_db
def test_beat_updates_the_same_row(frozen_clock: FixedClock) -> None:
    heartbeat.beat("scheduler")
    frozen_clock.advance(15)
    heartbeat.beat("scheduler")

    assert heartbeat.is_alive("scheduler")
    from core.models import Heartbeat

    assert Heartbeat.objects.filter(name="scheduler").count() == 1
    assert Heartbeat.objects.get(name="scheduler").beat_at == frozen_clock.now()


@pytest.mark.django_db
def test_check_heartbeat_exit_codes(frozen_clock: FixedClock) -> None:
    with pytest.raises(SystemExit) as dead:
        call_command("check_heartbeat", "scheduler")
    assert dead.value.code == 1

    heartbeat.beat("scheduler")
    with pytest.raises(SystemExit) as alive:
        call_command("check_heartbeat", "scheduler")
    assert alive.value.code == 0

    frozen_clock.advance(heartbeat.STALE_AFTER_SECONDS + 1)
    with pytest.raises(SystemExit) as stale:
        call_command("check_heartbeat", "scheduler")
    assert stale.value.code == 1


def test_beating_thread_beats_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    beats: list[str] = []
    first = threading.Event()

    def record(name: str) -> None:
        beats.append(name)
        first.set()

    monkeypatch.setattr(heartbeat, "beat", record)
    monkeypatch.setattr(heartbeat, "close_old_connections", lambda: None)
    stop = threading.Event()
    thread = heartbeat.start_beating("scheduler", stop)

    assert first.wait(2)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert beats[0] == "scheduler"
