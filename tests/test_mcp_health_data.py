from __future__ import annotations

import asyncio

import httpx
from mcp import Client

from xiaomi_health_sync.mcp_config import MCPSettings
from xiaomi_health_sync.mcp_health_data import NormalizedHealthReader
from xiaomi_health_sync.mcp_server import create_mcp_server


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


def test_reader_queries_generic_metric_in_requested_window() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "source": "xiaomi",
                    "key": "stress",
                    "measured_at": "2026-08-22T01:00:00+00:00",
                    "value": {"stress": 32},
                    "metrics": {},
                }
            ],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    result = reader.query(
        "stress",
        start_at="2026-08-22T00:00:00Z",
        end_at="2026-08-22T23:59:59Z",
        limit=100,
    )

    assert result["metric"] == "stress"
    assert result["count"] == 1
    assert result["records"][0]["value"] == {"stress": 32}
    assert len(seen) == 1
    request = seen[0]
    assert request.url.path == "/rest/v1/health_records"
    params = request.url.params
    assert params["key"] == "eq.stress"
    assert params["measured_at"] == "gte.2026-08-22T00:00:00Z"
    assert params.get_list("measured_at") == [
        "gte.2026-08-22T00:00:00Z",
        "lte.2026-08-22T23:59:59Z",
    ]
    assert params["limit"] == "100"


def test_reader_maps_heart_rate_to_specialized_table() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "source": "xiaomi",
                    "measured_at": "2026-08-22T01:00:00+00:00",
                    "bpm": 72,
                }
            ],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    result = reader.query(
        "heart_rate",
        start_at="2026-08-22T00:00:00Z",
        end_at="2026-08-22T23:59:59Z",
    )

    assert seen[0].url.path == "/rest/v1/heart_rate_samples"
    assert "key" not in seen[0].url.params
    assert result["records"][0]["bpm"] == 72


def test_reader_fetches_heart_rate_daily_summaries_through_private_rpc() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{
                "day": "2026-09-15",
                "sample_count": 1334,
                "avg_bpm": 72.5,
                "min_bpm": 48,
                "max_bpm": 132,
            }],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    result = reader.query_daily_summaries(
        "heart_rate",
        start_at="2026-08-17T00:00:00+08:00",
        end_at="2026-09-15T23:59:59.999999+08:00",
        timezone="Asia/Shanghai",
    )

    assert seen[0].method == "POST"
    assert seen[0].url.path == "/rest/v1/rpc/get_morning_heart_rate_daily"
    assert result == {
        "metric": "heart_rate",
        "days": [{
            "day": "2026-09-15",
            "summary": {
                "count": 1334,
                "field": "bpm",
                "avg": 72.5,
                "min": 48.0,
                "max": 132.0,
            },
        }],
    }


def test_reader_maps_workout_to_normalized_table() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{
                "source": "xiaomi",
                "source_record_id": "workout-1",
                "activity_type": "boxing",
                "start_at": "2026-09-14T10:00:00+00:00",
                "end_at": "2026-09-14T10:30:00+00:00",
            }],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    result = reader.query(
        "workout",
        start_at="2026-09-14T00:00:00Z",
        end_at="2026-09-14T23:59:59Z",
    )

    assert seen[0].url.path == "/rest/v1/workouts"
    params = seen[0].url.params
    assert params["source"] == "eq.xiaomi"
    assert params.get_list("start_at") == [
        "gte.2026-09-14T00:00:00Z",
        "lte.2026-09-14T23:59:59Z",
    ]
    assert "key" not in params
    assert result["records"][0]["activity_type"] == "boxing"


def test_reader_maps_glucose_to_nightscout_table_and_source() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "source": "nightscout",
                    "measured_at": "2026-08-23T05:00:00+00:00",
                    "glucose_mg_dl": 108,
                    "glucose_mmol_l": 6.0,
                    "direction": "Flat",
                }
            ],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    result = reader.query(
        "glucose",
        start_at="2026-08-23T00:00:00Z",
        end_at="2026-08-23T23:59:59Z",
    )

    assert seen[0].url.path == "/rest/v1/glucose_samples"
    params = seen[0].url.params
    assert params["source"] == "eq.nightscout"
    assert "key" not in params
    assert result["metric"] == "glucose"
    assert result["count"] == 1
    assert result["records"][0]["glucose_mg_dl"] == 108
    assert result["records"][0]["glucose_mmol_l"] == 6.0
    assert result["records"][0]["direction"] == "Flat"


def test_get_health_data_tool_returns_reader_records() -> None:
    class FakeReader:
        def query(self, metric: str, *, start_at: str, end_at: str, limit: int = 500):
            assert metric == "heart_rate"
            assert start_at == "2026-08-22T00:00:00Z"
            assert end_at == "2026-08-22T23:59:59Z"
            assert limit == 50
            return {
                "metric": metric,
                "count": 1,
                "records": [{"measured_at": start_at, "bpm": 70}],
            }

    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            health_reader=FakeReader(),
        )
        async with Client(server, raise_exceptions=True) as client:
            tools = await client.list_tools()
            assert "get_health_data" in {tool.name for tool in tools.tools}
            result = await client.call_tool(
                "get_health_data",
                {
                    "metric": "heart_rate",
                    "start_at": "2026-08-22T00:00:00Z",
                    "end_at": "2026-08-22T23:59:59Z",
                    "limit": 50,
                },
            )
            assert result.is_error is False
            assert result.structured_content == {
                "metric": "heart_rate",
                "count": 1,
                "records": [
                    {
                        "measured_at": "2026-08-22T00:00:00Z",
                        "bpm": 70,
                    }
                ],
            }

    asyncio.run(run())


def test_get_available_metrics_exposes_glucose_as_normalized_storage() -> None:
    async def run() -> None:
        server = create_mcp_server(_settings(), token_verifier=RejectAllVerifier())
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool("get_available_metrics", {})
            assert result.is_error is False
            payload = result.structured_content
            assert payload is not None
            assert "glucose" in payload["metrics"]
            assert "workout" in payload["metrics"]
            assert payload["source"] == "normalized_storage"

    asyncio.run(run())


def test_sync_now_does_not_route_glucose_into_xiaomi_sync() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.started: list[str | None] = []

        def start(self, metric: str | None):
            self.started.append(metric)
            return {"job_id": "job-1", "status": "queued", "metric": metric}

        def status(self, job_id: str):
            return {"job_id": job_id, "status": "not_found"}

        def active_job_count(self) -> int:
            return 0

    async def run() -> None:
        manager = FakeManager()
        server = create_mcp_server(
            _settings(),
            token_verifier=RejectAllVerifier(),
            sync_manager=manager,
        )
        async with Client(server, raise_exceptions=False) as client:
            result = await client.call_tool("sync_now", {"metric": "glucose"})
            assert result.is_error is True
            assert manager.started == []

    asyncio.run(run())
