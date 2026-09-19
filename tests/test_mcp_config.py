from __future__ import annotations

import pytest

from xiaomi_health_sync.mcp_config import load_mcp_settings


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://health.example.com/mcp")
    monkeypatch.setenv(
        "SUPABASE_AUTH_ISSUER_URL",
        "https://project.supabase.co/auth/v1",
    )
    monkeypatch.setenv(
        "SUPABASE_AUTH_JWKS_URL",
        "https://project.supabase.co/auth/v1/.well-known/jwks.json",
    )
    monkeypatch.setenv("MCP_ALLOWED_SUBJECT", "user-123")
    monkeypatch.setenv("SUPABASE_PROJECT_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")


def test_load_mcp_settings_parses_explicit_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8765")
    monkeypatch.setenv("MCP_REQUIRED_SCOPES", "openid,profile")

    settings = load_mcp_settings()

    assert settings.public_url == "https://health.example.com/mcp"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.supabase_issuer_url == "https://project.supabase.co/auth/v1"
    assert settings.supabase_jwks_url.endswith("/.well-known/jwks.json")
    assert settings.allowed_subject == "user-123"
    assert settings.required_scopes == ("openid", "profile")
    assert settings.supabase_project_url == "https://project.supabase.co"
    assert settings.supabase_publishable_key == "sb_publishable_example"


def test_load_mcp_settings_uses_safe_loopback_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)
    monkeypatch.delenv("MCP_REQUIRED_SCOPES", raising=False)

    settings = load_mcp_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8765
    assert settings.required_scopes == ("openid",)


def test_load_mcp_settings_requires_allowed_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.delenv("MCP_ALLOWED_SUBJECT", raising=False)

    with pytest.raises(ValueError, match="MCP_ALLOWED_SUBJECT"):
        load_mcp_settings()


def test_load_mcp_settings_rejects_invalid_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("MCP_PORT", "not-a-port")

    with pytest.raises(ValueError, match="MCP_PORT"):
        load_mcp_settings()
