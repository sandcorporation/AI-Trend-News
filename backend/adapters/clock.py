"""시계 어댑터: 토큰 만료·JWKS 캐시 판정이 읽는 현재 시각. 시험은 고정 시계를 끼운다."""

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_clock: Clock = SystemClock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    """등록 지점. 이전 시계를 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _clock
    previous, _clock = _clock, clock
    return previous


# 기다림도 어댑터로: 배치 대기(30초 간격)가 시험에서는 시계만 넘기고 실제로 자지 않게 한다.
_sleeper: Callable[[float], None] = time.sleep


def sleep(seconds: float) -> None:
    _sleeper(seconds)


def set_sleeper(sleeper: Callable[[float], None]) -> Callable[[float], None]:
    """등록 지점. 이전 값을 돌려줘 시험이 되돌릴 수 있게 한다."""
    global _sleeper
    previous, _sleeper = _sleeper, sleeper
    return previous
