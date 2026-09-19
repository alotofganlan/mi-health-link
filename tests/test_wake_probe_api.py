from __future__ import annotations

import asyncio
import time

import httpx

from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_server import create_mcp_app
from xiaomi_health_sync.wake_report import SleepReportResult


class RejectAllVerifier:
    async def verify_token(self, token: str):
        return None


class FakeWakeProbeService:
    def __init__(self, *, delay_seconds: float = 0) -> None:
        self.calls: list[object] = []
        self.delay_seconds = delay_seconds

    def process(self, payload, *, received_at=None) -> SleepReportResult:
        self.calls.append(payload)
        time.sleep(self.delay_seconds)
        return SleepReportResult(ok=True, status="no_new_sleep")


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


def valid_payload() -> dict[str, object]:
    return {
        "event": "unlock",
        "device": "primary_phone",
        "timestamp": 1780000000,
    }


def test_valid_token_accepts_wake_probe_without_waiting_for_slow_report(monkeypatch) -> None:
    monkeypatch.setenv("WAKE_PROBE_TOKEN", "correct-secret")
    service = FakeWakeProbeService(delay_seconds=0.2)

    async def run() -> None:
        app = create_mcp_app(
            settings(),
            token_verifier=RejectAllVerifier(),
            wake_probe_service=service,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            started = time.monotonic()
            response = await client.post(
                "/api/wake-probe",
                headers={"Authorization": "Bearer correct-secret"},
                json=valid_payload(),
            )
            elapsed = time.monotonic() - started
        assert response.status_code == 202
        assert response.json() == {"ok": True, "status": "accepted"}
        assert elapsed < 0.1

    asyncio.run(run())
    assert service.calls == [valid_payload()]


def test_wrong_or_missing_token_returns_401_without_calling_service(monkeypatch) -> None:
    monkeypatch.setenv("WAKE_PROBE_TOKEN", "correct-secret")
    service = FakeWakeProbeService()

    async def run() -> None:
        app = create_mcp_app(
            settings(),
            token_verifier=RejectAllVerifier(),
            wake_probe_service=service,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            wrong = await client.post(
                "/api/wake-probe",
                headers={"Authorization": "Bearer wrong-secret"},
                json=valid_payload(),
            )
            missing = await client.post("/api/wake-probe", json=valid_payload())
        assert wrong.status_code == 401
        assert missing.status_code == 401
        assert wrong.json()["status"] == "unauthorized"

    asyncio.run(run())
    assert service.calls == []


def test_invalid_json_or_coordinates_return_400(monkeypatch) -> None:
    monkeypatch.setenv("WAKE_PROBE_TOKEN", "correct-secret")
    service = FakeWakeProbeService()

    async def run() -> None:
        app = create_mcp_app(
            settings(),
            token_verifier=RejectAllVerifier(),
            wake_probe_service=service,
        )
        headers = {
            "Authorization": "Bearer correct-secret",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            broken = await client.post(
                "/api/wake-probe",
                headers=headers,
                content=b"{broken",
            )
            bad_coordinates = await client.post(
                "/api/wake-probe",
                headers=headers,
                json={**valid_payload(), "event": "screen_on"},
            )
        assert broken.status_code == 400
        assert bad_coordinates.status_code == 400
        assert broken.json()["status"] == "invalid_request"

    asyncio.run(run())
    assert service.calls == []


def test_unconfigured_server_token_fails_closed(monkeypatch) -> None:
    monkeypatch.delenv("WAKE_PROBE_TOKEN", raising=False)
    service = FakeWakeProbeService()

    async def run() -> None:
        app = create_mcp_app(
            settings(),
            token_verifier=RejectAllVerifier(),
            wake_probe_service=service,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://health.example.com",
        ) as client:
            response = await client.post(
                "/api/wake-probe",
                headers={"Authorization": "Bearer any-value"},
                json=valid_payload(),
            )
        assert response.status_code == 503
        assert response.json()["status"] == "not_configured"

    asyncio.run(run())
    assert service.calls == []
