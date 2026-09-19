from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import load_settings
from .mcp_auth import SupabaseTokenVerifier
from .mcp_bundle import HealthBundleService
from .mcp_config import MCPSettings, load_mcp_settings
from .mcp_coverage import (
    classify_coverage,
    ranges_cover_window,
    successful_range_check_at_or_after,
)
from .mcp_health_data import NormalizedHealthReader
from .mcp_sync import SyncJobManager, get_default_sync_manager
from .intensity_write import repair_intensity_window
from .morning_health import MorningHealthService
from .oauth_consent import oauth_consent_response, oauth_consent_script_response
from .supabase_store import GENERIC_NORMALIZED_KEYS
from .wake_context import WakeContextService
from .wake_endpoints import LocationUpdateService, UnlockProbeService
from .wake_payloads import parse_location_update_payload, parse_unlock_payload
from .wake_report import SleepReportService
from .wake_store import WakeStore
from .wake_weather import WakeWeather


logger = logging.getLogger(__name__)

XIAOMI_SYNC_METRICS = tuple(
    sorted(
        GENERIC_NORMALIZED_KEYS
        | {
            "heart_rate",
            "intensity",
            "menstruation",
            "sleep",
            "spo2",
            "temperature_characteristic",
            "weight",
            "diet",
            "workout",
        }
    )
)

SUPPORTED_METRICS = tuple(sorted(set(XIAOMI_SYNC_METRICS) | {"glucose"}))


class MCPDiagnosticsMiddleware:
    """Log MCP method names and tools/list counts without request parameters."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/mcp":
            await self.app(scope, receive, send)
            return

        captured_messages: list[Message] = []
        request_body = bytearray()
        while True:
            message = await receive()
            captured_messages.append(message)
            if message.get("type") != "http.request":
                break
            body = message.get("body", b"")
            if isinstance(body, bytes):
                request_body.extend(body)
            if not message.get("more_body", False):
                break

        request_method = self._jsonrpc_method(bytes(request_body))
        if request_method:
            logger.info("MCP request method=%s", request_method)

        replay_index = 0

        async def replay_receive() -> Message:
            nonlocal replay_index
            if replay_index < len(captured_messages):
                message = captured_messages[replay_index]
                replay_index += 1
                return message
            return await receive()

        response_body = bytearray()

        async def send_with_diagnostics(message: Message) -> None:
            if message.get("type") == "http.response.body" and request_method == "tools/list":
                body = message.get("body", b"")
                if isinstance(body, bytes):
                    response_body.extend(body)
                if not message.get("more_body", False):
                    tool_count = self._tools_count(bytes(response_body))
                    if tool_count is not None:
                        logger.info("MCP tools/list result tools=%d", tool_count)
            await send(message)

        await self.app(scope, replay_receive, send_with_diagnostics)

    @staticmethod
    def _jsonrpc_method(body: bytes) -> str | None:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        method = payload.get("method")
        return method if isinstance(method, str) and method else None

    @staticmethod
    def _tools_count(body: bytes) -> int | None:
        try:
            payload: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        tools = result.get("tools")
        if not isinstance(tools, list):
            return None
        return len(tools)


def _default_health_reader(settings: MCPSettings) -> NormalizedHealthReader:
    app_settings = load_settings()
    service_role_key = app_settings.supabase_service_role_key
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for health data queries")
    url = app_settings.supabase_url or settings.supabase_project_url
    return NormalizedHealthReader(url, service_role_key)


def _wake_store(settings: MCPSettings) -> WakeStore:
    app_settings = load_settings()
    service_role_key = app_settings.supabase_service_role_key
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for wake endpoints")
    url = app_settings.supabase_url or settings.supabase_project_url
    return WakeStore(url, service_role_key)


def _default_wake_probe_service(
    settings: MCPSettings,
    manager: SyncJobManager | None,
) -> UnlockProbeService:
    store = _wake_store(settings)
    return UnlockProbeService(
        store=store,
        sync_manager=manager or get_default_sync_manager(),
        report_service=SleepReportService(store=store),
    )


def _default_location_update_service(settings: MCPSettings) -> LocationUpdateService:
    return LocationUpdateService(
        store=_wake_store(settings),
        weather=WakeWeather(),
    )


def _default_wake_context_service(settings: MCPSettings) -> WakeContextService:
    app_settings = load_settings()
    service_role_key = app_settings.supabase_service_role_key
    if not service_role_key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is required for wake context")
    url = app_settings.supabase_url or settings.supabase_project_url
    reader = NormalizedHealthReader(url, service_role_key)
    timezone_name = os.getenv("WAKE_TIMEZONE", "Asia/Shanghai")
    report_timezone = ZoneInfo(timezone_name)
    return WakeContextService(
        store=WakeStore(url, service_role_key),
        weather=WakeWeather(),
        health=MorningHealthService(reader=reader, timezone=report_timezone),
        timezone_name=timezone_name,
    )


def _wait_for_sync(manager: SyncJobManager, timeout_seconds: float = 180.0) -> dict[str, object]:
    job = manager.start(None)
    job_id = job.get("job_id")
    if not isinstance(job_id, str):
        raise RuntimeError("sync manager did not return a job id")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        current = manager.status(job_id)
        status = current.get("status")
        if status == "completed":
            result = current.get("result")
            return result if isinstance(result, dict) else current
        if status == "failed":
            raise RuntimeError(str(current.get("error") or "Xiaomi synchronization failed"))
        time.sleep(0.2)
    raise TimeoutError("Xiaomi synchronization did not finish within 180 seconds")


def create_mcp_server(
    settings: MCPSettings,
    *,
    token_verifier: TokenVerifier | None = None,
    health_reader: Any | None = None,
    sync_manager: SyncJobManager | None = None,
    sync_runner: Callable[[], dict[str, object]] | None = None,
    wake_probe_service: Any | None = None,
    location_update_service: Any | None = None,
    wake_context_service: Any | None = None,
    intensity_repair_runner: Callable[..., dict[str, object]] | None = None,
) -> MCPServer:
    verifier = token_verifier or SupabaseTokenVerifier(settings)
    manager = sync_manager or (None if sync_runner is not None else get_default_sync_manager())
    mcp = MCPServer(
        "Xiaomi Health",
        description="Private normalized health data access plus Xiaomi synchronization tools.",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.supabase_issuer_url),
            resource_server_url=AnyHttpUrl(settings.public_url),
            required_scopes=list(settings.required_scopes),
        ),
    )
    background_tasks: set[asyncio.Task[Any]] = set()

    @mcp.tool()
    def get_available_metrics() -> dict[str, object]:
        """List health metrics currently supported by normalized storage."""
        return {
            "metrics": list(SUPPORTED_METRICS),
            "source": "normalized_storage",
        }

    @mcp.tool()
    def get_morning_context(report_id: str) -> dict[str, object]:
        """Return one report context without exposing coordinates."""
        service = wake_context_service or _default_wake_context_service(settings)
        return service.get(report_id)

    @mcp.tool()
    def repair_intensity_from_heart_rate(
        start_at: str,
        end_at: str,
        maximum_heart_rate: float,
        confirm_write: bool = False,
    ) -> dict[str, object]:
        """Preview missing Xiaomi intensity minutes from heart rate; write only when confirm_write is true."""
        runner = intensity_repair_runner or repair_intensity_window
        return runner(
            start_at=start_at,
            end_at=end_at,
            maximum_heart_rate=maximum_heart_rate,
            confirm_write=confirm_write,
        )

    @mcp.tool()
    def get_health_data(
        metric: str,
        start_at: str,
        end_at: str,
        limit: int = 500,
    ) -> dict[str, object]:
        """Query normalized health records in an ISO-8601 time window."""
        if metric not in SUPPORTED_METRICS:
            raise ValueError(f"Unsupported metric: {metric}")
        reader = health_reader or _default_health_reader(settings)
        return reader.query(metric, start_at=start_at, end_at=end_at, limit=limit)

    @mcp.tool()
    def get_health_bundle(
        queries: list[dict[str, Any]],
        sync_policy: str = "if_needed",
    ) -> dict[str, object]:
        """Read several Xiaomi health metrics at once; optionally refresh Xiaomi once when coverage needs it."""
        for query in queries:
            metric = query.get("metric") if isinstance(query, dict) else None
            if metric not in XIAOMI_SYNC_METRICS:
                raise ValueError(f"Unsupported bundle metric: {metric}")
            if not query.get("start_at") or not query.get("end_at"):
                raise ValueError("Each bundle query requires start_at and end_at")
        reader = health_reader or _default_health_reader(settings)
        if sync_runner is not None:
            blocking_sync = sync_runner
        else:
            assert manager is not None
            blocking_sync = lambda: _wait_for_sync(manager)
        return HealthBundleService(reader=reader, sync_runner=blocking_sync).get_bundle(
            queries,
            sync_policy=sync_policy,
        )

    @mcp.tool()
    def get_data_coverage(
        metric: str,
        start_at: str,
        end_at: str,
    ) -> dict[str, object]:
        """Describe normalized Xiaomi coverage, including persisted historical source checks."""
        if metric not in XIAOMI_SYNC_METRICS:
            return {"metric": metric, "start_at": start_at, "end_at": end_at, "status": "unsupported"}

        reader = health_reader or _default_health_reader(settings)
        local = reader.coverage(metric, start_at=start_at, end_at=end_at)
        latest_sync = None
        if manager is not None and hasattr(manager, "latest_for_metric"):
            latest_sync = manager.latest_for_metric(metric)

        source_checked_at = None
        sync_failed = False
        if isinstance(latest_sync, dict):
            status = latest_sync.get("status")
            completed_at = latest_sync.get("completed_at")
            if status in {"completed", "failed"} and isinstance(completed_at, str):
                source_checked_at = completed_at
            sync_failed = status == "failed"

        if latest_sync is None and hasattr(reader, "source_check"):
            persisted = reader.source_check(metric)
            if isinstance(persisted, dict):
                checked_at = persisted.get("source_checked_at")
                if isinstance(checked_at, str):
                    source_checked_at = checked_at
                sync_failed = persisted.get("source_check_status") == "failed"

        checks: list[dict[str, Any]] = []
        if hasattr(reader, "range_checks"):
            loaded = reader.range_checks(metric, start_at=start_at, end_at=end_at)
            if isinstance(loaded, list):
                checks = [row for row in loaded if isinstance(row, dict)]
        range_complete = ranges_cover_window(checks, start_at, end_at)
        if (
            sync_failed
            and range_complete
            and successful_range_check_at_or_after(checks, source_checked_at)
        ):
            sync_failed = False

        result = classify_coverage(
            metric=metric,
            start_at=start_at,
            end_at=end_at,
            count=int(local.get("count") or 0),
            first_at=local.get("first_at") if isinstance(local.get("first_at"), str) else None,
            latest_at=local.get("latest_at") if isinstance(local.get("latest_at"), str) else None,
            source_checked_at=source_checked_at,
            sync_failed=sync_failed,
            range_checked_complete=range_complete,
        )
        if checks:
            result["checked_ranges"] = checks
        return result

    if sync_runner is not None:
        @mcp.tool(name="sync_now")
        def sync_now_legacy() -> dict[str, object]:
            return sync_runner()

        @mcp.tool()
        def get_sync_status(job_id: str) -> dict[str, object]:
            return {"job_id": job_id, "status": "not_found"}
    else:
        assert manager is not None

        @mcp.tool(name="sync_now")
        def sync_now_background(metric: str | None = None) -> dict[str, object]:
            """Start recent Xiaomi synchronization and return a durable job id immediately."""
            if metric is not None and metric not in XIAOMI_SYNC_METRICS:
                raise ValueError(f"Unsupported Xiaomi sync metric: {metric}")
            return manager.start(metric)

        @mcp.tool()
        def backfill_health_data(
            metric: str,
            start_at: str,
            end_at: str,
        ) -> dict[str, object]:
            """Re-fetch one Xiaomi metric over an exact ISO-8601 historical range."""
            if metric not in XIAOMI_SYNC_METRICS:
                raise ValueError(f"Unsupported Xiaomi backfill metric: {metric}")
            return manager.start_backfill(metric, start_at, end_at)

        @mcp.tool()
        def get_sync_status(job_id: str) -> dict[str, object]:
            """Return recent-sync or historical-backfill job status."""
            return manager.status(job_id)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        active_sync_jobs = 0
        if manager is not None and hasattr(manager, "active_job_count"):
            active_sync_jobs = manager.active_job_count()
        return JSONResponse({"status": "ok", "active_sync_jobs": active_sync_jobs})

    def wake_auth_error(request: Request) -> JSONResponse | None:
        expected = (os.getenv("WAKE_PROBE_TOKEN") or "").strip()
        if not expected:
            return JSONResponse(
                {"ok": False, "status": "not_configured"},
                status_code=503,
            )
        authorization = request.headers.get("authorization") or ""
        scheme, separator, supplied = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not supplied
            or not secrets.compare_digest(supplied, expected)
        ):
            return JSONResponse(
                {"ok": False, "status": "unauthorized"},
                status_code=401,
            )
        return None

    @mcp.custom_route("/api/location-update", methods=["POST"])
    async def location_update_route(request: Request) -> JSONResponse:
        auth_error = wake_auth_error(request)
        if auth_error is not None:
            return auth_error
        received_at = datetime.now(timezone.utc)
        try:
            payload = await request.json()
            parse_location_update_payload(payload, received_at=received_at)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "status": "invalid_request"},
                status_code=400,
            )
        service = location_update_service or _default_location_update_service(settings)
        try:
            result = await asyncio.to_thread(
                service.process, payload, received_at=received_at
            )
        except Exception as exc:
            logger.error(
                "location_update processing_failed type=%s", type(exc).__name__
            )
            return JSONResponse(
                {"ok": False, "status": "service_error"},
                status_code=500,
            )
        return JSONResponse(result.as_dict(), status_code=200 if result.ok else 502)

    @mcp.custom_route("/api/wake-probe", methods=["POST"])
    async def wake_probe_route(request: Request) -> JSONResponse:
        auth_error = wake_auth_error(request)
        if auth_error is not None:
            return auth_error
        received_at = datetime.now(timezone.utc)
        try:
            payload = await request.json()
            parse_unlock_payload(
                payload,
                received_at=received_at,
                expected_device=os.getenv("WAKE_PROBE_DEVICE", "primary_phone"),
            )
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            return JSONResponse(
                {"ok": False, "status": "invalid_request"},
                status_code=400,
            )
        service = wake_probe_service or _default_wake_probe_service(settings, manager)

        async def process_in_background() -> None:
            try:
                result = await asyncio.to_thread(
                    service.process, payload, received_at=received_at
                )
                logger.info("wake_probe background_status=%s", result.status)
            except Exception as exc:
                logger.error(
                    "wake_probe processing_failed type=%s", type(exc).__name__
                )

        task = asyncio.create_task(process_in_background())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return JSONResponse({"ok": True, "status": "accepted"}, status_code=202)

    @mcp.custom_route("/oauth/consent", methods=["GET"])
    async def oauth_consent(request: Request) -> Response:
        return oauth_consent_response(request, settings)

    @mcp.custom_route("/oauth/consent.js", methods=["GET"])
    async def oauth_consent_script(_: Request) -> Response:
        return oauth_consent_script_response()

    return mcp


def _transport_security(settings: MCPSettings) -> TransportSecuritySettings:
    parsed = urlsplit(settings.public_url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("MCP_PUBLIC_URL must contain a hostname")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return TransportSecuritySettings(
        allowed_hosts=[hostname, f"{hostname}:*", "127.0.0.1:*", "localhost:*"],
        allowed_origins=[origin],
    )


def create_mcp_app(
    settings: MCPSettings,
    *,
    token_verifier: TokenVerifier | None = None,
    wake_probe_service: Any | None = None,
    location_update_service: Any | None = None,
    wake_context_service: Any | None = None,
):
    mcp = create_mcp_server(
        settings,
        token_verifier=token_verifier,
        wake_probe_service=wake_probe_service,
        location_update_service=location_update_service,
        wake_context_service=wake_context_service,
    )
    app = mcp.streamable_http_app(
        host=settings.host,
        json_response=True,
        stateless_http=True,
        transport_security=_transport_security(settings),
    )
    return MCPDiagnosticsMiddleware(app)


def main() -> None:
    import uvicorn

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = load_mcp_settings()
    app = create_mcp_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
