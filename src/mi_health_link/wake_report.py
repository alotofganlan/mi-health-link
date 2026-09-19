from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import subprocess
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from .config import load_settings
from .report_command import report_command_argv
from .wake_store import WakeStore
from .wake_versions import select_unreported_sleep


LOG = logging.getLogger("mi-health-link-sleep-report")


@dataclass(frozen=True)
class SleepReportResult:
    ok: bool
    status: str
    report_id: str | None = None
    report_type: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {"ok": self.ok, "status": self.status}
        if self.report_id is not None:
            value["report_id"] = self.report_id
        if self.report_type is not None:
            value["report_type"] = self.report_type
        return value


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def run_report_command(report_id: str, report_kind: str) -> bool:
    env = {
        **os.environ,
        "WAKE_REPORT_ID": report_id,
        "WAKE_REPORT_KIND": report_kind,
    }
    try:
        completed = subprocess.run(
            report_command_argv(),
            check=False,
            timeout=_env_int("MORNING_REPORT_TIMEOUT_SECONDS", 300),
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.error("report_command failed_to_run type=%s", type(exc).__name__)
        return False
    if completed.returncode != 0:
        LOG.error("report_command failed exit_code=%s", completed.returncode)
        return False
    return True


class SleepReportService:
    def __init__(
        self,
        *,
        store,
        report_runner: Callable[[str, str], bool] = run_report_command,
        timezone=None,
        device: str | None = None,
        report_id_factory: Callable[[], str] | None = None,
        unlock_window: timedelta | None = None,
        location_window: timedelta | None = None,
    ) -> None:
        self.store = store
        self.report_runner = report_runner
        self.timezone = timezone or ZoneInfo(os.getenv("WAKE_TIMEZONE", "Asia/Shanghai"))
        self.device = device or os.getenv("WAKE_PROBE_DEVICE", "primary_phone")
        self.report_id_factory = report_id_factory or (lambda: str(uuid4()))
        self.unlock_window = unlock_window or timedelta(
            minutes=_env_int("WAKE_UNLOCK_WINDOW_MINUTES", 60)
        )
        self.location_window = location_window or timedelta(
            minutes=_env_int("WAKE_LOCATION_WINDOW_MINUTES", 120)
        )

    def check_pending(self, *, now: datetime | None = None) -> SleepReportResult:
        now = now or datetime.now(timezone.utc)
        unlock_at = self.store.latest_unlock_at(self.device)
        if unlock_at is None:
            LOG.info("sleep_report waiting_for_unlock")
            return SleepReportResult(ok=True, status="waiting_for_unlock")

        local_unlock = unlock_at.astimezone(self.timezone)
        days = (
            (local_unlock.date() - timedelta(days=1)).isoformat(),
            local_unlock.date().isoformat(),
        )
        rows = self.store.list_sleep_rows_for_days(days)
        deliveries = self.store.list_deliveries_for_days(self.device, days)
        candidate = select_unreported_sleep(
            rows,
            deliveries,
            unlock_at=unlock_at,
            timezone=self.timezone,
            unlock_window_seconds=int(self.unlock_window.total_seconds()),
        )
        if candidate is None:
            LOG.info("sleep_report no_new_sleep")
            return SleepReportResult(ok=True, status="no_new_sleep")

        LOG.info("sleep_report candidate_found")
        presence = self.store.closest_presence(
            device=self.device,
            wake_at=candidate.sleep.wake_at.astimezone(timezone.utc),
            window=self.location_window,
        )
        if presence is None:
            presence = self.store.latest_presence(self.device)

        report_id = self.report_id_factory()
        delivery = self.store.claim_delivery(
            report_id=report_id,
            report_date=candidate.sleep.sleep_day,
            device=self.device,
            sleep_source_record_id=candidate.sleep.source_record_id,
            sleep_fingerprint=candidate.fingerprint,
            report_kind=candidate.report_kind,
            includes_yesterday_health=candidate.includes_yesterday_health,
            sleep_snapshot=candidate.sleep.as_snapshot(),
            presence_id=presence.id if presence is not None else None,
            claimed_at=now.astimezone(timezone.utc),
        )
        if delivery is None:
            return SleepReportResult(ok=True, status="no_new_sleep")
        if not self.report_runner(report_id, candidate.report_kind):
            self.store.release_delivery(report_id)
            return SleepReportResult(ok=False, status="report_trigger_failed")
        self.store.complete_delivery(report_id, now.astimezone(timezone.utc))
        LOG.info("sleep_report report_triggered")
        return SleepReportResult(
            ok=True,
            status="report_triggered",
            report_id=report_id,
            report_type=candidate.report_kind,
        )


def check_pending_sleep_report() -> dict[str, Any]:
    """Run the shared post-sync report check without making sync itself fail."""
    try:
        settings = load_settings()
        if not settings.supabase_url or not settings.supabase_service_role_key:
            raise RuntimeError("Supabase is required for sleep report checks")
        service = SleepReportService(
            store=WakeStore(
                settings.supabase_url,
                settings.supabase_service_role_key,
            )
        )
        return service.check_pending().as_dict()
    except Exception as exc:
        LOG.error("sleep_report check_failed type=%s", type(exc).__name__)
        return {"ok": False, "status": "report_check_failed"}
