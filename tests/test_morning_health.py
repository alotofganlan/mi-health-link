from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from mi_health_link.mcp_health_data import METRIC_SOURCES
from mi_health_link.morning_health import MORNING_METRICS, MorningHealthService


def _boundary(kind: str, day: str) -> dict[str, str]:
    return {"record_type": kind, "start_at": f"{day}T00:00:00+08:00"}


class FakeReader:
    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.queries: list[tuple[str, str, str]] = []
        self.daily_queries: list[tuple[str, str, str, str]] = []

    def query_all(self, metric: str, *, start_at: str, end_at: str):
        self.queries.append((metric, start_at, end_at))
        if metric in self.missing:
            records = []
        elif metric == "steps":
            first = date(2026, 8, 16)
            records = []
            for offset in range(30):
                day = first + timedelta(days=offset)
                steps = 8123 if day == date(2026, 9, 14) else 7000 + offset * 20
                records.append(
                    {
                        "measured_at": f"{day.isoformat()}T12:00:00+08:00",
                        "sid": "1234567890",
                        "metrics": {"steps": steps},
                    }
                )
        elif metric == "heart_rate":
            records = [
                {
                    "measured_at": "2026-09-14T10:00:00+08:00",
                    "bpm": 62,
                },
                {
                    "measured_at": "2026-09-14T11:00:00+08:00",
                    "bpm": 78,
                },
            ]
        elif metric == "menstruation":
            records = [
                _boundary("period_start", "2026-05-26"),
                _boundary("period_end", "2026-05-30"),
                _boundary("period_start", "2026-06-21"),
                _boundary("period_end", "2026-06-25"),
                _boundary("period_start", "2026-07-21"),
                _boundary("period_end", "2026-07-25"),
                _boundary("period_start", "2026-08-19"),
                _boundary("period_end", "2026-08-23"),
            ]
        else:
            records = []
        return {"metric": metric, "count": len(records), "records": records}

    def query_daily_summaries(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
        timezone: str,
    ):
        self.daily_queries.append((metric, start_at, end_at, timezone))
        assert metric == "heart_rate"
        first = date(2026, 8, 16)
        return {
            "metric": metric,
            "days": [
                {
                    "day": (first + timedelta(days=offset)).isoformat(),
                    "summary": {
                        "count": 1200,
                        "field": "bpm",
                        "avg": 65.0 + offset / 10,
                        "min": 50,
                        "max": 120,
                    },
                }
                for offset in range(30)
            ],
        }

    def coverage(self, metric: str, *, start_at: str, end_at: str):
        if metric in self.missing or metric not in {"steps", "heart_rate", "menstruation"}:
            return {
                "metric": metric,
                "status": "source_empty",
                "count": 0,
                "first_at": None,
                "latest_at": None,
            }
        return {
            "metric": metric,
            "status": "complete",
            "count": 30 if metric == "steps" else 2,
            "first_at": start_at,
            "latest_at": end_at,
        }


def test_build_returns_yesterday_health_trends_cycle_and_coverage():
    reader = FakeReader()
    service = MorningHealthService(reader, ZoneInfo("Asia/Shanghai"))

    result = service.build(date(2026, 9, 14))

    assert result["health"]["steps"]["summary"]["steps"] == 8123
    assert result["health"]["heart_rate"]["summary"]["avg"] == 70
    assert result["trends"]["steps"]["windows"]["7d"]["valid_days"] == 7
    assert result["trends"]["steps"]["windows"]["30d"]["valid_days"] == 30
    assert result["trends"]["heart_rate"]["windows"]["30d"]["valid_days"] == 30
    assert result["cycle"]["predicted_period"]["start"] == "2026-09-16"
    assert result["cycle"]["days_until_period"] == 1
    assert result["cycle"]["history"]["6_months"]["cycle_count"] > 0
    assert result["cycle"]["history"]["12_months"]["cycle_count"] > 0
    assert result["cycle"]["history"]["all"]["cycle_count"] >= 3
    assert result["data_coverage"]["steps"]["status"] == "complete"


def test_morning_metrics_cover_every_normalized_metric_or_top_level_section():
    assert set(MORNING_METRICS) == set(METRIC_SOURCES) - {"sleep", "menstruation"}
    assert "abnormal_heart_beat" in MORNING_METRICS
    assert "vitality" in MORNING_METRICS


def test_missing_metric_is_not_zero():
    service = MorningHealthService(
        FakeReader(missing={"spo2"}),
        ZoneInfo("Asia/Shanghai"),
    )

    result = service.build(date(2026, 9, 14))

    assert result["health"]["spo2"] == {"status": "missing"}
    assert result["data_coverage"]["spo2"]["status"] == "source_empty"
    assert "spo2" not in result["trends"]


def test_complete_event_coverage_distinguishes_zero_events_from_missing():
    class CompleteEventReader(FakeReader):
        def coverage(self, metric: str, *, start_at: str, end_at: str):
            result = super().coverage(metric, start_at=start_at, end_at=end_at)
            if metric in {"abnormal_heart_beat", "intensity", "valid_stand", "workout"}:
                return {
                    **result,
                    "status": "source_empty",
                    "range_checked_complete": True,
                }
            return result

    result = MorningHealthService(
        CompleteEventReader(),
        ZoneInfo("Asia/Shanghai"),
    ).build(date(2026, 9, 14))

    assert result["health"]["abnormal_heart_beat"]["summary"]["event_count"] == 0
    assert result["health"]["intensity"]["summary"]["active_minutes"] == 0
    assert result["health"]["valid_stand"]["summary"]["stand_period_count"] == 0
    assert result["health"]["workout"]["summary"]["workout_count"] == 0


def test_windows_use_shanghai_days_and_menstruation_uses_all_history():
    reader = FakeReader()

    MorningHealthService(reader, ZoneInfo("Asia/Shanghai")).build(date(2026, 9, 14))

    steps_queries = [query for query in reader.queries if query[0] == "steps"]
    assert ("steps", "2026-09-14T00:00:00+08:00", "2026-09-14T23:59:59.999999+08:00") in steps_queries
    assert ("steps", "2026-08-16T00:00:00+08:00", "2026-09-14T23:59:59.999999+08:00") in steps_queries
    assert reader.daily_queries == [
        (
            "heart_rate",
            "2026-08-16T00:00:00+08:00",
            "2026-09-14T23:59:59.999999+08:00",
            "Asia/Shanghai",
        )
    ]
    menstruation_query = next(query for query in reader.queries if query[0] == "menstruation")
    assert menstruation_query[1] == "1970-01-01T00:00:00+08:00"
