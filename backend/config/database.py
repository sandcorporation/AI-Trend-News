"""MySQL 접속 옵션의 시간 제한(#228).

제한이 없으면 응답 없는 DB를 gunicorn `--timeout 30`보다 오래 기다려, 워커가 죽고 응답이 비었다.
"""

from collections.abc import Mapping

# 새 접속(handshake까지). 3초: web healthcheck·배포 스모크의 5초 안에 503이 나간다.
CONNECT_TIMEOUT_SECONDS = 3


def mysql_options(environ: Mapping[str, str]) -> dict[str, str | int]:
    """`DB_IO_TIMEOUT`(초)은 web 프로세스만 켠다(`config/wsgi.py`).

    migrate의 긴 DDL이나 scheduler 작업은 끊으면 안 되므로 기본은 읽기·쓰기 제한이 없다.
    남아 있던 연결은 접속 제한만으로는 풀리지 않는다(ping·쿼리의 읽기가 매달린다).
    """
    options: dict[str, str | int] = {
        "charset": "utf8mb4",
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }
    io_timeout = int(environ.get("DB_IO_TIMEOUT") or 0)
    if io_timeout > 0:
        options |= {"read_timeout": io_timeout, "write_timeout": io_timeout}
    return options
