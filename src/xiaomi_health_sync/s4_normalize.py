from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Any, Callable

import httpx

from .config import load_settings
from .health_records import normalize_data_item
from .supabase_store import GENERIC_NORMALIZED_KEYS, SupabaseStore, stable_source_record_id
from .workout_records import extract_workout_records, workout_table_rows

S4_STRUCTURED_KEYS = (
    "sleep",
    "weight",
    "single_temperature",
    "temperature_characteristic",
    "temperature_trend",
    "menstruation",
    "menstrual_symptoms",
    "energy",
    "pai",
    "vitality",
    "vo2_max",
    "training_load",
    "running_ability_index",
    "grade_prediction",
    "physical_fitness_status",
)

DIRECT_NORMALIZED_KEYS = (
    "heart_rate",
    "spo2",
    "intensity",
    "weight",
    "sleep",
    "menstruation",
)
NORMALIZED_MIGRATION_KEYS = tuple(dict.fromkeys(
    (*DIRECT_NORMALIZED_KEYS, *sorted(GENERIC_NORMALIZED_KEYS))
))


def source_record_types_for_key(key: str) -> tuple[str, str]:
    return (
        f"xiaomi:history:{key}",
        f"xiaomi:verified_key_probe:{key}",
    )


def progress_messages_for_key(*, index: int, total: int, key: str, count: int) -> tuple[str, str]:
    return (
        f"[normalize {index}/{total}] {key}: starting",
        f"[normalize {index}/{total}] {key}: {count} records",
    )


def extract_history_items(raw_payload: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        return []
    response = raw_payload.get("response")
    if not isinstance(response, dict) or response.get("code") not in (None, 0):
        return []
    result = response.get("result")
    if not isinstance(result, dict):
        return []
    data_list = result.get("data_list")
    if not isinstance(data_list, list):
        return []
    return [item for item in data_list if isinstance(item, dict)]


def _legacy_health_item(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    raw_item = payload.get("raw_item")
    return raw_item if isinstance(raw_item, dict) else None


def _decoded_value(item: dict[str, Any]) -> dict[str, Any] | None:
    value = item.get("value")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, dict) else None
    return None


def _is_phone_sleep_item(item: dict[str, Any]) -> bool:
    sid = str(item.get("sid") or "")
    if sid.startswith("hlth.gen_"):
        return True

    value = _decoded_value(item)
    if not value or value.get("sleep_trace_duration") is None:
        return False
    segments = value.get("items")
    return bool(segments) and isinstance(segments, list) and all(
        isinstance(segment, dict) and segment.get("state") == 6 for segment in segments
    )


def sleep_source_kind(item: dict[str, Any]) -> str:
    return "phone" if _is_phone_sleep_item(item) else "watch"


def _local_sleep_day(item: dict[str, Any]) -> str | None:
    try:
        timestamp = int(item.get("time"))
        zone_offset = int(item.get("zone_offset") or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp + zone_offset, tz=timezone.utc).date().isoformat()


def prefer_watch_sleep_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = {}
    ungrouped: set[int] = set()

    for index, item in enumerate(items):
        day = _local_sleep_day(item)
        if day is None:
            ungrouped.add(index)
            continue
        grouped.setdefault(day, []).append(index)

    keep: set[int] = set(ungrouped)
    for indices in grouped.values():
        watch_indices = [index for index in indices if sleep_source_kind(items[index]) == "watch"]
        keep.update(watch_indices or indices)

    return [item for index, item in enumerate(items) if index in keep]


def _record_id(key: str, item: dict[str, Any], normalized: dict[str, Any]) -> str:
    watermark = item.get("watermark")
    if watermark not in (None, ""):
        return str(watermark)
    sid = item.get("sid")
    time_value = item.get("time")
    if sid not in (None, "") and time_value is not None:
        return f"{sid}:{key}:{time_value}"
    return stable_source_record_id(f"health_record:{key}", normalized)


def _read_raw_payloads(
    *,
    client: httpx.Client,
    store: SupabaseStore,
    record_type: str,
) -> list[Any]:
    response = client.get(
        f"{store.url.rstrip('/')}/rest/v1/raw_records",
        params={
            "select": "payload",
            "source": "eq.xiaomi",
            "record_type": f"eq.{record_type}",
            "order": "fetched_at.asc",
        },
        headers=store._headers(),
    )
    response.raise_for_status()
    rows = response.json()
    return [row.get("payload") for row in rows if isinstance(row, dict)]


def _migrate_workouts(*, client: httpx.Client, store: SupabaseStore) -> int:
    records: list[dict[str, Any]] = []

    for payload in _read_raw_payloads(
        client=client,
        store=store,
        record_type="workout_record",
    ):
        if isinstance(payload, dict) and payload.get("source_record_id") is not None:
            records.append(payload)

    for payload in _read_raw_payloads(
        client=client,
        store=store,
        record_type="xiaomi:special:workout:workout_history",
    ):
        if not isinstance(payload, dict):
            continue
        response = payload.get("response")
        records.extend(extract_workout_records(response))

    deduped: dict[str, dict[str, Any]] = {}
    for record in records:
        source_record_id = record.get("source_record_id")
        if source_record_id not in (None, ""):
            deduped[str(source_record_id)] = record

    rows = workout_table_rows(list(deduped.values()))
    if rows:
        store._upsert_table(
            table="workouts",
            on_conflict="source,source_record_id",
            body=rows,
        )
    return len(rows)


def normalize_saved_s4_history(
    *,
    store: SupabaseStore,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """Idempotently migrate verified Xiaomi raw corpus rows into normalized tables.

    Source response rows are read-only migration input. This function never
    deletes/truncates raw_records and is safe to run repeatedly.
    """
    counts: dict[str, int] = {}
    progress = progress or (lambda _message: None)
    total_keys = len(NORMALIZED_MIGRATION_KEYS)

    with httpx.Client(timeout=30.0, transport=store.transport) as client:
        latest_items: list[dict[str, Any]] = []
        for payload in _read_raw_payloads(
            client=client,
            store=store,
            record_type="xiaomi:latest_fitness",
        ):
            latest_items.extend(extract_history_items(payload))

        for index, key in enumerate(NORMALIZED_MIGRATION_KEYS, start=1):
            start_message, _ = progress_messages_for_key(
                index=index,
                total=total_keys,
                key=key,
                count=0,
            )
            progress(start_message)
            candidate_items = [
                item for item in latest_items if str(item.get("key") or "") == key
            ]

            source_types = (
                *source_record_types_for_key(key),
                f"discover:{key}",
            )
            for source_record_type in source_types:
                for payload in _read_raw_payloads(
                    client=client,
                    store=store,
                    record_type=source_record_type,
                ):
                    candidate_items.extend(extract_history_items(payload))

            for payload in _read_raw_payloads(
                client=client,
                store=store,
                record_type=f"health_record:{key}",
            ):
                item = _legacy_health_item(payload)
                if item is not None:
                    candidate_items.append(item)

            if key == "sleep":
                candidate_items = prefer_watch_sleep_items(candidate_items)

            seen_ids: set[str] = set()
            normalized_records: list[dict[str, Any]] = []
            for item in candidate_items:
                normalized = normalize_data_item(item)
                if normalized is None:
                    continue
                actual_key = str(normalized.get("key") or key)
                if actual_key != key:
                    continue
                rid = _record_id(actual_key, item, normalized)
                if rid in seen_ids:
                    continue
                seen_ids.add(rid)
                normalized_records.append(normalized)

            count = store.upsert_normalized_records(normalized_records)
            counts[key] = count
            _, done_message = progress_messages_for_key(
                index=index,
                total=total_keys,
                key=key,
                count=count,
            )
            progress(done_message)

        progress("[normalize workouts] starting")
        counts["workouts"] = _migrate_workouts(client=client, store=store)
        progress(f"[normalize workouts] {counts['workouts']} records")

    return counts


def main() -> None:
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for normalized migration.", file=sys.stderr)
        raise SystemExit(2)

    store = SupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    counts = normalize_saved_s4_history(
        store=store,
        progress=lambda message: print(message, file=sys.stderr, flush=True),
    )
    print("\nSummary:")
    for key, count in counts.items():
        print(f"{key}: {count}")
