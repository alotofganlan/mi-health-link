from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from mcp import Client

from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_coverage import classify_coverage
from xiaomi_health_sync.mcp_health_data import NormalizedHealthReader
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


def test_recent_successful_sync_with_trailing_source_gap_is_source_empty() -> None:
    result = classify_coverage(
        metric="heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T14:38:00+08:00",
        count=319,
        first_at="2026-08-22T01:51:00+08:00",
        latest_at="2026-08-22T12:35:00+08:00",
        source_checked_at="2026-08-22T14:38:30+08:00",
        sync_failed=False,
        now=datetime(2026, 8, 22, 6, 39, tzinfo=timezone.utc),
    )

    assert result["status"] == "source_empty"
    assert result["source_checked_at"] == "2026-08-22T14:38:30+08:00"
    assert result["latest_at"] == "2026-08-22T12:35:00+08:00"
    assert result["next_recheck_at"] is not None


def test_records_without_recent_source_check_are_partial() -> None:
    result = classify_coverage(
        metric="heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T14:38:00+08:00",
        count=319,
        first_at="2026-08-22T01:51:00+08:00",
        latest_at="2026-08-22T12:35:00+08:00",
        source_checked_at=None,
        sync_failed=False,
        now=datetime(2026, 8, 22, 6, 39, tzinfo=timezone.utc),
    )

    assert result["status"] == "partial"


def test_empty_without_source_check_is_not_synced() -> None:
    result = classify_coverage(
        metric="heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T14:38:00+08:00",
        count=0,
        first_at=None,
        latest_at=None,
        source_checked_at=None,
        sync_failed=False,
        now=datetime(2026, 8, 22, 6, 39, tzinfo=timezone.utc),
    )

    assert result["status"] == "not_synced"


def test_failed_sync_is_sync_failed() -> None:
    result = classify_coverage(
        metric="heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T14:38:00+08:00",
        count=319,
        first_at="2026-08-22T01:51:00+08:00",
        latest_at="2026-08-22T12:35:00+08:00",
        source_checked_at="2026-08-22T14:38:30+08:00",
        sync_failed=True,
        now=datetime(2026, 8, 22, 6, 39, tzinfo=timezone.utc),
    )

    assert result["status"] == "sync_failed"


def test_source_check_older_than_requested_end_is_partial() -> None:
    result = classify_coverage(
        metric="heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T15:30:00+08:00",
        count=319,
        first_at="2026-08-22T01:51:00+08:00",
        latest_at="2026-08-22T12:35:00+08:00",
        source_checked_at="2026-08-22T14:38:30+08:00",
        sync_failed=False,
        now=datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc),
    )

    assert result["status"] == "partial"


def test_reader_coverage_returns_count_first_and_latest() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        order = request.url.params["order"]
        if order.endswith(".asc"):
            return httpx.Response(
                200,
                json=[{"measured_at": "2026-08-22T01:51:00+08:00"}],
                headers={"Content-Range": "0-0/319"},
            )
        return httpx.Response(
            200,
            json=[{"measured_at": "2026-08-22T12:35:00+08:00"}],
            headers={"Content-Range": "0-0/319"},
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    coverage = reader.coverage(
        "heart_rate",
        start_at="2026-08-22T00:00:00+08:00",
        end_at="2026-08-22T14:38:00+08:00",
    )

    assert calls == 2
    assert coverage == {
        "metric": "heart_rate",
        "count": 319,
        "first_at": "2026-08-22T01:51:00+08:00",
        "latest_at": "2026-08-22T12:35:00+08:00",
    }


def test_get_data_coverage_uses_latest_metric_sync_status() -> None:
    class FakeReader:
        def coverage(self, metric: str, *, start_at: str, end_at: str):
            return {
                "metric": metric,
                "count": 319,
                "first_at": "2026-08-22T01:51:00+08:00",
                "latest_at": "2026-08-22T12:35:00+08:00",
            }

    class FakeManager:
        def latest_for_metric(self, metric: str):
            assert metric == "heart_rate"
            return {
                "job_id": "job-1",
                "status": "completed",
                "metric": metric,
                "completed_at": "2026-08-22T06:38:30+00:00",
            }

        def start(self, metric=None):
            raise AssertionError("not used")

        def status(self, job_id):
            raise AssertionError("not used")

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            health_reader=FakeReader(),
            sync_manager=FakeManager(),
        )
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert "get_data_coverage" in {tool.name for tool in tools.tools}
            result = await client.call_tool(
                "get_data_coverage",
                {
                    "metric": "heart_rate",
                    "start_at": "2026-08-22T00:00:00+08:00",
                    "end_at": "2026-08-22T14:38:00+08:00",
                },
            )
            assert result.is_error is False
            payload = result.structured_content
            assert payload is not None
            assert payload["status"] == "source_empty"
            assert payload["latest_at"] == "2026-08-22T12:35:00+08:00"
            assert payload["source_checked_at"] == "2026-08-22T06:38:30+00:00"

    asyncio.run(run())


def test_newer_complete_range_supersedes_stale_targeted_failure() -> None:
    class FakeReader:
        def coverage(self, metric: str, *, start_at: str, end_at: str):
            return {
                "metric": metric,
                "count": 0,
                "first_at": None,
                "latest_at": None,
            }

        def range_checks(self, metric: str, *, start_at: str, end_at: str):
            return [{
                "range_start": "2026-08-22T00:00:00+08:00",
                "range_end": "2026-08-22T14:38:00+08:00",
                "checked_at": "2026-08-22T07:00:00+00:00",
                "status": "success",
            }]

    class FakeManager:
        def latest_for_metric(self, metric: str):
            return {
                "job_id": "job-failed",
                "status": "failed",
                "metric": metric,
                "completed_at": "2026-08-22T06:30:00+00:00",
            }

        def start(self, metric=None):
            raise AssertionError("not used")

        def status(self, job_id):
            raise AssertionError("not used")

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            health_reader=FakeReader(),
            sync_manager=FakeManager(),
        )
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(
                "get_data_coverage",
                {
                    "metric": "heart_rate",
                    "start_at": "2026-08-22T00:00:00+08:00",
                    "end_at": "2026-08-22T14:38:00+08:00",
                },
            )
            payload = result.structured_content
            assert payload is not None
            assert payload["status"] == "source_empty"
            assert payload["range_checked_complete"] is True

    asyncio.run(run())
