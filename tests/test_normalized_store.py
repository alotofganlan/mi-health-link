import json

import httpx

from mi_health_link.health_records import normalize_data_item
from mi_health_link.supabase_store import SupabaseStore


def _store(handler):
    return SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_test",
        httpx.MockTransport(handler),
    )


def test_heart_rate_record_upserts_standard_sample_table():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "heart_rate",
        "sid": "watch",
        "time": 1784738040,
        "value": '{"time":1784738040,"bpm":72,"type":0}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    request = requests[0]
    assert request.url.path == "/rest/v1/heart_rate_samples"
    assert request.url.params["on_conflict"] == "source,measured_at"
    body = json.loads(request.content)
    assert body["source"] == "xiaomi"
    assert body["bpm"] == 72
    assert body["source_record_id"] == "watch:heart_rate:1784738040"
    assert body["measured_at"].startswith("2026-")


def test_multiple_heart_rate_records_use_one_batch_request():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    records = [
        normalize_data_item({
            "key": "heart_rate",
            "sid": "watch",
            "time": timestamp,
            "value": json.dumps({"time": timestamp, "bpm": bpm, "type": 0}),
        })
        for timestamp, bpm in ((1784738040, 72), (1784738100, 74))
    ]

    assert _store(handler).upsert_normalized_records(records) == 2
    assert len(requests) == 1
    assert requests[0].url.path == "/rest/v1/heart_rate_samples"
    body = json.loads(requests[0].content)
    assert isinstance(body, list)
    assert [row["bpm"] for row in body] == [72, 74]


def test_spo2_record_upserts_standard_sample_table():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "spo2",
        "sid": "watch",
        "time": 1784738040,
        "value": '{"time":1784738040,"spo2":98}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/rest/v1/spo2_samples"
    assert body["percent"] == 98


def test_intensity_record_is_idempotent_without_raw_record_fk():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "intensity",
        "sid": "watch",
        "time": 1784764620,
        "update_time": 1784949413,
        "zone_name": "Asia/Shanghai",
        "zone_offset": 28800,
        "value": '{"time":1784764620}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/rest/v1/intensity_samples"
    assert request.url.params["on_conflict"] == "source,source_record_id,measured_at"
    assert body["raw_record_id"] is None
    assert body["source_record_id"] == "watch"


def test_weight_record_maps_verified_columns():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "weight",
        "sid": "scale",
        "time": 1787046320,
        "value": json.dumps({
            "weight": 56.4,
            "bmi": 20.5,
            "body_fat_rate": 24.6,
            "muscle_mass": 40.0,
            "bone_mass": 2.5,
            "moisture_rate": 58.9,
            "basal_metabolism": 1288,
        }),
    })

    assert _store(handler).upsert_normalized_record(record) is True
    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/rest/v1/body_measurements"
    assert body["weight_kg"] == 56.4
    assert body["body_fat_pct"] == 24.6
    assert body["body_water_pct"] == 58.9
    assert body["basal_metabolism_kcal"] == 1288


def test_menstruation_keeps_status_code_in_normalized_raw_metadata():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "menstruation",
        "sid": "xiaomiwear_app_manually",
        "time": 1784851200,
        "value": '{"status":2,"date_time":1784851200,"update_time":1785107763}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/rest/v1/menstrual_records"
    assert body["record_type"] == "period_end"
    assert body["raw"]["status_code"] == 2
    assert "predicted" not in json.dumps(body).lower()
    assert "actual" not in json.dumps(body).lower()


def test_verified_metric_without_special_table_uses_structured_health_records():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "steps",
        "sid": "watch",
        "time": 1785367380,
        "update_time": 1785372264,
        "zone_name": "Asia/Shanghai",
        "zone_offset": 28800,
        "value": '{"time":1785367380,"steps":12,"distance":7,"calories":1}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    request = requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/rest/v1/health_records"
    assert request.url.params["on_conflict"] == "source,key,source_record_id"
    assert body["key"] == "steps"
    assert body["sid"] == "watch"
    assert body["value"]["steps"] == 12
    assert body["metrics"]["steps"] == 12
    assert "response" not in json.dumps(body)


def test_abnormal_heartbeat_is_stored_as_event_without_medical_inference():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(201, json=[])

    record = normalize_data_item({
        "key": "abnormal_heart_beat",
        "sid": "watch",
        "time": 1774203547,
        "value": '{"start_time":1774203547,"end_time":1774203761}',
    })

    assert _store(handler).upsert_normalized_record(record) is True
    body = json.loads(requests[0].content)
    assert requests[0].url.path == "/rest/v1/health_records"
    assert body["key"] == "abnormal_heart_beat"
    rendered = json.dumps(body).lower()
    assert "high" not in rendered
    assert "low" not in rendered
    assert "arrhythm" not in rendered


def test_unverified_key_is_skipped_without_database_write():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(500)

    record = normalize_data_item({
        "key": "temperature_characteristic",
        "time": 1784851200,
        "value": '{}',
    })

    assert _store(handler).upsert_normalized_record(record) is False
    assert requests == []
