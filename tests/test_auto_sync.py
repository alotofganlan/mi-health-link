from __future__ import annotations

import argparse

import xiaomi_health_sync.auto_sync as auto_sync
from xiaomi_health_sync.auto_discovery import XiaomiAuthExpiredError


def test_auto_sync_defaults_to_24_hour_window():
    args = auto_sync.build_parser().parse_args([])
    assert args.recent_window_hours == 24
    assert args.latest_limit == 30


def test_auto_sync_notifies_when_xiaomi_session_expires(monkeypatch) -> None:
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

    class Runner:
        def __init__(self, *, client, store, progress):
            pass

        def sync_recent(self, **kwargs):
            raise XiaomiAuthExpiredError("xiaomi_auth_expired: login required")

    notified: list[str] = []
    monkeypatch.setattr(auto_sync, "load_settings", lambda: Settings())
    monkeypatch.setattr(auto_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(auto_sync, "TemperatureAwareStore", lambda **kwargs: object())
    monkeypatch.setattr(auto_sync, "XiaomiHealthClient", Client)
    monkeypatch.setattr(auto_sync, "AutoDiscoveryRunner", Runner)
    monkeypatch.setattr(
        auto_sync,
        "notify_xiaomi_auth_expired",
        lambda message: notified.append(message) or True,
    )

    result = auto_sync.run(
        argparse.Namespace(
            recent_window_hours=24,
            latest_limit=30,
            selected_keys=None,
        )
    )

    assert result == 1
    assert notified == [
        "Xiaomi Cloud session expired. Open Mi Fitness and sign in again, "
        "then update the VPS Xiaomi credentials."
    ]
