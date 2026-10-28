from datetime import UTC, datetime

from services.scheduler import seconds_until_next

HOUR = 3600
AT_25 = 1500  # 매시 25분


def _ts(hour: int, minute: int, second: int = 0) -> float:
    return datetime(2026, 10, 28, hour, minute, second, tzinfo=UTC).timestamp()


def test_waits_until_offset_in_the_same_hour() -> None:
    assert seconds_until_next(_ts(9, 10), HOUR, AT_25) == 15 * 60


def test_after_offset_waits_until_next_hour() -> None:
    assert seconds_until_next(_ts(9, 30), HOUR, AT_25) == 55 * 60


def test_exactly_on_offset_waits_a_full_interval() -> None:
    assert seconds_until_next(_ts(9, 25), HOUR, AT_25) == HOUR


def test_fractional_now_keeps_alignment() -> None:
    assert seconds_until_next(_ts(9, 24, 59) + 0.5, HOUR, AT_25) == 0.5
