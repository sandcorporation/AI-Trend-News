import logging
import time
from typing import Any

from django.core.management.base import BaseCommand

from services.scheduler import run_cycle, seconds_until_next


class Command(BaseCommand):
    help = "스케줄러: 시작 즉시 한 번, 이후 벽시계 주기 경계 + offset초마다 COMMANDS를 실행한다."

    def add_arguments(self, parser: Any) -> None:  # noqa: ANN401 - argparse
        parser.add_argument("--interval", type=int, default=3600, help="주기(초). 기본 3600")
        parser.add_argument(
            "--offset", type=int, default=1500, help="주기 경계에서 미룰 초. 기본 1500(매시 25분)"
        )

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: ANN401 - Django 명령 서명
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        interval = int(options["interval"])
        offset = int(options["offset"]) % interval
        while True:
            results = run_cycle()
            failed = [n for n, ok in results.items() if not ok]
            logging.getLogger("scheduler").info("주기 끝: 실패 %s", failed or "없음")
            time.sleep(seconds_until_next(time.time(), interval, offset))
