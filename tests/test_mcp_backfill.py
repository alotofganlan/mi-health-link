from __future__ import annotations

import asyncio
from threading import Event
import time

import pytest
from mcp import Client

from xiaomi_health_sync.auto_discovery import AutoDiscoveryRunner
from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_server import create_mcp_server
from xiaomi_health_sync.mcp_sync import SyncJobManager
from xiaomi_health_sync.xiaomi import XiaomiResponse


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


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        self.calls.append((path, payload))
        return XiaomiResponse(
            status_code=200,
            json_data={"code": 0, "result": {"data_list": [], "has_more": False}},
            text="",
            request_id="req",
        )


class FakeStore:
    def __init__(self) -> None:
        self.progress = {"temperature_trend": 2_000_000_000}

    def load_discovered_keys(self):
        return {"temperature_trend"}

    def load_observed_keys(self):
        return {"temperature_trend"}

    def remember_discovered_key(self, key):
        raise AssertionError("not expected")

    def load_backfilled_keys(self):
        return {"temperature_trend"}

    def remember_backfilled_key(self, key):
        raise AssertionError("range backfill must not mutate completion checkpoint")

    def load_backfill_progress(self, key):
        return self.progress.get(key)

    def remember_backfill_progress(self, key, next_start_time):
        raise AssertionError("range backfill must not mutate history checkpoint")

    def upsert_normalized_record(self, record):
        return True


def test_sync_range_queries_exact_window_and_ignores_existing_checkpoint() -> None:
    client = FakeClient()
    store = FakeStore()
    runner = AutoDiscoveryRunner(
        client=client,
        store=store,
        history_window_seconds=30 * 86400,
    )

    result = runner.sync_range(
        selected_keys=("temperature_trend",),
        start_time=1_700_000_000,
        end_time=1_700_086_400,
    )

    assert result.discovered_keys == ["temperature_trend"]
    assert len(client.calls) == 1
    path, payload = client.calls[0]
    assert path == "/app/v1/data/get_fitness_data_by_time"
    assert payload["key"] == "temperature_trend"
    assert payload["start_time"] == 1_700_000_000
    assert payload["end_time"] == 1_700_086_400
    assert store.progress["temperature_trend"] == 2_000_000_000


def test_sync_range_rejects_reversed_window() -> None:
    with pytest.raises(ValueError, match="end_time"):
        AutoDiscoveryRunner(client=FakeClient(), store=FakeStore()).sync_range(
            selected_keys=("temperature_trend",),
            start_time=20,
            end_time=10,
        )


def test_job_manager_runs_backfill_as_durable_job() -> None:
    calls: list[tuple[str, str, str]] = []

    def backfill(metric: str, start_at: str, end_at: str):
        calls.append((metric, start_at, end_at))
        return {"status": "completed", "metric": metric, "start_at": start_at, "end_at": end_at}

    manager = SyncJobManager(
        sync_callable=lambda metric: {"status": "completed", "metric": metric},
        backfill_callable=backfill,
    )
    job = manager.start_backfill(
        "temperature_trend",
        "2026-01-01T00:00:00+08:00",
        "2026-07-24T23:59:59+08:00",
    )

    for _ in range(1000):
        status = manager.status(job["job_id"])
        if status["status"] in {"completed", "failed"}:
            break
    assert status["status"] == "completed"
    assert status["kind"] == "backfill"
    assert calls == [(
        "temperature_trend",
        "2026-01-01T00:00:00+08:00",
        "2026-07-24T23:59:59+08:00",
    )]


def test_backfill_job_reports_completed_window_progress_while_running() -> None:
    progress_seen = Event()
    release = Event()

    def backfill(metric: str, start_at: str, end_at: str, progress):
        progress("[range temperature_trend] completed 1767196800..1769788799")
        progress_seen.set()
        release.wait(timeout=2)
        return {"status": "completed", "metric": metric}

    manager = SyncJobManager(
        sync_callable=lambda metric: {"status": "completed", "metric": metric},
        backfill_callable=backfill,
    )
    job = manager.start_backfill(
        "temperature_trend",
        "2026-01-01T00:00:00+08:00",
        "2026-07-24T23:59:59+08:00",
    )
    assert progress_seen.wait(timeout=1)
    running = manager.status(str(job["job_id"]))
    assert running["status"] == "running"
    assert running["result"]["progress"]["completed_windows"] == 1
    assert running["result"]["progress"]["last_window_start"] == 1767196800
    assert running["result"]["progress"]["last_window_end"] == 1769788799
    release.set()

    for _ in range(100):
        finished = manager.status(str(job["job_id"]))
        if finished["status"] == "completed":
            break
        time.sleep(0.01)
    assert finished["status"] == "completed"


def test_mcp_exposes_backfill_for_normalized_temperature_metrics() -> None:
    class FakeManager:
        def start_backfill(self, metric: str, start_at: str, end_at: str):
            return {
                "job_id": "job-backfill",
                "status": "queued",
                "kind": "backfill",
                "metric": metric,
                "start_at": start_at,
                "end_at": end_at,
            }

        def start(self, metric=None):
            return {"job_id": "job-sync", "status": "queued", "metric": metric}

        def status(self, job_id):
            return {"job_id": job_id, "status": "queued"}

        def active_job_count(self):
            return 0

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            sync_manager=FakeManager(),
        )
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert "backfill_health_data" in {tool.name for tool in tools.tools}
            metrics = await client.call_tool("get_available_metrics", {})
            for metric in ("temperature_trend", "single_temperature"):
                assert metric in metrics.structured_content["metrics"]
                result = await client.call_tool(
                    "backfill_health_data",
                    {
                        "metric": metric,
                        "start_at": "2026-01-01T00:00:00+08:00",
                        "end_at": "2026-07-24T23:59:59+08:00",
                    },
                )
                assert result.is_error is False
                assert result.structured_content["job_id"] == "job-backfill"
                assert result.structured_content["kind"] == "backfill"

    asyncio.run(run())
