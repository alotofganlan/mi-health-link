from __future__ import annotations

import asyncio

import httpx
from pathlib import Path

from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_server import create_mcp_app


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


def test_consent_script_uses_supabase_oauth_server_api() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            response = await client.get("/oauth/consent.js")

        assert response.status_code == 200
        source = (
            Path(__file__).parents[1] / "web" / "oauth-consent.js"
        ).read_text(encoding="utf-8")
        assert "supabase.auth.oauth.getAuthorizationDetails" in source
        assert "supabase.auth.oauth.approveAuthorization" in source
        assert "supabase.auth.oauth.denyAuthorization" in source
        assert "/oauth/consent/details" not in source
        assert "/oauth/consent/approve" not in source
        assert "/oauth/consent/deny" not in source

    asyncio.run(run())


def test_mcp_settings_have_no_vps_auth_mode_switch() -> None:
    settings = _settings()
    assert not hasattr(settings, "auth_mode")
    assert not hasattr(settings, "oauth_enabled")
