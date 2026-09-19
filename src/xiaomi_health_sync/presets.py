from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class ProbePreset:
    path: str
    record_type: str
    payload: dict[str, Any]


def load_presets(path: Path) -> list[ProbePreset]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: list[ProbePreset] = []
    for _, group in raw.items():
        endpoint = group["path"]
        for item in group.get("items", []):
            result.append(
                ProbePreset(
                    path=endpoint,
                    record_type=item["record_type"],
                    payload=item["payload"],
                )
            )
    return result
