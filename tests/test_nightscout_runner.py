from __future__ import annotations

import httpx

from mi_health_link.nightscout_sync import sync_nightscout


class FakeGlucoseStore:
    def __init__(self, latest_ms: int | None):
        self.latest_ms = latest_ms
        self.saved: list[dict] = []

    def load_latest_glucose_timestamp_ms(self) -> int | None:
        return self.latest_ms

    def upsert_glucose_samples(self, samples: list[dict]) -> int:
        self.saved.extend(samples)
        return len(samples)


def test_sync_nightscout_uses_supabase_cursor_and_saves_normalized_rows():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, json=[
                {"_id": "two", "sgv": 126, "date": 3000, "direction": "Flat"},
                {"_id": "one", "sgv": 108, "date": 2000, "direction": "FortyFiveUp"},
            ])
        return httpx.Response(200, json=[])

    store = FakeGlucoseStore(latest_ms=1500)
    result = sync_nightscout(
        base_url="https://nightscout.example",
        token=None,
        store=store,
        batch_size=1000,
        transport=httpx.MockTransport(handler),
    )

    assert result == {
        "since_ms": 1500,
        "fetched_count": 2,
        "saved_count": 2,
        "invalid_count": 0,
        "latest_ms": 3000,
    }
    assert [row["source_record_id"] for row in store.saved] == ["one", "two"]
    assert dict(requests[0].url.params)["find[date][$gt]"] == "1500"


def test_sync_nightscout_skips_malformed_entries_without_aborting_batch():
    def handler(request: httpx.Request) -> httpx.Response:
        if "find%5Bdate%5D%5B%24lt%5D" not in str(request.url):
            return httpx.Response(200, json=[
                {"_id": "good", "sgv": 90, "date": 2000},
                {"_id": "bad", "date": 1900},
            ])
        return httpx.Response(200, json=[])

    store = FakeGlucoseStore(latest_ms=None)
    result = sync_nightscout(
        base_url="https://nightscout.example",
        token=None,
        store=store,
        batch_size=1000,
        transport=httpx.MockTransport(handler),
    )

    assert result["fetched_count"] == 2
    assert result["saved_count"] == 1
    assert result["invalid_count"] == 1
    assert store.saved[0]["glucose_mg_dl"] == 90
