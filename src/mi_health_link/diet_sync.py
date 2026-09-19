from __future__ import annotations

from typing import Any, Callable, Protocol

from .auto_discovery import DiscoveryError, XiaomiAuthExpiredError
from .diet import DIET_ENDPOINT, extract_diet_records, normalize_diet_records
from .xiaomi import XiaomiResponse


DEFAULT_DIET_LIMIT = 100
DEFAULT_MAX_DIET_PAGES = 200


class DietClientLike(Protocol):
    def get_diet_records_by_time(
        self,
        *,
        dining: int,
        limit: int,
        start_time: int,
        end_time: int,
        reverse: bool,
        next_key: str,
    ) -> XiaomiResponse: ...


class DietStoreLike(Protocol):
    def upsert_diet_records(self, records: list[dict[str, Any]]) -> int: ...


class DietSyncRunner:
    """Read and persist Xiaomi diet records without write/delete API calls."""

    def __init__(
        self,
        *,
        client: DietClientLike,
        store: DietStoreLike,
        dining: int = 0,
        limit: int = DEFAULT_DIET_LIMIT,
        max_pages: int = DEFAULT_MAX_DIET_PAGES,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if max_pages <= 0:
            raise ValueError("max_pages must be positive")
        self.client = client
        self.store = store
        self.dining = int(dining)
        self.limit = int(limit)
        self.max_pages = int(max_pages)
        self.progress = progress or (lambda _message: None)

    def sync_window(self, *, start_time: int, end_time: int) -> dict[str, int]:
        start_time = int(start_time)
        end_time = int(end_time)
        if start_time < 0:
            raise ValueError("start_time must be non-negative")
        if end_time < start_time:
            raise ValueError("end_time must be greater than or equal to start_time")

        next_key = ""
        seen_cursors: set[str] = set()
        pages = 0
        records_count = 0
        food_items = 0

        for page in range(1, self.max_pages + 1):
            pages = page
            payload = {
                "dining": self.dining,
                "limit": self.limit,
                "start_time": start_time,
                "end_time": end_time,
                "reverse": False,
                "next_key": next_key,
            }
            self.progress(
                f"[diet] requesting {start_time}..{end_time} page {page}"
            )
            response = self.client.get_diet_records_by_time(**payload)
            if response.status_code == 401:
                raise XiaomiAuthExpiredError(
                    "xiaomi_auth_expired: Xiaomi Cloud session expired during diet sync"
                )
            if not (200 <= response.status_code < 300):
                raise DiscoveryError(
                    f"diet endpoint HTTP {response.status_code}"
                )
            decoded = response.json_data
            if not isinstance(decoded, dict):
                raise DiscoveryError("diet endpoint returned non-JSON response")
            code = decoded.get("code")
            if code not in (None, 0):
                message = decoded.get("message") or f"API code {code}"
                raise DiscoveryError(f"diet endpoint: {message}")

            records = extract_diet_records(decoded)
            records_count += len(records)
            normalized = normalize_diet_records(records)
            if normalized:
                self.store.upsert_diet_records(normalized)
                food_items += len(normalized)

            result = decoded.get("result")
            if not isinstance(result, dict):
                break
            if not result.get("has_more") or not result.get("next_key"):
                break

            candidate = str(result["next_key"])
            if candidate in seen_cursors:
                raise DiscoveryError("diet endpoint pagination cursor loop")
            seen_cursors.add(candidate)
            next_key = candidate
        else:
            raise DiscoveryError(
                f"diet endpoint exceeded {self.max_pages} pages "
                f"in window {start_time}..{end_time}"
            )

        return {
            "pages": pages,
            "records": records_count,
            "food_items": food_items,
        }
