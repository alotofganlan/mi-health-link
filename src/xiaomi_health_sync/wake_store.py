from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx


@dataclass(frozen=True)
class Presence:
    id: int
    observed_at: datetime
    device: str
    latitude: float
    longitude: float
    accuracy: float | None
    city: str | None = None
    district: str | None = None
    country: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class Delivery:
    id: str
    report_date: str
    device: str
    sleep_source_record_id: str
    sleep_fingerprint: str
    report_kind: str
    includes_yesterday_health: bool
    sleep_snapshot: dict[str, Any]
    presence_id: int | None
    status: str
    claimed_at: datetime
    triggered_at: datetime | None = None


def _parse_datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class WakeStore:
    def __init__(
        self,
        url: str,
        service_role_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.service_role_key = service_role_key
        self.transport = transport

    def _headers(self, *, representation: bool = False) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if representation:
            headers["Prefer"] = "return=representation"
        if not self.service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        return headers

    @staticmethod
    def _presence(row: dict[str, Any]) -> Presence:
        return Presence(
            id=int(row["id"]),
            observed_at=_parse_datetime(row["observed_at"]),
            device=str(row["device"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            accuracy=_optional_number(row.get("accuracy")),
            city=str(row["city"]) if row.get("city") else None,
            district=str(row["district"]) if row.get("district") else None,
            region=str(row["region"]) if row.get("region") else None,
            country=str(row["country"]) if row.get("country") else None,
        )

    @staticmethod
    def _delivery(row: dict[str, Any]) -> Delivery:
        snapshot = row.get("sleep_snapshot")
        return Delivery(
            id=str(row["id"]),
            report_date=str(row["report_date"]),
            device=str(row["device"]),
            sleep_source_record_id=str(row["sleep_source_record_id"]),
            sleep_fingerprint=str(row["sleep_fingerprint"]),
            report_kind=str(row["report_kind"]),
            includes_yesterday_health=bool(row["includes_yesterday_health"]),
            sleep_snapshot=dict(snapshot) if isinstance(snapshot, dict) else {},
            presence_id=int(row["presence_id"]) if row.get("presence_id") else None,
            status=str(row["status"]),
            claimed_at=_parse_datetime(row["claimed_at"]),
            triggered_at=(
                _parse_datetime(row["triggered_at"])
                if row.get("triggered_at")
                else None
            ),
        )

    def save_presence(
        self,
        *,
        observed_at: datetime,
        device: str,
        event: str,
        latitude: float,
        longitude: float,
        accuracy: float | None,
        city: str | None,
        district: str | None,
        country: str | None,
    ) -> Presence:
        body = {
            "observed_at": observed_at.isoformat(),
            "device": device,
            "event": event,
            "latitude": round(latitude, 3),
            "longitude": round(longitude, 3),
            "accuracy": accuracy,
            "city": city,
            "district": district,
            "country": country,
            "source": "automate_location",
        }
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url}/rest/v1/device_presence",
                headers=self._headers(representation=True),
                json=body,
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise RuntimeError("Supabase did not return the saved presence")
        return self._presence(rows[0])

    def claim_delivery(
        self,
        *,
        report_id: str,
        report_date: str,
        device: str,
        sleep_source_record_id: str,
        sleep_fingerprint: str,
        report_kind: str,
        includes_yesterday_health: bool,
        sleep_snapshot: dict[str, Any],
        presence_id: int | None,
        claimed_at: datetime,
    ) -> Delivery | None:
        body = {
            "id": report_id,
            "report_date": report_date,
            "device": device,
            "sleep_source_record_id": sleep_source_record_id,
            "sleep_fingerprint": sleep_fingerprint,
            "report_kind": report_kind,
            "includes_yesterday_health": includes_yesterday_health,
            "sleep_snapshot": sleep_snapshot,
            "presence_id": presence_id,
            "status": "triggering",
            "claimed_at": claimed_at.isoformat(),
        }
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url}/rest/v1/wake_report_deliveries",
                headers=self._headers(representation=True),
                json=body,
            )
        if response.status_code == 409:
            return None
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            raise RuntimeError("Supabase did not return the claimed delivery")
        return self._delivery(rows[0])

    def release_delivery(self, report_id: str) -> None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.delete(
                f"{self.url}/rest/v1/wake_report_deliveries",
                params={"id": f"eq.{report_id}", "status": "eq.triggering"},
                headers=self._headers(),
            )
            response.raise_for_status()

    def closest_presence(
        self,
        *,
        device: str,
        wake_at: datetime,
        window: timedelta,
    ) -> Presence | None:
        start = wake_at - window
        end = wake_at + window
        params = [
            ("select", "*"),
            ("device", f"eq.{device}"),
            ("source", "eq.automate_location"),
            ("observed_at", f"gte.{start.isoformat()}"),
            ("observed_at", f"lte.{end.isoformat()}"),
            ("order", "observed_at.asc"),
        ]
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/device_presence",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        candidates = [
            self._presence(row)
            for row in rows
            if isinstance(row, dict)
        ] if isinstance(rows, list) else []
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: abs((item.observed_at - wake_at).total_seconds()),
        )

    def latest_sleep_sync_at(self) -> datetime | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/xiaomi_sync_state",
                params={
                    "select": "source_checked_at",
                    "source": "eq.xiaomi",
                    "key": "eq.sleep",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not rows[0].get("source_checked_at"):
            return None
        return _parse_datetime(rows[0]["source_checked_at"])

    def list_sleep_rows(self, report_date: str) -> list[dict[str, Any]]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/sleep_sessions",
                params={
                    "select": "source_record_id,start_at,end_at,score,avg_hr,avg_spo2,avg_hrv_ms,raw",
                    "source": "eq.xiaomi",
                    "raw->>sleep_day": f"eq.{report_date}",
                    "raw->>sleep_source": "eq.watch",
                    "order": "end_at.asc",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def list_deliveries(
        self,
        device: str,
        report_date: str,
    ) -> list[dict[str, Any]]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/wake_report_deliveries",
                params={
                    "select": "*",
                    "device": f"eq.{device}",
                    "report_date": f"eq.{report_date}",
                    "order": "created_at.asc",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def get_delivery(self, report_id: str) -> dict[str, Any] | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/wake_report_deliveries",
                params={"select": "*", "id": f"eq.{report_id}", "limit": "1"},
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        return dict(rows[0])

    def get_context_snapshot(self, report_id: str) -> dict[str, Any] | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/morning_context_snapshots",
                params={
                    "select": "*",
                    "report_id": f"eq.{report_id}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        return dict(rows[0])

    def save_context_snapshot(
        self,
        report_id: str,
        *,
        schema_version: str,
        source_versions: dict[str, Any],
        payload: dict[str, Any],
        snapshot_mode: str,
    ) -> dict[str, Any]:
        if snapshot_mode not in {"native", "legacy_hydrated"}:
            raise ValueError("snapshot_mode must be native or legacy_hydrated")
        body = {
            "report_id": report_id,
            "schema_version": schema_version,
            "source_versions": source_versions,
            "payload": payload,
            "snapshot_mode": snapshot_mode,
        }
        headers = self._headers(representation=True)
        headers["Prefer"] = "resolution=ignore-duplicates,return=representation"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url}/rest/v1/morning_context_snapshots",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            rows = response.json()
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return dict(rows[0])
        winner = self.get_context_snapshot(report_id)
        if winner is None:
            raise RuntimeError("Supabase did not return or persist the context snapshot")
        return winner

    def get_presence(self, presence_id: int) -> Presence | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/device_presence",
                params={"select": "*", "id": f"eq.{presence_id}", "limit": "1"},
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        return self._presence(rows[0])

    def save_unlock(self, *, device: str, observed_at: datetime) -> None:
        body = {
            "device": device,
            "last_unlock_at": observed_at.isoformat(),
            "source": "automate_unlock",
        }
        headers = self._headers(representation=True)
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url}/rest/v1/device_wake_state",
                params={"on_conflict": "device"},
                headers=headers,
                json=body,
            )
            response.raise_for_status()

    def latest_unlock_at(self, device: str) -> datetime | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/device_wake_state",
                params={
                    "select": "last_unlock_at",
                    "device": f"eq.{device}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not rows[0].get("last_unlock_at"):
            return None
        return _parse_datetime(rows[0]["last_unlock_at"])

    def latest_presence(self, device: str) -> Presence | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/device_presence",
                params={
                    "select": "*",
                    "device": f"eq.{device}",
                    "source": "eq.automate_location",
                    "order": "observed_at.desc",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return None
        return self._presence(rows[0])

    def list_sleep_rows_for_days(self, days: tuple[str, ...]) -> list[dict[str, Any]]:
        if not days:
            return []
        day_filter = "in.(" + ",".join(days) + ")"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/sleep_sessions",
                params={
                    "select": "source_record_id,start_at,end_at,score,avg_hr,avg_spo2,avg_hrv_ms,raw",
                    "source": "eq.xiaomi",
                    "raw->>sleep_day": day_filter,
                    "raw->>sleep_source": "eq.watch",
                    "order": "end_at.asc",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def list_deliveries_for_days(
        self, device: str, days: tuple[str, ...]
    ) -> list[dict[str, Any]]:
        if not days:
            return []
        day_filter = "in.(" + ",".join(days) + ")"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url}/rest/v1/wake_report_deliveries",
                params={
                    "select": "*",
                    "device": f"eq.{device}",
                    "report_date": day_filter,
                    "order": "created_at.asc",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def complete_delivery(self, report_id: str, triggered_at: datetime) -> None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.patch(
                f"{self.url}/rest/v1/wake_report_deliveries",
                params={"id": f"eq.{report_id}", "status": "eq.triggering"},
                headers=self._headers(),
                json={
                    "status": "completed",
                    "triggered_at": triggered_at.isoformat(),
                },
            )
            response.raise_for_status()
