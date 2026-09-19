from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any


@dataclass(frozen=True)
class ParsedSleep:
    source_record_id: str | None
    start_at: str
    end_at: str
    total_min: int
    deep_min: int
    light_min: int
    rem_min: int
    awake_min: int
    zone_offset_seconds: int
    stages: list[dict[str, Any]]
    raw: dict[str, Any]


def _iso_from_epoch(seconds: int, offset_seconds: int) -> str:
    tz = timezone(timedelta(seconds=offset_seconds))
    return datetime.fromtimestamp(seconds, tz=tz).isoformat()


def parse_sleep_entry(entry: dict[str, Any]) -> ParsedSleep:
    value = entry.get("value")
    payload = json.loads(value) if isinstance(value, str) else dict(value or {})
    offset = int(entry.get("zone_offset") or 0)

    start = int(payload["bedtime"])
    end = int(payload["wake_up_time"])

    stage_names = {
        2: "deep",
        3: "light",
        4: "rem",
        5: "awake",
    }
    stages = []
    derived = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
    for item in payload.get("items", []):
        state = int(item.get("state", -1))
        name = stage_names.get(state, f"unknown_{state}")
        s = int(item["start_time"])
        e = int(item["end_time"])
        duration_min = max(0, round((e - s) / 60))
        if name in derived:
            derived[name] += duration_min
        stages.append({
            "stage": name,
            "state": state,
            "start_at": _iso_from_epoch(s, offset),
            "end_at": _iso_from_epoch(e, offset),
            "duration_min": duration_min,
        })

    return ParsedSleep(
        source_record_id=entry.get("sid"),
        start_at=_iso_from_epoch(start, offset),
        end_at=_iso_from_epoch(end, offset),
        total_min=int(payload.get("duration") or round((end - start) / 60)),
        deep_min=int(payload.get("sleep_deep_duration") or derived["deep"]),
        light_min=int(payload.get("sleep_light_duration") or derived["light"]),
        rem_min=int(payload.get("sleep_rem_duration") or derived["rem"]),
        awake_min=int(payload.get("sleep_awake_duration") or derived["awake"]),
        zone_offset_seconds=offset,
        stages=stages,
        raw=payload,
    )
