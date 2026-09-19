from __future__ import annotations

import argparse
from dataclasses import dataclass

import mi_health_link.auto_sync as auto_sync
import mi_health_link.mcp_sync as mcp_sync


@dataclass
class _Result:
    discovered_keys: list[str]
    new_keys: list[str]


class _Settings:
    supabase_url = "https://project.supabase.co"
    supabase_service_role_key = "service-role"
    credentials_file = "credentials.json"
    region = "cn"


class _Client:
    def __init__(self, settings, credentials):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_mcp_sleep_sync_runs_shared_report_check(monkeypatch) -> None:
    class Store:
        def _upsert_table(self, **kwargs):
            pass

        def load_source_check(self, key):
            return None

        def remember_source_check(self, key, **kwargs):
            pass

    class Runner:
        def __init__(self, *, client, store):
            pass

        def sync_recent(self, **kwargs):
            kwargs["on_checked_range"]("sleep", 1, 2)
            return _Result(["sleep"], [])

    class Reader:
        def __init__(self, *args):
            pass

        def coverage(self, *args, **kwargs):
            return {"latest_at": None}

    calls: list[bool] = []
    monkeypatch.setattr(mcp_sync, "load_settings", lambda: _Settings())
    monkeypatch.setattr(mcp_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(mcp_sync, "TemperatureAwareStore", lambda **kwargs: Store())
    monkeypatch.setattr(mcp_sync, "XiaomiHealthClient", _Client)
    monkeypatch.setattr(mcp_sync, "AutoDiscoveryRunner", Runner)
    monkeypatch.setattr(mcp_sync, "NormalizedHealthReader", Reader)
    monkeypatch.setattr(
        mcp_sync,
        "check_pending_sleep_report",
        lambda: calls.append(True) or {"ok": True, "status": "no_new_sleep"},
        raising=False,
    )

    result = mcp_sync.run_sync_now(metric="sleep")

    assert calls == [True]
    assert result["sleep_report"] == {"ok": True, "status": "no_new_sleep"}


def test_mcp_non_sleep_sync_does_not_run_report_check(monkeypatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(
        mcp_sync,
        "check_pending_sleep_report",
        lambda: calls.append(True),
        raising=False,
    )
    assert mcp_sync._should_check_sleep_report("heart_rate") is False
    assert calls == []


def test_auto_sync_runs_shared_report_check_when_sleep_is_in_scope(monkeypatch, capsys) -> None:
    class Runner:
        def __init__(self, *, client, store, progress):
            pass

        def sync_recent(self, **kwargs):
            return _Result(["sleep"], [])

    calls: list[bool] = []
    monkeypatch.setattr(auto_sync, "load_settings", lambda: _Settings())
    monkeypatch.setattr(auto_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(auto_sync, "TemperatureAwareStore", lambda **kwargs: object())
    monkeypatch.setattr(auto_sync, "XiaomiHealthClient", _Client)
    monkeypatch.setattr(auto_sync, "AutoDiscoveryRunner", Runner)
    monkeypatch.setattr(
        auto_sync,
        "check_pending_sleep_report",
        lambda: calls.append(True) or {"ok": True, "status": "report_triggered"},
        raising=False,
    )

    status = auto_sync.run(
        argparse.Namespace(recent_window_hours=24, latest_limit=30, selected_keys=["sleep"])
    )

    assert status == 0
    assert calls == [True]
    assert '"sleep_report": {"ok": true, "status": "report_triggered"}' in capsys.readouterr().out
