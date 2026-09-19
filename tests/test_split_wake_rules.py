from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from xiaomi_health_sync.wake_payloads import (
    parse_location_update_payload,
    parse_unlock_payload,
)
from xiaomi_health_sync.wake_versions import select_unreported_sleep


CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 14, 8, 2, tzinfo=CST)


def sleep_row(
    source_id: str,
    *,
    start: str,
    end: str,
    minutes: int,
) -> dict:
    return {
        "source_record_id": source_id,
        "start_at": start,
        "end_at": end,
        "raw": {
            "sleep_day": "2026-09-14",
            "sleep_source": "watch",
            "metrics": {"total_sleep_seconds": minutes},
        },
    }


MAIN = sleep_row(
    "main",
    start="2026-09-13T23:00:00+08:00",
    end="2026-09-14T07:30:00+08:00",
    minutes=480,
)
NAP = sleep_row(
    "nap",
    start="2026-09-14T13:00:00+08:00",
    end="2026-09-14T13:25:00+08:00",
    minutes=25,
)


def test_location_payload_and_unlock_payload_are_separate() -> None:
    location = parse_location_update_payload(
        {
            "latitude": 12.3456,
            "longitude": 67.8901,
            "accuracy": 35,
            "location_time": NOW.timestamp(),
            "source": "automate",
        },
        received_at=NOW,
    )
    unlock = parse_unlock_payload(
        {"event": "unlock", "device": "primary_phone", "timestamp": NOW.timestamp()},
        received_at=NOW,
    )

    assert location.latitude == 12.3456
    assert location.source == "automate"
    assert unlock.device == "primary_phone"
    assert unlock.observed_at == NOW


@pytest.mark.parametrize(
    "payload",
    [
        {"latitude": 91, "longitude": 0, "source": "automate"},
        {"latitude": 0, "longitude": 181, "source": "automate"},
        {"latitude": 0, "longitude": 0, "source": "other"},
    ],
)
def test_location_payload_rejects_invalid_values(payload: dict) -> None:
    with pytest.raises(ValueError):
        parse_location_update_payload(payload, received_at=NOW)


def test_unlock_payload_does_not_require_location_and_rejects_wrong_device() -> None:
    with pytest.raises(ValueError):
        parse_unlock_payload(
            {"event": "unlock", "device": "other"},
            received_at=NOW,
            expected_device="primary_phone",
        )


def test_sleep_requires_unlock_after_wake_and_within_two_hours() -> None:
    assert select_unreported_sleep(
        [MAIN], [], unlock_at=datetime(2026, 9, 14, 7, 29, tzinfo=CST), timezone=CST
    ) is None
    assert select_unreported_sleep(
        [MAIN], [], unlock_at=datetime(2026, 9, 14, 9, 31, tzinfo=CST), timezone=CST
    ) is None

    candidate = select_unreported_sleep(
        [MAIN], [], unlock_at=datetime(2026, 9, 14, 8, 2, tzinfo=CST), timezone=CST
    )
    assert candidate is not None
    assert candidate.report_kind == "morning"
    assert candidate.includes_yesterday_health is True


def test_sleep_without_stable_id_or_basic_content_is_ignored() -> None:
    missing_id = sleep_row(
        "",
        start="2026-09-13T23:00:00+08:00",
        end="2026-09-14T07:30:00+08:00",
        minutes=0,
    )
    missing_id["raw"]["metrics"] = {}
    assert select_unreported_sleep([missing_id], [], unlock_at=NOW, timezone=CST) is None


def test_same_sleep_id_is_never_reported_again_even_if_snapshot_grew() -> None:
    longer = sleep_row(
        "main",
        start="2026-09-13T23:00:00+08:00",
        end="2026-09-14T07:45:00+08:00",
        minutes=500,
    )
    deliveries = [{"status": "completed", "sleep_source_record_id": "main"}]

    assert select_unreported_sleep(
        [longer], deliveries, unlock_at=NOW, timezone=CST
    ) is None


def test_shorter_second_sleep_is_a_nap() -> None:
    candidate = select_unreported_sleep(
        [MAIN, NAP],
        [{"status": "completed", "sleep_source_record_id": "main"}],
        unlock_at=datetime(2026, 9, 14, 13, 30, tzinfo=CST),
        timezone=CST,
    )

    assert candidate is not None
    assert candidate.sleep.source_record_id == "nap"
    assert candidate.report_kind == "nap"
    assert candidate.includes_yesterday_health is False
