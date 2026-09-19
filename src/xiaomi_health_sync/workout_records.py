from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


def _parse_value(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw.get("value")
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _source_record_id(raw: dict[str, Any]) -> str:
    watermark = raw.get("watermark")
    if watermark is not None:
        return str(watermark)
    sid = raw.get("sid") or "unknown"
    key = raw.get("key") or raw.get("category") or "workout"
    time = raw.get("time") or "unknown"
    return f"{sid}:{key}:{time}"


def _epoch_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def extract_workout_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    sport_records = result.get("sport_records")
    if not isinstance(sport_records, list):
        return []

    records: list[dict[str, Any]] = []
    for raw in sport_records:
        if not isinstance(raw, dict):
            continue
        value = _parse_value(raw)
        total_calories = value.get("total_cal", raw.get("total_cal"))
        activity_calories = value.get("calories", raw.get("calories"))
        calories = total_calories if total_calories is not None else activity_calories
        record = {
            "source_record_id": _source_record_id(raw),
            "sport_type": raw.get("key") or value.get("sport_type"),
            "sport_type_code": value.get("sport_type"),
            "category": raw.get("category"),
            "start_time": value.get("start_time", raw.get("time")),
            "end_time": value.get("end_time", raw.get("end_time")),
            "duration": value.get("duration", raw.get("duration")),
            "distance": value.get("distance", raw.get("distance")),
            "calories": calories,
            "total_calories": total_calories,
            "activity_calories": activity_calories,
            "steps": value.get("steps", raw.get("steps")),
            "avg_heart_rate": value.get("avg_hrm", raw.get("avg_hrm", raw.get("avg_heart_rate"))),
            "max_heart_rate": value.get("max_hrm", raw.get("max_hrm", raw.get("max_heart_rate"))),
            "min_heart_rate": value.get("min_hrm", raw.get("min_hrm", raw.get("min_heart_rate"))),
            "avg_pace": value.get("avg_pace", raw.get("avg_pace")),
            "training_load": value.get("train_load", raw.get("train_load")),
            "recover_time": value.get("recover_time", raw.get("recover_time")),
            "train_effect": value.get("train_effect", raw.get("train_effect")),
            "anaerobic_train_effect": value.get(
                "anaerobic_train_effect", raw.get("anaerobic_train_effect")
            ),
            "training_experience": value.get(
                "training_experience", raw.get("training_experience")
            ),
            "vitality": value.get("vitality", raw.get("vitality")),
            "zone_offset": raw.get("zone_offset"),
            "zone_name": raw.get("zone_name"),
            "sid": raw.get("sid"),
            "watermark": raw.get("watermark"),
            "raw_value": value,
            "raw_record": dict(raw),
        }
        records.append(record)
    return records


def workout_table_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        start_at = _epoch_iso(record.get("start_time"))
        end_at = _epoch_iso(record.get("end_time"))
        source_record_id = record.get("source_record_id")
        if start_at is None or end_at is None or source_record_id in (None, ""):
            continue
        rows.append({
            "source": "xiaomi",
            "source_record_id": str(source_record_id),
            "activity_type": record.get("sport_type") or record.get("category"),
            "start_at": start_at,
            "end_at": end_at,
            "duration_s": record.get("duration"),
            "distance_m": record.get("distance"),
            "calories_kcal": record.get("calories"),
            "avg_hr": record.get("avg_heart_rate"),
            "max_hr": record.get("max_heart_rate"),
            "min_hr": record.get("min_heart_rate"),
            "steps": record.get("steps"),
            "vo2_max": None,
            "training_load": record.get("training_load"),
            # Preserve the raw recovery code until its unit is verified.
            "recovery_min": None,
            "route": None,
            "raw": {
                "sport_type_code": record.get("sport_type_code"),
                "sport_type": record.get("sport_type"),
                "category": record.get("category"),
                "sid": record.get("sid"),
                "zone_name": record.get("zone_name"),
                "zone_offset": record.get("zone_offset"),
                "total_calories_kcal": record.get("total_calories"),
                "activity_calories_kcal": record.get("activity_calories"),
                "avg_pace": record.get("avg_pace"),
                "recover_time_code": record.get("recover_time"),
                "train_effect": record.get("train_effect"),
                "anaerobic_train_effect": record.get("anaerobic_train_effect"),
                "training_experience": record.get("training_experience"),
                "vitality": record.get("vitality"),
            },
        })
    return rows
