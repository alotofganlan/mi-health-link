from datetime import datetime, timezone

from xiaomi_health_sync.auto_discovery import AutoDiscoveryRunner
from xiaomi_health_sync.xiaomi import XiaomiResponse


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def encrypted_post(self, path, payload):
        self.calls.append((path, payload))
        return self.responses.pop(0)


class FakeStore:
    def __init__(self):
        self.progress = {"heart_rate": 123}
        self.backfilled = set()
        self.normalized = []

    def load_discovered_keys(self):
        return {"heart_rate"}

    def load_observed_keys(self):
        return {"heart_rate"}

    def remember_discovered_key(self, key):
        pass

    def load_backfilled_keys(self):
        return set(self.backfilled)

    def remember_backfilled_key(self, key):
        self.backfilled.add(key)

    def load_backfill_progress(self, key):
        return self.progress.get(key)

    def remember_backfill_progress(self, key, next_start_time):
        self.progress[key] = next_start_time

    def upsert_normalized_record(self, record):
        self.normalized.append(record)
        return True


def xr(payload):
    return XiaomiResponse(
        status_code=200,
        json_data=payload,
        text="",
        request_id="req",
    )


def test_recent_sync_does_not_advance_historical_checkpoint() -> None:
    end_time = 2_000_000_000
    latest = xr({
        "code": 0,
        "result": {
            "data_list": [{
                "key": "heart_rate",
                "sid": "watch",
                "time": end_time,
                "value": '{"time":2000000000,"bpm":72,"type":0}',
            }]
        },
    })
    recent = xr({
        "code": 0,
        "result": {
            "data_list": [{
                "key": "heart_rate",
                "sid": "watch",
                "time": end_time - 60,
                "value": '{"time":1999999940,"bpm":70,"type":0}',
            }],
            "has_more": False,
        },
    })
    store = FakeStore()
    client = FakeClient([latest, recent])

    result = AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(end_time, tz=timezone.utc),
    ).sync_recent(
        selected_keys=("heart_rate",),
        recent_window_seconds=24 * 60 * 60,
    )

    assert result.discovered_keys == ["heart_rate"]
    assert client.calls[1][1] == {
        "key": "heart_rate",
        "start_time": end_time - 24 * 60 * 60,
        "end_time": end_time,
    }
    assert store.progress["heart_rate"] == 123
    assert store.backfilled == set()


def test_recent_sync_reports_successful_empty_window_as_checked() -> None:
    end_time = 2_000_000_000
    empty = xr({
        "code": 0,
        "result": {"data_list": [], "has_more": False},
    })
    checked_ranges = []

    AutoDiscoveryRunner(
        client=FakeClient([empty, empty]),
        store=FakeStore(),
        now=lambda: datetime.fromtimestamp(end_time, tz=timezone.utc),
    ).sync_recent(
        selected_keys=("heart_rate",),
        recent_window_seconds=24 * 60 * 60,
        on_checked_range=lambda key, start, end: checked_ranges.append(
            (key, start, end)
        ),
    )

    assert checked_ranges == [(
        "heart_rate",
        end_time - 24 * 60 * 60,
        end_time,
    )]
