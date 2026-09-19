from xiaomi_health_sync.s4_audit import summarize_deep_fields, summarize_metric_fields


def test_summarize_metric_fields_reports_names_without_values():
    payloads = [
        {
            "metrics": {
                "weight_kg": 55.8,
                "bmi": 20.2,
                "body_fat_percent": None,
                "unmapped": {"mystery_a": 1, "mystery_b": {"x": 2}},
            }
        },
        {
            "metrics": {
                "weight_kg": 55.7,
                "body_fat_percent": 24.4,
                "unmapped": {"mystery_b": 3, "mystery_c": "secret-value"},
            }
        },
    ]

    summary = summarize_metric_fields(payloads)

    assert summary == {
        "records": 2,
        "mapped_present": ["bmi", "body_fat_percent", "weight_kg"],
        "unmapped_source_fields": ["mystery_a", "mystery_b", "mystery_c"],
    }
    assert "secret-value" not in repr(summary)


def test_summarize_metric_fields_tolerates_old_or_bad_records():
    summary = summarize_metric_fields([
        {},
        {"metrics": None},
        {"metrics": {"sleep_score": 81, "unmapped": None}},
    ])

    assert summary == {
        "records": 3,
        "mapped_present": ["sleep_score"],
        "unmapped_source_fields": [],
    }


def test_summarize_deep_fields_only_returns_structure_names():
    sleep = [
        {"metrics": {"segments": [{"start": 1, "end": 2, "stage": 3}, {"start": 2, "end": 3, "stage": 4}]}}
    ]
    menstruation = [
        {"value_raw": {"status": 1, "cycle_day": 8, "private_note": "do-not-print"}}
    ]

    summary = summarize_deep_fields(sleep_payloads=sleep, menstruation_payloads=menstruation)

    assert summary == {
        "sleep_segment_fields": ["end", "stage", "start"],
        "menstruation_value_fields": ["cycle_day", "private_note", "status"],
    }
    assert "do-not-print" not in repr(summary)
