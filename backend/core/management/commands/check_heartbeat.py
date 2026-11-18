import sys
from typing import Any

from django.core.management.base import BaseCommand

from services.heartbeat import is_alive


class Command(BaseCommand):
    help = "생존 신호가 90초 안이면 종료 코드 0, 아니면 1(compose healthcheck용)."

    def add_arguments(self, parser: Any) -> None:  # noqa: ANN401 - argparse
        parser.add_argument("name")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        sys.exit(0 if is_alive(options["name"]) else 1)
