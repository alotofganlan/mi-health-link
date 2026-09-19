from __future__ import annotations

import json

from xiaomi_health_sync.intensity_write import (
    XiaomiIntensityVerificationError,
    build_intensity_upload,
    qualifying_intensity_minutes,
    repair_intensity_from_heart_rate,
    write_intensity_minutes,
)
from xiaomi_health_sync.xiaomi import XiaomiResponse


def test_qualifying_minutes_use_64_percent_threshold_and_exclude_existing():
    samples = [
        (1789548005, 126),
        (1789548060, 127),
        (1789548090, 152),
        (1789548120, 140),
        (1789548180, 160),
    ]

    assert qualifying_intensity_minutes(
        samples,
        start_time=1789548000,
        end_time=1789548190,
        maximum_heart_rate=198,
        existing_minutes={1789548060},
    ) == (1789548120, 1789548180)


def test_upload_payload_contains_one_xiaomi_intensity_item_per_unique_minute():
    request = build_intensity_upload(
        timestamps=[1789548189, 1789548120, 1789548180],
        sid="xiaomiwear_app_manually",
        phone_id="xiaomiwear_app_manually",
        zone_offset_seconds=28800,
        zone_name="Asia/Shanghai",
    )

    assert request.path == "/app/v1/data/up_fitness_data"
    assert request.payload["phone_id"] == "xiaomiwear_app_manually"
    assert request.payload["data_list"] == [
        {
            "sid": "xiaomiwear_app_manually",
            "key": "intensity",
            "time": 1789548120,
            "value": json.dumps(
                {"time": 1789548120},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "zone_offset": 28800,
            "zone_name": "Asia/Shanghai",
        },
        {
            "sid": "xiaomiwear_app_manually",
            "key": "intensity",
            "time": 1789548180,
            "value": json.dumps(
                {"time": 1789548180},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "zone_offset": 28800,
            "zone_name": "Asia/Shanghai",
        },
    ]



class FakeIntensityClient:
    def __init__(self, *, persist_upload: bool = True) -> None:
        self.rows = [
            {
                "sid": "watch",
                "key": "intensity",
                "time": 1789548060,
                "value": "{\"time\":1789548060}",
            }
        ]
        self.persist_upload = persist_upload
        self.upload_payloads: list[dict[str, object]] = []

    def encrypted_post(self, path: str, payload: dict[str, object]) -> XiaomiResponse:
        if path.endswith("get_fitness_data_by_time"):
            return XiaomiResponse(
                status_code=200,
                json_data={
                    "code": 0,
                    "result": {"data_list": list(self.rows), "has_more": False},
                },
                text="",
            )
        self.upload_payloads.append(payload)
        if self.persist_upload:
            self.rows.extend(payload["data_list"])
        return XiaomiResponse(
            status_code=200,
            json_data={"code": 0},
            text="",
        )


class FakeRepairClient:
    def __init__(self) -> None:
        self.rows = {
            "heart_rate": [
                {"time": 1789548180, "value": "{\"bpm\":128}"},
                {"time": 1789548240, "value": "{\"bpm\":126}"},
                {"time": 1789548300, "value": "{\"bpm\":151}"},
            ],
            "intensity": [
                {"time": 1789548180, "sid": "watch", "value": "{\"time\":1789548180}"},
            ],
        }
        self.upload_payloads: list[dict[str, object]] = []

    def encrypted_post(self, path: str, payload: dict[str, object]) -> XiaomiResponse:
        if path.endswith("get_fitness_data_by_time"):
            key = str(payload["key"])
            return XiaomiResponse(
                status_code=200,
                json_data={
                    "code": 0,
                    "result": {"data_list": list(self.rows[key]), "has_more": False},
                },
                text="",
            )
        self.upload_payloads.append(payload)
        self.rows["intensity"].extend(payload["data_list"])
        return XiaomiResponse(status_code=200, json_data={"code": 0}, text="")


def test_write_uploads_only_missing_minutes_and_verifies_readback():
    client = FakeIntensityClient()

    result = write_intensity_minutes(
        client=client,
        timestamps=[1789548060, 1789548120],
        start_time=1789548000,
        end_time=1789548190,
    )

    assert result.uploaded_count == 1
    assert result.verified_count == 1
    assert result.already_present_count == 1
    assert result.timestamps == (1789548060, 1789548120)
    assert [
        item["time"]
        for item in client.upload_payloads[0]["data_list"]
    ] == [1789548120]


def test_write_raises_when_uploaded_minute_cannot_be_read_back():
    client = FakeIntensityClient(persist_upload=False)

    try:
        write_intensity_minutes(
            client=client,
            timestamps=[1789548120],
            start_time=1789548000,
            end_time=1789548190,
        )
    except XiaomiIntensityVerificationError as exc:
        assert "1789548120" in str(exc)
    else:
        raise AssertionError("expected read-back verification failure")


def test_repair_preview_reports_missing_minutes_without_writing():
    client = FakeRepairClient()

    result = repair_intensity_from_heart_rate(
        client=client,
        start_time=1789548180,
        end_time=1789548359,
        read_start_time=1789516800,
        read_end_time=1789603199,
        maximum_heart_rate=198,
        confirm_write=False,
    )

    assert result["status"] == "preview"
    assert result["threshold_bpm"] == 126.72
    assert result["qualifying_count"] == 2
    assert result["already_present_count"] == 1
    assert result["missing_timestamps"] == [1789548300]
    assert client.upload_payloads == []


def test_repair_requires_confirmation_then_writes_and_verifies_missing_minutes():
    client = FakeRepairClient()

    result = repair_intensity_from_heart_rate(
        client=client,
        start_time=1789548180,
        end_time=1789548359,
        read_start_time=1789516800,
        read_end_time=1789603199,
        maximum_heart_rate=198,
        confirm_write=True,
    )

    assert result["status"] == "completed"
    assert result["uploaded_count"] == 1
    assert result["verified_count"] == 1
    assert result["missing_timestamps"] == [1789548300]
