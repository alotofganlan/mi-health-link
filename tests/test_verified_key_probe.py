from mi_health_link.verified_key_probe import (
    WATCH_S4_41MM_VERIFIED_PROBE_KEYS,
    probe_verified_persist_keys,
)
from mi_health_link.xiaomi import XiaomiResponse


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def encrypted_post(self, path, payload):
        self.calls.append((path, dict(payload)))
        return self.responses.pop(0)


class FakeStore:
    def __init__(self, known=()):
        self.known = set(known)
        self.raw = []
        self.discovered = []

    def load_discovered_keys(self):
        return set(self.known)

    def remember_discovered_key(self, key):
        self.known.add(key)
        self.discovered.append(key)

    def save_raw(self, *, record_type, payload, **kwargs):
        self.raw.append((record_type, payload))
        return str(len(self.raw))


def xr(payload, status=200):
    return XiaomiResponse(status_code=status, json_data=payload, text="", request_id="req")


def test_s4_verified_probe_keys_cover_recovery_and_activity_metrics():
    expected = {
        "temperature_trend",
        "menstruation",
        "menstrual_symptoms",
        "energy",
        "pai",
        "vitality",
        "vo2_max",
        "training_load",
        "running_ability_index",
        "grade_prediction",
        "physical_fitness_status",
    }
    assert expected.issubset(set(WATCH_S4_41MM_VERIFIED_PROBE_KEYS))


def test_probe_registers_only_keys_xiaomi_actually_returns():
    client = FakeClient([
        xr({"code": 0, "result": {"data_list": [{"key": "menstruation", "value": "{}"}]}}),
        xr({"code": 0, "result": {"data_list": []}}),
    ])
    store = FakeStore()

    result = probe_verified_persist_keys(
        client=client,
        store=store,
        keys=("menstruation", "menstrual_symptoms"),
        latest_limit=30,
    )

    assert result.confirmed == ["menstruation"]
    assert result.unconfirmed == ["menstrual_symptoms"]
    assert store.discovered == ["menstruation"]
    assert [record_type for record_type, _ in store.raw] == [
        "xiaomi:verified_key_probe:menstruation",
        "xiaomi:verified_key_probe:menstrual_symptoms",
    ]


def test_probe_skips_already_discovered_keys():
    client = FakeClient([])
    store = FakeStore(known={"temperature_trend"})

    result = probe_verified_persist_keys(
        client=client,
        store=store,
        keys=("temperature_trend",),
    )

    assert result.confirmed == []
    assert result.unconfirmed == []
    assert client.calls == []
