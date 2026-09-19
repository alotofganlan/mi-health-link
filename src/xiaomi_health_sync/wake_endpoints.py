from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import os

from .wake_payloads import parse_location_update_payload, parse_unlock_payload
from .wake_report import SleepReportResult


LOG = logging.getLogger("xiaomi-health-wake-endpoints")


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class LocationUpdateService:
    def __init__(self, *, store, weather, device: str | None = None) -> None:
        self.store = store
        self.weather = weather
        self.device = device or os.getenv("WAKE_PROBE_DEVICE", "primary_phone")

    def process(
        self, value: object, *, received_at: datetime | None = None
    ) -> SleepReportResult:
        received_at = received_at or datetime.now(timezone.utc)
        payload = parse_location_update_payload(value, received_at=received_at)
        LOG.info("location_update received")
        latitude = round(payload.latitude, 3)
        longitude = round(payload.longitude, 3)
        place = self.weather.resolve_place(latitude, longitude)
        self.store.save_presence(
            observed_at=payload.observed_at.astimezone(timezone.utc),
            device=self.device,
            event="location_update",
            latitude=latitude,
            longitude=longitude,
            accuracy=payload.accuracy,
            city=place.city,
            district=place.district,
            country=place.country,
        )
        LOG.info("location_update completed")
        return SleepReportResult(ok=True, status="location_updated")


class UnlockProbeService:
    def __init__(
        self,
        *,
        store,
        sync_manager,
        report_service,
        device: str | None = None,
        sync_cooldown: timedelta | None = None,
    ) -> None:
        self.store = store
        self.sync_manager = sync_manager
        self.report_service = report_service
        self.device = device or os.getenv("WAKE_PROBE_DEVICE", "primary_phone")
        self.sync_cooldown = sync_cooldown or timedelta(
            seconds=_env_int("WAKE_PROBE_SYNC_COOLDOWN_SECONDS", 720)
        )

    def _start_sleep_sync(self) -> bool:
        LOG.info("xiaomi_sleep_sync started")
        job = self.sync_manager.start("sleep")
        job_id = job.get("job_id") if isinstance(job, dict) else None
        if not isinstance(job_id, str):
            LOG.error("xiaomi_sleep_sync failed_to_start")
            return False
        return True

    def process(
        self, value: object, *, received_at: datetime | None = None
    ) -> SleepReportResult:
        received_at = received_at or datetime.now(timezone.utc)
        payload = parse_unlock_payload(
            value,
            received_at=received_at,
            expected_device=self.device,
        )
        unlock_at = payload.observed_at.astimezone(timezone.utc)
        self.store.save_unlock(device=self.device, observed_at=unlock_at)
        LOG.info("wake_probe received")

        before = self.report_service.check_pending(now=received_at)
        if not before.ok or before.status == "report_triggered":
            return before

        last_sync = self.store.latest_sleep_sync_at()
        received_utc = received_at.astimezone(timezone.utc)
        if (
            last_sync is not None
            and received_utc - last_sync.astimezone(timezone.utc) < self.sync_cooldown
        ):
            LOG.info("xiaomi_sleep_sync skipped reason=cooldown")
            return SleepReportResult(ok=True, status="sync_cooldown")

        if not self._start_sleep_sync():
            return SleepReportResult(ok=False, status="xiaomi_sync_failed")
        return SleepReportResult(ok=True, status="waiting_for_sleep_data")
