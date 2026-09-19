from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import httpx

from .auth import XiaomiLoginError, import_credentials, login_interactive
from .auto_discovery import AutoDiscoveryRunner, DiscoveryError
from .config import load_credentials, load_settings
from .presets import load_presets
from .special_discovery import SpecialProbe, default_special_probes, run_special_probes
from .sync_store import DirectSupabaseStore
from .xiaomi import XiaomiHealthClient


def _store_from_settings(settings):
    if not settings.supabase_url or not settings.supabase_service_role_key:
        return None
    return DirectSupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )


def cmd_login(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        login_interactive(
            username=args.username,
            password=None,
            region=args.region or settings.region,
            sid=args.sid,
            output=settings.credentials_file,
        )
    except XiaomiLoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Saved Xiaomi session to {settings.credentials_file}")
    print("Keep this file private; do not upload or paste it into chat.")
    return 0


def cmd_import_auth(args: argparse.Namespace) -> int:
    settings = load_settings()
    try:
        import_credentials(
            Path(args.source),
            settings.credentials_file,
            args.region or settings.region,
        )
    except (XiaomiLoginError, OSError, json.JSONDecodeError) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 1
    print(f"Imported Xiaomi session into {settings.credentials_file}")
    return 0


def cmd_show_config(args: argparse.Namespace) -> int:
    settings = load_settings()
    print(json.dumps({
        "region": settings.region,
        "health_host": settings.health_host,
        "credentials_file": str(settings.credentials_file),
        "supabase_configured": bool(
            settings.supabase_url and settings.supabase_service_role_key
        ),
        "user_agent": settings.user_agent,
    }, ensure_ascii=False, indent=2))
    return 0


def _run_probe(path: str, payload: dict, record_type: str, save: bool) -> int:
    settings = load_settings()
    creds = load_credentials(settings.credentials_file, settings.region)

    with XiaomiHealthClient(settings, creds) as client:
        result = client.encrypted_post(path, payload)

    out = {
        "status_code": result.status_code,
        "request_id": result.request_id,
        "record_type": record_type,
        "response": result.json_data if result.json_data is not None else result.text,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))

    if save:
        store = _store_from_settings(settings)
        if store is None:
            print("Supabase is not configured; response was not saved.", file=sys.stderr)
            return 2
        store.save_raw(record_type=record_type, payload=out)
        print(f"Saved diagnostic raw response as record_type={record_type!r}.", file=sys.stderr)

    return 0 if 200 <= result.status_code < 300 else 1


def cmd_probe(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(args.payload)
    except json.JSONDecodeError as exc:
        print(f"Invalid --payload JSON: {exc}", file=sys.stderr)
        return 2
    return _run_probe(args.path, payload, args.record_type, args.save)


def cmd_discover(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = _store_from_settings(settings)
    if store is None:
        print(
            "Supabase is required for discovery state and normalized storage.",
            file=sys.stderr,
        )
        return 2
    if args.history_window_days <= 0:
        print("--history-window-days must be positive.", file=sys.stderr)
        return 2

    creds = load_credentials(settings.credentials_file, settings.region)
    backfill_start_time = (
        args.backfill_start_time
        if args.backfill_start_time is not None
        else 0
    )

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    print(
        "Starting normalized discovery; initial history policy: "
        "sleep=365 days, menstruation=all available history, others=30 days; "
        "completed keys resume from checkpoint; "
        f"history window={args.history_window_days} day(s).",
        file=sys.stderr,
        flush=True,
    )
    try:
        with XiaomiHealthClient(settings, creds) as client:
            result = AutoDiscoveryRunner(
                client=client,
                store=store,
                history_window_seconds=args.history_window_days * 24 * 60 * 60,
                progress=progress,
            ).run(
                backfill_start_time=backfill_start_time,
                latest_limit=args.latest_limit,
            )
    except KeyboardInterrupt:
        print(
            "\nInterrupted. Completed history windows were checkpointed; "
            "the next run will resume from the saved checkpoint.",
            file=sys.stderr,
            flush=True,
        )
        return 130
    except DiscoveryError as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    except httpx.TransportError as exc:
        print(f"Xiaomi network request failed after retries: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({
        "discovered_keys": result.discovered_keys,
        "new_keys": result.new_keys,
        "discovered_count": len(result.discovered_keys),
        "new_count": len(result.new_keys),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_explore_special(args: argparse.Namespace) -> int:
    settings = load_settings()
    store = _store_from_settings(settings)
    if store is None:
        print("Supabase is required to preserve special-endpoint raw payloads.", file=sys.stderr)
        return 2

    end_time = args.end_time if args.end_time is not None else int(time.time())
    start_time = (
        args.start_time
        if args.start_time is not None
        else end_time - 30 * 24 * 60 * 60
    )
    probes = default_special_probes(
        start_time=start_time,
        end_time=end_time,
    )

    if args.category != "all":
        probes = [probe for probe in probes if probe.category == args.category]

    if args.female_path:
        try:
            female_payload = json.loads(args.female_payload)
        except json.JSONDecodeError as exc:
            print(f"Invalid --female-payload JSON: {exc}", file=sys.stderr)
            return 2
        if not isinstance(female_payload, dict):
            print("--female-payload must decode to a JSON object.", file=sys.stderr)
            return 2
        if args.category in ("all", "female_health"):
            probes.append(SpecialProbe(
                name="female_health",
                category="female_health",
                path=args.female_path,
                payload=female_payload,
            ))
    elif args.category == "female_health":
        print(
            "No verified Xiaomi female-health endpoint is hard-coded. "
            "Pass --female-path after it is observed from real app traffic.",
            file=sys.stderr,
        )
        return 2

    creds = load_credentials(settings.credentials_file, settings.region)
    with XiaomiHealthClient(settings, creds) as client:
        results = run_special_probes(client=client, store=store, probes=probes)

    print(json.dumps({
        "probes": [
            {
                "category": row["category"],
                "name": row["name"],
                "path": row["path"],
                "status_code": row["status_code"],
                "page": row.get("page"),
            }
            for row in results
        ]
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_probe_presets(args: argparse.Namespace) -> int:
    presets = load_presets(Path(args.file))
    failures = 0
    for preset in presets:
        print(f"\n=== {preset.record_type} ===")
        rc = _run_probe(
            preset.path,
            preset.payload,
            preset.record_type,
            args.save,
        )
        failures += int(rc != 0)
    return 0 if failures == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mi-health-link")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login")
    s.add_argument("--username")
    s.add_argument("--region")
    s.add_argument("--sid", default="miothealth")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("import-auth")
    s.add_argument("--source", required=True)
    s.add_argument("--region")
    s.set_defaults(func=cmd_import_auth)

    s = sub.add_parser("show-config")
    s.set_defaults(func=cmd_show_config)

    s = sub.add_parser("probe")
    s.add_argument("--path", required=True)
    s.add_argument("--payload", required=True)
    s.add_argument("--record-type", required=True)
    s.add_argument("--save", action=argparse.BooleanOptionalAction, default=False)
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("discover")
    s.add_argument(
        "--latest-limit",
        type=int,
        default=30,
        help="Per-key limit used by Xiaomi's latest fitness endpoint.",
    )
    s.add_argument(
        "--backfill-start-time",
        type=int,
        default=None,
        help="Optional absolute Unix lower bound; per-key initial history policy applies by default.",
    )
    s.add_argument(
        "--history-window-days",
        type=int,
        default=30,
        help="Split history backfill into windows of this many days (default: 30).",
    )
    s.set_defaults(func=cmd_discover)

    s = sub.add_parser("explore-special")
    s.add_argument(
        "--category",
        choices=("all", "profile", "workout", "female_health"),
        default="all",
    )
    s.add_argument(
        "--start-time",
        type=int,
        default=None,
        help="Unix start time for special history probes (default: 30 days ago).",
    )
    s.add_argument("--end-time", type=int)
    s.add_argument(
        "--female-path",
        help="Verified female-health endpoint path observed from Xiaomi app traffic.",
    )
    s.add_argument("--female-payload", default="{}")
    s.set_defaults(func=cmd_explore_special)

    s = sub.add_parser("probe-presets")
    s.add_argument("--file", default="presets.json")
    s.add_argument("--save", action=argparse.BooleanOptionalAction, default=False)
    s.set_defaults(func=cmd_probe_presets)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
