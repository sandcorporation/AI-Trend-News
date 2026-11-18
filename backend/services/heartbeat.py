"""워커 생존 신호(#184): DB 행 한 줄을 15초마다 갱신하고, 90초 넘게 묵으면 죽은 것으로 본다."""

import logging
import threading
from datetime import timedelta

from django.db import close_old_connections

from adapters.clock import get_clock
from core.models import Heartbeat

BEAT_INTERVAL_SECONDS = 15
STALE_AFTER_SECONDS = 90

logger = logging.getLogger("heartbeat")


def beat(name: str) -> None:
    Heartbeat.objects.update_or_create(name=name, defaults={"beat_at": get_clock().now()})


def is_alive(name: str) -> bool:
    """신호가 없거나 `STALE_AFTER_SECONDS`보다 묵었으면 False."""
    beat_at = Heartbeat.objects.filter(name=name).values_list("beat_at", flat=True).first()
    if beat_at is None:
        return False
    return get_clock().now() - beat_at <= timedelta(seconds=STALE_AFTER_SECONDS)


def start_beating(name: str, stop: threading.Event | None = None) -> threading.Thread:
    """데몬 스레드로 신호를 이어 준다 — 명령이 오래 걸려도(편집자 배치 대기) 끊기지 않는다."""
    stop_event = stop or threading.Event()

    def loop() -> None:
        while not stop_event.is_set():
            try:
                close_old_connections()
                beat(name)
            except Exception:  # noqa: BLE001 - 한 번의 실패로 신호 스레드가 죽으면 안 된다
                logger.warning("생존 신호 갱신 실패: %s", name)
            stop_event.wait(BEAT_INTERVAL_SECONDS)

    thread = threading.Thread(target=loop, name=f"heartbeat-{name}", daemon=True)
    thread.start()
    return thread
