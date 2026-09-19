from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect
import re
from threading import Lock
from typing import Callable, Protocol
from uuid import uuid4

from .auto_discovery import AutoDiscoveryRunner
from .config import load_credentials, load_settings
from .diet_sync import DietSyncRunner
from .mcp_health_data import NormalizedHealthReader
from .notifications import XIAOMI_AUTH_EXPIRED_MESSAGE, notify_xiaomi_auth_expired
from .recent_sync import run_recent_sync
from .sync_store import DirectSupabaseStore
from .temperature_store import TemperatureAwareStore
from .wake_report import check_pending_sleep_report
from .workout_sync import WorkoutSyncRunner
from .xiaomi import XiaomiHealthClient


RECENT_SYNC_WINDOW_SECONDS = 24 * 60 * 60
SOURCE_RECHECK_DELAY = timedelta(minutes=30)
_RANGE_PROGRESS_RE = re.compile(r"^\[range [^\]]+\] completed (\d+)\.\.(\d+)$")


def _should_check_sleep_report(metric: str | None) -> bool:
    return metric in {None, "sleep"}


class SyncJobStore(Protocol):
    def save_sync_job(self, job: dict[str, object]) -> None: ...
    def load_sync_job(self, job_id: str) -> dict[str, object] | None: ...
    def load_latest_sync_job(self, metric: str) -> dict[str, object] | None: ...


def _parse_range(start_at: str, end_at: str) -> tuple[datetime, datetime]:
    def parse(name: str, value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{name} must be a valid ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"{name} must include a timezone offset")
        return parsed

    start = parse("start_at", start_at)
    end = parse("end_at", end_at)
    if end < start:
        raise ValueError("end_at must be greater than or equal to start_at")
    return start, end


def _persist_failed_source_check(store: DirectSupabaseStore, metric: str) -> None:
    checked = datetime.now(timezone.utc)
    previous = store.load_source_check(metric) or {}
    previous_latest = previous.get("source_latest_at")
    source_latest_at = previous_latest if isinstance(previous_latest, str) else None
    empty_check_count = int(previous.get("empty_check_count") or 0)
    store.remember_source_check(
        metric,
        checked_at=checked.isoformat(),
        status="failed",
        source_latest_at=source_latest_at,
        next_recheck_at=(checked + SOURCE_RECHECK_DELAY).isoformat(),
        empty_check_count=empty_check_count,
    )


def _persist_coverage_window(
    store: DirectSupabaseStore,
    metric: str,
    start_time: int,
    end_time: int,
    *,
    status: str = "success",
) -> None:
    store._upsert_table(
        table="xiaomi_coverage_ranges",
        on_conflict="source,key,range_start,range_end",
        body={
            "source": "xiaomi",
            "key": metric,
            "range_start": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
            "range_end": datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
        },
    )


def run_sync_now(metric: str | None = None) -> dict[str, object]:
    """Refresh recent Xiaomi data without consuming historical backfill state."""
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase is required for synchronization")

    credentials = load_credentials(settings.credentials_file, settings.region)
    store = TemperatureAwareStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )

    checked_metrics: set[str] = set()

    def persist_checked_range(key: str, start_time: int, end_time: int) -> None:
        _persist_coverage_window(store, key, start_time, end_time)
        checked_metrics.add(key)

    try:
        with XiaomiHealthClient(settings, credentials) as client:
            selected_keys = None if metric is None else {metric}
            sync_result = run_recent_sync(
                client=client,
                store=store,
                recent_window_seconds=RECENT_SYNC_WINDOW_SECONDS,
                selected_keys=selected_keys,
                on_checked_range=persist_checked_range,
                now_seconds=int(datetime.now(timezone.utc).timestamp()),
                health_runner_class=AutoDiscoveryRunner,
                diet_runner_class=DietSyncRunner,
                workout_runner_class=WorkoutSyncRunner,
            )
            health_result = sync_result.health
            diet_result = sync_result.diet
            workout_result = sync_result.workout
            if metric is not None and metric not in checked_metrics:
                raise RuntimeError(
                    f"Xiaomi recent sync did not complete for {metric}"
                )
    except Exception:
        if metric is not None:
            _persist_failed_source_check(store, metric)
        raise

    source_checked_at: str | None = None
    source_latest_at: str | None = None
    next_recheck_at: str | None = None
    empty_check_count: int | None = None

    if metric is not None:
        checked = datetime.now(timezone.utc)
        window_start = checked - timedelta(seconds=RECENT_SYNC_WINDOW_SECONDS)
        reader = NormalizedHealthReader(
            settings.supabase_url,
            settings.supabase_service_role_key,
        )
        local = reader.coverage(
            metric,
            start_at=window_start.isoformat(),
            end_at=checked.isoformat(),
        )
        latest_value = local.get("latest_at")
        source_latest_at = latest_value if isinstance(latest_value, str) else None

        previous = store.load_source_check(metric) or {}
        previous_latest = previous.get("source_latest_at")
        previous_empty_count = int(previous.get("empty_check_count") or 0)
        if source_latest_at == previous_latest:
            empty_check_count = previous_empty_count + 1
        else:
            empty_check_count = 0

        source_checked_at = checked.isoformat()
        next_recheck_at = (checked + SOURCE_RECHECK_DELAY).isoformat()
        store.remember_source_check(
            metric,
            checked_at=source_checked_at,
            status="success",
            source_latest_at=source_latest_at,
            next_recheck_at=next_recheck_at,
            empty_check_count=empty_check_count,
        )

    result_payload = {
        "status": "completed",
        "metric": metric,
        "window_seconds": RECENT_SYNC_WINDOW_SECONDS,
        "discovered_count": len(health_result.discovered_keys) if health_result is not None else 0,
        "new_count": len(health_result.new_keys) if health_result is not None else 0,
        "diet": diet_result,
        "workout": workout_result,
        "source_checked_at": source_checked_at,
        "source_latest_at": source_latest_at,
        "next_recheck_at": next_recheck_at,
        "empty_check_count": empty_check_count,
    }
    if _should_check_sleep_report(metric):
        result_payload["sleep_report"] = check_pending_sleep_report()
    return result_payload


def run_backfill_range(
    metric: str,
    start_at: str,
    end_at: str,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Re-fetch one discovered Xiaomi metric over an exact historical range."""
    start, end = _parse_range(start_at, end_at)
    settings = load_settings()
    if not settings.supabase_url or not settings.supabase_service_role_key:
        raise RuntimeError("Supabase is required for synchronization")

    credentials = load_credentials(settings.credentials_file, settings.region)
    store = TemperatureAwareStore(
        url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )

    def on_progress(message: str) -> None:
        match = _RANGE_PROGRESS_RE.match(message)
        if match is not None:
            _persist_coverage_window(
                store,
                metric,
                int(match.group(1)),
                int(match.group(2)),
            )
        if progress is not None:
            progress(message)

    with XiaomiHealthClient(settings, credentials) as client:
        if metric == "diet":
            diet_result = DietSyncRunner(
                client=client,
                store=store,
                progress=on_progress,
            ).sync_window(
                start_time=int(start.timestamp()),
                end_time=int(end.timestamp()),
            )
            _persist_coverage_window(
                store,
                metric,
                int(start.timestamp()),
                int(end.timestamp()),
            )
            return {
                "status": "completed",
                "kind": "backfill",
                "metric": metric,
                "start_at": start_at,
                "end_at": end_at,
                **diet_result,
            }

        if metric == "workout":
            workout_result = WorkoutSyncRunner(
                client=client,
                store=store,
                progress=on_progress,
            ).sync_window(
                start_time=int(start.timestamp()),
                end_time=int(end.timestamp()),
            )
            on_progress(
                f"[range workout] completed {int(start.timestamp())}..{int(end.timestamp())}"
            )
            return {
                "status": "completed",
                "kind": "backfill",
                "metric": metric,
                "start_at": start_at,
                "end_at": end_at,
                **workout_result,
            }

        result = AutoDiscoveryRunner(
            client=client,
            store=store,
            progress=on_progress,
        ).sync_range(
            selected_keys=(metric,),
            start_time=int(start.timestamp()),
            end_time=int(end.timestamp()),
        )

    return {
        "status": "completed",
        "kind": "backfill",
        "metric": metric,
        "start_at": start_at,
        "end_at": end_at,
        "discovered_count": len(result.discovered_keys),
        "new_count": len(result.new_keys),
    }


class SyncJobManager:
    """Serialize Xiaomi sync/backfill work and persist job status when configured."""

    def __init__(
        self,
        sync_callable: Callable[[str | None], dict[str, object]] = run_sync_now,
        *,
        backfill_callable: Callable[..., dict[str, object]] = run_backfill_range,
        job_store: SyncJobStore | None = None,
    ) -> None:
        self._sync_callable = sync_callable
        self._backfill_callable = backfill_callable
        self._job_store = job_store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xiaomi-sync")
        self._lock = Lock()
        self._jobs: dict[str, dict[str, object]] = {}
        self._active_jobs: dict[object, str] = {}

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _persist(self, job: dict[str, object]) -> None:
        if self._job_store is not None:
            self._job_store.save_sync_job(deepcopy(job))

    def start(self, metric: str | None = None) -> dict[str, object]:
        active_key: object = ("sync", metric)
        with self._lock:
            active_job_id = self._active_jobs.get(active_key)
            if active_job_id is not None:
                active_job = self._jobs.get(active_job_id)
                if active_job is not None and active_job.get("status") in {"queued", "running"}:
                    return deepcopy(active_job)

            job_id = uuid4().hex
            job: dict[str, object] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "sync",
                "metric": metric,
                "created_at": self._now(),
                "started_at": None,
                "completed_at": None,
                "result": None,
                "error": None,
                "error_code": None,
            }
            self._jobs[job_id] = job
            self._active_jobs[active_key] = job_id
            snapshot = deepcopy(job)

        self._persist(snapshot)
        self._executor.submit(self._run_sync_job, job_id, metric, active_key)
        return snapshot

    def start_backfill(self, metric: str, start_at: str, end_at: str) -> dict[str, object]:
        _parse_range(start_at, end_at)
        active_key: object = ("backfill", metric, start_at, end_at)
        with self._lock:
            active_job_id = self._active_jobs.get(active_key)
            if active_job_id is not None:
                active_job = self._jobs.get(active_job_id)
                if active_job is not None and active_job.get("status") in {"queued", "running"}:
                    return deepcopy(active_job)

            job_id = uuid4().hex
            job: dict[str, object] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "backfill",
                "metric": metric,
                "start_at": start_at,
                "end_at": end_at,
                "created_at": self._now(),
                "started_at": None,
                "completed_at": None,
                "result": {"progress": {"completed_windows": 0}},
                "error": None,
                "error_code": None,
            }
            self._jobs[job_id] = job
            self._active_jobs[active_key] = job_id
            snapshot = deepcopy(job)

        self._persist(snapshot)
        self._executor.submit(
            self._run_backfill_job,
            job_id,
            metric,
            start_at,
            end_at,
            active_key,
        )
        return snapshot

    def _finish_active_job(self, active_key: object, job_id: str) -> None:
        if self._active_jobs.get(active_key) == job_id:
            self._active_jobs.pop(active_key, None)

    def _mark_running(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = self._now()
            running = deepcopy(job)
        self._persist(running)
        return running

    def _record_backfill_progress(self, job_id: str, message: str) -> None:
        match = _RANGE_PROGRESS_RE.match(message)
        if match is None:
            return
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.get("status") != "running":
                return
            current_result = job.get("result")
            result = dict(current_result) if isinstance(current_result, dict) else {}
            current_progress = result.get("progress")
            progress = dict(current_progress) if isinstance(current_progress, dict) else {}
            progress["completed_windows"] = int(progress.get("completed_windows") or 0) + 1
            progress["last_window_start"] = int(match.group(1))
            progress["last_window_end"] = int(match.group(2))
            progress["updated_at"] = self._now()
            result["progress"] = progress
            job["result"] = result
            snapshot = deepcopy(job)
        self._persist(snapshot)

    def _complete_job(
        self,
        job_id: str,
        active_key: object,
        result: dict[str, object],
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            progress = None
            current_result = job.get("result")
            if isinstance(current_result, dict) and isinstance(current_result.get("progress"), dict):
                progress = deepcopy(current_result["progress"])
            job["status"] = "completed"
            job["completed_at"] = self._now()
            final_result = dict(result)
            if progress is not None:
                final_result["progress"] = progress
            job["result"] = final_result
            self._finish_active_job(active_key, job_id)
            completed = deepcopy(job)
        self._persist(completed)

    def _fail_job(self, job_id: str, active_key: object, exc: Exception) -> None:
        candidate_code = getattr(exc, "code", None)
        error_code = candidate_code if isinstance(candidate_code, str) and candidate_code else None
        if error_code == "xiaomi_auth_expired":
            notify_xiaomi_auth_expired(XIAOMI_AUTH_EXPIRED_MESSAGE)

        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "failed"
            job["completed_at"] = self._now()
            job["error"] = f"{type(exc).__name__}: {exc}"
            job["error_code"] = error_code
            self._finish_active_job(active_key, job_id)
            failed = deepcopy(job)
        self._persist(failed)

    def _run_sync_job(self, job_id: str, metric: str | None, active_key: object) -> None:
        self._mark_running(job_id)
        try:
            result = self._sync_callable(metric)
        except Exception as exc:
            self._fail_job(job_id, active_key, exc)
            return
        self._complete_job(job_id, active_key, result)

    def _run_backfill_job(
        self,
        job_id: str,
        metric: str,
        start_at: str,
        end_at: str,
        active_key: object,
    ) -> None:
        self._mark_running(job_id)
        progress = lambda message: self._record_backfill_progress(job_id, message)
        try:
            parameters = inspect.signature(self._backfill_callable).parameters
            if len(parameters) >= 4:
                result = self._backfill_callable(metric, start_at, end_at, progress)
            else:
                result = self._backfill_callable(metric, start_at, end_at)
        except Exception as exc:
            self._fail_job(job_id, active_key, exc)
            return
        self._complete_job(job_id, active_key, result)

    def status(self, job_id: str) -> dict[str, object]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                return deepcopy(job)
        if self._job_store is not None:
            persisted = self._job_store.load_sync_job(job_id)
            if persisted is not None:
                return deepcopy(persisted)
        return {"job_id": job_id, "status": "not_found"}

    def latest_for_metric(self, metric: str) -> dict[str, object] | None:
        """Return the newest terminal targeted recent-sync job for one metric."""
        with self._lock:
            for job in reversed(list(self._jobs.values())):
                if job.get("kind") == "backfill":
                    continue
                if job.get("metric") != metric:
                    continue
                if job.get("status") not in {"completed", "failed"}:
                    continue
                return deepcopy(job)
        if self._job_store is not None:
            persisted = self._job_store.load_latest_sync_job(metric)
            if persisted is not None:
                return deepcopy(persisted)
        return None

    def active_job_count(self) -> int:
        with self._lock:
            return sum(
                1
                for job in self._jobs.values()
                if job.get("status") in {"queued", "running"}
            )


_default_sync_manager: SyncJobManager | None = None
_default_sync_manager_lock = Lock()


def get_default_sync_manager() -> SyncJobManager:
    global _default_sync_manager
    with _default_sync_manager_lock:
        if _default_sync_manager is None:
            settings = load_settings()
            store = None
            if settings.supabase_url and settings.supabase_service_role_key:
                store = DirectSupabaseStore(
                    url=settings.supabase_url,
                    service_role_key=settings.supabase_service_role_key,
                )
            _default_sync_manager = SyncJobManager(job_store=store)
        return _default_sync_manager
