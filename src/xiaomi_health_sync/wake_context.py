from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .wake_weather import WeatherContext


class WakeContextService:
    def __init__(
        self,
        *,
        store,
        weather,
        health=None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.store = store
        self.weather = weather
        self.health = health
        self.timezone = ZoneInfo(timezone_name)

    def get(self, report_id: str) -> dict[str, Any]:
        cached = (
            self.store.get_context_snapshot(report_id)
            if hasattr(self.store, "get_context_snapshot")
            else None
        )
        if isinstance(cached, dict) and isinstance(cached.get("payload"), dict):
            return dict(cached["payload"])

        delivery = self.store.get_delivery(report_id)
        if not isinstance(delivery, dict):
            raise ValueError("wake report not found")

        report_date = date.fromisoformat(str(delivery["report_date"]))
        includes_yesterday = bool(delivery.get("includes_yesterday_health"))
        report_kind = str(delivery["report_kind"])
        titles = {
            "morning": "早安我的少年",
            "nap": "午睡简报",
            "sleep_update": "睡眠更新",
            "new_sleep": "新睡眠",
        }
        result: dict[str, Any] = {
            "schema_version": "1.0",
            "title": titles.get(report_kind, "睡眠简报"),
            "report_id": str(delivery["id"]),
            "report_date": report_date.isoformat(),
            "report_kind": report_kind,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "timezone": self.timezone.key,
            "includes_yesterday_health": includes_yesterday,
            "health_date": (
                (report_date - timedelta(days=1)).isoformat()
                if includes_yesterday
                else None
            ),
            "sleep": delivery.get("sleep_snapshot"),
        }

        presence_id = delivery.get("presence_id")
        presence = self.store.get_presence(int(presence_id)) if presence_id else None
        if presence is None:
            result.update(
                {
                    "location_available": False,
                    "location": None,
                    "weather": WeatherContext(
                        available=False,
                        reason="location_unavailable",
                    ).as_dict(),
                }
            )
        else:
            result.update(
                {
                    "location_available": True,
                    "location": {
                        "city": presence.city,
                        "district": presence.district,
                        "country": presence.country,
                        "observed_at": presence.observed_at.isoformat(),
                    },
                    "weather": self.weather.fetch_weather(
                        presence.latitude,
                        presence.longitude,
                    ).as_dict(),
                }
            )

        if includes_yesterday:
            if self.health is None:
                raise RuntimeError("morning health service is required")
            health_date = date.fromisoformat(str(result["health_date"]))
            result.update(self.health.build(health_date))
        else:
            result.update(
                {
                    "health": {},
                    "trends": {},
                    "cycle": {},
                    "data_coverage": {},
                    "warnings": [],
                }
            )
        result["calendar_sources"] = {
            "structured": {"status": "not_connected", "events": []},
            "xiaomi_calendar": {"status": "not_connected", "events": []},
        }

        cycle = result.get("cycle")
        cycle_details = cycle if isinstance(cycle, dict) else {}
        if not hasattr(self.store, "save_context_snapshot"):
            return result
        row = self.store.save_context_snapshot(
            report_id,
            schema_version="1.0",
            source_versions={
                "cycle_engine": cycle_details.get("engine"),
                "cycle_compatible_apk_version": cycle_details.get(
                    "compatible_apk_version"
                ),
            },
            payload=result,
            snapshot_mode="native",
        )
        payload = row.get("payload") if isinstance(row, dict) else None
        if not isinstance(payload, dict):
            raise RuntimeError("saved morning context snapshot has no payload")
        return dict(payload)
