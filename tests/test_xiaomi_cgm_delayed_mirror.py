from __future__ import annotations

from datetime import datetime, timezone

import httpx

from xiaomi_health_sync import cgm_mirror
from xiaomi_health_sync.xiaomi import XiaomiResponse


def _sample(timestamp_ms: int, value: float) -> dict:
    return {
        "source_record_id": f"ns-{timestamp_ms}",
        "measured_at": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).isoformat(),
        "glucose_mmol_l": value,
    }


def test_cgm_timestamp_is_normalized_to_minute():
    assert cgm_mirror.normalize_cgm_timestamp(1_787_465_645) == 1_787_465_640


class AcceptedButDelayedClient:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        self.calls.append((path, payload))
        if path == "/app/v1/data/up_fitness_data":
            return XiaomiResponse(200, {"code": 0}, "")
        return XiaomiResponse(
            200,
            {"code": 0, "result": {"data_list": [], "has_more": False}},
            "",
        )


def test_cgm_submit_treats_code_zero_as_accepted_without_immediate_readback():
    client = AcceptedButDelayedClient()

    result = cgm_mirror.submit_continuous_blood_glucose_batch(
        client=client,
        sid="watch-sid",
        phone_id="phone-id",
        samples=[
            (1_787_465_645, 5.0),
            (1_787_465_945, 6.1),
        ],
    )

    assert result.accepted_count == 2
    assert result.already_present_count == 0
    assert result.timestamps == (1_787_465_640, 1_787_465_940)
    upload = [payload for path, payload in client.calls if path == "/app/v1/data/up_fitness_data"]
    assert len(upload) == 1
    assert [item["time"] for item in upload[0]["data_list"]] == [1_787_465_640, 1_787_465_940]
    # One preflight read is allowed; no immediate post-upload read is required.
    assert sum(path == "/app/v1/data/get_fitness_data_by_time" for path, _ in client.calls) == 1


class FakeContinuousStore:
    def __init__(self, checkpoint_ms: int, rows: list[dict]):
        self.state = {"next_start_time": checkpoint_ms, "source_check_detail": {"pending": []}}
        self.rows = rows
        self.saved: list[dict] = []

    def load_cgm_mirror_state(self):
        return dict(self.state)

    def save_cgm_mirror_state(self, *, checkpoint_ms: int, detail: dict):
        self.state = {"next_start_time": checkpoint_ms, "source_check_detail": detail}
        self.saved.append(dict(self.state))

    def load_nightscout_glucose_samples_after(self, since_ms: int, *, limit: int):
        return list(self.rows)


class EmptyCloudClient:
    def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
        if path == "/app/v1/data/get_fitness_data_by_time":
            return XiaomiResponse(200, {"code": 0, "result": {"data_list": [], "has_more": False}}, "")
        return XiaomiResponse(200, {"code": 0}, "")


def test_cgm_mirror_advances_accepted_cursor_and_keeps_points_pending():
    first_ms = 1_787_465_645_771
    second_ms = 1_787_465_945_771
    store = FakeContinuousStore(
        first_ms - 300_000,
        [_sample(first_ms, 5.0), _sample(second_ms, 6.1)],
    )

    result = cgm_mirror.mirror_nightscout_to_xiaomi_cgm(
        store=store,
        client=EmptyCloudClient(),
        sid="watch-sid",
        phone_id="phone-id",
    )

    assert len(store.saved) == 1
    assert store.saved[0]["next_start_time"] == second_ms
    pending = store.saved[0]["source_check_detail"]["pending"]
    assert [item["xiaomi_ts"] for item in pending] == [1_787_465_640, 1_787_465_940]
    assert result["processed_count"] == 2
    assert result["accepted_count"] == 2
    assert result["verified_pending_count"] == 0
    assert result["pending_count"] == 2


def test_cgm_mirror_later_verifies_pending_without_resubmitting_it():
    first_ms = 1_787_465_645_771
    store = FakeContinuousStore(first_ms, [])
    store.state["source_check_detail"] = {
        "pending": [
            {"source_ms": first_ms, "xiaomi_ts": 1_787_465_640, "value": 5.0},
        ]
    }

    class VisibleCloudClient:
        def __init__(self):
            self.uploads = 0

        def encrypted_post(self, path: str, payload: dict) -> XiaomiResponse:
            if path == "/app/v1/data/up_fitness_data":
                self.uploads += 1
                return XiaomiResponse(200, {"code": 0}, "")
            row = {
                "time": 1_787_465_640,
                "value": '{"time":1787465640,"blood_sugar":5.0}',
            }
            return XiaomiResponse(200, {"code": 0, "result": {"data_list": [row], "has_more": False}}, "")

    client = VisibleCloudClient()
    result = cgm_mirror.mirror_nightscout_to_xiaomi_cgm(
        store=store,
        client=client,
        sid="watch-sid",
        phone_id="phone-id",
    )

    assert client.uploads == 0
    assert store.saved[-1]["source_check_detail"]["pending"] == []
    assert result["verified_pending_count"] == 1
    assert result["pending_count"] == 0


def test_real_state_writer_uses_allowed_success_status_even_with_pending_points():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PATCH":
            captured["body"] = request.content.decode()
            return httpx.Response(200, json=[{"key": "blood_sugar"}])
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    class RestStore:
        url = "https://project.supabase.co"
        transport = httpx.MockTransport(handler)

        def _headers(self):
            return {"apikey": "test"}

    cgm_mirror._save_state(
        RestStore(),
        checkpoint_ms=1_787_465_945_771,
        detail={"pending": [{"source_ms": 1, "xiaomi_ts": 60, "value": 5.0}]},
    )

    assert '"source_check_status":"success"' in captured["body"]
    assert '"source_check_status":"pending"' not in captured["body"]
