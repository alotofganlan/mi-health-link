from xiaomi_health_sync.health_records import normalize_data_item


def test_normalize_data_item_parses_value_json_and_preserves_raw():
    item = {
        "sid": "watch",
        "key": "single_temperature",
        "time": 100,
        "update_time": 101,
        "watermark": "wm-1",
        "value": '{"temperature":36.2,"type":1}',
    }

    record = normalize_data_item(item)

    assert record["key"] == "single_temperature"
    assert record["time"] == 100
    assert record["sid"] == "watch"
    assert record["watermark"] == "wm-1"
    assert record["value"] == {"temperature": 36.2, "type": 1}
    assert record["raw_item"] == item


def test_normalize_data_item_keeps_non_json_value_losslessly():
    item = {"key": "menstrual_symptoms", "time": 200, "value": "opaque"}

    record = normalize_data_item(item)

    assert record["value"] == "opaque"
    assert record["raw_item"] == item


def test_normalize_data_item_rejects_non_dict():
    assert normalize_data_item("bad") is None
