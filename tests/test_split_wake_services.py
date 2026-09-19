from __future__ import annotations

from datetime import datetime, timedelta, timezone

from xiaomi_health_sync.wake_endpoints import LocationUpdateService, UnlockProbeService
from xiaomi_health_sync.wake_report import SleepReportResult
from xiaomi_health_sync.wake_weather import ResolvedPlace


CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 14, 8, 2, tzinfo=CST)


class FakeStore:
    def __init__(self, last_sync=None) -> None:
        self.last_sync = last_sync
        self.locations: list[dict] = []
        self.unlocks: list[dict] = []

    def save_presence(self, **fields):
        self.locations.append(fields)

    def save_unlock(self, **fields):
        self.unlocks.append(fields)

    def latest_sleep_sync_at(self):
        return self.last_sync


class FakeWeather:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def resolve_place(self, latitude, longitude):
        self.calls.append((latitude, longitude))
        return ResolvedPlace("Test City", "Test District", "Test Country")


class FakeReports:
    def __init__(self, results: list[SleepReportResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def check_pending(self, *, now=None):
        self.calls += 1
        return self.results.pop(0)


class FakeSyncManager:
    def __init__(self, result=None) -> None:
        self.starts = 0
        self.result = result or {}

    def start(self, metric):
        assert metric == "sleep"
        self.starts += 1
        return {"job_id": "job-1"}

    def status(self, job_id):
        return {"status": "completed", "result": self.result}


def location_payload():
    return {
        "latitude": 12.3456,
        "longitude": 67.8901,
        "accuracy": 35,
        "location_time": NOW.timestamp(),
        "source": "automate",
    }


def unlock_payload():
    return {"event": "unlock", "device": "primary_phone", "timestamp": NOW.timestamp()}


def test_location_update_rounds_coordinates_before_external_lookup_and_logging(caplog) -> None:
    store = FakeStore()
    weather = FakeWeather()
    service = LocationUpdateService(
        store=store,
        weather=weather,
        device="primary_phone",
    )

    with caplog.at_level("INFO"):
        result = service.process(location_payload(), received_at=NOW)

    assert result.as_dict() == {"ok": True, "status": "location_updated"}
    assert weather.calls == [(12.346, 67.89)]
    assert store.locations[0]["latitude"] == 12.346
    assert store.locations[0]["longitude"] == 67.89
    assert store.locations[0]["event"] == "location_update"
    assert store.unlocks == []
    assert "Test City" not in caplog.text
    assert "Test District" not in caplog.text
    assert "Test Country" not in caplog.text


def test_unlock_saves_state_and_triggers_existing_pending_sleep_without_sync() -> None:
    store = FakeStore()
    reports = FakeReports([
        SleepReportResult(True, "report_triggered", "report-1", "morning")
    ])
    manager = FakeSyncManager()
    service = UnlockProbeService(
        store=store,
        sync_manager=manager,
        report_service=reports,
        device="primary_phone",
    )

    result = service.process(unlock_payload(), received_at=NOW)

    assert store.unlocks[0]["observed_at"] == NOW.astimezone(timezone.utc)
    assert result.status == "report_triggered"
    assert manager.starts == 0


def test_unlock_in_cooldown_records_state_without_sync() -> None:
    store = FakeStore(last_sync=NOW.astimezone(timezone.utc) - timedelta(minutes=3))
    reports = FakeReports([SleepReportResult(True, "no_new_sleep")])
    manager = FakeSyncManager()
    service = UnlockProbeService(
        store=store,
        sync_manager=manager,
        report_service=reports,
        device="primary_phone",
        sync_cooldown=timedelta(minutes=12),
    )

    result = service.process(unlock_payload(), received_at=NOW)

    assert result.status == "sync_cooldown"
    assert len(store.unlocks) == 1
    assert manager.starts == 0


def test_unlock_starts_background_sync_and_returns_without_polling() -> None:
    store = FakeStore()
    reports = FakeReports([SleepReportResult(True, "no_new_sleep")])

    class SlowManager(FakeSyncManager):
        def status(self, job_id):
            raise AssertionError("wake-probe must not wait for background sync")

    manager = SlowManager()
    service = UnlockProbeService(
        store=store,
        sync_manager=manager,
        report_service=reports,
        device="primary_phone",
    )

    result = service.process(unlock_payload(), received_at=NOW)

    assert manager.starts == 1
    assert reports.calls == 1
    assert result.status == "waiting_for_sleep_data"


def test_unlock_without_sleep_after_sync_returns_waiting() -> None:
    store = FakeStore()
    reports = FakeReports([SleepReportResult(True, "no_new_sleep")])
    service = UnlockProbeService(
        store=store,
        sync_manager=FakeSyncManager(),
        report_service=reports,
        device="primary_phone",
    )

    assert service.process(unlock_payload(), received_at=NOW).status == "waiting_for_sleep_data"
