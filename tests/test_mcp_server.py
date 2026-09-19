from __future__ import annotations

import asyncio

import httpx
from mcp import Client

from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_server import create_mcp_app, create_mcp_server


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


def test_foundation_tools_have_stable_names() -> None:
    async def run() -> None:
        server = create_mcp_server(_settings(), token_verifier=RejectAllVerifier())
        async with Client(server, raise_exceptions=True) as client:
            result = await client.list_tools()
            names = {tool.name for tool in result.tools}
            assert "get_available_metrics" in names
            assert "get_health_bundle" in names
            assert "get_sync_status" in names

    asyncio.run(run())


def test_get_available_metrics_does_not_need_xiaomi_cloud() -> None:
    async def run() -> None:
        server = create_mcp_server(_settings(), token_verifier=RejectAllVerifier())
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool("get_available_metrics", {})
            assert result.is_error is False
            assert result.structured_content is not None
            metrics = result.structured_content["metrics"]
            assert "sleep" in metrics
            assert "heart_rate" in metrics
            assert "spo2" in metrics
            assert "menstruation" in metrics

    asyncio.run(run())


def test_repair_intensity_tool_defaults_to_preview_and_uses_injected_runner() -> None:
    calls: list[dict[str, object]] = []

    def runner(**kwargs):
        calls.append(kwargs)
        return {"status": "preview", "missing_count": 3}

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            intensity_repair_runner=runner,
        )
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool(
                "repair_intensity_from_heart_rate",
                {
                    "start_at": "2026-09-16T16:40:57+08:00",
                    "end_at": "2026-09-16T17:23:45+08:00",
                    "maximum_heart_rate": 198,
                },
            )

        assert result.is_error is False
        assert result.structured_content["status"] == "preview"
        assert calls == [{
            "start_at": "2026-09-16T16:40:57+08:00",
            "end_at": "2026-09-16T17:23:45+08:00",
            "maximum_heart_rate": 198,
            "confirm_write": False,
        }]

    asyncio.run(run())


def test_http_health_is_public_and_mcp_requires_authentication() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            health = await client.get("/health")
            assert health.status_code == 200
            assert health.json() == {"status": "ok", "active_sync_jobs": 0}

            response = await client.post("/mcp", json={})
            assert response.status_code == 401
            authenticate = response.headers["www-authenticate"]
            assert "resource_metadata" in authenticate
            assert "/.well-known/oauth-protected-resource/mcp" in authenticate

    asyncio.run(run())


def test_protected_resource_metadata_points_to_supabase_issuer() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert response.status_code == 200
            payload = response.json()
            assert payload["resource"] == "https://health.example.com/mcp"
            assert payload["authorization_servers"] == [
                "https://project.supabase.co/auth/v1"
            ]
            assert payload["scopes_supported"] == ["openid"]

    asyncio.run(run())
