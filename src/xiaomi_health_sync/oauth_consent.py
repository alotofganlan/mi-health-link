from __future__ import annotations

from importlib.resources import files
import json

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from .mcp_config import MCPSettings


def _browser_config(settings: MCPSettings) -> str:
    payload = json.dumps(
        {
            "supabaseUrl": settings.supabase_project_url,
            "publishableKey": settings.supabase_publishable_key,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return payload.replace("<", "\\u003c").replace(">", "\\u003e")


def _content_security_policy(settings: MCPSettings) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            f"connect-src 'self' {settings.supabase_project_url}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "base-uri 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
    )


def oauth_consent_response(request: Request, settings: MCPSettings) -> Response:
    if not request.query_params.get("authorization_id"):
        return PlainTextResponse("Missing authorization_id", status_code=400)

    template = (
        files("xiaomi_health_sync")
        .joinpath("static/oauth-consent.html")
        .read_text(encoding="utf-8")
    )
    body = template.replace("__OAUTH_CONFIG__", _browser_config(settings), 1)
    return HTMLResponse(
        body,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": _content_security_policy(settings),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


def oauth_consent_script_response() -> Response:
    body = (
        files("xiaomi_health_sync")
        .joinpath("static/oauth-consent.js")
        .read_text(encoding="utf-8")
    )
    return Response(
        body,
        media_type="text/javascript",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
