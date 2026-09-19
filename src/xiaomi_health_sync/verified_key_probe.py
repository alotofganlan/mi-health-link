from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol

from .auto_discovery import LATEST_FITNESS_PATH, extract_latest_keys
from .diagnostics import diagnostic_response_payload
from .xiaomi import XiaomiResponse

# Verified from Xiaomi Health / Mi Fitness persistence code, not guessed names.
WATCH_S4_41MM_VERIFIED_PROBE_KEYS = (
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


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> XiaomiResponse: ...


class StoreLike(Protocol):
    def load_discovered_keys(self) -> set[str]: ...
    def remember_discovered_key(self, key: str) -> None: ...
    def save_raw(self, *, record_type: str, payload: Any, **kwargs: Any) -> str: ...


@dataclass(frozen=True)
class VerifiedProbeResult:
    confirmed: list[str]
    unconfirmed: list[str]


def probe_verified_persist_keys(
    *,
    client: XiaomiClientLike,
    store: StoreLike,
    keys: Iterable[str] = WATCH_S4_41MM_VERIFIED_PROBE_KEYS,
    latest_limit: int = 30,
    progress=lambda _message: None,
) -> VerifiedProbeResult:
    known = store.load_discovered_keys()
    confirmed: list[str] = []
    unconfirmed: list[str] = []

    pending = [key for key in keys if key not in known]
    for index, key in enumerate(pending, start=1):
        progress(f"[verified-probe {index}/{len(pending)}] requesting {key}")
        request_payload = {"params": [{"key": key, "limit": latest_limit}]}
        response = client.encrypted_post(LATEST_FITNESS_PATH, request_payload)
        raw = {
            "request_payload": request_payload,
            **diagnostic_response_payload(response),
        }
        store.save_raw(
            record_type=f"xiaomi:verified_key_probe:{key}",
            payload=raw,
        )

        returned = set(extract_latest_keys(response.json_data))
        if 200 <= response.status_code < 300 and key in returned:
            store.remember_discovered_key(key)
            confirmed.append(key)
            progress(f"[verified-probe] confirmed {key}")
        else:
            unconfirmed.append(key)
            progress(f"[verified-probe] no cloud data for {key}")

    return VerifiedProbeResult(
        confirmed=sorted(confirmed),
        unconfirmed=sorted(unconfirmed),
    )
