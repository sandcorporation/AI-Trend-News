"""예측 문장 속 명시 날짜(#191). DB·Django를 모르는 순수 함수 — 데이터 마이그레이션도 쓴다.

뽑는 꼴(연도가 없으면 작성일 이후 처음 오는 그 날짜):
- `2026년 11월 30일`, `2026-11-30`, `2026.11.30`, `11월 3일` → 그 날
- `2026년 11월 말`·`11월 말까지`·`11월까지`·`11월 중`·`11월 안` → 그 달 말일
  (`초`는 10일, `중순`은 20일, `하순`·`말`은 말일)
- `2026년 4분기`·`4분기 말` → 분기 말일
- `2026년 말`·`2026년까지`·`2026년 안`·`연말`·`올해 말` → 그해 12월 31일

`deadline`은 문장에 나온 날짜 중 가장 늦은 것이다(「11월 3일 중간선거 전까지」면 11월 3일).
"""

import calendar
import re
from collections.abc import Callable
from datetime import date
from functools import partial

_YEAR = r"(?:(?P<year>20\d\d)\s*년\s*)?"
_FULL = re.compile(_YEAR + r"(?P<month>1[0-2]|0?[1-9])\s*월\s*(?P<day>3[01]|[12]\d|0?[1-9])\s*일")
_ISO = re.compile(
    r"(?<!\d)(?P<year>20\d\d)[-./]\s*(?P<month>1[0-2]|0?[1-9])[-./]\s*(?P<day>3[01]|[12]\d|0?[1-9])(?!\d)"
)
_MONTH = re.compile(
    _YEAR
    + r"(?P<month>1[0-2]|0?[1-9])\s*월\s*(?P<part>말|하순|중순|초|까지|중(?!간)|안|내|이내|전)"
)
_QUARTER = re.compile(_YEAR + r"(?P<quarter>[1-4])\s*분기")
_PART_DAY = {"초": 10, "중순": 20}
_YEAR_END = re.compile(r"(?P<year>20\d\d)\s*년\s*(?:말|까지|안|내|이내|중)")
_THIS_YEAR_END = re.compile(r"연말|연내|올해\s*(?:말|안|중|까지)")


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _safe(year: int, month: int, day: int) -> date | None:
    if day > calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


def _on_day(year: int, *, month: int, day: int) -> date | None:
    return _safe(year, month, day)


def _first_on_or_after(created: date, make: Callable[[int], date | None]) -> date | None:
    for year in (created.year, created.year + 1):
        found = make(year)
        if found is not None and found >= created:
            return found
    return None


def dates_in(claim: str, created: date) -> list[date]:
    """문장에 나온 명시 날짜들(나온 순서가 아니라 꼴 순서)."""
    found: list[date] = []
    taken: list[tuple[int, int]] = []

    def free(match: re.Match[str]) -> bool:
        start, end = match.span()
        return all(end <= s or start >= e for s, e in taken)

    for match in _ISO.finditer(claim):
        value = _safe(int(match["year"]), int(match["month"]), int(match["day"]))
        if value is not None:
            found.append(value)
            taken.append(match.span())
    for match in _FULL.finditer(claim):
        if not free(match):
            continue
        month, day = int(match["month"]), int(match["day"])
        if match["year"]:
            value = _safe(int(match["year"]), month, day)
        else:
            value = _first_on_or_after(created, partial(_on_day, month=month, day=day))
        if value is not None:
            found.append(value)
            taken.append(match.span())
    for match in _MONTH.finditer(claim):
        if not free(match):
            continue
        month, part = int(match["month"]), match["part"]

        def make(year: int, month: int = month, part: str = part) -> date:
            if part in _PART_DAY:
                return date(year, month, _PART_DAY[part])
            return _month_end(year, month)

        if match["year"]:
            value = make(int(match["year"]))
        else:
            # 「11월까지」는 그 날짜가 작성일 이후인 첫해 — 이번 달이면 이번 달 말.
            value = next(
                (make(y) for y in (created.year, created.year + 1) if make(y) >= created),
                make(created.year + 1),
            )
        found.append(value)
        taken.append(match.span())
    for match in _QUARTER.finditer(claim):
        if not free(match):
            continue
        last_month = int(match["quarter"]) * 3
        if match["year"]:
            found.append(_month_end(int(match["year"]), last_month))
        else:
            year = (
                created.year
                if _month_end(created.year, last_month) >= created
                else created.year + 1
            )
            found.append(_month_end(year, last_month))
        taken.append(match.span())
    for match in _YEAR_END.finditer(claim):
        if free(match):
            found.append(date(int(match["year"]), 12, 31))
            taken.append(match.span())
    if not found and _THIS_YEAR_END.search(claim):
        found.append(date(created.year, 12, 31))
    return found


def deadline(claim: str, created: date) -> date | None:
    """문장 속 가장 늦은 명시 날짜(없으면 None)."""
    found = dates_in(claim, created)
    return max(found) if found else None
