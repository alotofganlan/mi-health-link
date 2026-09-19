from xiaomi_health_sync.metric_mapping import map_health_value


def test_weight_mapping_verified_healthy_body_shape():
    mapped = map_health_value("weight", {"weight": 55.8, "bmi": 20.2, "somatotype": 0, "whr": 1.1, "body_age": 19, "standard_weight_v2": 57.6})
    assert mapped["weight_kg"] == 55.8
    assert mapped["bmi"] == 20.2
    assert mapped["body_shape_code"] == 0
    assert mapped["body_shape"] == "healthy"
    assert mapped["waist_to_hip_ratio"] == 1.1
    assert mapped["body_age"] == 19
    assert mapped["standard_weight_v2_kg"] == 57.6


def test_weight_mapping_keeps_unknown_body_shape_code():
    mapped = map_health_value("weight", {"somatotype": 3, "future_metric": 123})
    assert mapped["body_shape_code"] == 3
    assert mapped["body_shape"] is None
    assert mapped["unmapped"] == {"future_metric": 123}


def test_sleep_mapping_normalizes_advanced_sleep_fields_and_s4_segments():
    value = {"duration": 28000, "sleep_deep_duration": 5000, "sleep_light_duration": 15000, "sleep_rem_duration": 6000, "sleep_awake_duration": 2000, "awake_count": 4, "avg_hr": 59, "min_hr": 47, "max_hr": 83, "avg_spo2": 97, "min_spo2": 92, "max_spo2": 99, "avg_breath": 14.2, "segment_details": [{"start_time": 1000, "end_time": 1600, "state": 2}, {"start_time": 1600, "end_time": 2200, "state": 3}, {"start_time": 2200, "end_time": 2500, "state": 4}, {"start_time": 2500, "end_time": 2600, "state": 5}, {"start_time": 2600, "end_time": 2700, "state": 6}], "bedtime": 1000, "wake_up_time": 29000, "device_bedtime": 950, "device_wake_up_time": 29100, "sleep_trace_duration": 28100, "is_uncomplete": False, "timezone": "Asia/Shanghai", "sleep_algorithm_version": "v3", "version": 7, "protoTime": 1700000000}
    mapped = map_health_value("sleep", value)
    assert mapped["avg_sleep_heart_rate_bpm"] == 59
    assert mapped["min_sleep_heart_rate_bpm"] == 47
    assert mapped["max_sleep_heart_rate_bpm"] == 83
    assert mapped["avg_sleep_spo2_percent"] == 97
    assert mapped["min_sleep_spo2_percent"] == 92
    assert mapped["max_sleep_spo2_percent"] == 99
    assert mapped["breathing_rate_per_min"] == 14.2
    assert [x["state"] for x in mapped["segments"]] == ["deep", "light", "rem", "awake", None]
    assert mapped["unmapped"] == {}


def test_menstruation_mapping_adds_boundary_semantics_and_keeps_status_code():
    assert map_health_value("menstruation", {"date_time": 1, "status": 1, "update_time": 2})["boundary_type"] == "period_start"
    assert map_health_value("menstruation", {"date_time": 1, "status": 2, "update_time": 2})["boundary_type"] == "period_end"


def test_temperature_trend_maps_observed_xiaomi_fields_without_losing_unknowns():
    mapped = map_health_value(
        "temperature_trend",
        {
            "time": 1234567890,
            "status": 0,
            "base_line": 35.7,
            "base_temp": 35.5,
            "diff_temp": -0.1,
            "future_field": 7,
        },
    )
    assert mapped["baseline_c"] == 35.7
    assert mapped["base_temperature_c"] == 35.5
    assert mapped["temperature_delta_c"] == -0.1
    assert mapped["status_code"] == 0
    assert mapped["measurement_time"] == 1234567890
    assert mapped["unmapped"] == {"future_field": 7}


def test_single_temperature_maps_observed_skin_temperature_field():
    assert map_health_value("single_temperature", {"time": 1234567891, "skin_temperature": 36.75}) == {
        "measurement_time": 1234567891,
        "skin_temperature_c": 36.75,
        "unmapped": {},
    }


def test_temperature_characteristic_keeps_current_time_only_payload_uninterpreted():
    assert map_health_value("temperature_characteristic", {"time": 1234567890}) == {
        "measurement_time": 1234567890,
        "unmapped": {},
    }
