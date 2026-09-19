from __future__ import annotations

import json
import sys
import time

import httpx

from .auto_discovery import AutoDiscoveryRunner, DiscoveryError
from .config import load_credentials, load_settings
from .supabase_store import SupabaseStore
from .verified_key_probe import WATCH_S4_41MM_VERIFIED_PROBE_KEYS, probe_verified_persist_keys
from .xiaomi import XiaomiHealthClient


def main() -> None:
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for discovery state and raw payload storage.", file=sys.stderr)
        raise SystemExit(2)

    store = SupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    creds = load_credentials(settings.credentials_file, settings.region)
    backfill_start_time = int(time.time()) - 30 * 24 * 60 * 60

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        with XiaomiHealthClient(settings, creds) as client:
            probe_result = probe_verified_persist_keys(
                client=client,
                store=store,
                keys=WATCH_S4_41MM_VERIFIED_PROBE_KEYS,
                latest_limit=30,
                progress=progress,
            )
            result = AutoDiscoveryRunner(
                client=client,
                store=store,
                history_window_seconds=30 * 24 * 60 * 60,
                progress=progress,
            ).run(
                backfill_start_time=backfill_start_time,
                latest_limit=30,
            )
    except KeyboardInterrupt:
        print("\nInterrupted; completed windows remain checkpointed.", file=sys.stderr)
        raise SystemExit(130)
    except (DiscoveryError, httpx.TransportError) as exc:
        print(f"S4 discovery failed: {exc}", file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps({
        "verified_probe": {
            "confirmed": probe_result.confirmed,
            "unconfirmed": probe_result.unconfirmed,
        },
        "discovered_keys": result.discovered_keys,
        "new_keys": result.new_keys,
    }, ensure_ascii=False, indent=2))
