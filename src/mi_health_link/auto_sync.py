from __future__ import annotations

import argparse
import json
import sys

import httpx

from .auto_discovery import AutoDiscoveryRunner, DiscoveryError, XiaomiAuthExpiredError
from .config import load_credentials, load_settings
from .diet_sync import DietSyncRunner
from .notifications import XIAOMI_AUTH_EXPIRED_MESSAGE, notify_xiaomi_auth_expired
from .recent_sync import run_recent_sync
from .temperature_store import TemperatureAwareStore
from .wake_report import check_pending_sleep_report
from .workout_sync import WorkoutSyncRunner
from .xiaomi import XiaomiHealthClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi-health-link-auto-sync")
    parser.add_argument(
        "--recent-window-hours",
        type=int,
        default=24,
        help="How far back to refresh each run (default: 24 hours).",
    )
    parser.add_argument(
        "--latest-limit",
        type=int,
        default=30,
        help="Per-key limit used by Xiaomi's latest fitness endpoint.",
    )
    parser.add_argument(
        "--key",
        action="append",
        dest="selected_keys",
        default=None,
        help="Optional Xiaomi key to sync; repeat for multiple keys.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    if args.recent_window_hours <= 0:
        print("--recent-window-hours must be positive.", file=sys.stderr)
        return 2
    if args.latest_limit <= 0:
        print("--latest-limit must be positive.", file=sys.stderr)
        return 2

    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for automatic synchronization.", file=sys.stderr)
        return 2

    store = TemperatureAwareStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    creds = load_credentials(settings.credentials_file, settings.region)

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        with XiaomiHealthClient(settings, creds) as client:
            requested = None if args.selected_keys is None else {
                str(key) for key in args.selected_keys if str(key)
            }
            sync_result = run_recent_sync(
                client=client,
                store=store,
                recent_window_seconds=args.recent_window_hours * 60 * 60,
                latest_limit=args.latest_limit,
                selected_keys=requested,
                progress=progress,
                health_runner_class=AutoDiscoveryRunner,
                diet_runner_class=DietSyncRunner,
                workout_runner_class=WorkoutSyncRunner,
            )
    except XiaomiAuthExpiredError as exc:
        notify_xiaomi_auth_expired(XIAOMI_AUTH_EXPIRED_MESSAGE)
        print(f"Automatic sync failed: {exc}", file=sys.stderr)
        return 1
    except DiscoveryError as exc:
        print(f"Automatic sync failed: {exc}", file=sys.stderr)
        return 1
    except httpx.TransportError as exc:
        print(f"Xiaomi network request failed after retries: {exc}", file=sys.stderr)
        return 1

    sleep_report = None
    if requested is None or "sleep" in requested:
        sleep_report = check_pending_sleep_report()

    print(json.dumps({
        "status": "ok",
        "recent_window_hours": args.recent_window_hours,
        "discovered_count": len(sync_result.health.discovered_keys) if sync_result.health is not None else 0,
        "new_keys": sync_result.health.new_keys if sync_result.health is not None else [],
        "diet": sync_result.diet,
        "workout": sync_result.workout,
        "sleep_report": sleep_report,
    }, ensure_ascii=False))
    return 0


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
