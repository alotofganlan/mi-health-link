from __future__ import annotations

from datetime import date
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

from xiaomi_health_sync.wake_context import WakeContextService
from xiaomi_health_sync.wake_report import SleepReportService
from xiaomi_health_sync.wake_store import Delivery, Presence


CST = timezone(timedelta(hours=8))
UNLOCK = datetime(2026, 9, 14, 8, 2, tzinfo=CST)


def sleep_row(
    source_id: str = "main",
    *,
    start: str = "2026-09-13T23:00:00+08:00",
    end: str = "2026-09-14T07:30:00+08:00",
    minutes: int = 480,
) -> dict:
    return {
        "source_record_id": source_id,
        "start_at": start,
        "end_at": end,
        "raw": {
            "sleep_day": "2026-09-14",
            "sleep_source": "watch",
            "metrics": {"total_sleep_seconds": minutes},
        },
    }


class FakeStore:
    def __init__(
        self,
        *,
        unlock_at: datetime | None,
        rows: list[dict],
        deliveries: list[dict] | None = None,
        nearby_presence: Presence | None = None,
        latest_presence: Presence | None = None,
    ) -> None:
        self.unlock_at = unlock_at
        self.rows = rows
        self.deliveries = list(deliveries or [])
        self.nearby_presence = nearby_presence
        self.fallback_presence = latest_presence
        self.claims: list[dict] = []
        self.completed: list[str] = []
        self.delivery_rows: dict[str, dict] = {}
        self.context_snapshots: dict[str, dict] = {}

    def latest_unlock_at(self, device: str):
        return self.unlock_at

    def list_sleep_rows_for_days(self, days):
        return list(self.rows)

    def list_deliveries_for_days(self, device, days):
        return list(self.deliveries)

    def closest_presence(self, **kwargs):
        return self.nearby_presence

    def latest_presence(self, device: str):
        return self.fallback_presence

    def claim_delivery(self, **fields):
        self.claims.append(fields)
        delivery = Delivery(
            id=fields["report_id"],
            report_date=fields["report_date"],
            device=fields["device"],
            sleep_source_record_id=fields["sleep_source_record_id"],
            sleep_fingerprint=fields["sleep_fingerprint"],
            report_kind=fields["report_kind"],
            includes_yesterday_health=fields["includes_yesterday_health"],
            sleep_snapshot=fields["sleep_snapshot"],
            presence_id=fields["presence_id"],
            status="triggering",
            claimed_at=fields["claimed_at"],
        )
        self.delivery_rows[delivery.id] = {
            **fields,
            "id": delivery.id,
            "status": "triggering",
        }
        return delivery

    def complete_delivery(self, report_id: str, triggered_at: datetime):
        self.completed.append(report_id)

    def release_delivery(self, report_id: str):
        raise AssertionError("report command should succeed")

    def get_delivery(self, report_id: str):
        return self.delivery_rows.get(report_id)

    def get_context_snapshot(self, report_id: str):
        return self.context_snapshots.get(report_id)

    def save_context_snapshot(self, report_id: str, **fields):
        row = {"report_id": report_id, **fields}
        winner = self.context_snapshots.setdefault(report_id, row)
        return winner

    def get_presence(self, presence_id: int):
        for presence in (self.nearby_presence, self.fallback_presence):
            if presence is not None and presence.id == presence_id:
                return presence
        return None


def build(store: FakeStore):
    reports: list[str] = []
    service = SleepReportService(
        store=store,
        report_runner=lambda report_id, report_kind: reports.append(report_id) or True,
        timezone=CST,
        device="primary_phone",
        report_id_factory=lambda: "report-1",
    )
    return service, reports


def test_no_unlock_never_triggers_existing_sleep() -> None:
    store = FakeStore(unlock_at=None, rows=[sleep_row()])
    service, reports = build(store)

    result = service.check_pending(now=UNLOCK)

    assert result.status == "waiting_for_unlock"
    assert reports == []


def test_unlock_before_or_too_late_never_triggers() -> None:
    for unlock in (
        datetime(2026, 9, 14, 7, 29, tzinfo=CST),
        datetime(2026, 9, 14, 9, 31, tzinfo=CST),
    ):
        store = FakeStore(unlock_at=unlock, rows=[sleep_row()])
        service, reports = build(store)
        assert service.check_pending(now=UNLOCK).status == "no_new_sleep"
        assert reports == []


def test_default_unlock_window_rejects_sleep_ending_over_one_hour_earlier() -> None:
    store = FakeStore(
        unlock_at=datetime(2026, 9, 14, 8, 31, tzinfo=CST),
        rows=[sleep_row()],
    )
    service, reports = build(store)

    assert service.check_pending(now=UNLOCK).status == "no_new_sleep"
    assert reports == []


def test_valid_unlock_triggers_main_sleep_once_with_yesterday_health() -> None:
    store = FakeStore(unlock_at=UNLOCK, rows=[sleep_row()])
    service, reports = build(store)

    result = service.check_pending(now=UNLOCK)

    assert result.as_dict() == {
        "ok": True,
        "status": "report_triggered",
        "report_id": "report-1",
        "report_type": "morning",
    }
    assert reports == ["report-1"]
    assert store.claims[0]["includes_yesterday_health"] is True
    assert store.completed == ["report-1"]


def test_existing_sleep_id_is_not_reported_again() -> None:
    store = FakeStore(
        unlock_at=UNLOCK,
        rows=[sleep_row(minutes=500, end="2026-09-14T07:45:00+08:00")],
        deliveries=[{
            "status": "completed",
            "sleep_source_record_id": "main",
        }],
    )
    service, reports = build(store)

    assert service.check_pending(now=UNLOCK).status == "no_new_sleep"
    assert reports == []


def test_nap_uses_nap_type_without_yesterday_health() -> None:
    main = sleep_row()
    nap = sleep_row(
        "nap",
        start="2026-09-14T13:00:00+08:00",
        end="2026-09-14T13:25:00+08:00",
        minutes=25,
    )
    unlock = datetime(2026, 9, 14, 13, 30, tzinfo=CST)
    store = FakeStore(
        unlock_at=unlock,
        rows=[main, nap],
        deliveries=[{"status": "completed", "sleep_source_record_id": "main"}],
    )
    service, _ = build(store)

    result = service.check_pending(now=unlock)

    assert result.report_type == "nap"
    assert store.claims[0]["report_kind"] == "nap"
    assert store.claims[0]["includes_yesterday_health"] is False


def test_falls_back_to_latest_location_when_none_is_near_sleep() -> None:
    old_location = Presence(
        id=9,
        observed_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
        device="primary_phone",
        latitude=12.346,
        longitude=67.890,
        accuracy=20,
        city="Test City",
        district="Test District",
        country="Test Country",
    )
    store = FakeStore(
        unlock_at=UNLOCK,
        rows=[sleep_row()],
        latest_presence=old_location,
    )
    service, _ = build(store)

    service.check_pending(now=UNLOCK)

    assert store.claims[0]["presence_id"] == 9


def test_new_morning_report_signals_slack_and_context_is_complete(monkeypatch) -> None:
    report_path = Path(__file__).parents[1] / "slack_morning_report.py"
    spec = importlib.util.spec_from_file_location("slack_e2e_report", report_path)
    assert spec is not None and spec.loader is not None
    slack_report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(slack_report)

    store = FakeStore(unlock_at=UNLOCK, rows=[sleep_row()])
    slack_messages: list[str] = []

    def signal_slack(report_id: str, report_kind: str) -> bool:
        monkeypatch.setenv("WAKE_REPORT_ID", report_id)
        monkeypatch.setenv("WAKE_REPORT_KIND", report_kind)
        slack_messages.append(slack_report.render_trigger(date(2026, 9, 14)))
        return True

    trigger = SleepReportService(
        store=store,
        report_runner=signal_slack,
        timezone=CST,
        device="primary_phone",
        report_id_factory=lambda: "report-e2e",
    )

    triggered = trigger.check_pending(now=UNLOCK)

    assert triggered.report_id == "report-e2e"
    assert slack_messages[0].startswith("Xiaomi Health morning report ready\n")
    assert "report_id=report-e2e" in slack_messages[0]
    assert "report_kind=morning" in slack_messages[0]
    assert "get_morning_context" in slack_messages[0]

    class Health:
        def build(self, health_date):
            assert health_date == date(2026, 9, 13)
            return {
                "health": {"steps": {"status": "available", "summary": {"steps": 8123}}},
                "trends": {"steps": {"windows": {"7d": {}, "30d": {}}}},
                "cycle": {"engine": "xiaomi_calendar_compatible"},
                "data_coverage": {"steps": {"status": "complete"}},
                "warnings": [],
            }

    class Weather:
        def fetch_weather(self, latitude, longitude):
            raise AssertionError("no location means weather must not be queried")

    context = WakeContextService(store=store, weather=Weather(), health=Health()).get(
        "report-e2e"
    )

    assert context["includes_yesterday_health"] is True
    assert context["health"]
    assert context["trends"]
    assert context["data_coverage"]
    assert context["cycle"]["engine"] == "xiaomi_calendar_compatible"
    assert store.context_snapshots["report-e2e"]["payload"] == context
