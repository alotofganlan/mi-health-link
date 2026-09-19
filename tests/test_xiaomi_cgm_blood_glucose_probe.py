from __future__ import annotations

import json

import pytest

import mi_health_link.blood_glucose as blood_glucose
from mi_health_link.xiaomi import XiaomiResponse


def test_continuous_payload_batches_multiple_points_without_measurement_period():
    builder = getattr(blood_glucose, "build_continuous_blood_glucose_upload", None)
    assert callable(builder)

    request = builder(
        sid="wearable-test-sid",
        phone_id="phone-test-id",
        samples=[
            (1_787_406_000, 5.1),
            (1_787_406_300, 5.4),
        ],
    )

    assert request.path == "/app/v1/data/up_fitness_data"
    assert request.payload["phone_id"] == "phone-test-id"
    assert len(request.payload["data_list"]) == 2

    for item, expected_time, expected_value in zip(
        request.payload["data_list"],
        (1_787_406_000, 1_787_406_300),
        (5.1, 5.4),
        strict=True,
    ):
        assert item["sid"] == "wearable-test-sid"
        assert item["key"] == "blood_sugar"
        assert item["time"] == expected_time
        assert item["zone_offset"] == 28800
        assert item["zone_name"] == "Asia/Shanghai"
        assert json.loads(item["value"]) == {
            "time": expected_time,
            "blood_sugar": expected_value,
        }
        assert "measurement_period" not in json.loads(item["value"])


class FakeReadClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        self.calls.append((path, payload))
        return XiaomiResponse(
            200,
            {"code": 0, "result": {"data_list": [], "has_more": False}},
            "",
        )


def test_continuous_reader_queries_blood_sugar_key():
    reader = getattr(blood_glucose, "read_continuous_blood_glucose", None)
    assert callable(reader)
    client = FakeReadClient()

    assert reader(client=client, start_time=1_787_406_000, end_time=1_787_406_600) == []

    assert client.calls == [
        (
            "/app/v1/data/get_fitness_data_by_time",
            {
                "key": "blood_sugar",
                "start_time": 1_787_406_000,
                "end_time": 1_787_406_600,
            },
        )
    ]


def _continuous_row(timestamp: int, value: float) -> dict:
    return {
        "sid": "wearable-test-sid",
        "key": "blood_sugar",
        "time": timestamp,
        "zone_offset": 28800,
        "zone_name": "Asia/Shanghai",
        "value": json.dumps(
            {"time": timestamp, "blood_sugar": value},
            separators=(",", ":"),
        ),
    }


class FakeBatchWriteClient:
    def __init__(self, history_pages: list[list[dict]]):
        self.history_pages = list(history_pages)
        self.calls: list[tuple[str, dict]] = []

    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        self.calls.append((path, payload))
        if path == "/app/v1/data/up_fitness_data":
            return XiaomiResponse(200, {"code": 0}, "")
        rows = self.history_pages.pop(0) if self.history_pages else []
        return XiaomiResponse(
            200,
            {"code": 0, "result": {"data_list": rows, "has_more": False}},
            "",
        )


def test_continuous_batch_writer_rejects_partial_readback():
    writer = getattr(blood_glucose, "write_continuous_blood_glucose_batch", None)
    assert callable(writer)
    client = FakeBatchWriteClient(
        [
            [],
            [_continuous_row(1_787_406_000, 5.1)],
        ]
    )

    with pytest.raises(blood_glucose.XiaomiBloodGlucoseVerificationError):
        writer(
            client=client,
            sid="wearable-test-sid",
            phone_id="phone-test-id",
            samples=[
                (1_787_406_000, 5.1),
                (1_787_406_300, 5.4),
            ],
            verification_attempts=1,
        )

    upload_calls = [
        payload
        for path, payload in client.calls
        if path == "/app/v1/data/up_fitness_data"
    ]
    assert len(upload_calls) == 1
    assert len(upload_calls[0]["data_list"]) == 2


def test_continuous_batch_writer_succeeds_only_after_both_points_read_back():
    writer = getattr(blood_glucose, "write_continuous_blood_glucose_batch", None)
    assert callable(writer)
    both = [
        _continuous_row(1_787_406_000, 5.1),
        _continuous_row(1_787_406_300, 5.4),
    ]
    client = FakeBatchWriteClient([[], both])

    result = writer(
        client=client,
        sid="wearable-test-sid",
        phone_id="phone-test-id",
        samples=[
            (1_787_406_000, 5.1),
            (1_787_406_300, 5.4),
        ],
        verification_attempts=1,
    )

    assert result.uploaded_count == 2
    assert result.verified_count == 2
    assert result.already_present_count == 0
    assert result.timestamps == (1_787_406_000, 1_787_406_300)
    assert sum(path == "/app/v1/data/up_fitness_data" for path, _ in client.calls) == 1


def test_continuous_batch_writer_retry_is_idempotent_when_both_points_exist():
    writer = getattr(blood_glucose, "write_continuous_blood_glucose_batch", None)
    assert callable(writer)
    both = [
        _continuous_row(1_787_406_000, 5.1),
        _continuous_row(1_787_406_300, 5.4),
    ]
    client = FakeBatchWriteClient([both, both])

    result = writer(
        client=client,
        sid="wearable-test-sid",
        phone_id="phone-test-id",
        samples=[
            (1_787_406_000, 5.1),
            (1_787_406_300, 5.4),
        ],
        verification_attempts=1,
    )

    assert result.uploaded_count == 0
    assert result.verified_count == 2
    assert result.already_present_count == 2
    assert result.timestamps == (1_787_406_000, 1_787_406_300)
    assert all(path != "/app/v1/data/up_fitness_data" for path, _ in client.calls)
