from __future__ import annotations

import json
import sys
from typing import Any

import httpx

from .config import load_settings
from .supabase_store import SupabaseStore

AUDIT_KEYS = ("weight", "sleep", "menstruation")


def summarize_metric_fields(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    mapped_present: set[str] = set()
    unmapped_source_fields: set[str] = set()

    for payload in payloads:
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        if not isinstance(metrics, dict):
            continue

        for key, value in metrics.items():
            if key == "unmapped":
                continue
            if value is not None:
                mapped_present.add(str(key))

        unmapped = metrics.get("unmapped")
        if isinstance(unmapped, dict):
            unmapped_source_fields.update(str(key) for key in unmapped)

    return {
        "records": len(payloads),
        "mapped_present": sorted(mapped_present),
        "unmapped_source_fields": sorted(unmapped_source_fields),
    }


def summarize_deep_fields(
    *,
    sleep_payloads: list[dict[str, Any]],
    menstruation_payloads: list[dict[str, Any]],
) -> dict[str, list[str]]:
    sleep_segment_fields: set[str] = set()
    menstruation_value_fields: set[str] = set()

    for payload in sleep_payloads:
        metrics = payload.get("metrics") if isinstance(payload, dict) else None
        segments = metrics.get("segments") if isinstance(metrics, dict) else None
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if isinstance(segment, dict):
                sleep_segment_fields.update(str(key) for key in segment)

    for payload in menstruation_payloads:
        value = payload.get("value_raw") if isinstance(payload, dict) else None
        if not isinstance(value, dict):
            value = payload.get("value") if isinstance(payload, dict) else None
        if isinstance(value, dict):
            menstruation_value_fields.update(str(key) for key in value)

    return {
        "sleep_segment_fields": sorted(sleep_segment_fields),
        "menstruation_value_fields": sorted(menstruation_value_fields),
    }


def audit_saved_health_records(*, store: SupabaseStore) -> dict[str, Any]:
    headers = store._headers()
    result: dict[str, Any] = {}
    payloads_by_key: dict[str, list[dict[str, Any]]] = {}

    with httpx.Client(timeout=30.0, transport=store.transport) as client:
        for key in AUDIT_KEYS:
            response = client.get(
                f"{store.url.rstrip('/')}/rest/v1/raw_records",
                params={
                    "select": "payload",
                    "source": "eq.xiaomi",
                    "record_type": f"eq.health_record:{key}",
                    "order": "fetched_at.desc",
                    "limit": "200",
                },
                headers=headers,
            )
            response.raise_for_status()
            rows = response.json()
            payloads = [
                row.get("payload")
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("payload"), dict)
            ]
            payloads_by_key[key] = payloads
            if key in ("weight", "sleep"):
                result[key] = summarize_metric_fields(payloads)

    result["deep_structure"] = summarize_deep_fields(
        sleep_payloads=payloads_by_key.get("sleep", []),
        menstruation_payloads=payloads_by_key.get("menstruation", []),
    )
    return result


def main() -> None:
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        print("Supabase is required for S4 field audit.", file=sys.stderr)
        raise SystemExit(2)

    store = SupabaseStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
    print("[audit 1/3] reading weight fields", file=sys.stderr, flush=True)
    print("[audit 2/3] reading sleep fields", file=sys.stderr, flush=True)
    print("[audit 3/3] reading menstruation/deep structure", file=sys.stderr, flush=True)
    result = audit_saved_health_records(store=store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
