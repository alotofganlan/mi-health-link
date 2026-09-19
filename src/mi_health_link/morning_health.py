from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .cycle_prediction import _parse_cycles, predict_xiaomi_cycle
from .mcp_bundle import HealthBundleService
from .mcp_coverage import classify_coverage, ranges_cover_window
from .mcp_health_data import METRIC_SOURCES


MORNING_METRICS = tuple(sorted(set(METRIC_SOURCES) - {"sleep", "menstruation"}))
ZERO_EVENT_METRICS = frozenset({
    "abnormal_heart_beat",
    "intensity",
    "valid_stand",
    "workout",
})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _parse_local_day(value: Any, timezone: ZoneInfo) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone).date()


def _months_before(day: date, months: int) -> date:
    absolute_month = day.year * 12 + day.month - 1 - months
    year, month_index = divmod(absolute_month, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _stats(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "median": None, "range": None}
    return {
        "count": len(values),
        "median": float(median(values)),
        "range": {"min": min(values), "max": max(values)},
    }


def _cycle_history(
    records: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
) -> dict[str, Any]:
    starts, durations = _parse_cycles(records)
    intervals = [(right, (right - left).days) for left, right in pairwise(starts)]

    def summarize(cutoff: date | None) -> dict[str, Any]:
        cycle_values = [length for right, length in intervals if cutoff is None or right >= cutoff]
        period_values = [
            duration
            for start, duration in zip(starts, durations)
            if cutoff is None or start >= cutoff
        ]
        selected_boundaries = [
            row
            for row in records
            if (day := _parse_local_day(row.get("start_at"), ZoneInfo("Asia/Shanghai")))
            is not None
            and (cutoff is None or day >= cutoff)
            and day <= as_of
        ]
        return {
            "cycle_count": len(cycle_values),
            "cycle_length_days": _stats(cycle_values),
            "period_duration_days": _stats(period_values),
            "record_coverage": {
                "boundary_count": len(selected_boundaries),
                "start": cutoff.isoformat() if cutoff else (starts[0].isoformat() if starts else None),
                "end": as_of.isoformat(),
            },
        }

    return {
        "6_months": summarize(_months_before(as_of, 6)),
        "12_months": summarize(_months_before(as_of, 12)),
        "all": summarize(None),
    }


def _trend_scalar(metric: str, summary: Mapping[str, Any]) -> tuple[str, float] | None:
    candidates: dict[str, tuple[str, ...]] = {
        "steps": ("steps",),
        "calories": ("calories_kcal",),
        "heart_rate": ("avg",),
        "resting_heart_rate": ("avg", "latest"),
        "spo2": ("avg",),
        "stress": ("avg",),
        "single_temperature": ("avg", "latest"),
        "temperature_trend": ("latest_delta_c",),
        "training_load": ("avg", "latest"),
        "pai": ("avg", "latest"),
        "running_ability_index": ("avg", "latest"),
        "valid_stand": ("stand_period_count",),
        "vitality": ("avg", "latest"),
        "weight": ("avg", "latest"),
        "diet": ("total_calories_kcal",),
        "glucose": ("avg", "latest"),
    }
    for field in candidates.get(metric, ()):
        value = summary.get(field)
        if _is_number(value):
            return field, float(value)
    return None


class MorningHealthService:
    def __init__(self, reader: Any, timezone: ZoneInfo) -> None:
        self.reader = reader
        self.timezone = timezone

    def _coverage(self, metric: str, start_at: str, end_at: str) -> dict[str, Any]:
        local = self.reader.coverage(metric, start_at=start_at, end_at=end_at)
        if isinstance(local, dict) and isinstance(local.get("status"), str):
            return {"start_at": start_at, "end_at": end_at, **local}

        source_checked_at = None
        sync_failed = False
        if hasattr(self.reader, "source_check"):
            source_check = self.reader.source_check(metric)
            if isinstance(source_check, dict):
                checked = source_check.get("source_checked_at")
                source_checked_at = checked if isinstance(checked, str) else None
                sync_failed = source_check.get("source_check_status") == "failed"
        checks: list[dict[str, Any]] = []
        if hasattr(self.reader, "range_checks"):
            loaded = self.reader.range_checks(metric, start_at=start_at, end_at=end_at)
            if isinstance(loaded, list):
                checks = [dict(row) for row in loaded if isinstance(row, dict)]
        result = classify_coverage(
            metric=metric,
            start_at=start_at,
            end_at=end_at,
            count=int(local.get("count") or 0),
            first_at=local.get("first_at") if isinstance(local.get("first_at"), str) else None,
            latest_at=local.get("latest_at") if isinstance(local.get("latest_at"), str) else None,
            source_checked_at=source_checked_at,
            sync_failed=sync_failed,
            range_checked_complete=ranges_cover_window(checks, start_at, end_at),
        )
        if checks:
            result["checked_ranges"] = checks
        return result

    def _daily_summaries(
        self,
        metric: str,
        records: Sequence[Mapping[str, Any]],
    ) -> dict[date, dict[str, Any]]:
        time_column = METRIC_SOURCES[metric].time_column
        grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            day = _parse_local_day(record.get(time_column), self.timezone)
            if day is not None:
                grouped[day].append(dict(record))
        return {
            day: HealthBundleService._summary(metric, rows)
            for day, rows in grouped.items()
        }

    @staticmethod
    def _trend(
        metric: str,
        daily: Mapping[date, Mapping[str, Any]],
        target_day: date,
    ) -> dict[str, Any] | None:
        target = _trend_scalar(metric, daily.get(target_day, {}))
        if target is None:
            return None
        field, target_value = target
        windows: dict[str, Any] = {}
        for label, days in (("7d", 7), ("30d", 30)):
            first = target_day - timedelta(days=days - 1)
            values = [
                scalar[1]
                for day, summary in daily.items()
                if first <= day <= target_day
                and (scalar := _trend_scalar(metric, summary)) is not None
            ]
            if len(values) < 3:
                windows[label] = {
                    "status": "insufficient_samples",
                    "valid_days": len(values),
                }
                continue
            middle = float(median(values))
            delta = target_value - middle
            windows[label] = {
                "status": "available",
                "valid_days": len(values),
                "median": middle,
                "delta": delta,
                "direction": "above" if delta > 0 else "below" if delta < 0 else "same",
            }
        return {"target": target_value, "field": field, "windows": windows}

    def build(self, health_date: date) -> dict[str, Any]:
        target_start = datetime.combine(
            health_date,
            time.min,
            tzinfo=self.timezone,
        ).isoformat()
        window_start = datetime.combine(
            health_date - timedelta(days=29),
            time.min,
            tzinfo=self.timezone,
        ).isoformat()
        window_end = datetime.combine(
            health_date,
            time.max,
            tzinfo=self.timezone,
        ).isoformat()
        health: dict[str, Any] = {}
        trends: dict[str, Any] = {}
        coverage: dict[str, Any] = {}
        warnings: list[dict[str, str]] = []

        for metric in MORNING_METRICS:
            payload = self.reader.query_all(
                metric,
                start_at=target_start,
                end_at=window_end,
            )
            rows = payload.get("records") if isinstance(payload, dict) else []
            records = [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            metric_coverage = self._coverage(metric, target_start, window_end)
            coverage[metric] = metric_coverage
            status = metric_coverage.get("status")
            if status not in {"complete", "source_empty"}:
                warnings.append({"metric": metric, "status": str(status or "unknown")})

            target_daily = self._daily_summaries(metric, records)
            target_summary = target_daily.get(health_date)
            if target_summary is None:
                if (
                    metric in ZERO_EVENT_METRICS
                    and metric_coverage.get("range_checked_complete") is True
                    and status in {"complete", "source_empty"}
                ):
                    health[metric] = {
                        "status": "available",
                        "summary": HealthBundleService._summary(metric, []),
                    }
                    continue
                health[metric] = {"status": "missing"}
                continue
            health[metric] = {"status": "available", "summary": target_summary}

            if not _trend_scalar(metric, target_summary):
                continue
            if metric == "heart_rate" and hasattr(self.reader, "query_daily_summaries"):
                daily_payload = self.reader.query_daily_summaries(
                    metric,
                    start_at=window_start,
                    end_at=window_end,
                    timezone=self.timezone.key,
                )
                daily = {
                    day: dict(summary)
                    for item in daily_payload.get("days", [])
                    if isinstance(item, dict)
                    and (day := _parse_local_day(item.get("day"), self.timezone))
                    is not None
                    and isinstance((summary := item.get("summary")), dict)
                }
            else:
                history_payload = self.reader.query_all(
                    metric,
                    start_at=window_start,
                    end_at=window_end,
                )
                history_rows = (
                    history_payload.get("records")
                    if isinstance(history_payload, dict)
                    else []
                )
                history_records = (
                    [dict(row) for row in history_rows if isinstance(row, dict)]
                    if isinstance(history_rows, list)
                    else []
                )
                daily = self._daily_summaries(metric, history_records)
            trend = self._trend(metric, daily, health_date)
            if trend is not None:
                trends[metric] = trend

        cycle_start = datetime.combine(date(1970, 1, 1), time.min, tzinfo=self.timezone).isoformat()
        cycle_payload = self.reader.query_all(
            "menstruation",
            start_at=cycle_start,
            end_at=window_end,
        )
        cycle_rows = cycle_payload.get("records") if isinstance(cycle_payload, dict) else []
        menstrual_records = (
            [dict(row) for row in cycle_rows if isinstance(row, dict)]
            if isinstance(cycle_rows, list)
            else []
        )
        coverage["menstruation"] = self._coverage("menstruation", cycle_start, window_end)
        as_of = health_date + timedelta(days=1)
        cycle = predict_xiaomi_cycle(menstrual_records, as_of=as_of)
        cycle["history"] = _cycle_history(menstrual_records, as_of=as_of)

        return {
            "health": health,
            "trends": trends,
            "cycle": cycle,
            "data_coverage": coverage,
            "warnings": warnings,
        }
