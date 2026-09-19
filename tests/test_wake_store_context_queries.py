from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from xiaomi_health_sync.wake_store import WakeStore


def test_get_delivery_and_presence_use_id_filters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("wake_report_deliveries"):
            return httpx.Response(
                200,
                json=[{
                    "id": "report-123",
                    "report_date": "2026-09-14",
                    "device": "primary_phone",
                    "sleep_source_record_id": "sleep-1",
                    "sleep_fingerprint": "fingerprint",
                    "report_kind": "morning",
                    "includes_yesterday_health": True,
                    "sleep_snapshot": {"wake_at": "2026-09-14T06:00:00+08:00"},
                    "presence_id": 41,
                    "status": "completed",
                    "claimed_at": "2026-09-13T22:01:00Z",
                    "triggered_at": "2026-09-13T22:02:00Z",
                }],
            )
        return httpx.Response(
            200,
            json=[{
                "id": 41,
                "observed_at": "2026-09-13T22:00:00Z",
                "device": "primary_phone",
                "latitude": 12.346,
                "longitude": 67.890,
                "accuracy": 20,
                "city": "Test City",
                "district": "Test District",
                "country": "Test Country",
            }],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "service-role",
        transport=httpx.MockTransport(handler),
    )

    delivery = store.get_delivery("report-123")
    presence = store.get_presence(41)

    assert delivery is not None
    assert delivery["id"] == "report-123"
    assert presence is not None
    assert presence.id == 41
    assert presence.observed_at == datetime(2026, 9, 13, 22, tzinfo=timezone.utc)
    assert requests[0].url.params["id"] == "eq.report-123"
    assert requests[1].url.params["id"] == "eq.41"


def test_context_queries_return_none_when_rows_are_missing() -> None:
    store = WakeStore(
        "https://project.supabase.co",
        "service-role",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    assert store.get_delivery("missing") is None
    assert store.get_presence(999) is None


def test_save_snapshot_returns_inserted_row_without_update() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json=[{
                "report_id": "report-1",
                "schema_version": "1.0",
                "source_versions": {"cycle_engine": "xiaomi_calendar_compatible"},
                "payload": {"report_id": "report-1"},
                "snapshot_mode": "native",
            }],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    saved = store.save_context_snapshot(
        "report-1",
        schema_version="1.0",
        source_versions={"cycle_engine": "xiaomi_calendar_compatible"},
        payload={"report_id": "report-1"},
        snapshot_mode="native",
    )

    assert saved["payload"]["report_id"] == "report-1"
    assert requests[0].method == "POST"
    assert requests[0].headers["prefer"] == "resolution=ignore-duplicates,return=representation"
    assert not any(request.method == "PATCH" for request in requests)


def test_save_snapshot_reads_existing_winner_after_duplicate() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=[])
        return httpx.Response(
            200,
            json=[{
                "report_id": "report-1",
                "schema_version": "1.0",
                "source_versions": {},
                "payload": {"report_id": "report-1", "winner": True},
                "snapshot_mode": "native",
            }],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    saved = store.save_context_snapshot(
        "report-1",
        schema_version="1.0",
        source_versions={},
        payload={"report_id": "report-1", "winner": False},
        snapshot_mode="native",
    )

    assert saved["payload"]["winner"] is True
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[1].url.params["report_id"] == "eq.report-1"


def test_get_context_snapshot_returns_none_when_missing() -> None:
    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )

    assert store.get_context_snapshot("missing") is None
