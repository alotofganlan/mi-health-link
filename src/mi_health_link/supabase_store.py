from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

import httpx

from .parsers import parse_sleep_entry

GENERIC_NORMALIZED_KEYS = frozenset({
    "abnormal_heart_beat",
    "calories",
    "grade_prediction",
    "menstrual_symptoms",
    "pai",
    "resting_heart_rate",
    "running_ability_index",
    "single_temperature",
    "steps",
    "stress",
    "temperature_trend",
    "training_load",
    "valid_stand",
    "vitality",
})


def stable_source_record_id(record_type: str, payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(f"{record_type}\0{canonical}".encode("utf-8")).hexdigest()
    return digest


def _epoch_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _normalized_record_id(record: dict[str, Any]) -> str:
    watermark = record.get("watermark")
    if watermark not in (None, ""):
        return str(watermark)
    sid = record.get("sid")
    key = str(record.get("key") or "")
    time_value = record.get("time")
    if sid not in (None, "") and time_value is not None:
        return f"{sid}:{key}:{time_value}"
    return stable_source_record_id(f"health_record:{key}", record)


def _normalized_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": record.get("key"),
        "sid": record.get("sid"),
        "tag": record.get("tag"),
        "zone_name": record.get("zone_name"),
        "zone_offset": record.get("zone_offset"),
        "update_time": record.get("update_time"),
        "watermark": record.get("watermark"),
        "metrics": record.get("metrics"),
    }


def _sleep_source_kind(record: dict[str, Any]) -> str:
    sid = str(record.get("sid") or "")
    if sid.startswith("hlth.gen_"):
        return "phone"
    value = record.get("value")
    if not isinstance(value, dict) or value.get("sleep_trace_duration") is None:
        return "watch"
    segments = value.get("items")
    if isinstance(segments, list) and segments and all(
        isinstance(segment, dict) and segment.get("state") == 6
        for segment in segments
    ):
        return "phone"
    return "watch"


def _sleep_day(record: dict[str, Any]) -> str | None:
    try:
        timestamp = int(record.get("time"))
        offset = int(record.get("zone_offset") or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp + offset, tz=timezone.utc).date().isoformat()


@dataclass
class SupabaseStore:
    url: str
    service_role_key: str
    transport: httpx.BaseTransport | None = None

    def _headers(self) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        if not self.service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        return headers

    def _load_registry(self, record_type: str) -> set[str]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/raw_records",
                params={
                    "select": "source_record_id",
                    "source": "eq.xiaomi",
                    "record_type": f"eq.{record_type}",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return {
            str(row["source_record_id"])
            for row in rows
            if row.get("source_record_id")
        }

    def load_observed_keys(self) -> set[str]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/raw_records",
                params={
                    "select": "payload",
                    "source": "eq.xiaomi",
                    "record_type": "like.discover:*",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()

        keys: set[str] = set()
        for row in rows:
            payload = row.get("payload")
            if not isinstance(payload, dict):
                continue
            api_response = payload.get("response")
            if not isinstance(api_response, dict) or api_response.get("code") not in (None, 0):
                continue
            result = api_response.get("result")
            if not isinstance(result, dict):
                continue
            data_list = result.get("data_list")
            if not isinstance(data_list, list):
                continue
            for item in data_list:
                if isinstance(item, dict) and item.get("key") is not None:
                    keys.add(str(item["key"]))
        return keys

    def load_discovered_keys(self) -> set[str]:
        return self._load_registry("discovered_key")

    def load_backfilled_keys(self) -> set[str]:
        return self._load_registry("history_backfilled_key")

    def load_backfill_progress(self, key: str) -> int | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/raw_records",
                params={
                    "select": "payload",
                    "source": "eq.xiaomi",
                    "record_type": "eq.history_backfill_progress",
                    "source_record_id": f"eq.{key}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows:
            return None
        payload = rows[0].get("payload")
        if not isinstance(payload, dict):
            return None
        value = payload.get("next_start_time")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def remember_discovered_key(self, key: str) -> str:
        return self.save_raw(
            record_type="discovered_key",
            payload={"key": key},
            source_record_id=key,
        )

    def remember_backfilled_key(self, key: str) -> str:
        return self.save_raw(
            record_type="history_backfilled_key",
            payload={"key": key},
            source_record_id=key,
        )

    def remember_backfill_progress(self, key: str, next_start_time: int) -> str:
        return self.save_raw(
            record_type="history_backfill_progress",
            payload={"key": key, "next_start_time": int(next_start_time)},
            source_record_id=key,
        )

    def _upsert_table(
        self,
        *,
        table: str,
        body: Any,
        on_conflict: str,
        return_representation: bool = False,
    ) -> list[dict[str, Any]]:
        headers = self._headers()
        if return_representation:
            headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/{table}",
                params={"on_conflict": on_conflict},
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            if not return_representation:
                return []
            payload = response.json()
        return payload if isinstance(payload, list) else []

    def _delete_table(self, *, table: str, params: dict[str, str]) -> None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.delete(
                f"{self.url.rstrip('/')}/rest/v1/{table}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()

    @staticmethod
    def _sum_numeric(rows: list[dict[str, Any]], field: str) -> int | float | None:
        values = [
            row.get(field)
            for row in rows
            if isinstance(row.get(field), (int, float))
            and not isinstance(row.get(field), bool)
        ]
        if not values:
            return None
        total = sum(values)
        if all(isinstance(value, int) for value in values):
            return int(total)
        return round(float(total), 6)

    def _diet_meal_id(self, source_meal_id: str) -> int | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/diet_meals",
                params={
                    "select": "id",
                    "source": "eq.xiaomi",
                    "source_meal_id": f"eq.{source_meal_id}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows:
            return None
        try:
            return int(rows[0]["id"])
        except (KeyError, TypeError, ValueError):
            return None

    def upsert_diet_records(self, records: list[dict[str, Any]]) -> int:
        """Persist normalized per-food rows into the meal and item tables."""
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in records:
            if not isinstance(row, dict):
                continue
            source_meal_id = row.get("source_meal_id")
            if source_meal_id in (None, ""):
                continue
            groups.setdefault(str(source_meal_id), []).append(row)

        handled = 0
        for source_meal_id, rows in groups.items():
            first = rows[0]
            meal_rows = self._upsert_table(
                table="diet_meals",
                on_conflict="source,source_meal_id",
                return_representation=True,
                body={
                    "source": "xiaomi",
                    "source_meal_id": source_meal_id,
                    "meal_date": first.get("meal_date"),
                    "dining": first.get("dining"),
                    "meal_type": first.get("meal_type") or "other",
                    "recorded_at": min(
                        (
                            str(row["eaten_at"])
                            for row in rows
                            if row.get("eaten_at") is not None
                        ),
                        default=None,
                    ),
                    "total_calorie_kcal": self._sum_numeric(rows, "calories"),
                    "total_protein_g": self._sum_numeric(rows, "protein"),
                    "total_carbohydrate_g": self._sum_numeric(rows, "carbohydrate"),
                    "total_fat_g": self._sum_numeric(rows, "fat"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "raw": {
                        "records": [
                            row.get("raw", {}).get("record")
                            for row in rows
                            if isinstance(row.get("raw"), dict)
                            and isinstance(row["raw"].get("record"), dict)
                        ]
                    },
                },
            )
            meal_id: int | None = None
            if meal_rows and isinstance(meal_rows[0], dict):
                try:
                    meal_id = int(meal_rows[0]["id"])
                except (KeyError, TypeError, ValueError):
                    meal_id = None
            if meal_id is None:
                meal_id = self._diet_meal_id(source_meal_id)
            if meal_id is None:
                raise RuntimeError(
                    f"Supabase did not return id for diet meal {source_meal_id}"
                )

            self._delete_table(
                table="diet_food_items",
                params={"diet_meal_id": f"eq.{meal_id}"},
            )
            food_bodies = [
                {
                    "diet_meal_id": meal_id,
                    "source": "xiaomi",
                    "source_item_id": str(
                        row.get("source_item_id") or row.get("source_record_id")
                    ),
                    "item_index": int(row.get("item_index") or 0),
                    "food_id": (
                        None
                        if row.get("food_id") is None
                        else str(row.get("food_id"))
                    ),
                    "name": row.get("food_item"),
                    "quantity": row.get("amount"),
                    "unit": row.get("amount_unit"),
                    "calorie_kcal": row.get("calories"),
                    "protein_g": row.get("protein"),
                    "carbohydrate_g": row.get("carbohydrate"),
                    "fat_g": row.get("fat"),
                    "dietary_fiber_g": row.get("dietary_fiber"),
                    "minerals": row.get("minerals"),
                    "vitamins": row.get("vitamins"),
                    "health_light": row.get("health_light"),
                    "image_url": row.get("image_url"),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "raw": (
                        row.get("raw", {}).get("food", {})
                        if isinstance(row.get("raw"), dict)
                        else {}
                    ),
                }
                for row in rows
            ]
            if food_bodies:
                self._upsert_table(
                    table="diet_food_items",
                    on_conflict="source,source_item_id,item_index",
                    body=food_bodies,
                )
                handled += len(food_bodies)
        return handled

    def _write_for_record(
        self,
        record: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        key = str(record.get("key") or "")
        value = record.get("value")
        metrics = record.get("metrics")
        measured_at = _epoch_iso(record.get("time"))
        if measured_at is None:
            return None
        metadata = _normalized_metadata(record)

        if key == "heart_rate" and isinstance(value, dict) and value.get("bpm") is not None:
            return (
                "heart_rate_samples",
                "source,measured_at",
                {
                    "source": "xiaomi",
                    "measured_at": measured_at,
                    "bpm": int(value["bpm"]),
                    "source_record_id": _normalized_record_id(record),
                    "raw": metadata,
                },
            )

        if key == "spo2" and isinstance(value, dict) and value.get("spo2") is not None:
            return (
                "spo2_samples",
                "source,measured_at",
                {
                    "source": "xiaomi",
                    "measured_at": measured_at,
                    "percent": float(value["spo2"]),
                    "source_record_id": _normalized_record_id(record),
                    "raw": metadata,
                },
            )

        if key == "intensity" and record.get("sid") not in (None, ""):
            return (
                "intensity_samples",
                "source,source_record_id,measured_at",
                {
                    "source": "xiaomi",
                    "source_record_id": str(record["sid"]),
                    "measured_at": measured_at,
                    "zone_name": record.get("zone_name"),
                    "zone_offset": record.get("zone_offset"),
                    "source_updated_at": _epoch_iso(record.get("update_time")),
                    "raw_record_id": None,
                },
            )

        if key == "weight" and isinstance(metrics, dict):
            return (
                "body_measurements",
                "source,source_record_id",
                {
                    "source": "xiaomi",
                    "source_record_id": _normalized_record_id(record),
                    "measured_at": measured_at,
                    "weight_kg": metrics.get("weight_kg"),
                    "bmi": metrics.get("bmi"),
                    "body_fat_pct": metrics.get("body_fat_percent"),
                    "muscle_mass_kg": metrics.get("muscle_mass_kg"),
                    "bone_mass_kg": metrics.get("bone_mass_kg"),
                    "body_water_pct": metrics.get("body_water_percent"),
                    "basal_metabolism_kcal": metrics.get("basal_metabolic_rate_kcal"),
                    "raw": metadata,
                },
            )

        if key == "menstruation" and isinstance(metrics, dict):
            boundary_type = metrics.get("boundary_type")
            if boundary_type not in ("period_start", "period_end"):
                return None
            date_time = _epoch_iso(metrics.get("date_time")) or measured_at
            return (
                "menstrual_records",
                "source,source_record_id",
                {
                    "source": "xiaomi",
                    "source_record_id": _normalized_record_id(record),
                    "record_type": boundary_type,
                    "start_at": date_time,
                    "end_at": None,
                    "value": None,
                    "raw": {
                        **metadata,
                        "status_code": metrics.get("status_code"),
                        "source_update_time": metrics.get("update_time"),
                    },
                },
            )

        if key in GENERIC_NORMALIZED_KEYS:
            return (
                "health_records",
                "source,key,source_record_id",
                {
                    "source": "xiaomi",
                    "key": key,
                    "source_record_id": _normalized_record_id(record),
                    "measured_at": measured_at,
                    "source_updated_at": _epoch_iso(record.get("update_time")),
                    "sid": None if record.get("sid") is None else str(record.get("sid")),
                    "zone_name": record.get("zone_name"),
                    "zone_offset": record.get("zone_offset"),
                    "value": value,
                    "metrics": metrics if isinstance(metrics, dict) else {},
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        return None

    def _upsert_sleep(self, record: dict[str, Any]) -> bool:
        raw_item = record.get("raw_item")
        value = record.get("value")
        day = _sleep_day(record)
        if not isinstance(raw_item, dict) or not isinstance(value, dict) or day is None:
            return False
        if value.get("bedtime") is None or value.get("wake_up_time") is None:
            return False

        source_kind = _sleep_source_kind(record)
        headers = self._headers()
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            if source_kind == "phone":
                existing_watch = client.get(
                    f"{self.url.rstrip('/')}/rest/v1/sleep_sessions",
                    params={
                        "select": "id",
                        "source": "eq.xiaomi",
                        "raw->>sleep_day": f"eq.{day}",
                        "raw->>sleep_source": "eq.watch",
                        "limit": "1",
                    },
                    headers=headers,
                )
                existing_watch.raise_for_status()
                if existing_watch.json():
                    return True
            else:
                existing_phone = client.get(
                    f"{self.url.rstrip('/')}/rest/v1/sleep_sessions",
                    params={
                        "select": "id",
                        "source": "eq.xiaomi",
                        "raw->>sleep_day": f"eq.{day}",
                        "raw->>sleep_source": "eq.phone",
                    },
                    headers=headers,
                )
                existing_phone.raise_for_status()
                for row in existing_phone.json():
                    session_id = row.get("id")
                    if session_id is None:
                        continue
                    stages = client.delete(
                        f"{self.url.rstrip('/')}/rest/v1/sleep_stages",
                        params={"sleep_session_id": f"eq.{session_id}"},
                        headers=headers,
                    )
                    stages.raise_for_status()
                    session = client.delete(
                        f"{self.url.rstrip('/')}/rest/v1/sleep_sessions",
                        params={"id": f"eq.{session_id}"},
                        headers=headers,
                    )
                    session.raise_for_status()

        parsed = parse_sleep_entry(raw_item)
        metrics = record.get("metrics") if isinstance(record.get("metrics"), dict) else {}
        rows = self._upsert_table(
            table="sleep_sessions",
            on_conflict="source,source_record_id",
            return_representation=True,
            body={
                "source": "xiaomi",
                "source_record_id": _normalized_record_id(record),
                "start_at": parsed.start_at,
                "end_at": parsed.end_at,
                "score": metrics.get("sleep_score"),
                "avg_hr": metrics.get("avg_sleep_heart_rate_bpm"),
                "avg_spo2": metrics.get("avg_sleep_spo2_percent"),
                "avg_hrv_ms": metrics.get("avg_sleep_hrv_ms"),
                "raw": {
                    **_normalized_metadata(record),
                    "sleep_source": source_kind,
                    "sleep_day": day,
                },
            },
        )
        if not rows or rows[0].get("id") is None:
            return True

        session_id = rows[0]["id"]
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            delete = client.delete(
                f"{self.url.rstrip('/')}/rest/v1/sleep_stages",
                params={"sleep_session_id": f"eq.{session_id}"},
                headers=self._headers(),
            )
            delete.raise_for_status()
            if source_kind == "watch" and parsed.stages:
                stage_rows = [
                    {
                        "sleep_session_id": session_id,
                        "stage": stage["stage"],
                        "start_at": stage["start_at"],
                        "end_at": stage["end_at"],
                        "raw": {
                            "state_code": stage["state"],
                            "duration_min": stage["duration_min"],
                        },
                    }
                    for stage in parsed.stages
                ]
                insert = client.post(
                    f"{self.url.rstrip('/')}/rest/v1/sleep_stages",
                    headers=self._headers(),
                    json=stage_rows,
                )
                insert.raise_for_status()
        return True

    def upsert_normalized_records(self, records: list[dict[str, Any]]) -> int:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        handled = 0

        for record in records:
            if not isinstance(record, dict):
                continue
            if record.get("key") == "sleep":
                handled += int(self._upsert_sleep(record))
                continue
            write = self._write_for_record(record)
            if write is None:
                continue
            table, on_conflict, body = write
            grouped.setdefault((table, on_conflict), []).append(body)
            handled += 1

        for (table, on_conflict), bodies in grouped.items():
            self._upsert_table(
                table=table,
                on_conflict=on_conflict,
                body=bodies,
            )
        return handled

    def upsert_normalized_record(self, record: dict[str, Any]) -> bool:
        if not isinstance(record, dict):
            return False
        if record.get("key") == "sleep":
            return self._upsert_sleep(record)
        write = self._write_for_record(record)
        if write is None:
            return False
        table, on_conflict, body = write
        self._upsert_table(table=table, on_conflict=on_conflict, body=body)
        return True

    def delete_raw_type(self, *, record_type: str, source: str = "xiaomi") -> None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.delete(
                f"{self.url.rstrip('/')}/rest/v1/raw_records",
                params={
                    "source": f"eq.{source}",
                    "record_type": f"eq.{record_type}",
                },
                headers=self._headers(),
            )
            response.raise_for_status()

    def save_raw(
        self,
        *,
        record_type: str,
        payload: Any,
        measured_at: str | None = None,
        source_record_id: str | None = None,
        source: str = "xiaomi",
    ) -> str:
        rid = source_record_id or stable_source_record_id(record_type, payload)
        body = {
            "source": source,
            "record_type": record_type,
            "source_record_id": rid,
            "measured_at": measured_at,
            "payload": payload,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/raw_records",
                params={"on_conflict": "source,record_type,source_record_id"},
                headers=self._headers(),
                json=body,
            )
            response.raise_for_status()
        return rid
