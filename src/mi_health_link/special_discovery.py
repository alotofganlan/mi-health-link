from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol

from .diagnostics import diagnostic_response_payload
from .workout_records import extract_workout_records
from .xiaomi import XiaomiResponse


@dataclass(frozen=True)
class SpecialProbe:
    name: str
    category: str
    path: str
    payload: dict[str, Any]


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> XiaomiResponse: ...


class RawStoreLike(Protocol):
    def save_raw(self, *, record_type: str, payload: Any, **kwargs: Any) -> str: ...


def default_special_probes(*, start_time: int, end_time: int) -> list[SpecialProbe]:
    return [
        SpecialProbe(
            name="profile",
            category="profile",
            path="/healthapp/user/get_miot_user_profile",
            payload={},
        ),
        SpecialProbe(
            name="workout_history",
            category="workout",
            path="/app/v1/data/get_sport_records_by_time",
            payload={
                "start_time": int(start_time),
                "end_time": int(end_time),
                "limit": 50,
            },
        ),
    ]


def _save_probe_response(
    *,
    store: RawStoreLike,
    probe: SpecialProbe,
    request_payload: dict[str, Any],
    response: XiaomiResponse,
    page: int | None = None,
) -> dict[str, Any]:
    raw = {
        "category": probe.category,
        "name": probe.name,
        "path": probe.path,
        "request_payload": request_payload,
        **diagnostic_response_payload(response),
    }
    if page is not None:
        raw["page"] = page
    store.save_raw(
        record_type=f"xiaomi:special:{probe.category}:{probe.name}",
        payload=raw,
    )
    return raw


def _save_normalized_workouts(store: RawStoreLike, response: XiaomiResponse) -> int:
    count = 0
    for record in extract_workout_records(response.json_data):
        measured_at = None
        start_time = record.get("start_time")
        if isinstance(start_time, (int, float)):
            measured_at = datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat()
        store.save_raw(
            record_type="workout_record",
            payload=record,
            measured_at=measured_at,
            source_record_id=str(record["source_record_id"]),
        )
        count += 1
    return count


def run_special_probes(
    *,
    client: XiaomiClientLike,
    store: RawStoreLike,
    probes: Iterable[SpecialProbe],
    max_pages: int = 200,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for probe in probes:
        if probe.category != "workout":
            response = client.encrypted_post(probe.path, probe.payload)
            results.append(
                _save_probe_response(
                    store=store,
                    probe=probe,
                    request_payload=probe.payload,
                    response=response,
                )
            )
            continue

        next_key: str | None = None
        seen_cursors: set[str] = set()
        for page in range(1, max_pages + 1):
            request_payload = dict(probe.payload)
            if next_key is not None:
                request_payload["next_key"] = next_key

            response = client.encrypted_post(probe.path, request_payload)
            row = _save_probe_response(
                store=store,
                probe=probe,
                request_payload=request_payload,
                response=response,
                page=page,
            )
            row["normalized_workouts"] = _save_normalized_workouts(store, response)
            results.append(row)

            body = response.json_data
            if not isinstance(body, dict) or body.get("code") not in (None, 0):
                break
            result = body.get("result")
            if not isinstance(result, dict):
                break
            if not result.get("has_more") or not result.get("next_key"):
                break

            candidate = str(result["next_key"])
            if candidate in seen_cursors:
                break
            seen_cursors.add(candidate)
            next_key = candidate

    return results
