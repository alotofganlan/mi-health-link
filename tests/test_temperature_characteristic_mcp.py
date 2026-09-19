from __future__ import annotations

import asyncio

from mcp import Client

from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_server import create_mcp_server


class RejectAllVerifier:
    async def verify_token(self, token: str):
        return None


class FakeManager:
    def start_backfill(self, metric: str, start_at: str, end_at: str):
        return {"job_id": "job", "status": "queued", "kind": "backfill", "metric": metric}

    def start(self, metric=None):
        return {"job_id": "sync", "status": "queued", "metric": metric}

    def status(self, job_id):
        return {"job_id": job_id, "status": "queued"}

    def active_job_count(self):
        return 0


def test_temperature_characteristic_is_queryable_and_backfillable() -> None:
    async def run() -> None:
        settings = MCPSettings(
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
        server = create_mcp_server(
            settings,
            token_verifier=RejectAllVerifier(),
            sync_manager=FakeManager(),
        )
        async with Client(server, raise_exceptions=True) as client:
            metrics = await client.call_tool("get_available_metrics", {})
            assert "temperature_characteristic" in metrics.structured_content["metrics"]
            result = await client.call_tool(
                "backfill_health_data",
                {
                    "metric": "temperature_characteristic",
                    "start_at": "2026-01-01T00:00:00+08:00",
                    "end_at": "2026-07-24T23:59:59+08:00",
                },
            )
            assert result.is_error is False
            assert result.structured_content["kind"] == "backfill"

    asyncio.run(run())
