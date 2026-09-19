from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone as datetime_timezone
import hashlib
import json
from typing import Any, Literal


REPORT_KINDS = ("morning", "nap", "sleep_update", "new_sleep")


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse_datetime(value: object, tz) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_timezone.utc)
    return parsed.astimezone(tz)


def _minutes(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    if amount < 0:
        return None
    return round(amount / 60) if amount > 24 * 60 else amount


@dataclass(frozen=True)
class SleepVersion:
    source_record_id: str
    sleep_day: str
    start_at: datetime
    wake_at: datetime
    total_minutes: int | None
    stage_minutes: dict[str, int]

    def as_snapshot(self) -> dict[str, Any]:
        return {
            "source_record_id": self.source_record_id,
            "sleep_day": self.sleep_day,
            "start_at": self.start_at.isoformat(),
            "wake_at": self.wake_at.isoformat(),
            "total_minutes": self.total_minutes,
            "stage_minutes": dict(sorted(self.stage_minutes.items())),
        }

    @property
    def logical_key(self) -> str:
        return f"{self.sleep_day}:{self.start_at.isoformat()}"

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            self.as_snapshot(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReportCandidate:
    sleep: SleepVersion
    fingerprint: str
    report_kind: Literal["morning", "sleep_update", "new_sleep", "nap"]
    includes_yesterday_health: bool


def canonical_sleep_version(row: dict[str, Any], timezone) -> SleepVersion:
    raw = _mapping(row.get("raw"))
    metrics = _mapping(raw.get("metrics"))
    start_at = _parse_datetime(row.get("start_at"), timezone)
    wake_at = _parse_datetime(row.get("end_at"), timezone)
    stage_minutes: dict[str, int] = {}
    for name in (
        "deep_sleep_seconds",
        "light_sleep_seconds",
        "rem_sleep_seconds",
        "awake_seconds",
    ):
        amount = _minutes(metrics.get(name))
        if amount is not None:
            stage_minutes[name.removesuffix("_seconds")] = amount
    return SleepVersion(
        source_record_id=str(row.get("source_record_id") or ""),
        sleep_day=str(raw.get("sleep_day") or wake_at.date().isoformat()),
        start_at=start_at,
        wake_at=wake_at,
        total_minutes=_minutes(metrics.get("total_sleep_seconds")),
        stage_minutes=stage_minutes,
    )


def _snapshot_grew(current: SleepVersion, previous: dict[str, Any]) -> bool:
    try:
        previous_wake = _parse_datetime(previous.get("wake_at"), current.wake_at.tzinfo)
    except (TypeError, ValueError):
        previous_wake = current.wake_at
    if current.wake_at > previous_wake:
        return True

    old_total = previous.get("total_minutes")
    if (
        current.total_minutes is not None
        and isinstance(old_total, (int, float))
        and not isinstance(old_total, bool)
        and current.total_minutes > old_total
    ):
        return True
    if current.total_minutes is not None and old_total is None:
        return True

    previous_stages = _mapping(previous.get("stage_minutes"))
    for key, value in current.stage_minutes.items():
        old_value = previous_stages.get(key)
        if old_value is None or (
            isinstance(old_value, (int, float))
            and not isinstance(old_value, bool)
            and value > old_value
        ):
            return True
    return False


def select_report_candidate(
    rows: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    *,
    probe_at: datetime,
    timezone,
    first_report_time: time,
) -> ReportCandidate | None:
    local_probe = probe_at.astimezone(timezone)
    report_date = local_probe.date().isoformat()
    completed = [
        delivery
        for delivery in deliveries
        if isinstance(delivery, dict) and delivery.get("status") == "completed"
    ]
    morning_complete = any(
        delivery.get("report_date") == report_date
        and delivery.get("report_kind") == "morning"
        for delivery in completed
    )

    versions: list[SleepVersion] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = _mapping(row.get("raw"))
        if raw.get("sleep_source") != "watch":
            continue
        try:
            version = canonical_sleep_version(row, timezone)
        except (TypeError, ValueError):
            continue
        if version.sleep_day == report_date:
            versions.append(version)

    if not versions:
        return None

    if not morning_complete:
        if local_probe.timetz().replace(tzinfo=None) < first_report_time:
            return None
        main = max(
            versions,
            key=lambda item: (item.total_minutes or 0, item.wake_at),
        )
        return ReportCandidate(
            sleep=main,
            fingerprint=main.fingerprint,
            report_kind="morning",
            includes_yesterday_health=True,
        )

    for current in sorted(versions, key=lambda item: item.wake_at, reverse=True):
        previous_for_sleep = [
            delivery
            for delivery in completed
            if delivery.get("sleep_source_record_id") == current.source_record_id
        ]
        if any(
            delivery.get("sleep_fingerprint") == current.fingerprint
            for delivery in previous_for_sleep
        ):
            continue
        if not previous_for_sleep:
            return ReportCandidate(
                sleep=current,
                fingerprint=current.fingerprint,
                report_kind="new_sleep",
                includes_yesterday_health=False,
            )
        latest = previous_for_sleep[-1]
        snapshot = latest.get("sleep_snapshot")
        if isinstance(snapshot, dict) and _snapshot_grew(current, snapshot):
            return ReportCandidate(
                sleep=current,
                fingerprint=current.fingerprint,
                report_kind="sleep_update",
                includes_yesterday_health=False,
            )
    return None


def _is_usable_sleep(version: SleepVersion) -> bool:
    return (
        bool(version.source_record_id)
        and version.wake_at > version.start_at
        and (
            (version.total_minutes is not None and version.total_minutes > 0)
            or any(value > 0 for value in version.stage_minutes.values())
        )
    )


def select_unreported_sleep(
    rows: list[dict[str, Any]],
    deliveries: list[dict[str, Any]],
    *,
    unlock_at: datetime,
    timezone,
    unlock_window_seconds: int = 60 * 60,
) -> ReportCandidate | None:
    """Select one unreported watch sleep followed by a timely unlock."""
    blocked_ids = {
        str(item.get("sleep_source_record_id"))
        for item in deliveries
        if isinstance(item, dict)
        and item.get("status") in {"triggering", "completed"}
        and item.get("sleep_source_record_id")
    }
    versions_by_key: dict[str, SleepVersion] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = _mapping(row.get("raw"))
        if raw.get("sleep_source") != "watch":
            continue
        try:
            version = canonical_sleep_version(row, timezone)
        except (TypeError, ValueError):
            continue
        if not _is_usable_sleep(version):
            continue
        current = versions_by_key.get(version.logical_key)
        quality = (
            version.wake_at,
            version.total_minutes or 0,
            len(version.stage_minutes),
        )
        if current is None or quality > (
            current.wake_at,
            current.total_minutes or 0,
            len(current.stage_minutes),
        ):
            versions_by_key[version.logical_key] = version
    versions = list(versions_by_key.values())
    if not versions:
        return None

    main_keys: set[str] = set()
    sleep_days = {item.sleep_day for item in versions}
    for sleep_day in sleep_days:
        daily = [item for item in versions if item.sleep_day == sleep_day]
        main = max(daily, key=lambda item: (item.total_minutes or 0, item.wake_at))
        main_keys.add(main.logical_key)

    local_unlock = unlock_at.astimezone(timezone)
    eligible = [
        item
        for item in versions
        if item.source_record_id not in blocked_ids
        and item.wake_at <= local_unlock
        and (local_unlock - item.wake_at).total_seconds() <= unlock_window_seconds
    ]
    if not eligible:
        return None
    for selected in sorted(eligible, key=lambda item: item.wake_at, reverse=True):
        previous_for_sleep = []
        for delivery in deliveries:
            if not isinstance(delivery, dict):
                continue
            snapshot = delivery.get("sleep_snapshot")
            if not isinstance(snapshot, dict):
                continue
            try:
                previous = _parse_datetime(snapshot.get("start_at"), timezone)
            except (TypeError, ValueError):
                continue
            previous_day = str(snapshot.get("sleep_day") or previous.date().isoformat())
            if f"{previous_day}:{previous.isoformat()}" == selected.logical_key:
                previous_for_sleep.append(delivery)

        if previous_for_sleep:
            if any(
                delivery.get("status") == "triggering"
                or delivery.get("sleep_fingerprint") == selected.fingerprint
                for delivery in previous_for_sleep
            ):
                continue
            latest = previous_for_sleep[-1]
            snapshot = latest.get("sleep_snapshot")
            if isinstance(snapshot, dict) and _snapshot_grew(selected, snapshot):
                return ReportCandidate(
                    sleep=selected,
                    fingerprint=selected.fingerprint,
                    report_kind="sleep_update",
                    includes_yesterday_health=False,
                )
            continue

        is_main = selected.logical_key in main_keys
        return ReportCandidate(
            sleep=selected,
            fingerprint=selected.fingerprint,
            report_kind="morning" if is_main else "nap",
            includes_yesterday_health=is_main,
        )
    return None
