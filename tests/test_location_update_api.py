from __future__ import annotations

import asyncio

import httpx

from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_server import create_mcp_app
from mi_health_link.wake_report import SleepReportResult


class RejectAllVerifier:
    async def verify_token(self, token: str):
        return None


class FakeLocationService:
    def __init__(self):
        self.calls = []

    def process(self, payload, *, received_at=None):
        self.calls.append(payload)
        return SleepReportResult(True, "location_updated")


def settings() -> MCPSettings:
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


def payload():
    return {
        "latitude": 12.3456,
        "longitude": 67.8901,
        "accuracy": 35,
        "location_time": 1780000000,
        "source": "automate",
    }


def test_location_update_route_authenticates_validates_and_calls_location_only(monkeypatch) -> None:
    monkeypatch.setenv("WAKE_PROBE_TOKEN", "correct-secret")
    service = FakeLocationService()

    async def run():
        app = create_mcp_app(
            settings(),
            token_verifier=RejectAllVerifier(),
            location_update_service=service,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            valid = await client.post(
                "/api/location-update",
                headers={"Authorization": "Bearer correct-secret"},
                json=payload(),
            )
            wrong_token = await client.post(
                "/api/location-update",
                headers={"Authorization": "Bearer wrong"},
                json=payload(),
            )
            invalid = await client.post(
                "/api/location-update",
                headers={"Authorization": "Bearer correct-secret"},
                json={**payload(), "latitude": 91},
            )
        assert valid.status_code == 200
        assert valid.json() == {"ok": True, "status": "location_updated"}
        assert wrong_token.status_code == 401
        assert invalid.status_code == 400

    asyncio.run(run())
    assert service.calls == [payload()]
