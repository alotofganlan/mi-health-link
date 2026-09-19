from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from typing import Any

import httpx

from .config import load_settings
from .sync_store import DirectSupabaseStore


MG_DL_PER_MMOL_L = 18.0


class NightscoutEntryError(ValueError):
    pass


def parse_entry(entry: dict[str, Any]) -> dict[str, Any]:
    try:
        date_ms = int(entry["date"])
        glucose = float(entry["sgv"])
    except (KeyError, TypeError, ValueError) as exc:
        raise NightscoutEntryError("Nightscout entry is missing date/sgv") from exc
    if date_ms <= 0 or glucose <= 0:
        raise NightscoutEntryError("Nightscout date and sgv must be positive")

    glucose_mg_dl = int(round(glucose))
    source_record_id = str(entry.get("_id") or f"date:{date_ms}")
    measured_at = datetime.fromtimestamp(
        date_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat()

    return {
        "source": "nightscout",
        "source_record_id": source_record_id,
        "measured_at": measured_at,
        "glucose_mg_dl": glucose_mg_dl,
        "glucose_mmol_l": round(glucose_mg_dl / MG_DL_PER_MMOL_L, 2),
        "direction": None if entry.get("direction") is None else str(entry["direction"]),
    }


def build_entries_params(
    *,
    since_ms: int | None = None,
    before_ms: int | None = None,
    count: int = 1000,
    token: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"count": int(count)}
    if since_ms is not None:
        params["find[date][$gt]"] = int(since_ms)
    if before_ms is not None:
        params["find[date][$lt]"] = int(before_ms)
    if token:
        params["token"] = token
    return params


def fetch_entries(
    *,
    base_url: str,
    since_ms: int | None = None,
    token: str | None = None,
    api_secret_sha1: str | None = None,
    batch_size: int = 1000,
    transport: httpx.BaseTransport | None = None,
) -> list[dict[str, Any]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    endpoint = f"{base_url.rstrip('/')}/api/v1/entries/sgv.json"
    rows: list[dict[str, Any]] = []
    before_ms: int | None = None
    headers = {"api-secret": api_secret_sha1} if api_secret_sha1 else None

    with httpx.Client(timeout=30.0, transport=transport) as client:
        while True:
            response = client.get(
                endpoint,
                params=build_entries_params(
                    since_ms=since_ms,
                    before_ms=before_ms,
                    count=batch_size,
                    token=token,
                ),
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise NightscoutEntryError("Nightscout entries response must be a list")
            page = [item for item in payload if isinstance(item, dict)]
            if not page:
                break
            rows.extend(page)

            dates: list[int] = []
            for item in page:
                try:
                    dates.append(int(item["date"]))
                except (KeyError, TypeError, ValueError):
                    continue
            if not dates:
                break
            next_before = min(dates)
            if before_ms is not None and next_before >= before_ms:
                break
            before_ms = next_before

    rows.sort(key=lambda item: int(item.get("date") or 0))
    return rows


def sync_nightscout(
    *,
    base_url: str,
    token: str | None,
    api_secret_sha1: str | None = None,
    store: Any,
    batch_size: int = 1000,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, int | None]:
    since_ms = store.load_latest_glucose_timestamp_ms()
    entries = fetch_entries(
        base_url=base_url,
        since_ms=since_ms,
        token=token,
        api_secret_sha1=api_secret_sha1,
        batch_size=batch_size,
        transport=transport,
    )

    samples: list[dict[str, Any]] = []
    invalid_count = 0
    latest_ms = since_ms
    for entry in entries:
        try:
            sample = parse_entry(entry)
            entry_ms = int(entry["date"])
        except (NightscoutEntryError, KeyError, TypeError, ValueError):
            invalid_count += 1
            continue
        samples.append(sample)
        if latest_ms is None or entry_ms > latest_ms:
            latest_ms = entry_ms

    saved_count = store.upsert_glucose_samples(samples)
    return {
        "since_ms": since_ms,
        "fetched_count": len(entries),
        "saved_count": int(saved_count),
        "invalid_count": invalid_count,
        "latest_ms": latest_ms,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi-health-link-nightscout-sync")
    parser.add_argument("--url", default=None, help="Nightscout base URL; defaults to NIGHTSCOUT_URL.")
    parser.add_argument("--token", default=None, help="Nightscout access token; defaults to NIGHTSCOUT_TOKEN.")
    parser.add_argument("--batch-size", type=int, default=1000)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.batch_size <= 0:
        print("--batch-size must be positive.", file=sys.stderr)
        return 2

    settings = load_settings()
    base_url = str(args.url or os.getenv("NIGHTSCOUT_URL") or "").strip()
    token = args.token if args.token is not None else (os.getenv("NIGHTSCOUT_TOKEN") or None)
    api_secret_sha1 = str(os.getenv("NIGHTSCOUT_API_SECRET_SHA1") or "").strip() or None
    if not base_url:
        print("Nightscout URL is required via --url or NIGHTSCOUT_URL.", file=sys.stderr)
        return 2
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for Nightscout synchronization.", file=sys.stderr)
        return 2

    store = DirectSupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        result = sync_nightscout(
            base_url=base_url,
            token=token,
            api_secret_sha1=api_secret_sha1,
            store=store,
            batch_size=args.batch_size,
        )
    except (httpx.HTTPError, NightscoutEntryError, ValueError) as exc:
        print(f"Nightscout synchronization failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "ok", **result}, ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
