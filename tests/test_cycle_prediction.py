from datetime import date

from xiaomi_health_sync.cycle_prediction import predict_xiaomi_cycle


def boundary(kind: str, day: str) -> dict[str, str]:
    return {"record_type": kind, "start_at": f"{day}T00:00:00+08:00"}


def test_synthetic_average_and_duration_fixture():
    rows = [
        boundary("period_start", "2026-06-08"),
        boundary("period_end", "2026-06-13"),
        boundary("period_start", "2026-07-04"),
        boundary("period_end", "2026-07-08"),
        boundary("period_start", "2026-08-01"),
        boundary("period_end", "2026-08-05"),
        boundary("period_start", "2026-09-01"),
        boundary("period_end", "2026-09-05"),
    ]

    result = predict_xiaomi_cycle(rows, as_of=date(2026, 9, 15))

    assert result["engine"] == "xiaomi_calendar_compatible"
    assert result["cycle_days"] == 28
    assert result["period_days"] == 5
    assert result["predicted_period"] == {
        "start": "2026-09-29",
        "end": "2026-10-03",
    }


def test_synthetic_varying_intervals_predict_two_day_reminder():
    rows = [
        boundary("period_start", "2026-05-26"),
        boundary("period_end", "2026-05-30"),
        boundary("period_start", "2026-06-21"),
        boundary("period_end", "2026-06-25"),
        boundary("period_start", "2026-07-21"),
        boundary("period_end", "2026-07-25"),
        boundary("period_start", "2026-08-19"),
        boundary("period_end", "2026-08-23"),
    ]

    result = predict_xiaomi_cycle(rows, as_of=date(2026, 9, 14))

    assert result["cycle_days"] == 28
    assert result["period_days"] == 5
    assert result["predicted_period"]["start"] == "2026-09-16"
    assert result["predicted_period"]["end"] == "2026-09-20"
    assert result["days_until_period"] == 2
    assert result["reminder"] == {"active": True, "lead_days": 7}


def test_insufficient_history_is_explicit():
    result = predict_xiaomi_cycle(
        [boundary("period_start", "2026-09-01")],
        as_of=date(2026, 9, 15),
    )

    assert result == {
        "engine": "xiaomi_calendar_compatible",
        "available": False,
        "reason": "insufficient_history",
        "start_count": 1,
    }


def test_boundaries_are_sorted_deduplicated_and_use_shanghai_date():
    rows = [
        boundary("period_end", "2026-08-23"),
        boundary("period_start", "2026-05-26"),
        boundary("period_end", "2026-05-30"),
        boundary("period_start", "2026-06-21"),
        boundary("period_end", "2026-06-25"),
        boundary("period_start", "2026-07-21"),
        boundary("period_end", "2026-07-25"),
        boundary("period_start", "2026-08-19"),
        boundary("period_start", "2026-08-19"),
        {"record_type": "note", "start_at": "2026-08-21T00:00:00+08:00"},
    ]

    result = predict_xiaomi_cycle(rows, as_of=date(2026, 9, 15))

    assert result["available"] is True
    assert result["predicted_period"]["start"] == "2026-09-16"


def test_out_of_range_cycle_omits_ovulation_and_fertile_window():
    rows = []
    for start, end in (
        ("2026-01-01", "2026-01-05"),
        ("2026-03-02", "2026-03-06"),
        ("2026-05-01", "2026-05-05"),
        ("2026-06-30", "2026-07-04"),
    ):
        rows.extend((boundary("period_start", start), boundary("period_end", end)))

    result = predict_xiaomi_cycle(rows, as_of=date(2026, 7, 1))

    assert result["cycle_days"] == 60
    assert result["ovulation_date"] is None
    assert result["fertile_window"] is None
