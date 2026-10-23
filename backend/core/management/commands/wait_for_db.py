import time
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import OperationalError, connection


class Command(BaseCommand):
    help = "DB(MySQL)에 접속될 때까지 기다린다. 배포가 migrate 전에 부른다."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--timeout", type=float, default=120.0, help="최대 대기(초). 기본 120")
        parser.add_argument("--interval", type=float, default=2.0, help="재시도 간격(초). 기본 2")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        deadline = time.monotonic() + float(options["timeout"])
        attempt = 0
        while True:
            attempt += 1
            try:
                connection.ensure_connection()
            except OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise CommandError(f"DB에 접속하지 못했다({attempt}회): {exc}") from exc
                time.sleep(float(options["interval"]))
                continue
            self.stdout.write(f"DB 접속됨 ({attempt}회째)")
            return
