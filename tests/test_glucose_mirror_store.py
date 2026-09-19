from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx

from xiaomi_health_sync.sync_store import DirectSupabaseStore


def _store(handler):
    return DirectSupabaseStore(
        url="https://project.supabase.co",
        service_role_key="sb_secret_test",
        transport=httpx.MockTransport(handler),
    )


def test_load_glucose_mirror_checkpoint_uses_dedicated_sync_state_source():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/xiaomi_sync_state"
        params = dict(request.url.params)
        assert params["select"] == "next_start_time"
        assert params["source"] == "eq.nightscout_to_xiaomi"
        assert params["key"] == "eq.single_blood_sugar"
        assert params["limit"] == "1"
        return httpx.Response(200, json=[{"next_start_time": 1_787_402_645_771}])

    store = _store(handler)
    loader = getattr(store, "load_glucose_mirror_checkpoint_ms", None)
    assert callable(loader)
    assert loader() == 1_787_402_645_771


def test_load_pending_nightscout_samples_queries_after_checkpoint_in_time_order():
    since_ms = 1_787_402_645_771
    expected_iso = datetime.fromtimestamp(
        since_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/glucose_samples"
        params = dict(request.url.params)
        assert params["select"] == "source_record_id,measured_at,glucose_mmol_l"
        assert params["source"] == "eq.nightscout"
        assert params["measured_at"] == f"gt.{expected_iso}"
        assert params["order"] == "measured_at.asc"
        assert params["limit"] == "100"
        return httpx.Response(200, json=[{
            "source_record_id": "next",
            "measured_at": "2026-08-22T13:00:05.771+00:00",
            "glucose_mmol_l": 5.7,
        }])

    store = _store(handler)
    loader = getattr(store, "load_nightscout_glucose_samples_after", None)
    assert callable(loader)
    rows = loader(since_ms, limit=100)
    assert rows[0]["source_record_id"] == "next"


def test_remember_glucose_mirror_checkpoint_inserts_state_when_missing():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PATCH":
            assert request.url.path == "/rest/v1/xiaomi_sync_state"
            assert dict(request.url.params) == {
                "source": "eq.nightscout_to_xiaomi",
                "key": "eq.single_blood_sugar",
            }
            return httpx.Response(200, json=[])
        assert request.method == "POST"
        assert request.url.path == "/rest/v1/xiaomi_sync_state"
        assert dict(request.url.params)["on_conflict"] == "source,key"
        return httpx.Response(201, json=[])

    store = _store(handler)
    remember = getattr(store, "remember_glucose_mirror_checkpoint_ms", None)
    assert callable(remember)
    assert remember(1_787_402_645_771) == 1_787_402_645_771

    assert [request.method for request in requests] == ["PATCH", "POST"]
    assert json.loads(requests[0].content) == {"next_start_time": 1_787_402_645_771}
    assert json.loads(requests[1].content) == {
        "source": "nightscout_to_xiaomi",
        "key": "single_blood_sugar",
        "next_start_time": 1_787_402_645_771,
    }
