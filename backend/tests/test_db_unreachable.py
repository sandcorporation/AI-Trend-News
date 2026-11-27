"""DB가 거절하지 않고 응답 없이 매달릴 때(#228): 시간 제한 안에 503·500을 내고, 돌아오면 회복한다.

시험 DB 앞에 TCP 중계를 두고 바이트 전달을 멈춰 `docker pause`와 같은 상태를 만든다.
"""

import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import suppress

import pytest
from django.db import close_old_connections, connections
from django.http import HttpResponse
from django.test import Client

from config import database

# 시험은 속도를 위해 1초로 줄인다(mysqlclient의 시간 제한은 정수 초라 더 못 줄인다).
# 운영 값은 test_connect_timeout_*·test_web_process_*가 본다. 실제로 기다리는 시험은
# 접속 제한·읽기 제한을 지나는 둘뿐이고, 나머지 화면·헬스 분기는 대기 없는 시험이 본다.
FAST_TIMEOUT = 1


class FreezableProxy:
    def __init__(self, target: tuple[str, int]) -> None:
        self.target = target
        self.flowing = threading.Event()
        self.flowing.set()
        self.sockets: list[socket.socket] = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        with suppress(OSError):
            while True:
                client = self.server.accept()[0]
                upstream = socket.create_connection(self.target)
                self.sockets += [client, upstream]
                for source, sink in ((client, upstream), (upstream, client)):
                    threading.Thread(target=self._pipe, args=(source, sink), daemon=True).start()

    def _pipe(self, source: socket.socket, sink: socket.socket) -> None:
        with suppress(OSError):
            while data := source.recv(65536):
                self.flowing.wait()
                sink.sendall(data)
            sink.shutdown(socket.SHUT_WR)

    def close(self) -> None:
        self.flowing.set()
        for sock in [self.server, *self.sockets]:
            with suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)
            sock.close()


@pytest.fixture
def proxy(db: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[FreezableProxy]:
    """기본 연결을 중계를 거치는 새 연결로 바꾼다(트랜잭션 밖, 읽기만 한다)."""
    original = connections["default"]
    config = original.settings_dict
    relay = FreezableProxy((config["HOST"], int(config["PORT"])))
    monkeypatch.setattr(database, "CONNECT_TIMEOUT_SECONDS", FAST_TIMEOUT)
    options = {**config["OPTIONS"], **database.mysql_options(FAST_TIMEOUT)}
    through = type(original)(
        {**config, "HOST": "127.0.0.1", "PORT": relay.port, "OPTIONS": options}, "default"
    )
    connections["default"] = through
    try:
        yield relay
    finally:
        relay.close()
        through.close()
        connections["default"] = original


def serve(path: str) -> HttpResponse:
    """시험 Client가 끊어 두는 요청 앞뒤 연결 정리를 실제 서버처럼 부른다."""
    close_old_connections()
    response = Client(raise_request_exception=False).get(path)
    close_old_connections()
    return response  # type: ignore[return-value]


def test_health_503_db_in_time_on_a_live_connection_and_recovers(proxy: FreezableProxy) -> None:
    assert serve("/api/health?scope=ready").status_code == 200  # 남는 연결을 만든다
    proxy.flowing.clear()

    started = time.monotonic()
    response = serve("/api/health")
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert json.loads(response.content) == {"status": "error", "failed": ["db"]}
    # 남은 연결의 ping(읽기 제한) + 다시 접속(접속 제한). scheduler 조회로 한 번 더 기다리지 않는다.
    assert elapsed < 3 * FAST_TIMEOUT

    proxy.flowing.set()
    assert json.loads(serve("/api/health?scope=ready").content) == {"status": "ok"}


def test_public_page_shows_the_500_screen_when_db_hangs(proxy: FreezableProxy) -> None:
    proxy.flowing.clear()

    started = time.monotonic()
    response = serve("/predictions")

    assert time.monotonic() - started < 2 * FAST_TIMEOUT
    assert response.status_code == 500
    assert "일시적인 오류가 났습니다" in response.content.decode()


def test_connect_timeout_is_set_for_every_process() -> None:
    assert connections["default"].settings_dict["OPTIONS"]["connect_timeout"] == 3


def test_web_process_limits_reads_and_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """gunicorn이 읽는 config.wsgi만 읽기·쓰기 제한을 켠다(migrate·scheduler는 긴 쿼리 허용)."""
    monkeypatch.setattr(os, "environ", dict(os.environ))
    import config.wsgi

    monkeypatch.setattr(database, "web_io_timeout", 0)
    os.environ.pop("WEB_DB_IO_TIMEOUT", None)
    assert "read_timeout" not in database.mysql_options(database.web_io_timeout)

    importlib.reload(config.wsgi)
    options = database.mysql_options(database.web_io_timeout)

    assert options["read_timeout"] == options["write_timeout"] == 5
    # 남은 연결 ping + 다시 접속이 gunicorn --timeout 30보다 훨씬 짧아야 한다.
    assert int(options["read_timeout"]) + int(options["connect_timeout"]) < 30


@pytest.mark.parametrize(("value", "expected"), [("9", 9), ("0", 0)])
def test_web_process_timeout_comes_from_web_db_io_timeout(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: int
) -> None:
    monkeypatch.setattr(os, "environ", {**os.environ, "WEB_DB_IO_TIMEOUT": value})
    monkeypatch.setattr(database, "web_io_timeout", 0)
    import config.wsgi

    importlib.reload(config.wsgi)

    assert database.web_io_timeout == expected


def test_environment_alone_does_not_limit_other_processes() -> None:
    """.env의 값은 wsgi를 거치지 않는 migrate·scheduler에 닿지 않는다."""
    code = "from config import settings; print(sorted(settings.DATABASES['default']['OPTIONS']))"
    env = {**os.environ, "WEB_DB_IO_TIMEOUT": "9"}
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "['charset', 'connect_timeout']"
