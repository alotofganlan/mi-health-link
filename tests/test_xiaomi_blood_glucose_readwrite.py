from __future__ import annotations

import importlib.util
import json

import mi_health_link.blood_glucose as blood_glucose
from mi_health_link.sync_store import DirectSupabaseStore, VERIFIED_BOOTSTRAP_KEYS
from mi_health_link.xiaomi import XiaomiResponse


def test_only_observed_manual_glucose_key_is_polled_by_normal_sync():
    assert "single_blood_sugar" in VERIFIED_BOOTSTRAP_KEYS
    assert "blood_sugar" not in VERIFIED_BOOTSTRAP_KEYS


def test_single_blood_sugar_is_normalized_into_glucose_samples():
    store = DirectSupabaseStore(url="https://example.supabase.co", service_role_key="sb_secret_test")
    record = {
        "sid": "xiaomiwear_app_manually",
        "key": "single_blood_sugar",
        "time": 1755864000,
        "update_time": 1755864000,
        "watermark": "wm-glucose-1",
        "zone_offset": 28800,
        "zone_name": "Asia/Shanghai",
        "value": {"time": 1755864000, "blood_sugar": 6.3, "measurement_period": 2},
        "metrics": {},
    }
    write = store._write_for_record(record)
    assert write is not None
    table, on_conflict, body = write
    assert table == "glucose_samples"
    assert on_conflict == "source,source_record_id"
    assert body["source"] == "xiaomi"
    assert body["source_record_id"] == "wm-glucose-1"
    assert body["glucose_mmol_l"] == 6.3
    assert body["glucose_mg_dl"] == 113
    assert body["direction"] is None
    assert body["measured_at"] == "2025-08-22T12:00:00+00:00"


def test_xiaomi_blood_glucose_writer_module_exists():
    assert importlib.util.find_spec("mi_health_link.blood_glucose") is not None


def test_upload_payload_matches_real_xiaomi_manual_glucose_template():
    builder = getattr(blood_glucose, "build_manual_blood_glucose_upload", None)
    assert callable(builder)
    request = builder(value_mmol_l=5.6, timestamp=1_777_000_000)
    assert request.path == "/app/v1/data/up_fitness_data"
    assert request.payload["phone_id"] == "xiaomiwear_app_manually"
    item = request.payload["data_list"][0]
    assert item["sid"] == "xiaomiwear_app_manually"
    assert item["key"] == "single_blood_sugar"
    assert item["time"] == 1_777_000_000
    assert item["zone_offset"] == 28800
    assert item["zone_name"] == "Asia/Shanghai"
    assert json.loads(item["value"]) == {
        "time": 1_777_000_000,
        "blood_sugar": 5.6,
        "measurement_period": 2,
    }


class FakeXiaomiGlucoseClient:
    def __init__(self, history_pages: list[list[dict]], upload_code: int = 0):
        self.history_pages = list(history_pages)
        self.upload_code = upload_code
        self.calls: list[tuple[str, dict]] = []

    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        self.calls.append((path, payload))
        if path == "/app/v1/data/up_fitness_data":
            return XiaomiResponse(200, {"code": self.upload_code}, "")
        rows = self.history_pages.pop(0) if self.history_pages else []
        return XiaomiResponse(
            200,
            {"code": 0, "result": {"data_list": rows, "has_more": False}},
            "",
        )


def _cloud_glucose_row(timestamp: int, value: float) -> dict:
    return {
        "sid": "xiaomiwear_app_manually",
        "key": "single_blood_sugar",
        "time": timestamp,
        "zone_offset": 28800,
        "zone_name": "Asia/Shanghai",
        "value": json.dumps(
            {"time": timestamp, "blood_sugar": value, "measurement_period": 2},
            separators=(",", ":"),
        ),
    }


def test_write_skips_upload_when_same_cloud_record_already_exists():
    writer = getattr(blood_glucose, "write_manual_blood_glucose", None)
    assert callable(writer)
    client = FakeXiaomiGlucoseClient([[_cloud_glucose_row(1_777_000_000, 5.6)]])

    result = writer(
        client=client,
        value_mmol_l=5.6,
        timestamp=1_777_000_000,
        verification_attempts=1,
    )

    assert result.already_present is True
    assert result.uploaded is False
    assert result.verified is True
    assert [path for path, _ in client.calls] == ["/app/v1/data/get_fitness_data_by_time"]


def test_write_uploads_then_requires_read_back_match():
    writer = getattr(blood_glucose, "write_manual_blood_glucose", None)
    assert callable(writer)
    client = FakeXiaomiGlucoseClient([
        [],
        [_cloud_glucose_row(1_777_000_000, 5.6)],
    ])

    result = writer(
        client=client,
        value_mmol_l=5.6,
        timestamp=1_777_000_000,
        verification_attempts=1,
    )

    assert result.already_present is False
    assert result.uploaded is True
    assert result.verified is True
    assert [path for path, _ in client.calls] == [
        "/app/v1/data/get_fitness_data_by_time",
        "/app/v1/data/up_fitness_data",
        "/app/v1/data/get_fitness_data_by_time",
    ]
