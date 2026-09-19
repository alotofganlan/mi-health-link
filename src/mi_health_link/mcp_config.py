from __future__ import annotations

from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class MCPSettings:
    public_url: str
    host: str
    port: int
    supabase_issuer_url: str
    supabase_jwks_url: str
    allowed_subject: str
    required_scopes: tuple[str, ...]
    supabase_project_url: str
    supabase_publishable_key: str
    supabase_audience: str = "authenticated"


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("MCP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("MCP_PORT must be between 1 and 65535")
    return port


def _parse_scopes(value: str) -> tuple[str, ...]:
    scopes = tuple(part.strip() for part in value.split(",") if part.strip())
    if not scopes:
        raise ValueError("MCP_REQUIRED_SCOPES must contain at least one scope")
    return scopes


def load_mcp_settings() -> MCPSettings:
    load_dotenv()
    public_url = _required_env("MCP_PUBLIC_URL").rstrip("/")
    return MCPSettings(
        public_url=public_url,
        host=(os.getenv("MCP_HOST") or "127.0.0.1").strip() or "127.0.0.1",
        port=_parse_port((os.getenv("MCP_PORT") or "8765").strip()),
        supabase_issuer_url=_required_env("SUPABASE_AUTH_ISSUER_URL").rstrip("/"),
        supabase_jwks_url=_required_env("SUPABASE_AUTH_JWKS_URL"),
        allowed_subject=_required_env("MCP_ALLOWED_SUBJECT"),
        required_scopes=_parse_scopes(os.getenv("MCP_REQUIRED_SCOPES") or "openid"),
        supabase_project_url=_required_env("SUPABASE_PROJECT_URL").rstrip("/"),
        supabase_publishable_key=_required_env("SUPABASE_PUBLISHABLE_KEY"),
        supabase_audience=(os.getenv("SUPABASE_AUTH_AUDIENCE") or "authenticated").strip()
        or "authenticated",
    )
