from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .supabase_store import SupabaseStore, stable_source_record_id


VERIFIED_BOOTSTRAP_KEYS = frozenset({
    "abnormal_heart_beat",
    "calories",
    "grade_prediction",
    "heart_rate",
    "intensity",
    "menstrual_symptoms",
    "menstruation",
    "pai",
    "resting_heart_rate",
    "running_ability_index",
    "single_blood_sugar",
    "single_temperature",
    "sleep",
    "spo2",
    "steps",
    "stress",
    "temperature_characteristic",
    "temperature_trend",
    "training_load",
    "valid_stand",
    "vitality",
    "weight",
})


def _epoch_iso(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def _xiaomi_record_id(record: dict[str, Any], *, key: str, timestamp: Any) -> str:
    watermark = record.get("watermark")
    if watermark not in (None, ""):
        return str(watermark)
    sid = record.get("sid")
    if sid not in (None, "") and timestamp is not None:
        return f"{sid}:{key}:{timestamp}"
    return stable_source_record_id(f"health_record:{key}", record)


class DirectSupabaseStore(SupabaseStore):
    """Production store for direct normalized sync and durable sync metadata."""

    def _load_sync_keys(self, field: str) -> set[str]:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={
                    "select": "key",
                    "source": "eq.xiaomi",
                    field: "eq.true",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        return {str(row["key"]) for row in rows if row.get("key")}

    def _update_sync_state(self, key: str, **fields) -> str:
        headers = self._headers()
        headers["Prefer"] = "return=representation"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.patch(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={"source": "eq.xiaomi", "key": f"eq.{key}"},
                headers=headers,
                json=fields,
            )
            response.raise_for_status()
            rows = response.json()
            if rows:
                return key

            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={"on_conflict": "source,key"},
                headers=self._headers(),
                json={"source": "xiaomi", "key": key, **fields},
            )
            response.raise_for_status()
        return key

    def load_observed_keys(self) -> set[str]:
        return set(VERIFIED_BOOTSTRAP_KEYS)

    def load_discovered_keys(self) -> set[str]:
        return self._load_sync_keys("discovered")

    def load_backfilled_keys(self) -> set[str]:
        return self._load_sync_keys("initial_backfill_complete")

    def load_backfill_progress(self, key: str) -> int | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={
                    "select": "next_start_time",
                    "source": "eq.xiaomi",
                    "key": f"eq.{key}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows:
            return None
        value = rows[0].get("next_start_time")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def remember_discovered_key(self, key: str) -> str:
        return self._update_sync_state(key, discovered=True)

    def remember_backfilled_key(self, key: str) -> str:
        return self._update_sync_state(key, initial_backfill_complete=True)

    def remember_backfill_progress(self, key: str, next_start_time: int) -> str:
        return self._update_sync_state(key, next_start_time=int(next_start_time))

    def remember_source_check(
        self,
        key: str,
        *,
        checked_at: str,
        status: str,
        source_latest_at: str | None,
        next_recheck_at: str | None,
        empty_check_count: int,
    ) -> str:
        return self._update_sync_state(
            key,
            source_checked_at=checked_at,
            source_check_status=status,
            source_latest_at=source_latest_at,
            next_recheck_at=next_recheck_at,
            empty_check_count=max(0, int(empty_check_count)),
        )

    def load_source_check(self, key: str) -> dict[str, Any] | None:
        fields = (
            "source_checked_at,source_check_status,source_latest_at,"
            "next_recheck_at,empty_check_count"
        )
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={
                    "select": fields,
                    "source": "eq.xiaomi",
                    "key": f"eq.{key}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows or not isinstance(rows[0], dict):
            return None
        row = rows[0]
        return {
            "source_checked_at": row.get("source_checked_at"),
            "source_check_status": row.get("source_check_status"),
            "source_latest_at": row.get("source_latest_at"),
            "next_recheck_at": row.get("next_recheck_at"),
            "empty_check_count": int(row.get("empty_check_count") or 0),
        }

    def _write_for_record(
        self,
        record: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        key = str(record.get("key") or "")
        value = record.get("value")

        if key == "single_blood_sugar" and isinstance(value, dict):
            glucose_value = value.get("blood_sugar")
            timestamp = value.get("time", record.get("time"))
            measured_at = _epoch_iso(timestamp)
            try:
                glucose_mmol_l = float(glucose_value)
            except (TypeError, ValueError):
                return None
            if measured_at is None or glucose_mmol_l <= 0:
                return None
            return (
                "glucose_samples",
                "source,source_record_id",
                {
                    "source": "xiaomi",
                    "source_record_id": _xiaomi_record_id(
                        record,
                        key=key,
                        timestamp=timestamp,
                    ),
                    "measured_at": measured_at,
                    "glucose_mg_dl": int(round(glucose_mmol_l * 18.0)),
                    "glucose_mmol_l": round(glucose_mmol_l, 2),
                    "direction": None,
                },
            )

        if key == "blood_sugar":
            measured_at = _epoch_iso(record.get("time"))
            if measured_at is None:
                return None
            return (
                "health_records",
                "source,key,source_record_id",
                {
                    "source": "xiaomi",
                    "key": key,
                    "source_record_id": _xiaomi_record_id(
                        record,
                        key=key,
                        timestamp=record.get("time"),
                    ),
                    "measured_at": measured_at,
                    "source_updated_at": _epoch_iso(record.get("update_time")),
                    "sid": None if record.get("sid") is None else str(record.get("sid")),
                    "zone_name": record.get("zone_name"),
                    "zone_offset": record.get("zone_offset"),
                    "value": value,
                    "metrics": record.get("metrics") if isinstance(record.get("metrics"), dict) else {},
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        return super()._write_for_record(record)

    def load_latest_glucose_timestamp_ms(self, source: str = "nightscout") -> int | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/glucose_samples",
                params={
                    "select": "measured_at",
                    "source": f"eq.{source}",
                    "order": "measured_at.desc",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows or not isinstance(rows[0], dict):
            return None
        measured_at = rows[0].get("measured_at")
        if not measured_at:
            return None
        try:
            parsed = datetime.fromisoformat(str(measured_at).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except ValueError:
            return None

    def load_glucose_mirror_checkpoint_ms(self) -> int | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={
                    "select": "next_start_time",
                    "source": "eq.nightscout_to_xiaomi",
                    "key": "eq.single_blood_sugar",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows or not isinstance(rows[0], dict):
            return None
        value = rows[0].get("next_start_time")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def load_nightscout_glucose_samples_after(
        self,
        since_ms: int,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if int(limit) <= 0:
            raise ValueError("limit must be positive")
        since_iso = datetime.fromtimestamp(
            int(since_ms) / 1000.0,
            tz=timezone.utc,
        ).isoformat()
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/glucose_samples",
                params={
                    "select": "source_record_id,measured_at,glucose_mmol_l",
                    "source": "eq.nightscout",
                    "measured_at": f"gt.{since_iso}",
                    "order": "measured_at.asc",
                    "limit": str(int(limit)),
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not isinstance(rows, list):
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def remember_glucose_mirror_checkpoint_ms(self, checkpoint_ms: int) -> int:
        checkpoint = int(checkpoint_ms)
        if checkpoint <= 0:
            raise ValueError("checkpoint_ms must be positive")
        headers = self._headers()
        headers["Prefer"] = "return=representation"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.patch(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={
                    "source": "eq.nightscout_to_xiaomi",
                    "key": "eq.single_blood_sugar",
                },
                headers=headers,
                json={"next_start_time": checkpoint},
            )
            response.raise_for_status()
            rows = response.json()
            if rows:
                return checkpoint

            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_state",
                params={"on_conflict": "source,key"},
                headers=self._headers(),
                json={
                    "source": "nightscout_to_xiaomi",
                    "key": "single_blood_sugar",
                    "next_start_time": checkpoint,
                },
            )
            response.raise_for_status()
        return checkpoint

    def upsert_glucose_samples(self, samples: list[dict[str, Any]]) -> int:
        if not samples:
            return 0
        self._upsert_table(
            table="glucose_samples",
            body=samples,
            on_conflict="source,source_record_id",
        )
        return len(samples)

    def save_sync_job(self, job: dict[str, object]) -> None:
        payload = {
            "job_id": str(job["job_id"]),
            "metric": job.get("metric"),
            "status": str(job.get("status") or "queued"),
            "created_at": job.get("created_at"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "result": job.get("result"),
            "error": job.get("error"),
        }
        headers = self._headers()
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_jobs",
                params={"on_conflict": "job_id"},
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

    def load_sync_job(self, job_id: str) -> dict[str, object] | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_jobs",
                params={
                    "select": "job_id,metric,status,created_at,started_at,completed_at,result,error",
                    "job_id": f"eq.{job_id}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows or not isinstance(rows[0], dict):
            return None
        return dict(rows[0])

    def load_latest_sync_job(self, metric: str) -> dict[str, object] | None:
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_sync_jobs",
                params={
                    "select": "job_id,metric,status,created_at,started_at,completed_at,result,error",
                    "metric": f"eq.{metric}",
                    "status": "in.(completed,failed)",
                    "order": "completed_at.desc.nullslast",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            rows = response.json()
        if not rows or not isinstance(rows[0], dict):
            return None
        return dict(rows[0])
