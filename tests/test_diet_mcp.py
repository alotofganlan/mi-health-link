from __future__ import annotations

import asyncio

import httpx
from mcp import Client

from mi_health_link.mcp_bundle import HealthBundleService
from mi_health_link.mcp_config import MCPSettings
from mi_health_link.mcp_health_data import NormalizedHealthReader
from mi_health_link.mcp_server import create_mcp_server


class _RejectAllVerifier:
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


def test_reader_queries_diet_meals_with_embedded_food_items() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "source": "xiaomi",
                    "source_meal_id": "2026-09-01:1",
                    "meal_date": "2026-09-01",
                    "dining": 1,
                    "meal_type": "breakfast",
                    "recorded_at": "2026-09-01T08:17:18+00:00",
                    "total_calorie_kcal": 457,
                    "total_protein_g": 31.98,
                    "total_carbohydrate_g": 44.43,
                    "total_fat_g": 17.25,
                    "diet_food_items": [
                        {
                            "name": "水煮鸡蛋",
                            "quantity": 1,
                            "unit": "个",
                            "calorie_kcal": 86,
                        }
                    ],
                }
            ],
        )

    reader = NormalizedHealthReader(
        "https://project.supabase.co",
        "service-role-test",
        transport=httpx.MockTransport(handler),
    )
    result = reader.query(
        "diet",
        start_at="2026-09-01T00:00:00Z",
        end_at="2026-09-01T23:59:59Z",
    )

    assert result["metric"] == "diet"
    assert result["count"] == 1
    assert result["records"][0]["meal_type"] == "breakfast"
    assert result["records"][0]["diet_food_items"][0]["name"] == "水煮鸡蛋"
    assert seen[0].url.path == "/rest/v1/diet_meals"
    assert "diet_food_items" in seen[0].url.params["select"]


def test_diet_bundle_summary_uses_source_totals() -> None:
    records = [
        {
            "source_meal_id": "2026-09-01:1",
            "meal_type": "breakfast",
            "total_calorie_kcal": 457,
            "total_protein_g": 31.98,
            "total_carbohydrate_g": 44.43,
            "total_fat_g": 17.25,
            "diet_food_items": [{}, {}, {}],
        }
    ]

    summary = HealthBundleService._summary("diet", records)

    assert summary["meal_count"] == 1
    assert summary["food_item_count"] == 3
    assert summary["total_calories_kcal"] == 457
    assert summary["total_protein_g"] == 31.98
    assert summary["total_carbohydrate_g"] == 44.43
    assert summary["total_fat_g"] == 17.25


def test_get_available_metrics_exposes_diet() -> None:
    async def run() -> None:
        server = create_mcp_server(
            _settings(),
            token_verifier=_RejectAllVerifier(),
        )
        async with Client(server, raise_exceptions=True) as client:
            result = await client.call_tool("get_available_metrics", {})
            assert result.is_error is False
            assert result.structured_content is not None
            assert "diet" in result.structured_content["metrics"]

    asyncio.run(run())
