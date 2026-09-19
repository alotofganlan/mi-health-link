from __future__ import annotations

import json
from typing import Any

from .metric_mapping import map_health_value


def _parse_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def normalize_data_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None

    key = item.get("key")
    parsed_value = _parse_value(item.get("value"))
    return {
        "sid": item.get("sid"),
        "tag": item.get("tag"),
        "key": key,
        "time": item.get("time"),
        "update_time": item.get("update_time"),
        "watermark": item.get("watermark"),
        "zone_offset": item.get("zone_offset"),
        "zone_name": item.get("zone_name"),
        "value": parsed_value,
        "value_raw": parsed_value,
        "metrics": map_health_value(str(key or ""), parsed_value),
        "raw_item": dict(item),
    }
