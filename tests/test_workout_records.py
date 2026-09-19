from xiaomi_health_sync.workout_records import extract_workout_records, workout_table_rows


def test_extract_workout_records_parses_real_xiaomi_value_json():
    payload = {
        "code": 0,
        "result": {
            "sport_records": [
                {
                    "key": "outdoor_running",
                    "sid": "huami.device/abc",
                    "time": 1683114349,
                    "value": "{\"start_time\":1683114349,\"end_time\":1683114713,\"duration\":363,\"calories\":38,\"avg_hrm\":138,\"max_hrm\":155,\"min_hrm\":93,\"distance\":900,\"steps\":823,\"avg_pace\":402}",
                    "watermark": 58969186893825,
                    "category": "running",
                    "zone_offset": 28800,
                }
            ]
        },
    }

    records = extract_workout_records(payload)

    assert len(records) == 1
    record = records[0]
    assert record["sport_type"] == "outdoor_running"
    assert record["category"] == "running"
    assert record["start_time"] == 1683114349
    assert record["end_time"] == 1683114713
    assert record["duration"] == 363
    assert record["distance"] == 900
    assert record["calories"] == 38
    assert record["activity_calories"] == 38
    assert record["total_calories"] is None
    assert record["steps"] == 823
    assert record["avg_heart_rate"] == 138
    assert record["max_heart_rate"] == 155
    assert record["min_heart_rate"] == 93
    assert record["avg_pace"] == 402
    assert record["source_record_id"] == "58969186893825"
    assert record["raw_record"]["sid"] == "huami.device/abc"
    assert record["raw_value"]["distance"] == 900


def test_total_cal_is_preferred_for_workout_total_energy():
    payload = {
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
                    '"max_hrm":170,"min_hrm":95,"train_load":22,'
                    '"recover_time":14,"train_effect":2.5,'
                    '"anaerobic_train_effect":0.5,"training_experience":5}'
                ),
            }]
        },
    }

    record = extract_workout_records(payload)[0]
    assert record["calories"] == 119
    assert record["total_calories"] == 119
    assert record["activity_calories"] == 102
    assert record["training_load"] == 22
    assert record["recover_time"] == 14
    assert record["training_experience"] == 5

    row = workout_table_rows([record])[0]
    assert row["activity_type"] == "physical_training"
    assert row["calories_kcal"] == 119
    assert row["training_load"] == 22
    assert row["recovery_min"] is None
    assert row["raw"]["activity_calories_kcal"] == 102
    assert row["raw"]["recover_time_code"] == 14
    assert row["raw"]["training_experience"] == 5
    assert "raw_record" not in row["raw"]


def test_extract_workout_records_falls_back_when_value_is_missing_or_invalid():
    payload = {
        "code": 0,
        "result": {
            "sport_records": [
                {"key": "walking", "time": 123, "sid": "s1"},
                {"key": "yoga", "time": 456, "sid": "s2", "value": "not-json"},
            ]
        },
    }

    records = extract_workout_records(payload)

    assert records[0]["sport_type"] == "walking"
    assert records[0]["start_time"] == 123
    assert records[0]["source_record_id"] == "s1:walking:123"
    assert records[0]["raw_value"] == {}
    assert records[1]["sport_type"] == "yoga"
    assert records[1]["start_time"] == 456
    assert records[1]["source_record_id"] == "s2:yoga:456"
    assert records[1]["raw_value"] == {}
