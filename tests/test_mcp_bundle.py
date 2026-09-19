from __future__ import annotations

from xiaomi_health_sync.mcp_bundle import HealthBundleService


class FakeReader:
    def __init__(self) -> None:
        self.query_calls: list[tuple[str, str, str]] = []
        self.coverage_calls: list[tuple[str, str, str]] = []
        self.generation = 0

    def query_all(self, metric: str, *, start_at: str, end_at: str):
        self.query_calls.append((metric, start_at, end_at))
        if metric == "heart_rate":
            values = [60, 80] if self.generation == 0 else [62, 78, 70]
            return {
                "metric": metric,
                "count": len(values),
                "records": [
                    {"measured_at": f"2026-08-22T10:0{i}:00+08:00", "bpm": value}
                    for i, value in enumerate(values)
                ],
            }
        return {
            "metric": metric,
            "count": 1,
            "records": [{"measured_at": "2026-08-22T10:00:00+08:00", "percent": 99}],
        }

    def coverage(self, metric: str, *, start_at: str, end_at: str):
        self.coverage_calls.append((metric, start_at, end_at))
        if self.generation == 0 and metric == "heart_rate":
            latest = "2026-08-22T10:01:00+08:00"
        else:
            latest = end_at
        return {
            "metric": metric,
            "count": 2,
            "first_at": "2026-08-22T10:00:00+08:00",
            "latest_at": latest,
        }

    def source_check(self, metric: str):
        if self.generation == 0 and metric == "heart_rate":
            return None
        return {
            "source_checked_at": "2026-08-22T10:30:00+08:00",
            "source_check_status": "success",
        }


class SyncOnce:
    def __init__(self, reader: FakeReader) -> None:
        self.reader = reader
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.reader.generation += 1
        return {"status": "completed"}


def _queries():
    return [
        {
            "metric": "heart_rate",
            "start_at": "2026-08-22T00:00:00+08:00",
            "end_at": "2026-08-22T10:30:00+08:00",
            "mode": "summary",
        },
        {
            "metric": "spo2",
            "start_at": "2026-08-22T00:00:00+08:00",
            "end_at": "2026-08-22T10:30:00+08:00",
            "mode": "series",
        },
    ]


def test_never_reads_multiple_metrics_without_syncing() -> None:
    reader = FakeReader()
    sync = SyncOnce(reader)
    service = HealthBundleService(reader=reader, sync_runner=sync)

    result = service.get_bundle(_queries(), sync_policy="never")

    assert sync.calls == 0
    assert result["sync"]["performed"] is False
    assert [item["metric"] for item in result["results"]] == ["heart_rate", "spo2"]
    heart = result["results"][0]
    assert heart["summary"]["min"] == 60
    assert heart["summary"]["max"] == 80
    assert heart["summary"]["avg"] == 70
    assert "records" not in heart
    assert len(result["results"][1]["records"]) == 1


def test_if_needed_syncs_once_then_rereads_all_selected_metrics() -> None:
    reader = FakeReader()
    sync = SyncOnce(reader)
    service = HealthBundleService(reader=reader, sync_runner=sync)

    result = service.get_bundle(_queries(), sync_policy="if_needed")

    assert sync.calls == 1
    assert result["sync"]["performed"] is True
    assert result["sync"]["reason"] == "coverage"
    assert len(reader.query_calls) == 4
    heart = result["results"][0]
    assert heart["summary"]["latest"] == 70
    assert heart["coverage"]["status"] == "complete"


def test_always_syncs_once_even_when_coverage_is_complete() -> None:
    reader = FakeReader()
    reader.generation = 1
    sync = SyncOnce(reader)
    service = HealthBundleService(reader=reader, sync_runner=sync)

    result = service.get_bundle(_queries(), sync_policy="always")

    assert sync.calls == 1
    assert result["sync"]["performed"] is True
    assert result["sync"]["reason"] == "always"


def test_invalid_sync_policy_is_rejected() -> None:
    service = HealthBundleService(reader=FakeReader(), sync_runner=lambda: {})
    try:
        service.get_bundle(_queries(), sync_policy="sometimes")
    except ValueError as exc:
        assert "sync_policy" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_steps_summary_prefers_watch_when_same_minute_exists() -> None:
    records = [
        {"measured_at": "2026-08-24T09:21:00+08:00", "sid": "hlth.gen_phone", "metrics": {"steps": 77, "distance": 46, "calories": 3.08}},
        {"measured_at": "2026-08-24T09:21:00+08:00", "sid": "1234567890", "metrics": {"steps": 81, "distance": 44, "calories": 2}},
        {"measured_at": "2026-08-24T10:42:00+08:00", "sid": "hlth.gen_phone", "metrics": {"steps": 21, "distance": 12, "calories": 0.84}},
    ]
    summary = HealthBundleService._summary("steps", records)
    assert summary["steps"] == 102
    assert summary["distance_m"] == 56
    assert summary["source_resolution"]["duplicates_removed"] == 1
    assert summary["source_resolution"]["policy"] == "watch_over_phone_same_timestamp"


def test_stress_summary_reads_numeric_value_from_metrics() -> None:
    records = [
        {"measured_at": "2026-08-24T15:20:00+08:00", "metrics": {"stress": 35}},
        {"measured_at": "2026-08-24T15:30:00+08:00", "metrics": {"stress": 34}},
        {"measured_at": "2026-08-24T15:40:00+08:00", "metrics": {"stress": 26}},
    ]
    summary = HealthBundleService._summary("stress", records)
    assert summary["latest"] == 26
    assert summary["min"] == 26
    assert summary["max"] == 35
    assert summary["avg"] == 95 / 3


def test_sleep_summary_collapses_incomplete_versions_and_totals_sessions() -> None:
    records = [
        {
            "start_at": "2026-08-23T16:34:00+00:00",
            "end_at": "2026-08-23T18:40:00+00:00",
            "raw": {"metrics": {"total_sleep_seconds": 126, "is_incomplete": True}},
        },
        {
            "start_at": "2026-08-23T16:34:00+00:00",
            "end_at": "2026-08-23T22:46:00+00:00",
            "avg_hr": 64,
            "avg_spo2": 98,
            "raw": {"metrics": {"total_sleep_seconds": 366, "deep_sleep_seconds": 140, "light_sleep_seconds": 119, "rem_sleep_seconds": 107, "is_incomplete": False}},
        },
        {
            "start_at": "2026-08-23T07:41:00+00:00",
            "end_at": "2026-08-23T08:15:00+00:00",
            "raw": {"metrics": {"total_sleep_seconds": 34, "is_incomplete": False}},
        },
    ]
    summary = HealthBundleService._summary("sleep", records)
    assert summary["session_count"] == 2
    assert summary["total_sleep_minutes"] == 400
    assert summary["incomplete_session_count"] == 0
    assert summary["latest_session"]["avg_hr"] == 64


def test_temperature_trend_summary_exposes_baseline_and_delta_statistics() -> None:
    records = [
        {"measured_at": "2026-08-20T00:02:34+00:00", "metrics": {"baseline_c": 35.5, "base_temperature_c": 35.8, "temperature_delta_c": 0.3, "status_code": 0}},
        {"measured_at": "2026-08-20T23:03:34+00:00", "metrics": {"baseline_c": 35.6, "base_temperature_c": 36.0, "temperature_delta_c": 0.4, "status_code": 0}},
        {"measured_at": "2026-08-23T23:01:42+00:00", "metrics": {"baseline_c": 35.7, "base_temperature_c": 35.5, "temperature_delta_c": -0.1, "status_code": 0}},
    ]
    summary = HealthBundleService._summary("temperature_trend", records)
    assert summary["latest_baseline_c"] == 35.7
    assert summary["latest_base_temperature_c"] == 35.5
    assert summary["latest_delta_c"] == -0.1
    assert summary["delta_min_c"] == -0.1
    assert summary["delta_max_c"] == 0.4
    assert summary["status_code"] == 0


def test_single_temperature_summary_uses_skin_temperature() -> None:
    records = [
        {"measured_at": "2026-04-01T02:06:33+00:00", "metrics": {"skin_temperature_c": 36.72}},
        {"measured_at": "2026-04-02T01:57:29+00:00", "metrics": {"skin_temperature_c": 36.75}},
    ]
    summary = HealthBundleService._summary("single_temperature", records)
    assert summary["latest"] == 36.75
    assert summary["min"] == 36.72
    assert summary["max"] == 36.75
    assert summary["field"] == "skin_temperature_c"


def test_temperature_characteristic_summary_does_not_invent_semantics() -> None:
    records = [
        {"measured_at": "2026-08-23T08:21:08+00:00", "metrics": {"measurement_time": 1787473268}},
        {"measured_at": "2026-08-23T23:01:42+00:00", "metrics": {"measurement_time": 1787526102}},
    ]
    summary = HealthBundleService._summary("temperature_characteristic", records)
    assert summary["count"] == 2
    assert summary["latest_record"] == records[-1]
    assert "latest" not in summary


def test_abnormal_heartbeat_summary_reports_events_without_diagnosis() -> None:
    records = [
        {
            "measured_at": "2026-08-28T18:16:16+00:00",
            "metrics": {"start_time": 1787940976, "end_time": 1787941140},
        },
        {
            "measured_at": "2026-08-28T20:00:00+00:00",
            "metrics": {"start_time": 1787947200, "end_time": 1787947260},
        },
    ]

    summary = HealthBundleService._summary("abnormal_heart_beat", records)

    assert summary["event_count"] == 2
    assert summary["total_event_seconds"] == 224
    assert summary["latest_event"] == records[-1]
    rendered = str(summary).lower()
    assert "arrhythm" not in rendered
    assert "diagnos" not in rendered


def test_intensity_summary_names_qualifying_minutes() -> None:
    records = [
        {"measured_at": "2026-09-15T10:00:00+08:00"},
        {"measured_at": "2026-09-15T10:01:00+08:00"},
    ]

    summary = HealthBundleService._summary("intensity", records)

    assert summary["active_minutes"] == 2


def test_workout_summary_totals_daily_activity() -> None:
    records = [
        {
            "start_at": "2026-09-15T10:00:00+08:00",
            "duration_s": 1800,
            "distance_m": 3200,
            "calories_kcal": 220,
        },
        {
            "start_at": "2026-09-15T18:00:00+08:00",
            "duration_s": 900,
            "distance_m": 1000,
            "calories_kcal": 80,
        },
    ]

    summary = HealthBundleService._summary("workout", records)

    assert summary["workout_count"] == 2
    assert summary["total_duration_minutes"] == 45
    assert summary["total_distance_m"] == 4200
    assert summary["total_calories_kcal"] == 300
    assert summary["latest_workout"] == records[-1]
