from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sys
from typing import Any

import httpx

from .blood_glucose import (
    MANUAL_RECORD_SID,
    XiaomiBloodGlucoseConflictError,
    XiaomiBloodGlucoseWriteError,
    build_continuous_blood_glucose_upload,
    read_continuous_blood_glucose,
    upload_response_succeeded,
)
from .blood_glucose_cli import load_current_wearable_sid
from .config import load_credentials, load_settings
from .sync_store import DirectSupabaseStore
from .xiaomi import XiaomiHealthClient


STATE_SOURCE = "nightscout_to_xiaomi"
STATE_KEY = "blood_sugar"
DEFAULT_BATCH_SIZE = 12
DEFAULT_PENDING_LIMIT = 48
INITIAL_LOOKBACK_MS = 30 * 60 * 1000
BOOTSTRAP_CLOUD_LOOKBACK_SECONDS = 24 * 60 * 60
VALUE_TOLERANCE = 0.02


@dataclass(frozen=True)
class CgmSubmitResult:
    accepted_count: int
    already_present_count: int
    timestamps: tuple[int, ...]
    accepted_timestamps: tuple[int, ...]
    already_present_timestamps: tuple[int, ...]


def normalize_cgm_timestamp(timestamp: int) -> int:
    ts = int(timestamp)
    if ts <= 0:
        raise ValueError("timestamp must be positive")
    return (ts // 60) * 60


def _measured_at_ms(row: dict[str, Any]) -> int:
    measured_at = row.get("measured_at")
    if not measured_at:
        raise ValueError("Nightscout glucose sample is missing measured_at")
    parsed = datetime.fromisoformat(str(measured_at).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _parse_cloud_row(row: dict[str, Any]) -> tuple[int, float] | None:
    raw = row.get("value")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        value = raw
    if not isinstance(value, dict):
        return None
    try:
        timestamp = normalize_cgm_timestamp(int(value.get("time", row.get("time"))))
        glucose = float(value["blood_sugar"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp, glucose


def _cloud_match(
    rows: list[dict[str, Any]],
    *,
    timestamp: int,
    value: float,
) -> tuple[bool, bool]:
    target_ts = normalize_cgm_timestamp(timestamp)
    same_timestamp = False
    for row in rows:
        parsed = _parse_cloud_row(row)
        if parsed is None:
            continue
        row_ts, row_value = parsed
        if row_ts != target_ts:
            continue
        same_timestamp = True
        if abs(row_value - float(value)) <= VALUE_TOLERANCE:
            return True, True
    return same_timestamp, False


def submit_continuous_blood_glucose_batch(
    *,
    client: Any,
    sid: str,
    phone_id: str,
    samples: list[tuple[int, float]],
) -> CgmSubmitResult:
    normalized: list[tuple[int, float]] = []
    seen: set[int] = set()
    for timestamp, value in samples:
        ts = normalize_cgm_timestamp(timestamp)
        glucose = float(value)
        if glucose <= 0:
            raise ValueError("value_mmol_l must be positive")
        if ts in seen:
            raise ValueError("continuous glucose sample timestamps must be unique after minute normalization")
        seen.add(ts)
        normalized.append((ts, glucose))
    if not normalized:
        raise ValueError("samples must not be empty")

    timestamps = tuple(ts for ts, _ in normalized)
    window_start = max(0, min(timestamps) - 300)
    window_end = max(timestamps) + 300
    existing = read_continuous_blood_glucose(
        client=client,
        start_time=window_start,
        end_time=window_end,
    )

    missing: list[tuple[int, float]] = []
    already_present: list[int] = []
    for ts, glucose in normalized:
        same_timestamp, same_value = _cloud_match(
            existing,
            timestamp=ts,
            value=glucose,
        )
        if same_value:
            already_present.append(ts)
        elif same_timestamp:
            raise XiaomiBloodGlucoseConflictError(
                "A different Xiaomi continuous glucose value already exists at a target minute"
            )
        else:
            missing.append((ts, glucose))

    if missing:
        request = build_continuous_blood_glucose_upload(
            sid=str(sid),
            phone_id=str(phone_id),
            samples=missing,
        )
        response = client.encrypted_post(request.path, request.payload)
        if not upload_response_succeeded(response.status_code, response.json_data):
            body = response.json_data if isinstance(response.json_data, dict) else {}
            raise XiaomiBloodGlucoseWriteError(
                "Xiaomi continuous glucose upload rejected: "
                f"HTTP {response.status_code}, code={body.get('code')}, message={body.get('message')}"
            )

    accepted = tuple(ts for ts, _ in missing)
    return CgmSubmitResult(
        accepted_count=len(accepted),
        already_present_count=len(already_present),
        timestamps=timestamps,
        accepted_timestamps=accepted,
        already_present_timestamps=tuple(already_present),
    )


def _load_state(store: Any) -> dict[str, Any]:
    loader = getattr(store, "load_cgm_mirror_state", None)
    if callable(loader):
        state = loader()
        return dict(state) if isinstance(state, dict) else {}

    with httpx.Client(timeout=30.0, transport=getattr(store, "transport", None)) as client:
        response = client.get(
            f"{store.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
            params={
                "select": "next_start_time,source_check_detail",
                "source": f"eq.{STATE_SOURCE}",
                "key": f"eq.{STATE_KEY}",
                "limit": "1",
            },
            headers=store._headers(),
        )
        response.raise_for_status()
        rows = response.json()
    if not rows or not isinstance(rows[0], dict):
        return {}
    return dict(rows[0])


def _save_state(store: Any, *, checkpoint_ms: int, detail: dict[str, Any]) -> None:
    saver = getattr(store, "save_cgm_mirror_state", None)
    if callable(saver):
        saver(checkpoint_ms=int(checkpoint_ms), detail=detail)
        return

    checked_at = datetime.now(timezone.utc).isoformat()
    fields = {
        "next_start_time": int(checkpoint_ms),
        "source_check_detail": detail,
        "source_checked_at": checked_at,
        "source_check_status": "success",
    }
    headers = store._headers()
    headers["Prefer"] = "return=representation"
    with httpx.Client(timeout=30.0, transport=getattr(store, "transport", None)) as client:
        response = client.patch(
            f"{store.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
            params={"source": f"eq.{STATE_SOURCE}", "key": f"eq.{STATE_KEY}"},
            headers=headers,
            json=fields,
        )
        response.raise_for_status()
        rows = response.json()
        if rows:
            return
        response = client.post(
            f"{store.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
            params={"on_conflict": "source,key"},
            headers=store._headers(),
            json={"source": STATE_SOURCE, "key": STATE_KEY, **fields},
        )
        response.raise_for_status()


def _sanitize_pending(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    pending: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            source_ms = int(item["source_ms"])
            xiaomi_ts = normalize_cgm_timestamp(int(item["xiaomi_ts"]))
            glucose = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if source_ms <= 0 or glucose <= 0:
            continue
        pending.append({"source_ms": source_ms, "xiaomi_ts": xiaomi_ts, "value": glucose})
    return pending


def _verify_pending(client: Any, pending: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if not pending:
        return [], 0
    start = max(0, min(int(item["xiaomi_ts"]) for item in pending) - 300)
    end = max(int(item["xiaomi_ts"]) for item in pending) + 300
    rows = read_continuous_blood_glucose(client=client, start_time=start, end_time=end)
    remaining: list[dict[str, Any]] = []
    verified = 0
    for item in pending:
        same_timestamp, same_value = _cloud_match(
            rows,
            timestamp=int(item["xiaomi_ts"]),
            value=float(item["value"]),
        )
        if same_value:
            verified += 1
            continue
        if same_timestamp:
            raise XiaomiBloodGlucoseConflictError(
                "A different Xiaomi continuous glucose value appeared at a pending minute"
            )
        remaining.append(item)
    return remaining, verified


def _bootstrap_checkpoint_ms(client: Any, *, now_ms: int) -> int:
    end = int(now_ms // 1000)
    start = max(0, end - BOOTSTRAP_CLOUD_LOOKBACK_SECONDS)
    rows = read_continuous_blood_glucose(client=client, start_time=start, end_time=end)
    timestamps = [parsed[0] for row in rows if (parsed := _parse_cloud_row(row)) is not None]
    if timestamps:
        # Xiaomi stores CGM points at minute precision. Skip the whole latest visible minute.
        return (max(timestamps) + 59) * 1000
    return int(now_ms) - INITIAL_LOOKBACK_MS


def mirror_nightscout_to_xiaomi_cgm(
    *,
    store: Any,
    client: Any,
    sid: str,
    phone_id: str = MANUAL_RECORD_SID,
    now_ms: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    pending_limit: int = DEFAULT_PENDING_LIMIT,
    submitter: Any = submit_continuous_blood_glucose_batch,
) -> dict[str, int | str | None]:
    if int(batch_size) <= 0:
        raise ValueError("batch_size must be positive")
    if int(pending_limit) <= 0:
        raise ValueError("pending_limit must be positive")
    current_ms = int(now_ms) if now_ms is not None else int(datetime.now(timezone.utc).timestamp() * 1000)

    state = _load_state(store)
    detail = state.get("source_check_detail") if isinstance(state.get("source_check_detail"), dict) else {}
    pending = _sanitize_pending(detail.get("pending"))
    pending, verified_pending_count = _verify_pending(client, pending)

    raw_checkpoint = state.get("next_start_time")
    try:
        checkpoint_ms = int(raw_checkpoint) if raw_checkpoint is not None else None
    except (TypeError, ValueError):
        checkpoint_ms = None
    if checkpoint_ms is None:
        checkpoint_ms = _bootstrap_checkpoint_ms(client, now_ms=current_ms)

    capacity = max(0, int(pending_limit) - len(pending))
    rows: list[dict[str, Any]] = []
    if capacity > 0:
        rows = store.load_nightscout_glucose_samples_after(
            checkpoint_ms,
            limit=min(int(batch_size), capacity),
        )
        rows = sorted(rows, key=_measured_at_ms)

    accepted_count = 0
    already_present_count = 0
    processed_count = 0
    if rows:
        prepared: list[tuple[int, float]] = []
        source_by_xiaomi_ts: dict[int, dict[str, Any]] = {}
        for row in rows:
            source_ms = _measured_at_ms(row)
            try:
                glucose = float(row["glucose_mmol_l"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Nightscout glucose sample is missing glucose_mmol_l") from exc
            if glucose <= 0:
                raise ValueError("Nightscout glucose value must be positive")
            source_seconds = source_ms // 1000
            xiaomi_ts = normalize_cgm_timestamp(source_seconds)
            prepared.append((source_seconds, glucose))
            source_by_xiaomi_ts[xiaomi_ts] = {
                "source_ms": source_ms,
                "xiaomi_ts": xiaomi_ts,
                "value": glucose,
            }

        result = submitter(
            client=client,
            sid=str(sid),
            phone_id=str(phone_id),
            samples=prepared,
        )
        accepted_count = int(result.accepted_count)
        already_present_count = int(result.already_present_count)
        for xiaomi_ts in result.accepted_timestamps:
            item = source_by_xiaomi_ts.get(int(xiaomi_ts))
            if item is not None:
                pending.append(item)
        processed_count = len(rows)
        checkpoint_ms = max(_measured_at_ms(row) for row in rows)

    new_detail = {
        "kind": "cgm_mirror_v2",
        "pending": pending,
        "verified_pending_count": verified_pending_count,
        "last_accepted_count": accepted_count,
        "last_already_present_count": already_present_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(store, checkpoint_ms=checkpoint_ms, detail=new_detail)

    return {
        "status": "ok",
        "mode": "blood_sugar",
        "processed_count": processed_count,
        "accepted_count": accepted_count,
        "already_present_count": already_present_count,
        "verified_pending_count": verified_pending_count,
        "pending_count": len(pending),
        "checkpoint_ms": checkpoint_ms,
    }


def run() -> int:
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for Xiaomi CGM mirroring.", file=sys.stderr)
        return 2
    store = DirectSupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    try:
        credentials = load_credentials(settings.credentials_file, settings.region)
        sid = load_current_wearable_sid(settings)
        with XiaomiHealthClient(settings, credentials) as client:
            result = mirror_nightscout_to_xiaomi_cgm(
                store=store,
                client=client,
                sid=sid,
            )
    except (httpx.HTTPError, XiaomiBloodGlucoseWriteError, OSError, ValueError) as exc:
        print(f"Xiaomi CGM mirror failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
