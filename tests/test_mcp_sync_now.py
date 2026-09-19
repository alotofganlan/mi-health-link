from __future__ import annotations

import asyncio

from mcp import Client

from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_server import create_mcp_server


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


def test_sync_now_tool_calls_injected_runner() -> None:
    calls = 0

    def sync_runner() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {
            "status": "completed",
            "discovered_count": 21,
            "new_count": 0,
        }

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            sync_runner=sync_runner,
        )
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            assert "sync_now" in names

            result = await client.call_tool("sync_now", {})
            assert result.is_error is False
            assert result.structured_content == {
                "status": "completed",
                "discovered_count": 21,
                "new_count": 0,
            }
            assert calls == 1

    asyncio.run(run())
