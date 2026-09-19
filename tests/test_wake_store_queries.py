from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from xiaomi_health_sync.wake_store import WakeStore


def test_list_sleep_rows_selects_watch_records_for_report_date() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"source_record_id": "sleep-1"}])

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    rows = store.list_sleep_rows("2026-09-14")

    assert rows == [{"source_record_id": "sleep-1"}]
    params = seen[0].url.params
    assert params["raw->>sleep_day"] == "eq.2026-09-14"
    assert params["raw->>sleep_source"] == "eq.watch"
    assert params["order"] == "end_at.asc"


def test_list_deliveries_returns_completed_and_triggering_rows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["device"] == "eq.primary_phone"
        assert request.url.params["report_date"] == "eq.2026-09-14"
        return httpx.Response(200, json=[{"id": "report-1", "status": "completed"}])

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    assert store.list_deliveries("primary_phone", "2026-09-14") == [
        {"id": "report-1", "status": "completed"}
    ]


def test_complete_delivery_only_updates_triggering_claim() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    triggered_at = datetime(2026, 9, 14, 0, 10, tzinfo=timezone.utc)
    store.complete_delivery("report-1", triggered_at)

    assert seen[0].method == "PATCH"
    assert seen[0].url.params["id"] == "eq.report-1"
    assert seen[0].url.params["status"] == "eq.triggering"
    assert json.loads(seen[0].content) == {
        "status": "completed",
        "triggered_at": "2026-09-14T00:10:00+00:00",
    }
