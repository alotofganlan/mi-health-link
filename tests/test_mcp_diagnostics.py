from __future__ import annotations

import asyncio
import logging

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from xiaomi_health_sync.mcp_server import MCPDiagnosticsMiddleware


def test_diagnostics_log_method_and_tool_count_without_params(caplog) -> None:
    async def endpoint(_request):
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "get_available_metrics"},
                        {"name": "get_sync_status"},
                    ]
                },
            }
        )

    async def run() -> None:
        app = MCPDiagnosticsMiddleware(
            Starlette(routes=[Route("/mcp", endpoint, methods=["POST"])])
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://health.example.com",
        ) as client:
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {"secret": "do-not-log-me"},
                },
            )
            assert response.status_code == 200

    with caplog.at_level(logging.INFO, logger="xiaomi_health_sync.mcp_server"):
        asyncio.run(run())

    assert "MCP request method=tools/list" in caplog.text
    assert "MCP tools/list result tools=2" in caplog.text
    assert "do-not-log-me" not in caplog.text
