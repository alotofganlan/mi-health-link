from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from mi_health_link.wake_versions import (
    canonical_sleep_version,
    select_report_candidate,
    select_unreported_sleep,
)


CST = timezone(timedelta(hours=8))


def sleep_row(
    *,
    source_id: str = "sleep-main",
    start: str = "2026-09-13T21:00:00+08:00",
    end: str = "2026-09-14T06:00:00+08:00",
    total: int = 1,
    incomplete: bool = True,
    deep: int | None = None,
    sleep_day: str = "2026-09-14",
) -> dict:
    metrics = {
        "total_sleep_seconds": total,
        "is_incomplete": incomplete,
    }
    if deep is not None:
        metrics["deep_sleep_seconds"] = deep
    return {
        "source_record_id": source_id,
        "start_at": start,
        "end_at": end,
        "raw": {
            "sleep_day": sleep_day,
            "sleep_source": "watch",
            "metrics": metrics,
        },
    }


def completed_delivery(row: dict, *, kind: str = "morning") -> dict:
    version = canonical_sleep_version(row, CST)
    return {
        "status": "completed",
        "report_date": version.sleep_day,
        "report_kind": kind,
        "sleep_fingerprint": version.fingerprint,
        "sleep_source_record_id": version.source_record_id,
        "sleep_snapshot": version.as_snapshot(),
    }


def test_first_sleep_after_five_is_morning_regardless_of_duration_or_incomplete() -> None:
    candidate = select_report_candidate(
        [sleep_row(total=1, incomplete=True)],
        [],
        probe_at=datetime(2026, 9, 14, 5, 1, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )

    assert candidate is not None
    assert candidate.report_kind == "morning"
    assert candidate.includes_yesterday_health is True
    assert candidate.sleep.total_minutes == 1


def test_first_sleep_before_five_does_not_report() -> None:
    candidate = select_report_candidate(
        [sleep_row()],
        [],
        probe_at=datetime(2026, 9, 14, 4, 59, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is None


def test_unchanged_sleep_does_not_report_twice() -> None:
    row = sleep_row(total=351, incomplete=False, deep=80)
    candidate = select_report_candidate(
        [row],
        [completed_delivery(row)],
        probe_at=datetime(2026, 9, 14, 7, 0, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is None


def test_later_wake_time_is_sleep_update() -> None:
    old = sleep_row(total=351, end="2026-09-14T06:00:00+08:00")
    new = sleep_row(total=351, end="2026-09-14T06:20:00+08:00")
    candidate = select_report_candidate(
        [new],
        [completed_delivery(old)],
        probe_at=datetime(2026, 9, 14, 6, 21, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is not None
    assert candidate.report_kind == "sleep_update"
    assert candidate.includes_yesterday_health is False


def test_larger_duration_or_new_stage_data_is_sleep_update() -> None:
    old = sleep_row(total=300, deep=None)
    longer = sleep_row(total=320, deep=70)
    candidate = select_report_candidate(
        [longer],
        [completed_delivery(old)],
        probe_at=datetime(2026, 9, 14, 7, 0, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is not None
    assert candidate.report_kind == "sleep_update"


def test_decrease_only_correction_does_not_report() -> None:
    old = sleep_row(total=351, deep=80)
    smaller = sleep_row(total=340, deep=70)
    candidate = select_report_candidate(
        [smaller],
        [completed_delivery(old)],
        probe_at=datetime(2026, 9, 14, 7, 0, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is None


def test_new_sleep_after_morning_is_new_sleep() -> None:
    main = sleep_row(total=351)
    nap = sleep_row(
        source_id="sleep-nap",
        start="2026-09-14T13:00:00+08:00",
        end="2026-09-14T13:25:00+08:00",
        total=20,
    )
    candidate = select_report_candidate(
        [main, nap],
        [completed_delivery(main)],
        probe_at=datetime(2026, 9, 14, 13, 26, tzinfo=CST),
        timezone=CST,
        first_report_time=time(5, 0),
    )
    assert candidate is not None
    assert candidate.report_kind == "new_sleep"
    assert candidate.sleep.source_record_id == "sleep-nap"
    assert candidate.includes_yesterday_health is False


def test_stale_intermediate_slice_does_not_trigger_near_morning_unlock() -> None:
    stale = sleep_row(
        source_id="watch:sleep:0510",
        start="2026-09-16T00:28:00+08:00",
        end="2026-09-16T05:10:00+08:00",
        total=272,
        sleep_day="2026-09-16",
    )

    candidate = select_unreported_sleep(
        [stale],
        [],
        unlock_at=datetime.fromisoformat("2026-09-16T06:59:38+08:00"),
        timezone=CST,
        unlock_window_seconds=60 * 60,
    )

    assert candidate is None


def test_versions_with_same_start_collapse_to_latest_before_morning_report() -> None:
    stale = sleep_row(
        source_id="watch:sleep:0510",
        start="2026-09-16T00:28:00+08:00",
        end="2026-09-16T05:10:00+08:00",
        total=272,
        sleep_day="2026-09-16",
    )
    final = sleep_row(
        source_id="watch:sleep:0657",
        start="2026-09-16T00:28:00+08:00",
        end="2026-09-16T06:57:00+08:00",
        total=376,
        sleep_day="2026-09-16",
    )

    candidate = select_unreported_sleep(
        [stale, final],
        [],
        unlock_at=datetime.fromisoformat("2026-09-16T06:59:38+08:00"),
        timezone=CST,
        unlock_window_seconds=60 * 60,
    )

    assert candidate is not None
    assert candidate.report_kind == "morning"
    assert candidate.sleep.source_record_id == "watch:sleep:0657"


def test_growth_of_delivered_logical_sleep_is_update_not_second_morning() -> None:
    stale = sleep_row(
        source_id="watch:sleep:0510",
        start="2026-09-16T00:28:00+08:00",
        end="2026-09-16T05:10:00+08:00",
        total=272,
        sleep_day="2026-09-16",
    )
    final = sleep_row(
        source_id="watch:sleep:0657",
        start="2026-09-16T00:28:00+08:00",
        end="2026-09-16T06:57:00+08:00",
        total=376,
        sleep_day="2026-09-16",
    )

    candidate = select_unreported_sleep(
        [final],
        [completed_delivery(stale)],
        unlock_at=datetime.fromisoformat("2026-09-16T06:59:38+08:00"),
        timezone=CST,
        unlock_window_seconds=60 * 60,
    )

    assert candidate is not None
    assert candidate.report_kind == "sleep_update"
    assert candidate.includes_yesterday_health is False
