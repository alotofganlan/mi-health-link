from __future__ import annotations

from datetime import datetime, timezone
import json

from xiaomi_health_sync.wake_context import WakeContextService
from xiaomi_health_sync.wake_store import Presence
from xiaomi_health_sync.wake_weather import WeatherContext


class FakeStore:
    def __init__(
        self,
        presence: Presence | None,
        *,
        snapshot: dict | None = None,
        report_kind: str = "morning",
        includes_yesterday: bool = True,
    ) -> None:
        self.presence = presence
        self.snapshot = snapshot
        self.report_kind = report_kind
        self.includes_yesterday = includes_yesterday
        self.saved: list[dict] = []

    def get_context_snapshot(self, report_id: str):
        if self.snapshot is None:
            return None
        return {"payload": self.snapshot}

    def get_delivery(self, report_id: str):
        assert report_id == "report-123"
        return {
            "id": report_id,
            "report_date": "2026-09-14",
            "report_kind": self.report_kind,
            "includes_yesterday_health": self.includes_yesterday,
            "sleep_snapshot": {
                "source_record_id": "sleep-main",
                "sleep_day": "2026-09-14",
                "start_at": "2026-09-13T23:50:00+08:00",
                "wake_at": "2026-09-14T06:00:00+08:00",
                "total_minutes": 351,
                "stage_minutes": {"deep_sleep": 70},
            },
            "presence_id": 41 if self.presence else None,
            "status": "completed",
        }

    def get_presence(self, presence_id: int):
        assert presence_id == 41
        return self.presence

    def save_context_snapshot(self, report_id: str, **kwargs):
        row = {"report_id": report_id, **kwargs}
        self.saved.append(row)
        return {"payload": kwargs["payload"]}


class FakeWeather:
    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []

    def fetch_weather(self, latitude: float, longitude: float) -> WeatherContext:
        self.calls.append((latitude, longitude))
        return WeatherContext(
            available=True,
            timezone="Asia/Shanghai",
            current={"temperature_c": 28.4, "weather_code": 2},
            daily={"temperature_max_c": 33, "temperature_min_c": 25},
        )


class FakeMorningHealth:
    def __init__(self, *, fail_on_call: bool = False) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[object] = []

    def build(self, health_date):
        if self.fail_on_call:
            raise AssertionError("health must not be rebuilt")
        self.calls.append(health_date)
        return {
            "health": {"steps": {"status": "available", "summary": {"steps": 8123}}},
            "trends": {
                "steps": {
                    "target": 8123,
                    "windows": {"7d": {"valid_days": 7}, "30d": {"valid_days": 30}},
                }
            },
            "cycle": {"engine": "xiaomi_calendar_compatible", "days_until_period": 2},
            "data_coverage": {"steps": {"status": "complete"}},
            "warnings": [],
        }


def test_context_returns_title_sleep_place_and_vps_weather_without_coordinates() -> None:
    presence = Presence(
        id=41,
        observed_at=datetime(2026, 9, 13, 22, 8, tzinfo=timezone.utc),
        device="primary_phone",
        latitude=12.346,
        longitude=67.890,
        accuracy=20,
        city="Test City",
        district="Test District",
        region="Test Region",
        country="Test Country",
    )
    weather = FakeWeather()
    health = FakeMorningHealth()
    store = FakeStore(presence)
    service = WakeContextService(store=store, weather=weather, health=health)

    result = service.get("report-123")

    assert result["title"] == "早安我的少年"
    assert result["report_kind"] == "morning"
    assert result["includes_yesterday_health"] is True
    assert result["health_date"] == "2026-09-13"
    assert result["schema_version"] == "1.0"
    assert result["timezone"] == "Asia/Shanghai"
    assert result["health"]["steps"]["summary"]["steps"] == 8123
    assert result["trends"]["steps"]["windows"]["7d"]["valid_days"] == 7
    assert result["cycle"]["days_until_period"] == 2
    assert result["data_coverage"]["steps"]["status"] == "complete"
    assert result["calendar_sources"] == {
        "structured": {"status": "not_connected", "events": []},
        "xiaomi_calendar": {"status": "not_connected", "events": []},
    }
    assert result["location"] == {
        "city": "Test City",
        "district": "Test District",
        "country": "Test Country",
        "observed_at": "2026-09-13T22:08:00+00:00",
    }
    assert "region" not in result["location"]
    assert result["weather"]["available"] is True
    assert weather.calls == [(12.346, 67.890)]
    encoded = json.dumps(result, ensure_ascii=False)
    assert "latitude" not in encoded
    assert "longitude" not in encoded
    assert "12.346" not in encoded
    assert "67.890" not in encoded
    assert len(store.saved) == 1
    assert store.saved[0]["schema_version"] == "1.0"
    assert store.saved[0]["snapshot_mode"] == "native"


def test_context_without_presence_marks_weather_unavailable() -> None:
    weather = FakeWeather()
    service = WakeContextService(
        store=FakeStore(None),
        weather=weather,
        health=FakeMorningHealth(),
    )

    result = service.get("report-123")

    assert result["location_available"] is False
    assert result["location"] is None
    assert result["weather"] == {
        "available": False,
        "provider": "open_meteo",
        "reason": "location_unavailable",
    }
    assert weather.calls == []


def test_unknown_report_id_raises_value_error() -> None:
    class MissingStore(FakeStore):
        def get_delivery(self, report_id: str):
            return None

    service = WakeContextService(
        store=MissingStore(None),
        weather=FakeWeather(),
        health=FakeMorningHealth(),
    )
    try:
        service.get("missing")
    except ValueError as exc:
        assert str(exc) == "wake report not found"
    else:
        raise AssertionError("missing report must be rejected")


def test_repeated_read_returns_saved_snapshot_without_rebuilding() -> None:
    snapshot = {"schema_version": "1.0", "report_id": "report-123", "fixed": True}
    store = FakeStore(None, snapshot=snapshot)
    service = WakeContextService(
        store=store,
        weather=FakeWeather(),
        health=FakeMorningHealth(fail_on_call=True),
    )

    assert service.get("report-123") == snapshot
    assert store.saved == []


def test_nap_context_does_not_query_yesterday_health() -> None:
    store = FakeStore(None, report_kind="nap", includes_yesterday=False)
    health = FakeMorningHealth(fail_on_call=True)
    service = WakeContextService(store=store, weather=FakeWeather(), health=health)

    result = service.get("report-123")

    assert result["title"] == "午睡简报"
    assert result["includes_yesterday_health"] is False
    assert result["health"] == {}
    assert result["trends"] == {}
    assert result["cycle"] == {}
    assert result["data_coverage"] == {}
