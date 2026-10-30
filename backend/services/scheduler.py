"""스케줄러가 매시간 차례로 부르는 관리 명령 목록과 한 주기 실행기.

새 명령(재전송, 경보 점검 등)은 `COMMANDS`에 이름만 더한다. 한 명령이 실패해도 다음 명령을 부른다.
"""

import logging
from collections.abc import Callable

from django.core.management import call_command

logger = logging.getLogger("scheduler")

# 실행 순서. 한 곳에서만 고친다.
COMMANDS: tuple[str, ...] = (
    "collect",
    "prune_items",
    "check_alerts",
    "resend_inquiries",
    "run_editor",
)

CommandCaller = Callable[[str], object]


def run_cycle(
    commands: tuple[str, ...] = COMMANDS, call: CommandCaller = call_command
) -> dict[str, bool]:
    """명령을 차례로 부른다. 이름 → 성공 여부를 돌려준다. 예외는 기록만 하고 계속한다."""
    results: dict[str, bool] = {}
    for name in commands:
        logger.info("명령 시작: %s", name)
        try:
            call(name)
        except Exception:  # noqa: BLE001 - 한 명령의 어떤 실패도 다음 명령을 막지 않는다
            logger.exception("명령 실패: %s", name)
            results[name] = False
        else:
            logger.info("명령 끝: %s", name)
            results[name] = True
    return results


def seconds_until_next(now: float, interval: int, offset: int) -> float:
    """다음 실행까지 남은 초. 실행 시각은 벽시계 `interval` 경계에서 `offset`초 뒤다.

    epoch 초 기준이라 KST(+9시간)에서도 매시 분이 같다 — interval 3600, offset 1500이면 매시 25분.
    지금이 바로 실행 시각이면 다음 주기까지 기다린다.
    """
    wait = (offset - now) % interval
    return wait if wait > 0 else float(interval)
