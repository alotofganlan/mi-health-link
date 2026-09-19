from __future__ import annotations

DAY_SECONDS = 24 * 60 * 60
SLEEP_INITIAL_DAYS = 365
TEMPERATURE_INITIAL_DAYS = 365
DEFAULT_INITIAL_DAYS = 30
TEMPERATURE_KEYS = frozenset({
    "temperature_trend",
    "temperature_characteristic",
    "single_temperature",
})


def initial_backfill_start_time(key: str, end_time: int) -> int:
    """Return the first-history start timestamp for a Xiaomi fitness key.

    Menstruation is intentionally unbounded (Unix epoch) so Xiaomi can return
    every historical boundary it still retains. Sleep and temperature metrics
    get one year; other ordinary fitness keys get one month.
    """
    if key == "menstruation":
        return 0
    if key == "sleep":
        days = SLEEP_INITIAL_DAYS
    elif key in TEMPERATURE_KEYS:
        days = TEMPERATURE_INITIAL_DAYS
    else:
        days = DEFAULT_INITIAL_DAYS
    return max(0, int(end_time) - days * DAY_SECONDS)
