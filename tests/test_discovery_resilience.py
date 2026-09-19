from datetime import datetime, timezone

import httpx

from mi_health_link.auto_discovery import AutoDiscoveryRunner
from mi_health_link.xiaomi import XiaomiResponse


class FakeClient:
    def __init__(self):
        self.calls = []

    def encrypted_post(self, path, payload):
        self.calls.append((path, payload))
        key = payload.get("params", [{}])[0].get("key") if "params" in payload else payload.get("key")
        if path.endswith("get_latest_fitness_data") and key == "abnormal_heart_beat":
            raise httpx.ReadTimeout("slow Xiaomi response")
        return XiaomiResponse(
            status_code=200,
            json_data={"code": 0, "result": {"data_list": [{"key": key}], "has_more": False}},
            text="",
            request_id="req",
        )


class FakeStore:
    def __init__(self):
        self.known = {"abnormal_heart_beat", "steps"}
        self.backfilled = {"abnormal_heart_beat", "steps"}
        self.raw = []

    def load_discovered_keys(self):
        return set(self.known)

    def load_observed_keys(self):
        return set(self.known)

    def remember_discovered_key(self, key):
        self.known.add(key)

    def load_backfilled_keys(self):
        return set(self.backfilled)

    def remember_backfilled_key(self, key):
        self.backfilled.add(key)

    def load_backfill_progress(self, key):
        return None

    def remember_backfill_progress(self, key, next_start_time):
        pass

    def upsert_normalized_record(self, record):
        return False

    def save_raw(self, *, record_type, payload, **kwargs):
        self.raw.append((record_type, payload))
        return "rid"


def test_latest_network_failure_is_reported_and_next_key_continues():
    messages = []
    client = FakeClient()
    result = AutoDiscoveryRunner(
        client=client,
        store=FakeStore(),
        now=lambda: datetime(2026, 8, 21, tzinfo=timezone.utc),
        progress=messages.append,
    ).run(backfill_start_time=0)

    assert result.discovered_keys == ["abnormal_heart_beat", "steps"]
    assert any("failed abnormal_heart_beat" in message for message in messages)
    assert any("requesting steps" in message for message in messages)
