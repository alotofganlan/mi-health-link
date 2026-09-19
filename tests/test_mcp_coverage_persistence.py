from __future__ import annotations

import asyncio

import httpx
from mcp import Client

from xiaomi_health_sync.mcp_config import MCPSettings
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


def test_reader_loads_persisted_source_check() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v1/xiaomi_sync_state"
        assert request.url.params["key"] == "eq.heart_rate"
        return httpx.Response(200, json=[{
            "source_checked_at": "2026-08-22T06:38:30+00:00",
            "source_check_status": "success",
            "source_latest_at": "2026-08-22T04:35:00+00:00",
            "next_recheck_at": "2026-08-22T07:08:30+00:00",
            "empty_check_count": 1,
        }])

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    assert reader.source_check("heart_rate") == {
        "source_checked_at": "2026-08-22T06:38:30+00:00",
        "source_check_status": "success",
        "source_latest_at": "2026-08-22T04:35:00+00:00",
        "next_recheck_at": "2026-08-22T07:08:30+00:00",
        "empty_check_count": 1,
    }


def test_get_data_coverage_falls_back_to_persisted_source_check() -> None:
    class FakeReader:
        def coverage(self, metric: str, *, start_at: str, end_at: str):
            return {
                "metric": metric,
                "count": 319,
                "first_at": "2026-08-22T01:51:00+08:00",
                "latest_at": "2026-08-22T12:35:00+08:00",
            }

        def source_check(self, metric: str):
            return {
                "source_checked_at": "2026-08-22T06:38:30+00:00",
                "source_check_status": "success",
                "source_latest_at": "2026-08-22T04:35:00+00:00",
                "next_recheck_at": "2026-08-22T07:08:30+00:00",
                "empty_check_count": 1,
            }

    class EmptyManager:
        def latest_for_metric(self, metric: str):
            return None

        def start(self, metric=None):
            raise AssertionError("not used")

        def status(self, job_id):
            raise AssertionError("not used")

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            health_reader=FakeReader(),
            sync_manager=EmptyManager(),
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
            assert payload["source_checked_at"] == "2026-08-22T06:38:30+00:00"

    asyncio.run(run())
