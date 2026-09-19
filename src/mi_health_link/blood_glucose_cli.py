from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sys

import httpx

from .blood_glucose import (
    DEFAULT_MEASUREMENT_PERIOD,
    DEFAULT_ZONE_OFFSET_SECONDS,
    MANUAL_RECORD_SID,
    XiaomiBloodGlucoseWriteError,
    build_manual_blood_glucose_upload,
    read_continuous_blood_glucose,
    read_manual_blood_glucose,
    write_continuous_blood_glucose_batch,
    write_manual_blood_glucose,
)
from .config import Settings, load_credentials, load_settings
from .sync_store import DirectSupabaseStore
from .xiaomi import XiaomiHealthClient


@dataclass(frozen=True)
class LatestNightscoutSample:
    timestamp: int
    glucose_mmol_l: float


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mi-health-link-blood-glucose")
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read", help="Read manual blood glucose records from Xiaomi Cloud.")
    read.add_argument("--start-time", type=int, required=True)
    read.add_argument("--end-time", type=int, required=True)

    inspect = sub.add_parser(
        "inspect-cgm-recent",
        help="Read recent Xiaomi continuous-glucose metadata without printing glucose values or SIDs.",
    )
    inspect.add_argument("--minutes", type=int, default=120)

    push = sub.add_parser("push", help="Prepare or send one manual blood glucose record.")
    push.add_argument("--value", type=float, required=True, help="Blood glucose in mmol/L.")
    push.add_argument("--timestamp", type=int, required=True, help="Unix timestamp in seconds.")
    push.add_argument("--measurement-period", type=int, default=DEFAULT_MEASUREMENT_PERIOD)
    push.add_argument("--zone-offset-seconds", type=int, default=DEFAULT_ZONE_OFFSET_SECONDS)
    push.add_argument("--phone-id", default=None)
    push.add_argument("--send", action="store_true", help="Actually write to Xiaomi Cloud and verify by read-back.")

    probe = sub.add_parser(
        "probe-cgm-latest-two",
        help="Probe Xiaomi continuous glucose storage with the latest two Nightscout samples.",
    )
    probe.add_argument(
        "--send",
        action="store_true",
        help="Actually perform the two-point Xiaomi Cloud probe.",
    )

    return parser


def _supabase_headers(settings: Settings) -> dict[str, str]:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError("Supabase configuration is required")
    headers = {"apikey": settings.supabase_service_role_key}
    if not settings.supabase_service_role_key.startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {settings.supabase_service_role_key}"
    return headers


def _parse_nightscout_sample(row: dict) -> LatestNightscoutSample:
    measured_at = datetime.fromisoformat(str(row["measured_at"]).replace("Z", "+00:00"))
    if measured_at.tzinfo is None:
        measured_at = measured_at.replace(tzinfo=timezone.utc)
    glucose_mmol_l = float(row["glucose_mmol_l"])
    if glucose_mmol_l <= 0:
        raise ValueError("Nightscout glucose value must be positive")
    return LatestNightscoutSample(
        timestamp=int(measured_at.timestamp()),
        glucose_mmol_l=glucose_mmol_l,
    )


def load_latest_nightscout_samples(
    settings: Settings,
    *,
    count: int,
    transport: httpx.BaseTransport | None = None,
) -> list[LatestNightscoutSample]:
    if count <= 0:
        raise ValueError("count must be positive")
    headers = _supabase_headers(settings)
    with httpx.Client(timeout=30.0, transport=transport) as client:
        response = client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/glucose_samples",
            params={
                "select": "measured_at,glucose_mmol_l",
                "source": "eq.nightscout",
                "order": "measured_at.desc",
                "limit": str(count),
            },
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json()

    if not isinstance(rows, list) or len(rows) < count:
        raise ValueError(f"At least {count} Nightscout glucose samples are required")
    samples = [
        _parse_nightscout_sample(row)
        for row in rows[:count]
        if isinstance(row, dict)
    ]
    if len(samples) < count:
        raise ValueError(f"At least {count} valid Nightscout glucose samples are required")
    return sorted(samples, key=lambda sample: sample.timestamp)


def load_current_wearable_sid(
    settings: Settings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> str:
    headers = _supabase_headers(settings)
    with httpx.Client(timeout=30.0, transport=transport) as client:
        response = client.get(
            f"{settings.supabase_url.rstrip('/')}/rest/v1/health_records",
            params={
                "select": "sid",
                "source": "eq.xiaomi",
                "key": "eq.resting_heart_rate",
                "sid": "not.is.null",
                "order": "measured_at.desc",
                "limit": "1",
            },
            headers=headers,
        )
        response.raise_for_status()
        rows = response.json()

    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ValueError("No current Xiaomi wearable SID is available")
    sid = str(rows[0].get("sid") or "").strip()
    if not sid:
        raise ValueError("Current Xiaomi wearable SID is empty")
    return sid


def _positive_timestamp(value) -> int | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def summarize_cgm_rows(rows: list[dict]) -> dict[str, object]:
    outer_timestamps: list[int] = []
    inner_timestamps: list[int] = []
    same_timestamp_count = 0
    has_glucose_field_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue
        outer = _positive_timestamp(row.get("time"))
        if outer is not None:
            outer_timestamps.append(outer)

        raw_value = row.get("value")
        if isinstance(raw_value, str):
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                value = None
        else:
            value = raw_value

        inner = None
        if isinstance(value, dict):
            inner = _positive_timestamp(value.get("time"))
            if "blood_sugar" in value:
                has_glucose_field_count += 1
        if inner is not None:
            inner_timestamps.append(inner)
        if outer is not None and inner is not None and outer == inner:
            same_timestamp_count += 1

    return {
        "count": len(rows),
        "outer_timestamps": sorted(outer_timestamps),
        "inner_timestamps": sorted(inner_timestamps),
        "same_timestamp_count": same_timestamp_count,
        "has_glucose_field_count": has_glucose_field_count,
    }


def record_cgm_inspection(settings: Settings, summary: dict[str, object]) -> None:
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise ValueError("Supabase configuration is required")

    checked_at = datetime.now(timezone.utc)
    all_timestamps = [
        int(value)
        for field in ("outer_timestamps", "inner_timestamps")
        for value in (summary.get(field) or [])
    ]
    latest_timestamp = max(all_timestamps) if all_timestamps else None
    source_latest_at = (
        datetime.fromtimestamp(latest_timestamp, tz=timezone.utc).isoformat()
        if latest_timestamp is not None
        else None
    )
    detail = {"kind": "cgm_readonly_inspect_v1", **summary}
    store = DirectSupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    store._update_sync_state(
        "blood_sugar",
        source_checked_at=checked_at.isoformat(),
        source_check_status="success",
        source_check_detail=detail,
        source_latest_at=source_latest_at,
        next_recheck_at=None,
        empty_check_count=0 if int(summary.get("count") or 0) else 1,
    )


def _open_client() -> XiaomiHealthClient:
    settings = load_settings()
    credentials = load_credentials(settings.credentials_file, settings.region)
    return XiaomiHealthClient(settings, credentials)


def run(args: argparse.Namespace) -> int:
    if args.command == "push" and not args.send:
        request = build_manual_blood_glucose_upload(
            value_mmol_l=args.value,
            timestamp=args.timestamp,
            phone_id=args.phone_id or MANUAL_RECORD_SID,
            measurement_period=args.measurement_period,
            zone_offset_seconds=args.zone_offset_seconds,
        )
        print(json.dumps({
            "sent": False,
            "path": request.path,
            "payload": request.payload,
        }, ensure_ascii=False))
        return 0

    if args.command == "probe-cgm-latest-two" and not args.send:
        print(json.dumps({
            "status": "dry_run",
            "sent": False,
            "requested_count": 2,
        }, ensure_ascii=False))
        return 0

    try:
        if args.command == "inspect-cgm-recent":
            if args.minutes <= 0:
                raise ValueError("--minutes must be positive")
            try:
                settings = load_settings()
                end_time = int(datetime.now(timezone.utc).timestamp())
                start_time = end_time - int(args.minutes) * 60
                with _open_client() as client:
                    rows = read_continuous_blood_glucose(
                        client=client,
                        start_time=start_time,
                        end_time=end_time,
                    )
                summary = summarize_cgm_rows(rows)
            except Exception:
                print("Xiaomi CGM inspection failed: phase=read", file=sys.stderr)
                return 1

            try:
                record_cgm_inspection(settings, summary)
            except Exception:
                print("Xiaomi CGM inspection failed: phase=record", file=sys.stderr)
                return 1

            print(json.dumps({
                "status": "ok",
                "key": "blood_sugar",
                **summary,
            }, ensure_ascii=False))
            return 0

        if args.command == "probe-cgm-latest-two":
            settings = load_settings()
            samples = load_latest_nightscout_samples(settings, count=2)
            sid = load_current_wearable_sid(settings)
            with _open_client() as client:
                batch_result = write_continuous_blood_glucose_batch(
                client=client,
                    sid=sid,
                    phone_id=MANUAL_RECORD_SID,
                    samples=[
                        (sample.timestamp, sample.glucose_mmol_l)
                        for sample in samples
                    ],
                )
            print(json.dumps({
                "status": "ok",
                "source": "nightscout",
                "requested_count": len(samples),
                "uploaded_count": batch_result.uploaded_count,
                "verified_count": batch_result.verified_count,
                "already_present_count": batch_result.already_present_count,
                "timestamps": list(batch_result.timestamps),
            }, ensure_ascii=False))
            return 0

        with _open_client() as client:
            if args.command == "read":
                rows = read_manual_blood_glucose(
                        client=client,
                        start_time=args.start_time,
                        end_time=args.end_time,
                    )
                print(json.dumps({
                        "status": "ok",
                        "count": len(rows),
                        "records": rows,
                }, ensure_ascii=False))
                return 0

            result = write_manual_blood_glucose(
                    client=client,
                value_mmol_l=args.value,
                timestamp=args.timestamp,
                phone_id=args.phone_id or MANUAL_RECORD_SID,
                measurement_period=args.measurement_period,
                zone_offset_seconds=args.zone_offset_seconds,
            )
    except (httpx.HTTPError, XiaomiBloodGlucoseWriteError, ValueError) as exc:
        print(f"Xiaomi blood glucose operation failed: {exc}", file=sys.stderr)
        return 1

    output = {
        "status": "ok",
        "sent": result.uploaded,
        "verified": result.verified,
        "already_present": result.already_present,
        "timestamp": result.timestamp,
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
