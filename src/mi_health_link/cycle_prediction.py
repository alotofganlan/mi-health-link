from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
_BOUNDARY_TYPES = {"period_start", "period_end"}


def _local_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI).date()


def _parse_cycles(records: Sequence[Mapping[str, Any]]) -> tuple[list[date], list[int]]:
    boundaries: set[tuple[str, date]] = set()
    for record in records:
        kind = record.get("record_type")
        day = _local_date(record.get("start_at"))
        if kind in _BOUNDARY_TYPES and day is not None:
            boundaries.add((str(kind), day))

    starts = sorted(day for kind, day in boundaries if kind == "period_start")
    ends = sorted(day for kind, day in boundaries if kind == "period_end")
    durations: list[int] = []
    for index, start in enumerate(starts):
        next_start = starts[index + 1] if index + 1 < len(starts) else None
        matching_end = next(
            (
                end
                for end in ends
                if end >= start and (next_start is None or end < next_start)
            ),
            None,
        )
        if matching_end is not None:
            durations.append((matching_end - start).days + 1)
    return starts, durations


def _xiaomi_value(values: Sequence[int]) -> int:
    a, b, c = values[-3:]
    if a == b or a == c:
        return a
    if b == c:
        return b
    return (a + b + c) // 3


def predict_xiaomi_cycle(
    records: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    starts, completed_durations = _parse_cycles(records)
    if len(starts) < 4 or len(completed_durations) < 3:
        return {
            "engine": "xiaomi_calendar_compatible",
            "available": False,
            "reason": "insufficient_history",
            "start_count": len(starts),
        }

    cycle_days = _xiaomi_value(
        [(right - left).days for left, right in pairwise(starts[-4:])]
    )
    period_days = _xiaomi_value(completed_durations[-3:])
    period_start = starts[-1] + timedelta(days=cycle_days)
    period_end = period_start + timedelta(days=period_days - 1)
    days_until_period = (period_start - as_of).days

    if 12 <= cycle_days <= 19:
        ovulation_index = 6
    elif 20 <= cycle_days <= 59:
        ovulation_index = cycle_days - 13
    else:
        ovulation_index = None
    ovulation = (
        period_start + timedelta(days=ovulation_index - 1)
        if ovulation_index is not None
        else None
    )

    return {
        "engine": "xiaomi_calendar_compatible",
        "compatible_apk_version": "3.58.0",
        "available": True,
        "cycle_days": cycle_days,
        "period_days": period_days,
        "predicted_period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
        },
        "days_until_period": days_until_period,
        "reminder": {
            "active": 0 <= days_until_period <= 7,
            "lead_days": 7,
        },
        "ovulation_date": ovulation.isoformat() if ovulation else None,
        "fertile_window": (
            {
                "start": (ovulation - timedelta(days=5)).isoformat(),
                "end": (ovulation + timedelta(days=4)).isoformat(),
            }
            if ovulation
            else None
        ),
        "notice": "日历估计，仅供健康记录参考，不用于避孕或诊断。",
    }
