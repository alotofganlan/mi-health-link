from mi_health_link.history_policy import DAY_SECONDS, initial_backfill_start_time


def test_sleep_initial_backfill_is_one_year():
    end = 2_000_000_000
    assert initial_backfill_start_time("sleep", end) == end - 365 * DAY_SECONDS


def test_temperature_initial_backfill_is_one_year():
    end = 2_000_000_000
    for key in ("temperature_trend", "temperature_characteristic", "single_temperature"):
        assert initial_backfill_start_time(key, end) == end - 365 * DAY_SECONDS


def test_menstruation_initial_backfill_is_all_history():
    assert initial_backfill_start_time("menstruation", 2_000_000_000) == 0


def test_other_initial_backfill_is_thirty_days():
    end = 2_000_000_000
    assert initial_backfill_start_time("heart_rate", end) == end - 30 * DAY_SECONDS
