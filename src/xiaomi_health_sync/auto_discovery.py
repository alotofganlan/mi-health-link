from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

import httpx

from .health_records import normalize_data_item
from .history_policy import initial_backfill_start_time
from .xiaomi import XiaomiResponse

LATEST_FITNESS_PATH = "/app/v1/data/get_latest_fitness_data"
HISTORY_FITNESS_PATH = "/app/v1/data/get_fitness_data_by_time"
DEFAULT_HISTORY_WINDOW_SECONDS = 365 * 24 * 60 * 60


class DiscoveryError(RuntimeError):
    """Raised when Xiaomi discovery or history backfill cannot complete safely."""


class XiaomiAuthExpiredError(DiscoveryError):
    """Raised only when Xiaomi Cloud rejects the stored Xiaomi session."""

    code = "xiaomi_auth_expired"


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> XiaomiResponse: ...


class DiscoveryStoreLike(Protocol):
    def load_discovered_keys(self) -> set[str]: ...
    def load_observed_keys(self) -> set[str]: ...
    def remember_discovered_key(self, key: str) -> None: ...
    def load_backfilled_keys(self) -> set[str]: ...
    def remember_backfilled_key(self, key: str) -> None: ...
    def load_backfill_progress(self, key: str) -> int | None: ...
    def remember_backfill_progress(self, key: str, next_start_time: int) -> None: ...
    def upsert_normalized_record(self, record: dict[str, Any]) -> bool: ...


@dataclass(frozen=True)
class AutoDiscoveryResult:
    discovered_keys: list[str]
    new_keys: list[str]


def extract_latest_keys(response_json: Any | None) -> list[str]:
    if not isinstance(response_json, dict):
        return []
    result = response_json.get("result")
    if not isinstance(result, dict):
        return []
    data_list = result.get("data_list")
    if not isinstance(data_list, list):
        return []

    return sorted({
        str(row["key"])
        for row in data_list
        if isinstance(row, dict) and row.get("key") is not None
    })


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


def _response_data_items(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    result = response_json.get("result")
    if not isinstance(result, dict):
        return []
    data_list = result.get("data_list")
    if not isinstance(data_list, list):
        return []
    return [item for item in data_list if isinstance(item, dict)]


class AutoDiscoveryRunner:
    def __init__(
        self,
        *,
        client: XiaomiClientLike,
        store: DiscoveryStoreLike,
        now: Callable[[], datetime] | None = None,
        max_history_pages: int = 200,
        history_window_seconds: int = DEFAULT_HISTORY_WINDOW_SECONDS,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if history_window_seconds <= 0:
            raise ValueError("history_window_seconds must be positive")
        self.client = client
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_history_pages = max_history_pages
        self.history_window_seconds = history_window_seconds
        self.progress = progress or (lambda _message: None)

    def _emit(self, message: str) -> None:
        self.progress(message)

    def _ingest_items(self, response_json: dict[str, Any]) -> None:
        records = []
        for item in _response_data_items(response_json):
            normalized = normalize_data_item(item)
            if normalized is not None:
                records.append(normalized)
        if not records:
            return

        batch_upsert = getattr(self.store, "upsert_normalized_records", None)
        if callable(batch_upsert):
            batch_upsert(records)
            return
        for normalized in records:
            self.store.upsert_normalized_record(normalized)

    def _prepare_requested_keys(
        self,
        selected_keys: Iterable[str] | None,
    ) -> tuple[set[str], set[str], list[str]]:
        known = self.store.load_discovered_keys()
        observed = self.store.load_observed_keys()
        bootstrap_keys = sorted(observed - known)
        for key in bootstrap_keys:
            self.store.remember_discovered_key(key)

        active_keys = set(known) | set(observed)
        if not active_keys:
            raise DiscoveryError(
                "No Xiaomi-observed fitness keys are available. "
                "Import or probe a real Xiaomi response before discovery."
            )

        if selected_keys is None:
            requested_keys = set(active_keys)
        else:
            requested_keys = {str(key) for key in selected_keys if str(key)}
            unknown = requested_keys - active_keys
            if unknown:
                raise DiscoveryError(
                    "Requested Xiaomi keys have not been discovered: "
                    + ", ".join(sorted(unknown))
                )
            if not requested_keys:
                raise DiscoveryError("No Xiaomi keys were selected for synchronization")

        return active_keys, requested_keys, bootstrap_keys

    def _sync_latest_keys(
        self,
        requested_keys: set[str],
        *,
        latest_limit: int,
    ) -> set[str]:
        returned_keys: set[str] = set()
        latest_keys = sorted(requested_keys)
        for index, requested_key in enumerate(latest_keys, start=1):
            self._emit(
                f"[latest {index}/{len(latest_keys)}] requesting {requested_key}"
            )
            latest_payload = {
                "params": [{"key": requested_key, "limit": latest_limit}]
            }
            try:
                latest = self.client.encrypted_post(LATEST_FITNESS_PATH, latest_payload)
            except httpx.TransportError as exc:
                self._emit(
                    f"[latest {index}/{len(latest_keys)}] failed {requested_key}: "
                    f"{type(exc).__name__}: {exc}; continuing"
                )
                continue

            latest_json = _require_success(
                latest,
                f"latest fitness {requested_key}",
            )
            self._ingest_items(latest_json)
            returned_keys.update(extract_latest_keys(latest_json))
            self._emit(
                f"[latest {index}/{len(latest_keys)}] completed {requested_key}"
            )
        return returned_keys

    def run(
        self,
        *,
        backfill_start_time: int = 0,
        latest_limit: int = 30,
        selected_keys: Iterable[str] | None = None,
    ) -> AutoDiscoveryResult:
        active_keys, requested_keys, bootstrap_keys = self._prepare_requested_keys(
            selected_keys
        )
        returned_keys = self._sync_latest_keys(
            requested_keys,
            latest_limit=latest_limit,
        )

        extra_keys = returned_keys - active_keys
        for key in sorted(extra_keys):
            self.store.remember_discovered_key(key)
        active_keys |= returned_keys
        new_keys = sorted(set(bootstrap_keys) | extra_keys)

        end_time = int(self.now().timestamp())
        sync_keys = sorted(active_keys if selected_keys is None else requested_keys)
        for index, key in enumerate(sync_keys, start=1):
            self._emit(f"[history {index}/{len(sync_keys)}] starting {key}")
            try:
                self._backfill_key(key, int(backfill_start_time), end_time)
            except httpx.TransportError as exc:
                self._emit(
                    f"[history {index}/{len(sync_keys)}] failed {key}: "
                    f"{type(exc).__name__}: {exc}; checkpoint kept, continuing"
                )
                continue
            self._emit(f"[history {index}/{len(sync_keys)}] completed {key}")

        return AutoDiscoveryResult(
            discovered_keys=sorted(active_keys),
            new_keys=new_keys,
        )

    def sync_recent(
        self,
        *,
        selected_keys: Iterable[str] | None = None,
        recent_window_seconds: int = 24 * 60 * 60,
        latest_limit: int = 30,
        on_checked_range: Callable[[str, int, int], None] | None = None,
    ) -> AutoDiscoveryResult:
        """Refresh recent data without consuming historical backfill checkpoints."""
        if recent_window_seconds <= 0:
            raise ValueError("recent_window_seconds must be positive")

        active_keys, requested_keys, bootstrap_keys = self._prepare_requested_keys(
            selected_keys
        )
        returned_keys = self._sync_latest_keys(
            requested_keys,
            latest_limit=latest_limit,
        )

        extra_keys = returned_keys - active_keys
        for key in sorted(extra_keys):
            self.store.remember_discovered_key(key)
        active_keys |= returned_keys
        new_keys = sorted(set(bootstrap_keys) | extra_keys)

        end_time = int(self.now().timestamp())
        start_time = max(0, end_time - recent_window_seconds)
        recent_keys = sorted(requested_keys)
        for index, key in enumerate(recent_keys, start=1):
            self._emit(f"[recent {index}/{len(recent_keys)}] starting {key}")
            try:
                self._backfill_window(key, start_time, end_time)
            except httpx.TransportError as exc:
                self._emit(
                    f"[recent {index}/{len(recent_keys)}] failed {key}: "
                    f"{type(exc).__name__}: {exc}; continuing"
                )
                continue
            if on_checked_range is not None:
                on_checked_range(key, start_time, end_time)
            self._emit(f"[recent {index}/{len(recent_keys)}] completed {key}")

        return AutoDiscoveryResult(
            discovered_keys=sorted(active_keys),
            new_keys=new_keys,
        )

    def sync_range(
        self,
        *,
        selected_keys: Iterable[str],
        start_time: int,
        end_time: int,
    ) -> AutoDiscoveryResult:
        """Re-fetch an exact historical range without reading or changing checkpoints."""
        start_time = int(start_time)
        end_time = int(end_time)
        if start_time < 0:
            raise ValueError("start_time must be non-negative")
        if end_time < start_time:
            raise ValueError("end_time must be greater than or equal to start_time")

        active_keys, requested_keys, bootstrap_keys = self._prepare_requested_keys(
            selected_keys
        )
        window_keys = sorted(requested_keys)
        for index, key in enumerate(window_keys, start=1):
            self._emit(f"[range {index}/{len(window_keys)}] starting {key}")
            window_start = start_time
            while window_start <= end_time:
                window_end = min(
                    window_start + self.history_window_seconds - 1,
                    end_time,
                )
                self._backfill_window(key, window_start, window_end)
                self._emit(
                    f"[range {key}] completed {window_start}..{window_end}"
                )
                window_start = window_end + 1
            self._emit(f"[range {index}/{len(window_keys)}] completed {key}")

        return AutoDiscoveryResult(
            discovered_keys=sorted(active_keys),
            new_keys=bootstrap_keys,
        )

    def _backfill_key(self, key: str, start_time: int, end_time: int) -> None:
        checkpoint = self.store.load_backfill_progress(key)
        policy_start = initial_backfill_start_time(key, end_time)
        resume_start = checkpoint if checkpoint is not None else policy_start
        window_start = max(start_time, resume_start)

        if window_start > end_time:
            self.store.remember_backfilled_key(key)
            return

        while window_start <= end_time:
            window_end = min(
                window_start + self.history_window_seconds - 1,
                end_time,
            )
            self._backfill_window(key, window_start, window_end)
            next_start = window_end + 1
            self.store.remember_backfill_progress(key, next_start)
            self._emit(
                f"[history {key}] completed {window_start}..{window_end}; "
                f"checkpoint={next_start}"
            )
            window_start = next_start

        self.store.remember_backfilled_key(key)

    def _backfill_window(self, key: str, start_time: int, end_time: int) -> None:
        next_key: str | None = None
        seen_cursors: set[str] = set()

        for page in range(1, self.max_history_pages + 1):
            request_payload: dict[str, Any] = {
                "key": key,
                "start_time": start_time,
                "end_time": end_time,
            }
            if next_key is not None:
                request_payload["next_key"] = next_key

            self._emit(
                f"[history {key}] requesting {start_time}..{end_time} page {page}"
            )
            history = self.client.encrypted_post(HISTORY_FITNESS_PATH, request_payload)
            history_json = _require_success(
                history,
                f"history {key} {start_time}..{end_time} page {page}",
            )
            self._ingest_items(history_json)
            result = history_json.get("result")
            if not isinstance(result, dict):
                return

            if not result.get("has_more") or not result.get("next_key"):
                return

            candidate = str(result["next_key"])
            if candidate in seen_cursors:
                raise DiscoveryError(f"history {key}: pagination cursor loop")
            seen_cursors.add(candidate)
            next_key = candidate

        raise DiscoveryError(
            f"history {key}: exceeded {self.max_history_pages} pages "
            f"in window {start_time}..{end_time}"
        )
