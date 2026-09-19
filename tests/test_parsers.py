import json

from mi_health_link.parsers import parse_sleep_entry


def test_parse_sleep_entry_xiaomi_shape():
    entry = {
        "sid": "hlth.example",
        "key": "sleep",
        "time": 1787007752,
        "value": json.dumps({
            "bedtime": 1786982983,
            "sleep_deep_duration": 76,
            "sleep_light_duration": 337,
            "duration": 413,
            "items": [
                {"end_time": 1786988383, "state": 3, "start_time": 1786982983},
                {"end_time": 1786989163, "state": 2, "start_time": 1786988383},
            ],
            "timezone": 32,
            "sleep_awake_duration": 0,
            "wake_up_time": 1787007752,
        }),
        "zone_offset": 28800,
        "zone_name": "Asia/Shanghai",
    }
    parsed = parse_sleep_entry(entry)
    assert parsed.total_min == 413
    assert parsed.deep_min == 76
    assert parsed.light_min == 337
    assert parsed.rem_min == 0
    assert parsed.awake_min == 0
    assert parsed.zone_offset_seconds == 28800
    assert parsed.stages[0]["stage"] == "light"
    assert parsed.stages[1]["stage"] == "deep"
