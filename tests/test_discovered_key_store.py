import json

import httpx

from mi_health_link.sync_store import DirectSupabaseStore, VERIFIED_BOOTSTRAP_KEYS


def _store(handler):
    return DirectSupabaseStore(
        "https://example.supabase.co",
        "sb_secret_test",
        httpx.MockTransport(handler),
    )


def test_load_discovered_keys_reads_sync_state_only():
    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/xiaomi_sync_state"
        assert request.url.params["select"] == "key"
        assert request.url.params["discovered"] == "eq.true"
        return httpx.Response(200, json=[{"key": "steps"}, {"key": "sleep"}])

    assert _store(handler).load_discovered_keys() == {"sleep", "steps"}


def test_fresh_direct_sync_bootstraps_verified_keys_without_reading_raw():
    def handler(request: httpx.Request):
        raise AssertionError(f"load_observed_keys must not call HTTP: {request}")

    keys = _store(handler).load_observed_keys()
    assert keys == VERIFIED_BOOTSTRAP_KEYS
    assert "sleep" in keys
    assert "menstruation" in keys
    assert "temperature_characteristic" in keys
    assert "single_blood_sugar" in keys
    assert "blood_sugar" not in keys
    assert len(keys) == 22


def test_first_state_write_patches_then_inserts_without_raw_write():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "PATCH":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json=[])

    assert _store(handler).remember_discovered_key("sleep") == "sleep"

    assert [r.method for r in requests] == ["PATCH", "POST"]
    assert all(r.url.path == "/rest/v1/xiaomi_sync_state" for r in requests)
    assert json.loads(requests[0].content) == {"discovered": True}
    assert json.loads(requests[1].content) == {
        "source": "xiaomi",
        "key": "sleep",
        "discovered": True,
    }


def test_existing_state_write_uses_patch_only_and_preserves_other_fields():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.method == "PATCH"
        return httpx.Response(200, json=[{"key": "sleep"}])

    assert _store(handler).remember_backfilled_key("sleep") == "sleep"

    assert len(requests) == 1
    assert json.loads(requests[0].content) == {"initial_backfill_complete": True}


def test_backfill_progress_loads_checkpoint_from_sync_state():
    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.url.path == "/rest/v1/xiaomi_sync_state"
        assert request.url.params["key"] == "eq.sleep"
        return httpx.Response(200, json=[{"next_start_time": 123}])

    assert _store(handler).load_backfill_progress("sleep") == 123


def test_backfill_progress_patch_does_not_reset_discovery_flags():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.method == "PATCH"
        return httpx.Response(200, json=[{"key": "sleep"}])

    assert _store(handler).remember_backfill_progress("sleep", 456) == "sleep"
    assert json.loads(requests[0].content) == {"next_start_time": 456}


def test_remember_source_check_persists_freshness_fields() -> None:
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        assert request.method == "PATCH"
        return httpx.Response(200, json=[{"key": "heart_rate"}])

    _store(handler).remember_source_check(
        "heart_rate",
        checked_at="2026-08-22T06:38:30+00:00",
        status="success",
        source_latest_at="2026-08-22T04:35:00+00:00",
        next_recheck_at="2026-08-22T07:08:30+00:00",
        empty_check_count=1,
    )

    assert json.loads(requests[0].content) == {
        "source_checked_at": "2026-08-22T06:38:30+00:00",
        "source_check_status": "success",
        "source_latest_at": "2026-08-22T04:35:00+00:00",
        "next_recheck_at": "2026-08-22T07:08:30+00:00",
        "empty_check_count": 1,
    }


def test_load_source_check_reads_persisted_freshness_fields() -> None:
    def handler(request: httpx.Request):
        assert request.method == "GET"
        assert request.url.params["key"] == "eq.heart_rate"
        return httpx.Response(200, json=[{
            "source_checked_at": "2026-08-22T06:38:30+00:00",
            "source_check_status": "success",
            "source_latest_at": "2026-08-22T04:35:00+00:00",
            "next_recheck_at": "2026-08-22T07:08:30+00:00",
            "empty_check_count": 1,
        }])

    assert _store(handler).load_source_check("heart_rate") == {
        "source_checked_at": "2026-08-22T06:38:30+00:00",
        "source_check_status": "success",
        "source_latest_at": "2026-08-22T04:35:00+00:00",
        "next_recheck_at": "2026-08-22T07:08:30+00:00",
        "empty_check_count": 1,
    }
