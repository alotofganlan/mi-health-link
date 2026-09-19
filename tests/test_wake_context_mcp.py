from __future__ import annotations

import asyncio

from mcp import Client

from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_server import create_mcp_server


class RejectAllVerifier:
    async def verify_token(self, token: str):
        return None


class FakeContextService:
    def get(self, report_id: str):
        assert report_id == "report-123"
        return {
            "title": "早安我的少年",
            "report_id": report_id,
            "location": {"city": "Test City"},
            "weather": {"available": True},
        }


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


def test_get_morning_context_tool_uses_report_id() -> None:
    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            wake_context_service=FakeContextService(),
        )
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert "get_morning_context" in {tool.name for tool in tools.tools}

            result = await client.call_tool(
                "get_morning_context",
                {"report_id": "report-123"},
            )
            assert result.is_error is False
            assert result.structured_content["title"] == "早安我的少年"
            assert result.structured_content["location"] == {"city": "Test City"}

    asyncio.run(run())
