from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def ranges_cover_window(
    checks: list[dict[str, Any]],
    start_at: str,
    end_at: str,
) -> bool:
    """Return True when successful persisted checks continuously cover a window."""
    requested_start = _parse_iso(start_at)
    requested_end = _parse_iso(end_at)
    if requested_start is None or requested_end is None or requested_end < requested_start:
        return False

    intervals: list[tuple[datetime, datetime]] = []
    for check in checks:
        if check.get("status") != "success":
            continue
        start = _parse_iso(str(check.get("range_start") or ""))
        end = _parse_iso(str(check.get("range_end") or ""))
        if start is None or end is None or end < start:
            continue
        if end < requested_start or start > requested_end:
            continue
        intervals.append((max(start, requested_start), min(end, requested_end)))

    if not intervals:
        return False
    intervals.sort(key=lambda item: item[0])
    cursor = requested_start
    tolerance = timedelta(seconds=1)
    for start, end in intervals:
        if start > cursor + tolerance:
            return False
        if end > cursor:
            cursor = end
        if cursor >= requested_end:
            return True
    return cursor >= requested_end


def successful_range_check_at_or_after(
    checks: list[dict[str, Any]],
    checked_at: str | None,
) -> bool:
    """Return whether a successful range check is at least as new as a source check."""
    threshold = _parse_iso(checked_at)
    if threshold is None:
        return False
    for check in checks:
        if check.get("status") != "success":
            continue
        range_checked_at = _parse_iso(str(check.get("checked_at") or ""))
        if range_checked_at is not None and range_checked_at >= threshold:
            return True
    return False


def _next_recheck(source_checked_at: str | None, now: datetime) -> str | None:
    checked = _parse_iso(source_checked_at)
    if checked is None:
        return None
    checked_utc = checked.astimezone(timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    age = max(timedelta(0), now_utc - checked_utc)
    if age <= timedelta(days=1):
        delay = timedelta(minutes=30)
    elif age <= timedelta(days=3):
        delay = timedelta(hours=2)
    elif age <= timedelta(days=14):
        delay = timedelta(days=1)
    elif age <= timedelta(days=90):
        delay = timedelta(days=3)
    else:
        delay = timedelta(days=14)
    return (checked_utc + delay).isoformat()


def classify_coverage(
    *,
    metric: str,
    start_at: str,
    end_at: str,
    count: int,
    first_at: str | None,
    latest_at: str | None,
    source_checked_at: str | None,
    sync_failed: bool,
    range_checked_complete: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify normalized coverage without confusing source lag with sync failure."""
    current = now or datetime.now(timezone.utc)
    requested_end = _parse_iso(end_at)
    checked = _parse_iso(source_checked_at)
    source_check_covers_end = (
        checked is not None
        and requested_end is not None
        and checked >= requested_end
    )

    if sync_failed:
        status = "sync_failed"
    elif range_checked_complete:
        status = "source_empty" if count <= 0 else "complete"
    elif count <= 0:
        status = "source_empty" if source_check_covers_end else "not_synced"
    else:
        latest = _parse_iso(latest_at)
        if latest is not None and requested_end is not None and latest >= requested_end:
            status = "complete"
        elif source_check_covers_end:
            status = "source_empty"
        else:
            status = "partial"

    return {
        "metric": metric,
        "start_at": start_at,
        "end_at": end_at,
        "status": status,
        "count": max(0, int(count)),
        "first_at": first_at,
        "latest_at": latest_at,
        "source_checked_at": source_checked_at,
        "range_checked_complete": bool(range_checked_complete),
        "next_recheck_at": _next_recheck(source_checked_at, current),
    }
