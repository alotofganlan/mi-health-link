from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from threading import Event
import time

from mcp import Client

import xiaomi_health_sync.mcp_sync as mcp_sync
from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_server import create_mcp_server


class RejectAllVerifier:
    async def verify_token(self, token: str):
        return None


def _settings() -> MCPSettings:
    return MCPSettings(
        public_url="https://health.example.com/mcp",
        host="127.0.0.1",
        port=8765,
        supabase_issuer_url="https://project.supabase.co/auth/v1",
        supabase_jwks_url="https://project.supabase.co/auth/v1/.well-known/jwks.json",
        allowed_subject="user-123",
        required_scopes=("openid",),
        supabase_project_url="https://project.supabase.co",
        supabase_publishable_key="sb_publishable_example",
    )


@dataclass
class _DiscoveryResult:
    discovered_keys: list[str]
    new_keys: list[str]


def test_run_sync_now_scopes_existing_runner_to_requested_metric(monkeypatch) -> None:
    class Settings:
        supabase_url = "https://project.supabase.co"
        supabase_service_role_key = "service-role"
        credentials_file = "credentials.json"
        region = "cn"

    class Client:
        def __init__(self, settings, credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    seen: dict[str, object] = {}

    class Store:
        def load_source_check(self, key):
            return None

        def remember_source_check(self, key, **fields):
            seen["source_check_key"] = key
            seen["source_check"] = fields

        def _upsert_table(self, **kwargs):
            pass

    store = Store()

    class Runner:
        def __init__(self, *, client, store):
            pass

        def sync_recent(
            self,
            *,
            selected_keys=None,
            recent_window_seconds=None,
            on_checked_range=None,
        ):
            seen["selected_keys"] = selected_keys
            seen["recent_window_seconds"] = recent_window_seconds
            on_checked_range("heart_rate", 1_999_913_600, 2_000_000_000)
            return _DiscoveryResult(discovered_keys=["heart_rate"], new_keys=[])

    class Reader:
        def __init__(self, url, service_role_key):
            pass

        def coverage(self, metric, *, start_at, end_at):
            seen["coverage_metric"] = metric
            return {
                "metric": metric,
                "count": 319,
                "first_at": "2026-08-22T01:51:00+08:00",
                "latest_at": "2026-08-22T12:35:00+08:00",
            }

    monkeypatch.setattr(mcp_sync, "load_settings", lambda: Settings())
    monkeypatch.setattr(mcp_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(mcp_sync, "DirectSupabaseStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "TemperatureAwareStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "XiaomiHealthClient", Client)
    monkeypatch.setattr(mcp_sync, "AutoDiscoveryRunner", Runner)
    monkeypatch.setattr(mcp_sync, "NormalizedHealthReader", Reader)

    result = mcp_sync.run_sync_now(metric="heart_rate")

    assert seen["selected_keys"] == ("heart_rate",)
    assert seen["recent_window_seconds"] == 24 * 60 * 60
    assert seen["coverage_metric"] == "heart_rate"
    assert seen["source_check_key"] == "heart_rate"
    source_check = seen["source_check"]
    assert source_check["status"] == "success"
    assert source_check["source_latest_at"] == "2026-08-22T12:35:00+08:00"
    assert source_check["next_recheck_at"] is not None
    assert source_check["empty_check_count"] == 0
    assert result["metric"] == "heart_rate"
    assert result["status"] == "completed"


def test_global_run_sync_persists_each_successful_recent_checked_range(monkeypatch) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(2_000_000_000, tz=tz)

    class Settings:
        supabase_url = "https://project.supabase.co"
        supabase_service_role_key = "service-role"
        credentials_file = "credentials.json"
        region = "cn"

    class Client:
        def __init__(self, settings, credentials):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    persisted = []

    class Store:
        def _upsert_table(self, **kwargs):
            persisted.append(kwargs)

    store = Store()

    class Runner:
        def __init__(self, *, client, store):
            pass

        def sync_recent(
            self,
            *,
            selected_keys=None,
            recent_window_seconds=None,
            on_checked_range=None,
        ):
            assert selected_keys is None
            assert on_checked_range is not None
            on_checked_range("abnormal_heart_beat", 1_999_913_600, 2_000_000_000)
            on_checked_range("heart_rate", 1_999_913_600, 2_000_000_000)
            return _DiscoveryResult(
                discovered_keys=["abnormal_heart_beat", "heart_rate"],
                new_keys=[],
            )

    class WorkoutRunner:
        def __init__(self, *, client, store):
            pass

        def sync_window(self, *, start_time, end_time):
            return {"records": 0, "pages": 1}

    class DietRunner:
        def __init__(self, *, client, store):
            pass

        def sync_window(self, *, start_time, end_time):
            return {"meals": 0, "items": 0}

    monkeypatch.setattr(mcp_sync, "load_settings", lambda: Settings())
    monkeypatch.setattr(mcp_sync, "load_credentials", lambda *args: object())
    monkeypatch.setattr(mcp_sync, "TemperatureAwareStore", lambda **kwargs: store)
    monkeypatch.setattr(mcp_sync, "XiaomiHealthClient", Client)
    monkeypatch.setattr(mcp_sync, "AutoDiscoveryRunner", Runner)
    monkeypatch.setattr(mcp_sync, "WorkoutSyncRunner", WorkoutRunner)
    monkeypatch.setattr(mcp_sync, "DietSyncRunner", DietRunner)
    monkeypatch.setattr(mcp_sync, "datetime", FixedDatetime)

    result = mcp_sync.run_sync_now()

    assert result["status"] == "completed"
    assert all(row["table"] == "xiaomi_coverage_ranges" for row in persisted)
    assert all(
        row["on_conflict"] == "source,key,range_start,range_end"
        for row in persisted
    )
    assert [
        (
            row["body"]["key"],
            row["body"]["range_start"],
            row["body"]["range_end"],
            row["body"]["status"],
        )
        for row in persisted
    ] == [
        (
            "abnormal_heart_beat",
            "2033-05-17T03:33:20+00:00",
            "2033-05-18T03:33:20+00:00",
            "success",
        ),
        (
            "heart_rate",
            "2033-05-17T03:33:20+00:00",
            "2033-05-18T03:33:20+00:00",
            "success",
        ),
        (
            "workout",
            "2033-05-17T03:33:20+00:00",
            "2033-05-18T03:33:20+00:00",
            "success",
        ),
        (
            "diet",
            "2033-05-17T03:33:20+00:00",
            "2033-05-18T03:33:20+00:00",
            "success",
        ),
    ]


def test_sync_now_returns_job_immediately_and_status_finishes() -> None:
    started = Event()
    release = Event()

    def slow_sync(metric=None):
        started.set()
        release.wait(timeout=2)
        return {"status": "completed", "metric": metric}

    manager = mcp_sync.SyncJobManager(slow_sync)

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            sync_manager=manager,
        )
        async with Client(server, raise_exceptions=True) as client:
            before = time.monotonic()
            result = await client.call_tool("sync_now", {"metric": "heart_rate"})
            elapsed = time.monotonic() - before
            assert result.is_error is False
            payload = result.structured_content
            assert payload is not None
            assert elapsed < 0.5
            assert payload["status"] in {"queued", "running"}
            assert payload["metric"] == "heart_rate"
            job_id = payload["job_id"]

            assert started.wait(timeout=1)
            release.set()

            for _ in range(50):
                status_result = await client.call_tool("get_sync_status", {"job_id": job_id})
                status = status_result.structured_content
                assert status is not None
                if status["status"] == "completed":
                    assert status["result"]["metric"] == "heart_rate"
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("sync job did not finish")

    asyncio.run(run())


def test_sync_manager_reuses_active_job_for_same_metric() -> None:
    started = Event()
    release = Event()
    calls = 0

    def slow_sync(metric=None):
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=2)
        return {"status": "completed", "metric": metric}

    manager = mcp_sync.SyncJobManager(slow_sync)

    first = manager.start("heart_rate")
    assert started.wait(timeout=1)
    second = manager.start("heart_rate")

    try:
        assert second["job_id"] == first["job_id"]
        assert second["status"] in {"queued", "running"}
        assert calls == 1
    finally:
        release.set()


def test_sync_status_survives_manager_restart_via_job_store() -> None:
    class JobStore:
        def __init__(self):
            self.jobs: dict[str, dict[str, object]] = {}

        def save_sync_job(self, job: dict[str, object]) -> None:
            self.jobs[str(job["job_id"])] = deepcopy(job)

        def load_sync_job(self, job_id: str):
            job = self.jobs.get(job_id)
            return deepcopy(job) if job is not None else None

    store = JobStore()
    manager = mcp_sync.SyncJobManager(
        lambda metric=None: {"status": "completed", "metric": metric},
        job_store=store,
    )
    started = manager.start("heart_rate")
    job_id = str(started["job_id"])

    for _ in range(100):
        current = manager.status(job_id)
        if current["status"] == "completed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("sync job did not complete")

    restarted_manager = mcp_sync.SyncJobManager(
        lambda metric=None: {"status": "completed", "metric": metric},
        job_store=store,
    )
    recovered = restarted_manager.status(job_id)
    assert recovered["status"] == "completed"
    assert recovered["metric"] == "heart_rate"
    assert recovered["result"] == {"status": "completed", "metric": "heart_rate"}


def test_active_job_count_tracks_only_queued_or_running_jobs() -> None:
    started = Event()
    release = Event()

    def slow_sync(metric=None):
        started.set()
        release.wait(timeout=2)
        return {"status": "completed", "metric": metric}

    manager = mcp_sync.SyncJobManager(slow_sync)
    assert manager.active_job_count() == 0
    job = manager.start("heart_rate")
    assert started.wait(timeout=1)
    assert manager.active_job_count() == 1
    release.set()

    for _ in range(100):
        if manager.status(str(job["job_id"]))["status"] == "completed":
            break
        time.sleep(0.01)
    assert manager.active_job_count() == 0

def test_sync_failure_exposes_machine_readable_error_code(monkeypatch) -> None:
    class CodedError(RuntimeError):
        code = "xiaomi_auth_expired"

    def fail_sync(metric=None):
        raise CodedError("Xiaomi Cloud session expired")

    notified: list[str] = []
    monkeypatch.setattr(
        mcp_sync,
        "notify_xiaomi_auth_expired",
        lambda message: notified.append(message) or True,
    )

    manager = mcp_sync.SyncJobManager(fail_sync)
    started = manager.start("heart_rate")
    job_id = str(started["job_id"])

    for _ in range(100):
        current = manager.status(job_id)
        if current["status"] == "failed":
            break
        time.sleep(0.01)
    else:
        raise AssertionError("sync job did not fail")

    assert current["error_code"] == "xiaomi_auth_expired"
    assert current["error"] == "CodedError: Xiaomi Cloud session expired"
    assert notified == [
        "Xiaomi Cloud session expired. Open Mi Fitness and sign in again, "
        "then update the VPS Xiaomi credentials."
    ]
