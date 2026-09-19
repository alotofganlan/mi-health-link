from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .supabase_store import GENERIC_NORMALIZED_KEYS


@dataclass(frozen=True)
class MetricSource:
    table: str
    time_column: str
    key_filter: bool = False
    source: str = "xiaomi"


METRIC_SOURCES: dict[str, MetricSource] = {
    **{
        metric: MetricSource("health_records", "measured_at", key_filter=True)
        for metric in GENERIC_NORMALIZED_KEYS
    },
    "temperature_characteristic": MetricSource("health_records", "measured_at", key_filter=True),
    "heart_rate": MetricSource("heart_rate_samples", "measured_at"),
    "spo2": MetricSource("spo2_samples", "measured_at"),
    "intensity": MetricSource("intensity_samples", "measured_at"),
    "weight": MetricSource("body_measurements", "measured_at"),
    "menstruation": MetricSource("menstrual_records", "start_at"),
    "sleep": MetricSource("sleep_sessions", "start_at"),
    "glucose": MetricSource("glucose_samples", "measured_at", source="nightscout"),
    "diet": MetricSource("diet_meals", "recorded_at"),
    "workout": MetricSource("workouts", "start_at"),
}


@dataclass
class NormalizedHealthReader:
    url: str
    service_role_key: str
    transport: httpx.BaseTransport | None = None

    def _headers(self, *, count_exact: bool = False) -> dict[str, str]:
        headers = {
            "apikey": self.service_role_key,
            "Accept": "application/json",
        }
        if count_exact:
            headers["Prefer"] = "count=exact"
        if not self.service_role_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.service_role_key}"
        return headers

    @staticmethod
    def _source(metric: str) -> MetricSource:
        source = METRIC_SOURCES.get(metric)
        if source is None:
            raise ValueError(f"Unsupported metric: {metric}")
        return source

    @staticmethod
    def _window_params(
        metric: str,
        source: MetricSource,
        *,
        start_at: str,
        end_at: str,
    ) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = [
            ("source", f"eq.{source.source}"),
            (source.time_column, f"gte.{start_at}"),
            (source.time_column, f"lte.{end_at}"),
        ]
        if source.key_filter:
            params.append(("key", f"eq.{metric}"))
        return params

    def query(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
        limit: int = 500,
        offset: int = 0,
    ) -> dict[str, Any]:
        source = self._source(metric)
        if limit < 1 or limit > 5000:
            raise ValueError("limit must be between 1 and 5000")
        if offset < 0:
            raise ValueError("offset must not be negative")

        params: list[tuple[str, str]] = [
            ("select", "*,diet_food_items(*)" if metric == "diet" else "*"),
            *self._window_params(metric, source, start_at=start_at, end_at=end_at),
            ("order", f"{source.time_column}.asc"),
            ("limit", str(limit)),
            ("offset", str(offset)),
        ]

        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/{source.table}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()

        records = payload if isinstance(payload, list) else []
        return {
            "metric": metric,
            "count": len(records),
            "records": records,
        }

    def query_all(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
        page_size: int = 1000,
        max_records: int = 20000,
    ) -> dict[str, Any]:
        """Read a complete time window using internal pagination."""
        if page_size < 1 or page_size > 5000:
            raise ValueError("page_size must be between 1 and 5000")
        if max_records < page_size:
            raise ValueError("max_records must be at least page_size")

        records: list[dict[str, Any]] = []
        offset = 0
        while offset < max_records:
            page = self.query(
                metric,
                start_at=start_at,
                end_at=end_at,
                limit=min(page_size, max_records - offset),
                offset=offset,
            )
            rows = page.get("records") if isinstance(page, dict) else []
            if not isinstance(rows, list):
                rows = []
            clean_rows = [row for row in rows if isinstance(row, dict)]
            records.extend(clean_rows)
            if len(clean_rows) < page_size:
                break
            offset += len(clean_rows)

        truncated = len(records) >= max_records
        return {
            "metric": metric,
            "count": len(records),
            "records": records,
            "truncated": truncated,
        }

    def query_daily_summaries(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
        timezone: str,
    ) -> dict[str, Any]:
        if metric != "heart_rate":
            raise ValueError(f"Daily summaries are not supported for: {metric}")
        headers = self._headers()
        headers["Content-Type"] = "application/json"
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.post(
                f"{self.url.rstrip('/')}/rest/v1/rpc/get_morning_heart_rate_daily",
                headers=headers,
                json={
                    "p_start_at": start_at,
                    "p_end_at": end_at,
                    "p_timezone": timezone,
                },
            )
            response.raise_for_status()
            payload = response.json()
        rows = payload if isinstance(payload, list) else []
        days: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("day"), str):
                continue
            summary: dict[str, Any] = {
                "count": int(row.get("sample_count") or 0),
                "field": "bpm",
            }
            for source, target in (
                ("avg_bpm", "avg"),
                ("min_bpm", "min"),
                ("max_bpm", "max"),
            ):
                value = row.get(source)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    summary[target] = float(value)
            days.append({"day": row["day"], "summary": summary})
        return {"metric": metric, "days": days}

    @staticmethod
    def _content_range_total(response: httpx.Response, fallback: int) -> int:
        content_range = response.headers.get("Content-Range", "")
        if "/" not in content_range:
            return fallback
        total = content_range.rsplit("/", 1)[-1]
        try:
            return int(total)
        except ValueError:
            return fallback

    def coverage(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
    ) -> dict[str, Any]:
        """Return lightweight first/latest/count coverage for a normalized metric."""
        source = self._source(metric)
        base_params = self._window_params(
            metric,
            source,
            start_at=start_at,
            end_at=end_at,
        )
        endpoint = f"{self.url.rstrip('/')}/rest/v1/{source.table}"

        def fetch_edge(order: str) -> tuple[httpx.Response, list[dict[str, Any]]]:
            params = [
                ("select", source.time_column),
                *base_params,
                ("order", f"{source.time_column}.{order}"),
                ("limit", "1"),
            ]
            with httpx.Client(timeout=30.0, transport=self.transport) as client:
                response = client.get(
                    endpoint,
                    params=params,
                    headers=self._headers(count_exact=True),
                )
                response.raise_for_status()
                payload = response.json()
            rows = payload if isinstance(payload, list) else []
            return response, [row for row in rows if isinstance(row, dict)]

        first_response, first_rows = fetch_edge("asc")
        _, latest_rows = fetch_edge("desc")
        count = self._content_range_total(first_response, len(first_rows))
        first_at = first_rows[0].get(source.time_column) if first_rows else None
        latest_at = latest_rows[0].get(source.time_column) if latest_rows else None

        return {
            "metric": metric,
            "count": count,
            "first_at": first_at,
            "latest_at": latest_at,
        }

    def range_checks(
        self,
        metric: str,
        *,
        start_at: str,
        end_at: str,
    ) -> list[dict[str, Any]]:
        """Load durable Xiaomi source-check intervals overlapping a requested window."""
        source = self._source(metric)
        if source.source != "xiaomi":
            return []
        with httpx.Client(timeout=30.0, transport=self.transport) as client:
            response = client.get(
                f"{self.url.rstrip('/')}/rest/v1/xiaomi_coverage_ranges",
                params={
                    "select": "range_start,range_end,checked_at,status",
                    "source": "eq.xiaomi",
                    "key": f"eq.{metric}",
                    "range_end": f"gte.{start_at}",
                    "range_start": f"lte.{end_at}",
                    "order": "range_start.asc",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            return []
        return [dict(row) for row in payload if isinstance(row, dict)]

    def source_check(self, metric: str) -> dict[str, Any] | None:
        """Load the last durable Xiaomi source check for a Xiaomi-backed metric."""
        source = self._source(metric)
        if source.source != "xiaomi":
            return None
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
                    "key": f"eq.{metric}",
                    "limit": "1",
                },
                headers=self._headers(),
            )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return None
        row = payload[0]
        return {
            "source_checked_at": row.get("source_checked_at"),
            "source_check_status": row.get("source_check_status"),
            "source_latest_at": row.get("source_latest_at"),
            "next_recheck_at": row.get("next_recheck_at"),
            "empty_check_count": int(row.get("empty_check_count") or 0),
        }
