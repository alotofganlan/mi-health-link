from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .supabase_store import _epoch_iso, _normalized_metadata, _normalized_record_id
from .sync_store import DirectSupabaseStore


class TemperatureAwareStore(DirectSupabaseStore):
    """Direct store with normalized support for Xiaomi temperature characteristics."""

    def _write_for_record(
        self,
        record: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]] | None:
        key = str(record.get("key") or "")
        if key != "temperature_characteristic":
            return super()._write_for_record(record)

        measured_at = _epoch_iso(record.get("time"))
        if measured_at is None:
            return None

        value = record.get("value")
        metrics = record.get("metrics")
        return (
            "health_records",
            "source,key,source_record_id",
            {
                "source": "xiaomi",
                "key": key,
                "source_record_id": _normalized_record_id(record),
                "measured_at": measured_at,
                "source_updated_at": _epoch_iso(record.get("update_time")),
                "sid": None if record.get("sid") is None else str(record.get("sid")),
                "zone_name": record.get("zone_name"),
                "zone_offset": record.get("zone_offset"),
                "value": value,
                "metrics": metrics if isinstance(metrics, dict) else {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
