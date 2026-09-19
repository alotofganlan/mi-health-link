from xiaomi_health_sync.special_discovery import (
    default_special_probes,
    run_special_probes,
)
from xiaomi_health_sync.xiaomi import XiaomiResponse


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def encrypted_post(self, path, payload):
        self.calls.append((path, dict(payload)))
        return self.responses.pop(0)


class FakeStore:
    def __init__(self):
        self.raw = []

    def save_raw(self, *, record_type, payload, **kwargs):
        self.raw.append((record_type, payload, kwargs))
        return kwargs.get("source_record_id") or str(len(self.raw))


def xr(payload, status=200):
    return XiaomiResponse(status_code=status, json_data=payload, text="", request_id=None)


def test_default_special_probes_separate_profile_and_workout():
    probes = default_special_probes(start_time=100, end_time=200)
    by_name = {probe.name: probe for probe in probes}

    assert by_name["profile"].path == "/healthapp/user/get_miot_user_profile"
    assert by_name["profile"].category == "profile"
    assert by_name["workout_history"].path == "/app/v1/data/get_sport_records_by_time"
    assert by_name["workout_history"].category == "workout"
    assert by_name["workout_history"].payload == {
        "start_time": 100,
        "end_time": 200,
        "limit": 50,
    }


def test_workout_probe_follows_next_key_saves_raw_pages_and_normalized_records():
    probes = [
        probe
        for probe in default_special_probes(start_time=100, end_time=200)
        if probe.category == "workout"
    ]
    page1 = xr({
        "code": 0,
        "result": {
            "sport_records": [{
                "key": "running",
                "time": 101,
                "watermark": 111,
                "value": "{\"start_time\":101,\"end_time\":111,\"duration\":10,\"avg_hrm\":140}",
            }],
            "has_more": True,
            "next_key": "cursor-2",
        },
    })
    page2 = xr({
        "code": 0,
        "result": {
            "sport_records": [{
                "key": "walking",
                "time": 102,
                "sid": "device-1",
                "value": "{\"start_time\":102,\"end_time\":122,\"duration\":20,\"steps\":30}",
            }],
            "has_more": False,
        },
    })
    client = FakeClient([page1, page2])
    store = FakeStore()

    results = run_special_probes(client=client, store=store, probes=probes)

    assert client.calls == [
        (
            "/app/v1/data/get_sport_records_by_time",
            {"start_time": 100, "end_time": 200, "limit": 50},
        ),
        (
            "/app/v1/data/get_sport_records_by_time",
            {
                "start_time": 100,
                "end_time": 200,
                "limit": 50,
                "next_key": "cursor-2",
            },
        ),
    ]
    assert len(results) == 2

    raw_pages = [row for row in store.raw if row[0] == "xiaomi:special:workout:workout_history"]
    normalized = [row for row in store.raw if row[0] == "workout_record"]
    assert [payload["page"] for _, payload, _ in raw_pages] == [1, 2]
    assert [kwargs["source_record_id"] for _, _, kwargs in normalized] == ["111", "device-1:walking:102"]
    assert normalized[0][1]["avg_heart_rate"] == 140
    assert normalized[1][1]["steps"] == 30
