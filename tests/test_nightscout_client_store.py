from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx

from xiaomi_health_sync.nightscout_sync import fetch_entries
from xiaomi_health_sync.sync_store import DirectSupabaseStore


def test_fetch_entries_pages_backward_without_losing_records():
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params.multi_items())
        seen_queries.append(query)
        before = query.get("find[date][$lt]")
        if before is None:
            payload = [
                {"_id": "c", "sgv": 120, "date": 3000, "direction": "Flat"},
                {"_id": "b", "sgv": 110, "date": 2000, "direction": "FortyFiveDown"},
            ]
        elif before == "2000":
            payload = [{"_id": "a", "sgv": 100, "date": 1000, "direction": "Flat"}]
        else:
            payload = []
        return httpx.Response(200, json=payload)

    rows = fetch_entries(
        base_url="https://nightscout.example",
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    assert [row["_id"] for row in rows] == ["a", "b", "c"]
    assert seen_queries == [
        {"count": "2"},
        {"count": "2", "find[date][$lt]": "2000"},
        {"count": "2", "find[date][$lt]": "1000"},
    ]


def test_fetch_entries_keeps_since_filter_on_every_incremental_page():
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = dict(request.url.params.multi_items())
        seen_queries.append(query)
        if len(seen_queries) == 1:
            return httpx.Response(200, json=[
                {"_id": "c", "sgv": 120, "date": 3000},
                {"_id": "b", "sgv": 110, "date": 2000},
            ])
        return httpx.Response(200, json=[])

    rows = fetch_entries(
        base_url="https://nightscout.example/",
        token="reader-token",
        since_ms=1500,
        batch_size=2,
        transport=httpx.MockTransport(handler),
    )

    assert [row["_id"] for row in rows] == ["b", "c"]
    assert seen_queries[0]["find[date][$gt]"] == "1500"
    assert seen_queries[1]["find[date][$gt]"] == "1500"
    assert seen_queries[1]["find[date][$lt]"] == "2000"
    assert all(query["token"] == "reader-token" for query in seen_queries)


def test_fetch_entries_sends_hashed_api_secret_header():
    seen_headers: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers.get("api-secret"))
        return httpx.Response(200, json=[])

    fetch_entries(
        base_url="https://nightscout.example",
        api_secret_sha1="0123456789abcdef0123456789abcdef01234567",
        transport=httpx.MockTransport(handler),
    )

    assert seen_headers == ["0123456789abcdef0123456789abcdef01234567"]


def test_glucose_store_loads_latest_timestamp_and_upserts_by_source_record_id():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{
                "measured_at": "2026-08-22T10:00:00+00:00"
            }])
        assert request.method == "POST"
        return httpx.Response(201, json=[])

    store = DirectSupabaseStore(
        url="https://project.supabase.co",
        service_role_key="sb_secret_test",
        transport=httpx.MockTransport(handler),
    )

    latest_ms = store.load_latest_glucose_timestamp_ms()
    assert latest_ms == int(
        datetime(2026, 8, 22, 10, 0, tzinfo=timezone.utc).timestamp() * 1000
    )

    count = store.upsert_glucose_samples([{
        "source": "nightscout",
        "source_record_id": "abc",
        "measured_at": "2026-08-22T10:05:00+00:00",
        "glucose_mg_dl": 108,
        "glucose_mmol_l": 6.0,
        "direction": "Flat",
    }])
    assert count == 1

    get_request, post_request = requests
    assert get_request.url.path.endswith("/rest/v1/glucose_samples")
    assert dict(get_request.url.params)["order"] == "measured_at.desc"
    assert post_request.url.path.endswith("/rest/v1/glucose_samples")
    assert dict(post_request.url.params)["on_conflict"] == "source,source_record_id"
    assert json.loads(post_request.content)[0]["source_record_id"] == "abc"
