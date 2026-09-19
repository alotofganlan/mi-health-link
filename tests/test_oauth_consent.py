from __future__ import annotations

import asyncio

import httpx

from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_server import create_mcp_app


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


def test_consent_requires_authorization_id() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            response = await client.get("/oauth/consent")
            assert response.status_code == 400
            assert "authorization_id" in response.text

    asyncio.run(run())


def test_consent_page_keeps_authorization_id_out_of_server_rendered_html() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        transport = httpx.ASGITransport(app=app)
        marker = "opaque-<script>alert(1)</script>"
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            response = await client.get(
                "/oauth/consent",
                params={"authorization_id": marker},
            )
            assert response.status_code == 200
            assert marker not in response.text
            assert "sb_publishable_example" in response.text
            assert "SUPABASE_SERVICE_ROLE_KEY" not in response.text
            assert response.headers["content-security-policy"]

    asyncio.run(run())


def test_consent_page_uses_only_repository_hosted_javascript() -> None:
    async def run() -> None:
        app = create_mcp_app(_settings(), token_verifier=RejectAllVerifier())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            response = await client.get(
                "/oauth/consent",
                params={"authorization_id": "opaque-id"},
            )
            assert response.status_code == 200
            assert '<script type="module" src="/oauth/consent.js"></script>' in response.text
            assert '<script type="module">' not in response.text
            assert "script-src 'self'" in response.headers["content-security-policy"]
            assert "cdn.jsdelivr.net" not in response.headers["content-security-policy"]

            script = await client.get("/oauth/consent.js")
            assert script.status_code == 200
            assert script.headers["content-type"].startswith("text/javascript")
            assert "cdn.jsdelivr.net" not in script.text
            assert 'from "https://' not in script.text
            assert "from 'https://" not in script.text
            assert "getAuthorizationDetails" in script.text
            assert "approveAuthorization" in script.text
            assert "denyAuthorization" in script.text

    asyncio.run(run())
