from datetime import datetime, timezone

import pytest

from xiaomi_health_sync.auto_discovery import (
    AutoDiscoveryRunner,
    DiscoveryError,
    XiaomiAuthExpiredError,
    extract_latest_keys,
)
from xiaomi_health_sync.history_policy import initial_backfill_start_time
from xiaomi_health_sync.xiaomi import XiaomiResponse


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def encrypted_post(self, path, payload):
        self.calls.append((path, payload))
        return self.responses.pop(0)


class FakeStore:
    def __init__(
        self,
        known=(),
        observed=(),
        backfilled=(),
        progress=None,
        supported=None,
    ):
        self.known = set(known)
        self.observed = set(observed)
        self.backfilled = set(backfilled)
        self.progress = dict(progress or {})
        self.raw = []
        self.discovered = []
        self.normalized = []
        self.supported = set(supported or {
            "sleep",
            "heart_rate",
            "spo2",
            "weight",
            "menstruation",
            "intensity",
        })

    def load_discovered_keys(self):
        return set(self.known)

    def load_observed_keys(self):
        return set(self.observed)

    def remember_discovered_key(self, key):
        self.known.add(key)
        self.discovered.append(key)

    def load_backfilled_keys(self):
        return set(self.backfilled)

    def remember_backfilled_key(self, key):
        self.backfilled.add(key)

    def load_backfill_progress(self, key):
        return self.progress.get(key)

    def remember_backfill_progress(self, key, next_start_time):
        self.progress[key] = next_start_time

    def upsert_normalized_record(self, record):
        if record.get("key") not in self.supported:
            return False
        self.normalized.append(record)
        return True

    def save_raw(self, *, record_type, payload, **kwargs):
        self.raw.append((record_type, payload))
        return f"rid-{len(self.raw)}"


def xr(payload, status=200):
    return XiaomiResponse(
        status_code=status,
        json_data=payload,
        text="",
        request_id="req",
    )


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("sleep", 2_000_000_000 - 365 * 86400),
        ("menstruation", 0),
        ("heart_rate", 2_000_000_000 - 30 * 86400),
    ],
)
def test_initial_backfill_start_time_uses_per_key_policy(key, expected):
    assert initial_backfill_start_time(key, 2_000_000_000) == expected


def test_extract_latest_keys_uses_every_data_list_key():
    response = {
        "code": 0,
        "result": {
            "data_list": [
                {"key": "steps", "value": "a"},
                {"key": "sleep", "value": "b"},
                {"key": "steps", "value": "c"},
                {"key": 123},
                {"value": "no-key"},
            ]
        },
    }
    assert extract_latest_keys(response) == ["123", "sleep", "steps"]


def test_supported_latest_and_history_are_normalized_without_raw_envelopes():
    latest = xr({
        "code": 0,
        "result": {
            "data_list": [{
                "key": "heart_rate",
                "sid": "watch",
                "time": 2_000_000_000,
                "value": '{"time":2000000000,"bpm":72,"type":0}',
            }]
        },
    })
    history = xr({
        "code": 0,
        "result": {
            "data_list": [{
                "key": "heart_rate",
                "sid": "watch",
                "time": 1_999_999_940,
                "value": '{"time":1999999940,"bpm":70,"type":0}',
            }],
            "has_more": False,
        },
    })
    store = FakeStore(known={"heart_rate"}, observed={"heart_rate"})
    client = FakeClient([latest, history])

    AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(2_000_000_000, tz=timezone.utc),
        history_window_seconds=30 * 86400 + 1,
    ).run(backfill_start_time=0)

    assert store.raw == []
    assert [record["key"] for record in store.normalized] == [
        "heart_rate",
        "heart_rate",
    ]
    assert store.normalized[0]["value"]["bpm"] == 72


def test_invalid_latest_is_error_and_is_not_persisted_raw():
    store = FakeStore(
        known={"steps"},
        observed={"steps"},
        backfilled={"steps"},
    )
    client = FakeClient([
        xr({"code": -8, "message": "invalid params", "result": None})
    ])

    with pytest.raises(DiscoveryError, match="invalid params"):
        AutoDiscoveryRunner(client=client, store=store).run()

    assert store.raw == []
    assert store.normalized == []


def test_history_backfill_follows_next_key_until_complete():
    latest = xr({
        "code": 0,
        "result": {"data_list": [{"key": "sleep"}]},
    })
    page1 = xr({
        "code": 0,
        "result": {
            "data_list": [{"key": "sleep", "time": 1}],
            "has_more": True,
            "next_key": "cursor-2",
        },
    })
    page2 = xr({
        "code": 0,
        "result": {
            "data_list": [{"key": "sleep", "time": 2}],
            "has_more": False,
        },
    })
    store = FakeStore(known={"sleep"}, observed={"sleep"})
    client = FakeClient([latest, page1, page2])

    AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
        history_window_seconds=10**10,
    ).run(backfill_start_time=0)

    expected_start = 1787270400 - 365 * 86400
    assert client.calls[1][1] == {
        "key": "sleep",
        "start_time": expected_start,
        "end_time": 1787270400,
    }
    assert client.calls[2][1] == {
        "key": "sleep",
        "start_time": expected_start,
        "end_time": 1787270400,
        "next_key": "cursor-2",
    }
    assert store.raw == []
    assert store.backfilled == {"sleep"}


def test_history_is_windowed_checkpointed_and_reports_progress():
    latest = xr({"code": 0, "result": {"data_list": [{"key": "sleep"}]}})
    done = xr({"code": 0, "result": {"data_list": [], "has_more": False}})
    store = FakeStore(known={"sleep"}, observed={"sleep"})
    client = FakeClient([latest, done, done, done])
    events = []

    AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(25, tz=timezone.utc),
        history_window_seconds=10,
        progress=events.append,
    ).run(backfill_start_time=0)

    history_calls = [
        payload for path, payload in client.calls
        if path == "/app/v1/data/get_fitness_data_by_time"
    ]
    assert history_calls == [
        {"key": "sleep", "start_time": 0, "end_time": 9},
        {"key": "sleep", "start_time": 10, "end_time": 19},
        {"key": "sleep", "start_time": 20, "end_time": 25},
    ]
    assert store.progress["sleep"] == 26
    assert any("sleep" in event and "0..9" in event for event in events)
    assert "sleep" in store.backfilled


def test_completed_initial_backfill_still_runs_incrementally_from_checkpoint():
    end_time = 2_000_000_000
    checkpoint = end_time - 5
    latest = xr({"code": 0, "result": {"data_list": [{"key": "sleep"}]}})
    done = xr({"code": 0, "result": {"data_list": [], "has_more": False}})
    store = FakeStore(
        known={"sleep"},
        observed={"sleep"},
        backfilled={"sleep"},
        progress={"sleep": checkpoint},
    )
    client = FakeClient([latest, done])

    AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(end_time, tz=timezone.utc),
        history_window_seconds=10,
    ).run(backfill_start_time=0)

    assert client.calls[-1][1] == {
        "key": "sleep",
        "start_time": checkpoint,
        "end_time": end_time,
    }
    assert store.progress["sleep"] == end_time + 1


def test_unsupported_key_is_skipped_but_successful_history_checkpoint_advances():
    end_time = 2_000_000_000
    key = "future_unknown_metric"
    latest = xr({
        "code": 0,
        "result": {"data_list": [{"key": key, "time": end_time}]},
    })
    history = xr({
        "code": 0,
        "result": {
            "data_list": [{"key": key, "time": end_time - 10}],
            "has_more": False,
        },
    })
    store = FakeStore(
        known={key},
        observed={key},
    )
    client = FakeClient([latest, history])

    AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(end_time, tz=timezone.utc),
        history_window_seconds=30 * 86400 + 1,
    ).run(backfill_start_time=0)

    assert store.normalized == []
    assert store.progress[key] == end_time + 1
    assert key in store.backfilled


def test_extra_key_returned_by_xiaomi_is_registered_and_backfilled():
    end_time = 2_000_000_000
    latest = xr({
        "code": 0,
        "result": {
            "data_list": [{"key": "steps"}, {"key": "new_metric"}]
        },
    })
    history = xr({
        "code": 0,
        "result": {"data_list": [], "has_more": False},
    })
    store = FakeStore(
        known={"steps"},
        observed={"steps"},
        backfilled={"steps"},
        progress={"steps": end_time + 1},
    )
    client = FakeClient([latest, history])

    result = AutoDiscoveryRunner(
        client=client,
        store=store,
        now=lambda: datetime.fromtimestamp(end_time, tz=timezone.utc),
        history_window_seconds=30 * 86400 + 1,
    ).run(backfill_start_time=0)

    assert result.new_keys == ["new_metric"]
    assert "new_metric" in store.known
    assert "new_metric" in store.backfilled
    assert client.calls[-1][1]["start_time"] == end_time - 30 * 86400


def test_no_observed_or_registered_keys_fails_instead_of_sending_empty_request():
    store = FakeStore()
    client = FakeClient([])

    with pytest.raises(DiscoveryError, match="No Xiaomi-observed fitness keys"):
        AutoDiscoveryRunner(client=client, store=store).run()

    assert client.calls == []


def test_xiaomi_401_is_classified_as_xiaomi_auth_expired():
    store = FakeStore(known={"steps"}, observed={"steps"})
    client = FakeClient([
        xr({"code": 401, "message": "unauthorized"}, status=401)
    ])

    with pytest.raises(
        XiaomiAuthExpiredError,
        match="xiaomi_auth_expired",
    ) as raised:
        AutoDiscoveryRunner(client=client, store=store).run()

    assert raised.value.code == "xiaomi_auth_expired"
