import json

import httpx

from mi_health_link.s4_normalize import (
    DIRECT_NORMALIZED_KEYS,
    S4_STRUCTURED_KEYS,
    extract_history_items,
    normalize_saved_s4_history,
    prefer_watch_sleep_items,
    progress_messages_for_key,
    sleep_source_kind,
    source_record_types_for_key,
)
from mi_health_link.supabase_store import SupabaseStore


def test_s4_structured_keys_include_sleep_weight_and_new_verified_metrics():
    expected = {
        "sleep",
        "weight",
        "energy",
        "pai",
        "vitality",
        "vo2_max",
        "training_load",
        "running_ability_index",
        "grade_prediction",
        "physical_fitness_status",
    }
    assert expected.issubset(set(S4_STRUCTURED_KEYS))


def test_direct_normalized_keys_have_verified_standard_targets():
    assert set(DIRECT_NORMALIZED_KEYS) == {
        "heart_rate",
        "spo2",
        "intensity",
        "weight",
        "sleep",
        "menstruation",
    }


def test_normalizer_reads_history_and_verified_latest_for_each_key():
    assert source_record_types_for_key("pai") == (
        "xiaomi:history:pai",
        "xiaomi:verified_key_probe:pai",
    )


def test_progress_messages_show_position_and_counts():
    start, done = progress_messages_for_key(index=3, total=15, key="sleep", count=80)
    assert start == "[normalize 3/15] sleep: starting"
    assert done == "[normalize 3/15] sleep: 80 records"


def test_extract_history_items_reads_saved_history_response():
    raw_payload = {
        "response": {
            "code": 0,
            "result": {
                "data_list": [
                    {"key": "single_temperature", "time": 1, "value": "{}"},
                    {"key": "single_temperature", "time": 2, "value": "{}"},
                ]
            },
        }
    }

    assert [item["time"] for item in extract_history_items(raw_payload)] == [1, 2]


def test_extract_history_items_returns_empty_for_error_or_bad_shape():
    assert extract_history_items({"response": {"code": -1}}) == []
    assert extract_history_items({"response": {"code": 0, "result": {}}}) == []


def _sleep_item(*, sid: str, wake_time: int, zone_offset: int = 28800, state: int = 3) -> dict:
    return {
        "key": "sleep",
        "sid": sid,
        "time": wake_time,
        "zone_offset": zone_offset,
        "value": {
            "bedtime": wake_time - 3600,
            "wake_up_time": wake_time,
            "items": [{"start_time": wake_time - 3600, "end_time": wake_time, "state": state}],
        },
    }


def test_watch_sleep_replaces_phone_for_entire_local_sleep_day_even_if_phone_is_longer():
    phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787266800, state=6)
    watch = _sleep_item(sid="1234567890", wake_time=1787263200, state=3)

    selected = prefer_watch_sleep_items([phone, watch])

    assert selected == [watch]


def test_phone_sleep_is_kept_when_no_watch_sleep_exists_on_that_local_day():
    phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787266800, state=6)

    assert prefer_watch_sleep_items([phone]) == [phone]


def test_all_watch_sleep_records_are_kept_when_main_sleep_and_watch_nap_share_a_day():
    watch_main = _sleep_item(sid="1234567890", wake_time=1787263200, state=3)
    watch_nap = _sleep_item(sid="1234567890", wake_time=1787295600, state=3)
    phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787266800, state=6)

    selected = prefer_watch_sleep_items([phone, watch_main, watch_nap])

    assert selected == [watch_main, watch_nap]


def test_sleep_source_priority_is_scoped_per_local_day():
    day_one_phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787266800, state=6)
    day_one_watch = _sleep_item(sid="1234567890", wake_time=1787263200, state=3)
    day_two_phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787353200, state=6)

    selected = prefer_watch_sleep_items([day_one_phone, day_one_watch, day_two_phone])

    assert selected == [day_one_watch, day_two_phone]


def test_sleep_source_kind_labels_phone_generated_and_watch_records():
    phone = _sleep_item(sid="hlth.gen_phone", wake_time=1787266800, state=6)
    watch = _sleep_item(sid="1234567890", wake_time=1787263200, state=3)

    assert sleep_source_kind(phone) == "phone"
    assert sleep_source_kind(watch) == "watch"


def test_legacy_migration_upserts_standard_table_and_never_deletes_raw_records():
    requests = []
    heart_item = {
        "key": "heart_rate",
        "sid": "watch",
        "time": 1784738040,
        "value": '{"time":1784738040,"bpm":72,"type":0}',
    }

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "GET" and request.url.path == "/rest/v1/raw_records":
            record_type = request.url.params.get("record_type")
            if record_type == "eq.xiaomi:history:heart_rate":
                return httpx.Response(200, json=[{
                    "payload": {
                        "response": {
                            "code": 0,
                            "result": {"data_list": [heart_item]},
                        }
                    }
                }])
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/rest/v1/heart_rate_samples":
            return httpx.Response(201, json=[])
        return httpx.Response(200, json=[])

    store = SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_test",
        httpx.MockTransport(handler),
    )
    counts = normalize_saved_s4_history(store=store)

    assert counts["heart_rate"] == 1
    assert any(request.url.path == "/rest/v1/heart_rate_samples" for request in requests)
    assert not any(
        request.method == "DELETE" and request.url.path == "/rest/v1/raw_records"
        for request in requests
    )


def test_legacy_workout_history_is_migrated_with_total_calories():
    requests = []
    workout_payload = {
        "response": {
            "code": 0,
            "result": {
                "sport_records": [{
                    "key": "physical_training",
                    "sid": "watch",
                    "time": 1787299396,
                    "category": "physical_training",
                    "watermark": 168243103793200,
                    "value": (
                        '{"sport_type":311,"start_time":1787299396,'
                        '"end_time":1787300285,"duration":888,'
                        '"calories":102,"total_cal":119,"avg_hrm":145,'
                        '"max_hrm":170,"min_hrm":95,"train_load":22}'
                    ),
                }]
            },
        }
    }

    def handler(request: httpx.Request):
        requests.append(request)
        if request.method == "GET" and request.url.path == "/rest/v1/raw_records":
            if request.url.params.get("record_type") == "eq.xiaomi:special:workout:workout_history":
                return httpx.Response(200, json=[{"payload": workout_payload}])
            return httpx.Response(200, json=[])
        if request.method == "POST" and request.url.path == "/rest/v1/workouts":
            return httpx.Response(201, json=[])
        return httpx.Response(200, json=[])

    store = SupabaseStore(
        "https://example.supabase.co",
        "sb_secret_test",
        httpx.MockTransport(handler),
    )
    counts = normalize_saved_s4_history(store=store)

    assert counts["workouts"] == 1
    workout_request = next(
        request for request in requests if request.url.path == "/rest/v1/workouts"
    )
    body = json.loads(workout_request.content)
    assert body[0]["calories_kcal"] == 119
    assert body[0]["raw"]["activity_calories_kcal"] == 102
