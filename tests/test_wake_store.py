from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import httpx

from xiaomi_health_sync.wake_store import WakeStore


UTC = timezone.utc


def test_save_presence_rounds_coordinates_and_omits_private_address_fields() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            201,
            json=[
                {
                    "id": 41,
                    "observed_at": "2026-09-14T06:10:00+00:00",
                    "device": "primary_phone",
                    "event": "unlock",
                    "latitude": 12.346,
                    "longitude": 67.890,
                    "accuracy": 35.0,
                    "city": "Test City",
                    "district": "Test District",
                    "region": "Test Region",
                    "country": "Test Country",
                    "source": "automate_location",
                    "created_at": "2026-09-14T06:10:01+00:00",
                }
            ],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    presence = store.save_presence(
        observed_at=datetime(2026, 9, 14, 6, 10, tzinfo=UTC),
        device="primary_phone",
        event="unlock",
        latitude=12.34564,
        longitude=67.89014,
        accuracy=35.0,
        city="Test City",
        district="Test District",
        country="Test Country",
    )

    assert presence.id == 41
    assert presence.latitude == 12.346
    assert presence.longitude == 67.890
    assert presence.district == "Test District"
    assert len(seen) == 1
    assert seen[0].url.path == "/rest/v1/device_presence"
    body = json.loads(seen[0].content)
    assert body["latitude"] == 12.346
    assert body["longitude"] == 67.890
    assert body["district"] == "Test District"
    assert "region" not in body
    assert body["source"] == "automate_location"
    assert "ip" not in body
    assert "address" not in body
    assert "raw" not in body
    assert "authorization" not in {key.lower() for key in seen[0].headers}


def test_claim_delivery_returns_none_when_fingerprint_was_already_claimed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/wake_report_deliveries"
        return httpx.Response(
            409,
            json={"code": "23505", "message": "duplicate key value"},
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    delivery = store.claim_delivery(
        report_id="report-1",
        report_date="2026-09-14",
        device="primary_phone",
        sleep_source_record_id="sleep-1",
        sleep_fingerprint="fingerprint-1",
        report_kind="morning",
        includes_yesterday_health=True,
        sleep_snapshot={"wake_at": "2026-09-14T06:00:00+08:00"},
        presence_id=41,
        claimed_at=datetime(2026, 9, 14, 6, 10, tzinfo=UTC),
    )

    assert delivery is None


def test_release_delivery_deletes_only_an_unfinished_claim() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    store.release_delivery("report-1")

    assert len(seen) == 1
    assert seen[0].method == "DELETE"
    assert seen[0].url.params["id"] == "eq.report-1"
    assert seen[0].url.params["status"] == "eq.triggering"


def test_closest_presence_queries_only_the_configured_wake_window() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "observed_at": "2026-09-14T00:08:00+00:00",
                    "device": "primary_phone",
                    "event": "unlock",
                    "latitude": 12.346,
                    "longitude": 67.890,
                    "accuracy": 20,
                    "city": "Test City",
                    "district": "Test District",
                    "region": "Test Region",
                    "country": "Test Country",
                    "source": "automate_location",
                    "created_at": "2026-09-14T00:08:01+00:00",
                }
            ],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    wake_at = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)
    presence = store.closest_presence(
        device="primary_phone",
        wake_at=wake_at,
        window=timedelta(minutes=120),
    )

    assert presence is not None and presence.id == 42
    params = seen[0].url.params
    assert params["observed_at"] == "gte.2026-09-13T22:00:00+00:00"
    assert params.get_list("observed_at") == [
        "gte.2026-09-13T22:00:00+00:00",
        "lte.2026-09-14T02:00:00+00:00",
    ]


def test_latest_sleep_sync_at_reads_existing_sync_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == "eq.sleep"
        return httpx.Response(
            200,
            json=[{"source_checked_at": "2026-09-14T06:01:02+00:00"}],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    assert store.latest_sleep_sync_at() == datetime(
        2026, 9, 14, 6, 1, 2, tzinfo=UTC
    )
