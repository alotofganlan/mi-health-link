from __future__ import annotations

import httpx
import pytest

import mi_health_link.mcp_sync as mcp_sync
from mi_health_link.xiaomi import XiaomiResponse


def test_targeted_sync_failure_persists_failed_source_check(monkeypatch) -> None:
    class Settings:
        supabase_url = "https://project.supabase.co"
        supabase_service_role_key = "service-role"
        credentials_file = "credentials.json"
        region = "cn"

    class Client:
        def __init__(self, settings, credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    seen: dict[str, object] = {}

    class Store:
        def load_source_check(self, key):
            return {"empty_check_count": 2, "source_latest_at": "2026-08-22T04:35:00+00:00"}

        def remember_source_check(self, key, **fields):
            seen["key"] = key
            seen["fields"] = fields

    store = Store()

    class Runner:
        def __init__(self, *, client, store):
            pass

        def sync_recent(
            self,
            *,
            selected_keys=None,
            recent_window_seconds=None,
            on_checked_range=None,
        ):
            raise RuntimeError("xiaomi unavailable")

    monkeypatch.setattr(mcp_sync, "load_settings", lambda: Settings())
    monkeypatch.setattr(mcp_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(mcp_sync, "DirectSupabaseStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "TemperatureAwareStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "XiaomiHealthClient", Client)
    monkeypatch.setattr(mcp_sync, "AutoDiscoveryRunner", Runner)

    with pytest.raises(RuntimeError, match="xiaomi unavailable"):
        mcp_sync.run_sync_now(metric="heart_rate")

    assert seen["key"] == "heart_rate"
    fields = seen["fields"]
    assert fields["status"] == "failed"
    assert fields["source_latest_at"] == "2026-08-22T04:35:00+00:00"
    assert fields["empty_check_count"] == 2
    assert fields["next_recheck_at"] is not None


def test_targeted_transport_failure_is_not_recorded_as_success(monkeypatch) -> None:
    class Settings:
        supabase_url = "https://project.supabase.co"
        supabase_service_role_key = "service-role"
        credentials_file = "credentials.json"
        region = "cn"

    class Client:
        def __init__(self, settings, credentials):
            self.calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def encrypted_post(self, path, payload):
            self.calls += 1
            if self.calls == 1:
                return XiaomiResponse(
                    status_code=200,
                    json_data={
                        "code": 0,
                        "result": {"data_list": [], "has_more": False},
                    },
                    text="",
                    request_id="latest",
                )
            raise httpx.ConnectError("xiaomi unavailable")

    seen = {"ranges": []}

    class Store:
        def load_discovered_keys(self):
            return {"heart_rate"}

        def load_observed_keys(self):
            return {"heart_rate"}

        def remember_discovered_key(self, key):
            raise AssertionError("not expected")

        def load_backfilled_keys(self):
            return set()

        def remember_backfilled_key(self, key):
            raise AssertionError("not expected")

        def load_backfill_progress(self, key):
            return None

        def remember_backfill_progress(self, key, next_start_time):
            raise AssertionError("not expected")

        def upsert_normalized_record(self, record):
            return True

        def load_source_check(self, key):
            return None

        def remember_source_check(self, key, **fields):
            seen["source_check"] = fields

        def _upsert_table(self, **kwargs):
            seen["ranges"].append(kwargs)

    class Reader:
        def __init__(self, url, service_role_key):
            pass

        def coverage(self, metric, *, start_at, end_at):
            return {
                "metric": metric,
                "count": 0,
                "first_at": None,
                "latest_at": None,
            }

    store = Store()
    monkeypatch.setattr(mcp_sync, "load_settings", lambda: Settings())
    monkeypatch.setattr(mcp_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(mcp_sync, "TemperatureAwareStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "XiaomiHealthClient", Client)
    monkeypatch.setattr(mcp_sync, "NormalizedHealthReader", Reader)

    with pytest.raises(RuntimeError, match="heart_rate"):
        mcp_sync.run_sync_now(metric="heart_rate")

    assert seen["source_check"]["status"] == "failed"
    assert seen["ranges"] == []
