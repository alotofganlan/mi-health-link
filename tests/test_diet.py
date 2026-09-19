from __future__ import annotations

import json

import httpx

from mi_health_link.diet import normalize_diet_records
from mi_health_link.diet_sync import DietSyncRunner
from mi_health_link.supabase_store import SupabaseStore
from mi_health_link.xiaomi import XiaomiResponse


def _raw_record(*, watermark: str = "wm-1", time: int = 1788250638) -> dict:
    return {
        "sid": "01affa2c-b5df-4d3e-96cc-d898461a31e9",
        "dining": 1,
        "time": time,
        "value": json.dumps(
            {
                "item_list": [
                    {
                        "level1": {
                            "food_id": 1001021,
                            "name": "水煮鸡蛋",
                            "weight": 1.0,
                            "weight_unit": "个",
                            "calorie": 86.0,
                            "carbohydrate": 0.06,
                            "protein": 7.26,
                            "fat": 6.3,
                            "dietary_fiber": 0.000006,
                        },
                        "level2": [],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        "zone_offset": 28800,
        "zone_name": "Asia/Shanghai",
        "update_time": time,
        "watermark": watermark,
    }


def test_normalize_diet_records_expands_real_food_payload() -> None:
    rows = normalize_diet_records([_raw_record()])

    assert len(rows) == 1
    assert rows[0]["source_record_id"] == "wm-1:0"
    assert rows[0]["eaten_at"] == "2026-09-01T08:17:18+00:00"
    assert rows[0]["meal_type_code"] == 1
    assert rows[0]["food_item"] == "水煮鸡蛋"
    assert rows[0]["food_id"] == 1001021
    assert rows[0]["amount"] == 1.0
    assert rows[0]["amount_unit"] == "个"
    assert rows[0]["calories"] == 86.0
    assert rows[0]["carbohydrate"] == 0.06
    assert rows[0]["protein"] == 7.26
    assert rows[0]["fat"] == 6.3
    assert rows[0]["dietary_fiber"] == 0.000006


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_diet_records_by_time(self, **payload: object) -> XiaomiResponse:
        self.calls.append(payload)
        return XiaomiResponse(
            status_code=200,
            json_data={
                "code": 0,
                "message": "ok",
                "result": {
                    "diet_records": [_raw_record()],
                    "next_key": "",
                    "has_more": False,
                },
            },
            text="",
        )


class _FakeStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def upsert_diet_records(self, rows: list[dict[str, object]]) -> int:
        self.rows.extend(rows)
        return len(rows)


def test_diet_sync_uses_unix_seconds_and_read_only_endpoint() -> None:
    client = _FakeClient()
    store = _FakeStore()

    result = DietSyncRunner(client=client, store=store).sync_window(
        start_time=1788192000,
        end_time=1788250671,
    )

    assert result == {"pages": 1, "records": 1, "food_items": 1}
    assert client.calls == [
        {
            "dining": 0,
            "limit": 100,
            "start_time": 1788192000,
            "end_time": 1788250671,
            "reverse": False,
            "next_key": "",
        }
    ]
    assert len(store.rows) == 1


def test_supabase_store_groups_diet_meals_and_replaces_food_items() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path.endswith("/diet_meals"):
            return httpx.Response(201, json=[{"id": 42}])
        if request.method == "DELETE" and request.url.path.endswith("/diet_food_items"):
            return httpx.Response(204)
        if request.method == "POST" and request.url.path.endswith("/diet_food_items"):
            return httpx.Response(201, json=[])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    store = SupabaseStore(
        "https://project.supabase.co",
        "sb_secret_test",
        transport=httpx.MockTransport(handler),
    )
    rows = normalize_diet_records([_raw_record()])
    assert store.upsert_diet_records(rows) == 1

    meal_request = next(request for request in requests if request.url.path.endswith("/diet_meals"))
    meal_body = json.loads(meal_request.content)
    assert meal_body["source_meal_id"] == "2026-09-01:1"
    assert meal_body["meal_type"] == "breakfast"
    assert meal_body["total_calorie_kcal"] == 86.0

    food_request = next(
        request for request in requests
        if request.method == "POST" and request.url.path.endswith("/diet_food_items")
    )
    food_body = json.loads(food_request.content)
    assert food_body[0]["diet_meal_id"] == 42
    assert food_body[0]["name"] == "水煮鸡蛋"
    assert food_body[0]["source_item_id"] == "wm-1"
