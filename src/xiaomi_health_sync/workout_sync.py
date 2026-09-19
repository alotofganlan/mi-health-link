from __future__ import annotations

from typing import Any, Callable, Protocol

from .auto_discovery import DiscoveryError, XiaomiAuthExpiredError
from .workout_records import extract_workout_records, workout_table_rows
from .xiaomi import XiaomiResponse


WORKOUT_HISTORY_PATH = "/app/v1/data/get_sport_records_by_time"


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> XiaomiResponse: ...


class WorkoutStoreLike(Protocol):
    def _upsert_table(
        self,
        *,
        table: str,
        on_conflict: str,
        body: dict[str, Any] | list[dict[str, Any]],
        return_representation: bool = False,
    ) -> list[dict[str, Any]]: ...


def _require_success(response: XiaomiResponse, operation: str) -> dict[str, Any]:
    if response.status_code == 401:
        raise XiaomiAuthExpiredError(
            f"xiaomi_auth_expired: Xiaomi Cloud session expired during {operation}"
        )
    if not (200 <= response.status_code < 300):
        raise DiscoveryError(f"{operation} HTTP {response.status_code}")
    if not isinstance(response.json_data, dict):
        raise DiscoveryError(f"{operation} returned non-JSON response")
    code = response.json_data.get("code")
    if code not in (None, 0):
        message = response.json_data.get("message") or f"API code {code}"
        raise DiscoveryError(f"{operation}: {message}")
    return response.json_data


class WorkoutSyncRunner:
    def __init__(
        self,
        *,
        client: XiaomiClientLike,
        store: WorkoutStoreLike,
        progress: Callable[[str], None] | None = None,
        page_limit: int = 50,
        max_pages: int = 200,
    ) -> None:
        if page_limit <= 0:
            raise ValueError("page_limit must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.client = client
        self.store = store
        self.progress = progress or (lambda _message: None)
        self.page_limit = int(page_limit)
        self.max_pages = int(max_pages)

    def sync_window(self, *, start_time: int, end_time: int) -> dict[str, int]:
        start_time = int(start_time)
        end_time = int(end_time)
        if start_time < 0:
            raise ValueError("start_time must not be negative")
        if end_time < start_time:
            raise ValueError("end_time must be greater than or equal to start_time")

        next_key: str | None = None
        seen_cursors: set[str] = set()
        record_count = 0

        for page in range(1, self.max_pages + 1):
            request_payload: dict[str, Any] = {
                "start_time": start_time,
                "end_time": end_time,
                "limit": self.page_limit,
            }
            if next_key is not None:
                request_payload["next_key"] = next_key

            self.progress(f"[workout page {page}] starting")
            response = self.client.encrypted_post(
                WORKOUT_HISTORY_PATH,
                request_payload,
            )
            body = _require_success(response, f"workout history page {page}")
            records = extract_workout_records(body)
            rows = workout_table_rows(records)
            if rows:
                self.store._upsert_table(
                    table="workouts",
                    on_conflict="source,source_record_id",
                    body=rows,
                )
            record_count += len(rows)
            self.progress(f"[workout page {page}] completed {len(rows)} records")

            result = body.get("result")
            if not isinstance(result, dict):
                return {"records": record_count, "pages": page}
            if not result.get("has_more") or not result.get("next_key"):
                return {"records": record_count, "pages": page}

            candidate = str(result["next_key"])
            if candidate in seen_cursors:
                raise DiscoveryError("workout history pagination cursor loop")
            seen_cursors.add(candidate)
            next_key = candidate

        raise DiscoveryError(
            f"workout history exceeded {self.max_pages} pages "
            f"in window {start_time}..{end_time}"
        )
