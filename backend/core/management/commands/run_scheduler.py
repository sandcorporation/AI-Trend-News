import logging
import time
from typing import Any

from django.core.management.base import BaseCommand

from services.scheduler import run_cycle


class Command(BaseCommand):
    help = "스케줄러: 시작 즉시 한 번, 이후 매 주기 COMMANDS를 차례로 실행한다."

    def add_arguments(self, parser: Any) -> None:  # noqa: ANN401 - argparse
        parser.add_argument("--interval", type=int, default=3600, help="주기(초). 기본 3600")

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        interval = int(options["interval"])
        while True:
            started = time.monotonic()
            results = run_cycle()
            failed = [n for n, ok in results.items() if not ok]
            logging.getLogger("scheduler").info("주기 끝: 실패 %s", failed or "없음")
            time.sleep(max(1.0, interval - (time.monotonic() - started)))
