from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


DIET_RECORD_KEY = "diet"
DIET_ENDPOINT = "/app/v1/data/get_diet_records_by_time"

# Xiaomi dining codes used by the current app. Unknown codes stay "other"
# while the original numeric code is retained for future mapping.
MEAL_TYPE_BY_DINING = {
    1: "breakfast",
    2: "morning_snack",
    3: "lunch",
    4: "afternoon_snack",
    5: "dinner",
    6: "evening_snack",
}


def _parse_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _epoch_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _local_date(value: Any, zone_offset: Any) -> str | None:
    try:
        seconds = int(value)
        offset = int(zone_offset or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds + offset, tz=timezone.utc).date().isoformat()


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 6)
    try:
        return round(float(str(value)), 6)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _first(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] is not None:
            return item[name]
    return None


def _food_items(value: Any) -> list[dict[str, Any]]:
    parsed = _parse_json(value)
    if not isinstance(parsed, dict):
        return []

    item_list = parsed.get("item_list")
    if not isinstance(item_list, list):
        return []

    items: list[dict[str, Any]] = []
    for entry in item_list:
        if not isinstance(entry, dict):
            continue
        level1 = entry.get("level1")
        if isinstance(level1, dict):
            items.append(dict(level1))
        level2 = entry.get("level2")
        if isinstance(level2, list):
            items.extend(dict(item) for item in level2 if isinstance(item, dict))
    return items


def normalize_diet_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand Xiaomi diet records into one normalized row per food item."""
    rows: list[dict[str, Any]] = []

    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        eaten_at = _epoch_iso(record.get("time"))
        meal_date = _local_date(record.get("time"), record.get("zone_offset"))
        if eaten_at is None or meal_date is None:
            continue

        dining = _integer(record.get("dining"))
        meal_type = MEAL_TYPE_BY_DINING.get(dining, "other")
        source_meal_id = f"{meal_date}:{dining if dining is not None else 'unknown'}"

        watermark = record.get("watermark")
        if watermark not in (None, ""):
            base_id = str(watermark)
        else:
            base_id = ":".join(
                str(record.get(name) or "")
                for name in ("sid", "dining", "time")
            ) + f":{record_index}"

        for item_index, food in enumerate(_food_items(record.get("value"))):
            source_item_id = base_id
            rows.append(
                {
                    "source_record_id": f"{base_id}:{item_index}",
                    "source_item_id": source_item_id,
                    "item_index": item_index,
                    "source_meal_id": source_meal_id,
                    "meal_date": meal_date,
                    "dining": dining,
                    "eaten_at": eaten_at,
                    "meal_type_code": dining,
                    "meal_type": meal_type,
                    "food_item": _first(food, "name", "food_name"),
                    "food_id": _first(food, "food_id", "id"),
                    "amount": _number(_first(food, "weight", "quantity")),
                    "amount_unit": _first(food, "weight_unit", "unit"),
                    "calories": _number(_first(food, "calorie", "calories")),
                    "carbohydrate": _number(
                        _first(food, "carbohydrate", "carbs", "carbohydrate_g")
                    ),
                    "protein": _number(_first(food, "protein", "protein_g")),
                    "fat": _number(_first(food, "fat", "fat_g")),
                    "dietary_fiber": _number(
                        _first(food, "dietary_fiber", "dietary_fiber_g", "fiber")
                    ),
                    "minerals": _number(_first(food, "minerals", "mineral")),
                    "vitamins": _number(_first(food, "vitamins", "vitamin")),
                    "health_light": _integer(
                        _first(food, "health_light", "healthLight")
                    ),
                    "image_url": _first(food, "image_url", "image", "img_url"),
                    "raw": {
                        "record": dict(record),
                        "food": dict(food),
                    },
                }
            )

    return rows


def extract_diet_records(response_json: Any) -> list[dict[str, Any]]:
    if not isinstance(response_json, dict):
        return []
    result = response_json.get("result")
    if not isinstance(result, dict):
        return []
    records = result.get("diet_records")
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, dict)]
