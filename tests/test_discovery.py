from xiaomi_health_sync.discovery import (
    summarize_discovery_response,
    summary_payload,
)


def test_discovery_detects_returned_records_and_keys():
    response = {
        "code": 0,
        "message": "ok",
        "result": {
            "data_list": [
                {"key": "sleep", "time": 100, "update_time": 101},
                {"key": "sleep", "time": 200, "update_time": 201},
            ]
        },
    }
    item = summarize_discovery_response(
        "sleep",
        http_status=200,
        response_json=response,
    )
    assert item.has_data is True
    assert item.record_count == 2
    assert item.returned_keys == ["sleep"]
    assert item.newest_time == 201


def test_discovery_handles_empty_success():
    response = {"code": 0, "message": "ok", "result": {"data_list": []}}
    item = summarize_discovery_response(
        "hrv",
        http_status=200,
        response_json=response,
    )
    assert item.has_data is False
    assert item.record_count == 0
    assert item.api_code == 0


def test_discovery_summary_separates_data_empty_and_errors():
    with_data = summarize_discovery_response(
        "sleep",
        http_status=200,
        response_json={
            "code": 0,
            "result": {"data_list": [{"key": "sleep", "time": 1}]},
        },
    )
    empty = summarize_discovery_response(
        "hrv",
        http_status=200,
        response_json={"code": 0, "result": {"data_list": []}},
    )
    error = summarize_discovery_response(
        "unknown",
        http_status=200,
        response_json={"code": -8, "message": "invalid params", "result": None},
    )

    summary = summary_payload([with_data, empty, error])
    assert summary["keys_with_data"] == ["sleep"]
    assert summary["keys_without_data"] == ["hrv"]
    assert summary["keys_with_errors"] == ["unknown"]
