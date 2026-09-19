from __future__ import annotations

import pytest

from xiaomi_health_sync.auto_discovery import DiscoveryError
from xiaomi_health_sync.workout_sync import WORKOUT_HISTORY_PATH, WorkoutSyncRunner
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
        self.writes = []

    def _upsert_table(self, *, table, on_conflict, body, return_representation=False):
        self.writes.append((table, on_conflict, body))
        return []


def xr(payload):
    return XiaomiResponse(
        status_code=200,
        json_data=payload,
        text="",
        request_id="req",
    )


def workout(watermark, start_time, *, sport_type="boxing"):
    return {
        "key": sport_type,
        "category": "physical_training",
        "sid": "watch",
        "time": start_time,
        "watermark": watermark,
        "value": (
            f'{{"sport_type":311,"start_time":{start_time},'
            f'"end_time":{start_time + 1800},"duration":1800,'
            '"total_cal":180,"avg_hrm":145,"max_hrm":171,"min_hrm":92}'
        ),
    }


def test_sync_window_paginates_and_upserts_workouts():
    first = xr({
        "code": 0,
        "result": {
            "sport_records": [workout("w1", 2_000_000_000)],
            "has_more": True,
            "next_key": "cursor-2",
        },
    })
    second = xr({
        "code": 0,
        "result": {
            "sport_records": [workout("w2", 2_000_003_600)],
            "has_more": False,
        },
    })
    client = FakeClient([first, second])
    store = FakeStore()

    result = WorkoutSyncRunner(client=client, store=store).sync_window(
        start_time=1_999_900_000,
        end_time=2_000_100_000,
    )

    assert result == {"records": 2, "pages": 2}
    assert client.calls[0] == (
        WORKOUT_HISTORY_PATH,
        {
            "start_time": 1_999_900_000,
            "end_time": 2_000_100_000,
            "limit": 50,
        },
    )
    assert client.calls[1][1]["next_key"] == "cursor-2"
    assert len(store.writes) == 2
    row = store.writes[0][2][0]
    assert row["source_record_id"] == "w1"
    assert row["activity_type"] == "boxing"
    assert row["calories_kcal"] == 180
    assert row["avg_hr"] == 145
    assert row["max_hr"] == 171


def test_sync_window_rejects_repeated_cursor():
    response = {
        "code": 0,
        "result": {
            "sport_records": [],
            "has_more": True,
            "next_key": "same",
        },
    }
    with pytest.raises(DiscoveryError, match="cursor loop"):
        WorkoutSyncRunner(
            client=FakeClient([xr(response), xr(response)]),
            store=FakeStore(),
        ).sync_window(start_time=100, end_time=200)
