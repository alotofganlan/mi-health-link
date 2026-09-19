from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from mi_health_link.wake_store import WakeStore


UTC = timezone.utc


def test_save_and_load_latest_unlock_uses_one_row_per_device() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(201, json=[{"device": "primary_phone"}])
        return httpx.Response(
            200,
            json=[{"last_unlock_at": "2026-09-14T00:02:00Z"}],
        )

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    observed_at = datetime(2026, 9, 14, 0, 2, tzinfo=UTC)

    store.save_unlock(device="primary_phone", observed_at=observed_at)
    loaded = store.latest_unlock_at("primary_phone")

    assert loaded == observed_at
    assert requests[0].url.path == "/rest/v1/device_wake_state"
    assert requests[0].url.params["on_conflict"] == "device"
    assert "resolution=merge-duplicates" in requests[0].headers["prefer"]
    assert json.loads(requests[0].content) == {
        "device": "primary_phone",
        "last_unlock_at": "2026-09-14T00:02:00+00:00",
        "source": "automate_unlock",
    }


def test_latest_presence_returns_last_saved_location() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{
                "id": 41,
                "observed_at": "2026-09-10T00:00:00Z",
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
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    presence = store.latest_presence("primary_phone")

    assert presence is not None and presence.id == 41
    assert seen[0].url.params["order"] == "observed_at.desc"
    assert seen[0].url.params["limit"] == "1"


def test_list_sleep_rows_and_deliveries_for_two_days() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    days = ("2026-09-13", "2026-09-14")

    store.list_sleep_rows_for_days(days)
    store.list_deliveries_for_days("primary_phone", days)

    assert seen[0].url.params["raw->>sleep_day"] == "in.(2026-09-13,2026-09-14)"
    assert seen[1].url.params["report_date"] == "in.(2026-09-13,2026-09-14)"


def test_location_update_is_stored_as_location_event() -> None:
    body_seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body_seen.update(json.loads(request.content))
        return httpx.Response(201, json=[{
            "id": 1,
            **body_seen,
        }])

    store = WakeStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    store.save_presence(
        observed_at=datetime(2026, 9, 14, tzinfo=UTC),
        device="primary_phone",
        event="location_update",
        latitude=12.3456,
        longitude=67.8901,
        accuracy=35,
        city="Test City",
        district="Test District",
        country="Test Country",
    )

    assert body_seen["event"] == "location_update"
    assert body_seen["source"] == "automate_location"
