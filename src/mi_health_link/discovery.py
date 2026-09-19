from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable


DEFAULT_DISCOVERY_KEYS = [
    # Activity / daily summaries
    "steps",
    "calories",
    "distance",
    "intensity",
    "valid_stand",
    # Cardiovascular
    "heart_rate",
    "resting_heart_rate",
    "abnormal_heart_beat",
    "blood_pressure",
    "vo2_max",
    "hrv",
    # Sleep / recovery
    "sleep",
    "stress",
    # Oxygen
    "spo2",
    # Body / temperature
    "weight",
    "body_composition",
    "temperature_characteristic",
    "single_temperature",
    # Common profile/goal-ish candidates seen around Mi Fitness ecosystems
    "goal",
]


@dataclass(frozen=True)
class DiscoveryItem:
    key: str
    http_status: int
    api_code: int | str | None
    message: str | None
    record_count: int
    returned_keys: list[str]
    newest_time: int | None
    has_data: bool


def summarize_discovery_response(
    key: str,
    *,
    http_status: int,
    response_json: Any | None,
) -> DiscoveryItem:
    api_code = None
    message = None
    data_list: list[Any] = []

    if isinstance(response_json, dict):
        api_code = response_json.get("code")
        message = response_json.get("message")
        result = response_json.get("result")
        if isinstance(result, dict):
            maybe = result.get("data_list")
            if isinstance(maybe, list):
                data_list = maybe

    returned_keys: set[str] = set()
    newest_time: int | None = None

    for row in data_list:
        if not isinstance(row, dict):
            continue
        row_key = row.get("key")
        if row_key is not None:
            returned_keys.add(str(row_key))

        for field in ("time", "update_time"):
            value = row.get(field)
            if isinstance(value, (int, float)):
                ivalue = int(value)
                newest_time = ivalue if newest_time is None else max(newest_time, ivalue)

    return DiscoveryItem(
        key=key,
        http_status=http_status,
        api_code=api_code,
        message=str(message) if message is not None else None,
        record_count=len(data_list),
        returned_keys=sorted(returned_keys),
        newest_time=newest_time,
        has_data=len(data_list) > 0,
    )


def summary_payload(items: Iterable[DiscoveryItem]) -> dict[str, Any]:
    rows = list(items)
    return {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(rows),
        "keys_with_data": [x.key for x in rows if x.has_data],
        "keys_without_data": [x.key for x in rows if not x.has_data and x.api_code == 0],
        "keys_with_errors": [
            x.key for x in rows
            if x.api_code not in (None, 0) or not (200 <= x.http_status < 300)
        ],
        "items": [asdict(x) for x in rows],
    }
