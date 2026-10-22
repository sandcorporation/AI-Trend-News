import pytest
from django.db import connection
from django.db.utils import OperationalError
from django.test import Client


@pytest.mark.django_db
def test_health_reports_database_reachable(client: Client) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


@pytest.mark.django_db
def test_health_reports_unavailable_when_database_is_down(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> None:
        raise OperationalError("connection refused")

    monkeypatch.setattr(connection, "cursor", refuse)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "unavailable"}
