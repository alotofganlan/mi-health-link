from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable

from .auto_discovery import AutoDiscoveryRunner
from .diet_sync import DietSyncRunner
from .workout_sync import WorkoutSyncRunner


CheckedRangeCallback = Callable[[str, int, int], None]
ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class RecentSyncResult:
    health: Any | None
    diet: dict[str, int] | None
    workout: dict[str, int] | None
    start_time: int
    end_time: int


def _runner_args(client: Any, store: Any, progress: ProgressCallback | None) -> dict:
    args = {"client": client, "store": store}
    if progress is not None:
        args["progress"] = progress
    return args


def run_recent_sync(
    *,
    client: Any,
    store: Any,
    recent_window_seconds: int,
    latest_limit: int = 30,
    selected_keys: set[str] | None = None,
    progress: ProgressCallback | None = None,
    on_checked_range: CheckedRangeCallback | None = None,
    now_seconds: int | None = None,
    health_runner_class: type = AutoDiscoveryRunner,
    diet_runner_class: type = DietSyncRunner,
    workout_runner_class: type = WorkoutSyncRunner,
) -> RecentSyncResult:
    """Synchronize recent health, workout, and diet data through one code path."""
    if recent_window_seconds <= 0:
        raise ValueError("recent_window_seconds must be positive")
    if latest_limit <= 0:
        raise ValueError("latest_limit must be positive")

    requested = None if selected_keys is None else {
        str(key) for key in selected_keys if str(key)
    }
    health_keys = None
    if requested is not None:
        health_keys = tuple(sorted(requested - {"diet", "workout"}))

    end_time = int(time.time()) if now_seconds is None else int(now_seconds)
    start_time = max(0, end_time - int(recent_window_seconds))

    health_result = None
    if requested is None or health_keys:
        sync_args = {
            "selected_keys": health_keys,
            "recent_window_seconds": recent_window_seconds,
            "on_checked_range": on_checked_range,
        }
        if latest_limit != 30:
            sync_args["latest_limit"] = latest_limit
        health_result = health_runner_class(
            **_runner_args(client, store, progress)
        ).sync_recent(**sync_args)

    workout_result = None
    if requested is None or "workout" in requested:
        workout_result = workout_runner_class(
            **_runner_args(client, store, progress)
        ).sync_window(start_time=start_time, end_time=end_time)
        if on_checked_range is not None:
            on_checked_range("workout", start_time, end_time)

    diet_result = None
    if requested is None or "diet" in requested:
        diet_result = diet_runner_class(
            **_runner_args(client, store, progress)
        ).sync_window(start_time=start_time, end_time=end_time)
        if on_checked_range is not None:
            on_checked_range("diet", start_time, end_time)

    return RecentSyncResult(
        health=health_result,
        diet=diet_result,
        workout=workout_result,
        start_time=start_time,
        end_time=end_time,
    )
