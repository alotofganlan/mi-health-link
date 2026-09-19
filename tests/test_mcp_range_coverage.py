from datetime import datetime, timezone

from mi_health_link.mcp_coverage import classify_coverage, ranges_cover_window


def test_contiguous_successful_ranges_cover_requested_window() -> None:
    checks = [
        {"range_start": "2026-01-01T00:00:00+08:00", "range_end": "2026-01-31T23:59:59+08:00", "status": "success"},
        {"range_start": "2026-02-01T00:00:00+08:00", "range_end": "2026-03-01T00:00:00+08:00", "status": "success"},
    ]
    assert ranges_cover_window(
        checks,
        "2026-01-01T00:00:00+08:00",
        "2026-03-01T00:00:00+08:00",
    ) is True


def test_gap_or_failed_range_does_not_cover_requested_window() -> None:
    checks = [
        {"range_start": "2026-01-01T00:00:00+08:00", "range_end": "2026-01-31T23:59:59+08:00", "status": "success"},
        {"range_start": "2026-02-02T00:00:00+08:00", "range_end": "2026-03-01T00:00:00+08:00", "status": "success"},
        {"range_start": "2026-02-01T00:00:00+08:00", "range_end": "2026-02-01T23:59:59+08:00", "status": "failed"},
    ]
    assert ranges_cover_window(
        checks,
        "2026-01-01T00:00:00+08:00",
        "2026-03-01T00:00:00+08:00",
    ) is False


def test_full_historical_check_distinguishes_empty_from_not_synced() -> None:
    empty = classify_coverage(
        metric="temperature_trend",
        start_at="2026-01-01T00:00:00+08:00",
        end_at="2026-02-01T00:00:00+08:00",
        count=0,
        first_at=None,
        latest_at=None,
        source_checked_at=None,
        sync_failed=False,
        range_checked_complete=True,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert empty["status"] == "source_empty"

    populated = classify_coverage(
        metric="temperature_trend",
        start_at="2026-01-01T00:00:00+08:00",
        end_at="2026-02-01T00:00:00+08:00",
        count=12,
        first_at="2026-01-03T00:00:00+08:00",
        latest_at="2026-01-29T00:00:00+08:00",
        source_checked_at=None,
        sync_failed=False,
        range_checked_complete=True,
        now=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert populated["status"] == "complete"
