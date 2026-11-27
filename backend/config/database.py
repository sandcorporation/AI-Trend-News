"""MySQL 접속 옵션의 시간 제한(#228).

제한이 없으면 응답 없는 DB를 gunicorn `--timeout 30`보다 오래 기다려, 워커가 죽고 응답이 비었다.
"""

# 새 접속(handshake까지). 3초: web healthcheck·배포 스모크의 5초 안에 503이 나간다.
# 남아 있던 연결은 ping의 읽기 제한(web 기본 5초) 뒤 다시 접속(3초)해서 약 8초가 걸린다.
CONNECT_TIMEOUT_SECONDS = 3

# 읽기·쓰기 제한(초, 0이면 없음). web 프로세스만 `config/wsgi.py`가 settings를 읽기 전에 정한다.
# 환경 변수를 여기서 읽지 않아 migrate의 긴 DDL이나 scheduler 작업은 .env 값에도 끊기지 않는다.
web_io_timeout = 0


def mysql_options(io_timeout: int) -> dict[str, str | int]:
    """남아 있던 연결은 접속 제한만으로는 풀리지 않는다(ping·쿼리의 읽기가 매달린다)."""
    options: dict[str, str | int] = {
        "charset": "utf8mb4",
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
    }
    if io_timeout > 0:
        options |= {"read_timeout": io_timeout, "write_timeout": io_timeout}
    return options
