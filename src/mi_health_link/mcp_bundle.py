from __future__ import annotations

from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Callable

from .mcp_coverage import (
    classify_coverage,
    ranges_cover_window,
    successful_range_check_at_or_after,
)


SYNC_POLICIES = {"never", "if_needed", "always"}
QUERY_MODES = {"summary", "series", "smart"}
NUMERIC_FIELDS = {
    "heart_rate": "bpm",
    "glucose": "glucose_mmol_l",
    "pai": "total_pai",
    "resting_heart_rate": "bpm",
    "running_ability_index": "running_ability_index",
    "spo2": "percent",
    "training_load": "current_day_train_load",
    "vitality": "latest_accumulated_vitality",
    "weight": "weight_kg",
    "single_temperature": "skin_temperature_c",
}


class HealthBundleService:
    """Read multiple normalized health metrics with optional one-shot refresh."""

    def __init__(self, *, reader: Any, sync_runner: Callable[[], dict[str, object]]) -> None:
        self.reader = reader
        self.sync_runner = sync_runner

    def _coverage(self, query: dict[str, Any]) -> dict[str, Any]:
        metric = str(query["metric"])
        start_at = str(query["start_at"])
        end_at = str(query["end_at"])
        local = self.reader.coverage(metric, start_at=start_at, end_at=end_at)
        source_checked_at = None
        sync_failed = False
        if hasattr(self.reader, "source_check"):
            persisted = self.reader.source_check(metric)
            if isinstance(persisted, dict):
                checked_at = persisted.get("source_checked_at")
                if isinstance(checked_at, str):
                    source_checked_at = checked_at
                sync_failed = persisted.get("source_check_status") == "failed"
        checks: list[dict[str, Any]] = []
        if hasattr(self.reader, "range_checks"):
            loaded = self.reader.range_checks(metric, start_at=start_at, end_at=end_at)
            if isinstance(loaded, list):
                checks = [row for row in loaded if isinstance(row, dict)]
        range_complete = ranges_cover_window(checks, start_at, end_at)
        if (
            sync_failed
            and range_complete
            and successful_range_check_at_or_after(checks, source_checked_at)
        ):
            sync_failed = False
        result = classify_coverage(
            metric=metric,
            start_at=start_at,
            end_at=end_at,
            count=int(local.get("count") or 0),
            first_at=local.get("first_at") if isinstance(local.get("first_at"), str) else None,
            latest_at=local.get("latest_at") if isinstance(local.get("latest_at"), str) else None,
            source_checked_at=source_checked_at,
            sync_failed=sync_failed,
            range_checked_complete=range_complete,
        )
        if checks:
            result["checked_ranges"] = checks
        return result

    @staticmethod
    def _should_sync(coverage: dict[str, Any]) -> bool:
        status = coverage.get("status")
        if status in {"not_synced", "partial", "sync_failed"}:
            return True
        if status != "source_empty":
            return False
        if coverage.get("range_checked_complete") is True:
            return False
        next_recheck = coverage.get("next_recheck_at")
        if not isinstance(next_recheck, str):
            return True
        try:
            when = datetime.fromisoformat(next_recheck.replace("Z", "+00:00"))
        except ValueError:
            return True
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when <= datetime.now(timezone.utc)

    @staticmethod
    def _nested_numeric(row: dict[str, Any], field: str) -> float | None:
        direct = row.get(field)
        if isinstance(direct, (int, float)):
            return float(direct)
        for container_name in ("metrics", "value"):
            container = row.get(container_name)
            if isinstance(container, dict):
                value = container.get(field)
                if isinstance(value, (int, float)):
                    return float(value)
        return None

    @staticmethod
    def _steps_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        chosen: dict[str, dict[str, Any]] = {}
        duplicates_removed = 0
        for index, row in enumerate(records):
            timestamp = str(row.get("measured_at") or f"__row_{index}")
            current = chosen.get(timestamp)
            if current is None:
                chosen[timestamp] = row
                continue
            duplicates_removed += 1
            current_phone = str(current.get("sid") or "").startswith("hlth.gen_")
            candidate_phone = str(row.get("sid") or "").startswith("hlth.gen_")
            if current_phone and not candidate_phone:
                chosen[timestamp] = row

        resolved = sorted(chosen.values(), key=lambda row: str(row.get("measured_at") or ""))
        steps = sum(HealthBundleService._nested_numeric(row, "steps") or 0 for row in resolved)
        distance = sum(HealthBundleService._nested_numeric(row, "distance") or 0 for row in resolved)
        calories = sum(HealthBundleService._nested_numeric(row, "calories") or 0 for row in resolved)
        return {
            "count": len(resolved),
            "raw_count": len(records),
            "latest_record": resolved[-1] if resolved else None,
            "steps": int(round(steps)),
            "distance_m": int(round(distance)),
            "calories_kcal": calories,
            "source_resolution": {
                "policy": "watch_over_phone_same_timestamp",
                "duplicates_removed": duplicates_removed,
            },
        }

    @staticmethod
    def _sleep_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        by_start: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(records):
            start = str(row.get("start_at") or f"__row_{index}")
            existing = by_start.get(start)
            if existing is None:
                by_start[start] = row
                continue

            def quality(candidate: dict[str, Any]) -> tuple[int, str]:
                raw = candidate.get("raw")
                metrics = raw.get("metrics") if isinstance(raw, dict) else None
                incomplete = bool(metrics.get("is_incomplete")) if isinstance(metrics, dict) else False
                return (0 if incomplete else 1, str(candidate.get("end_at") or ""))

            if quality(row) > quality(existing):
                by_start[start] = row

        sessions = sorted(by_start.values(), key=lambda row: str(row.get("start_at") or ""))
        total_minutes = 0.0
        incomplete_count = 0
        for row in sessions:
            raw = row.get("raw")
            metrics = raw.get("metrics") if isinstance(raw, dict) else None
            if not isinstance(metrics, dict):
                continue
            value = metrics.get("total_sleep_seconds")
            if isinstance(value, (int, float)):
                total_minutes += float(value)
            if metrics.get("is_incomplete") is True:
                incomplete_count += 1

        return {
            "count": len(sessions),
            "session_count": len(sessions),
            "raw_session_count": len(records),
            "total_sleep_minutes": int(round(total_minutes)),
            "incomplete_session_count": incomplete_count,
            "latest_session": sessions[-1] if sessions else None,
        }

    @staticmethod
    def _temperature_trend_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {"count": len(records)}
        if not records:
            return summary
        latest = records[-1]
        summary["latest_record"] = latest
        baseline = HealthBundleService._nested_numeric(latest, "baseline_c")
        base_temperature = HealthBundleService._nested_numeric(latest, "base_temperature_c")
        latest_delta = HealthBundleService._nested_numeric(latest, "temperature_delta_c")
        status = HealthBundleService._nested_numeric(latest, "status_code")
        if baseline is not None:
            summary["latest_baseline_c"] = baseline
        if base_temperature is not None:
            summary["latest_base_temperature_c"] = base_temperature
        if latest_delta is not None:
            summary["latest_delta_c"] = latest_delta
        if status is not None:
            summary["status_code"] = int(status)
        deltas = [
            value
            for row in records
            if (value := HealthBundleService._nested_numeric(row, "temperature_delta_c")) is not None
        ]
        if deltas:
            summary["delta_min_c"] = min(deltas)
            summary["delta_max_c"] = max(deltas)
            summary["delta_avg_c"] = fmean(deltas)
        return summary

    @staticmethod
    def _diet_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "count": len(records),
            "meal_count": len(records),
            "food_item_count": sum(
                len(row.get("diet_food_items") or [])
                for row in records
                if isinstance(row.get("diet_food_items"), list)
            ),
        }
        for field, output in (
            ("total_calorie_kcal", "total_calories_kcal"),
            ("total_protein_g", "total_protein_g"),
            ("total_carbohydrate_g", "total_carbohydrate_g"),
            ("total_fat_g", "total_fat_g"),
        ):
            values = [
                row.get(field)
                for row in records
                if isinstance(row.get(field), (int, float))
                and not isinstance(row.get(field), bool)
            ]
            if values:
                summary[output] = sum(values)
        if records:
            summary["latest_record"] = records[-1]
        return summary

    @staticmethod
    def _abnormal_heartbeat_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        total_seconds = 0
        for row in records:
            start = HealthBundleService._nested_numeric(row, "start_time")
            end = HealthBundleService._nested_numeric(row, "end_time")
            if start is not None and end is not None and end >= start:
                total_seconds += int(round(end - start))
        return {
            "count": len(records),
            "event_count": len(records),
            "total_event_seconds": total_seconds,
            "latest_event": records[-1] if records else None,
            "interpretation": "observed_events_only",
        }

    @staticmethod
    def _calories_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        values = [
            value
            for row in records
            if (value := HealthBundleService._nested_numeric(row, "calories"))
            is not None
        ]
        summary: dict[str, Any] = {
            "count": len(records),
            "calories_kcal": sum(values),
        }
        if records:
            summary["latest_record"] = records[-1]
        return summary

    @staticmethod
    def _valid_stand_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(records),
            "stand_period_count": len(records),
            "latest_record": records[-1] if records else None,
        }

    @staticmethod
    def _intensity_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(records),
            "active_minutes": len(records),
            "latest_record": records[-1] if records else None,
        }

    @staticmethod
    def _workout_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        def total(field: str) -> float:
            return sum(
                value
                for row in records
                if (value := HealthBundleService._nested_numeric(row, field)) is not None
            )

        duration_seconds = total("duration_s")
        distance_m = total("distance_m")
        calories_kcal = total("calories_kcal")
        return {
            "count": len(records),
            "workout_count": len(records),
            "total_duration_minutes": duration_seconds / 60,
            "total_distance_m": distance_m,
            "total_calories_kcal": calories_kcal,
            "latest_workout": records[-1] if records else None,
        }

    @staticmethod
    def _summary(metric: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        if metric == "abnormal_heart_beat":
            return HealthBundleService._abnormal_heartbeat_summary(records)
        if metric == "calories":
            return HealthBundleService._calories_summary(records)
        if metric == "diet":
            return HealthBundleService._diet_summary(records)
        if metric == "steps":
            return HealthBundleService._steps_summary(records)
        if metric == "intensity":
            return HealthBundleService._intensity_summary(records)
        if metric == "sleep":
            return HealthBundleService._sleep_summary(records)
        if metric == "temperature_trend":
            return HealthBundleService._temperature_trend_summary(records)
        if metric == "valid_stand":
            return HealthBundleService._valid_stand_summary(records)
        if metric == "workout":
            return HealthBundleService._workout_summary(records)

        summary: dict[str, Any] = {"count": len(records)}
        if not records:
            return summary

        summary["latest_record"] = records[-1]
        numeric_field = NUMERIC_FIELDS.get(metric)
        if numeric_field is None and metric == "stress":
            numeric_field = "stress"
        if numeric_field is not None:
            values = [
                value
                for row in records
                if (value := HealthBundleService._nested_numeric(row, numeric_field)) is not None
            ]
            if values:
                summary.update(
                    {
                        "field": numeric_field,
                        "latest": values[-1],
                        "min": min(values),
                        "max": max(values),
                        "avg": fmean(values),
                    }
                )

        if metric == "weight":
            latest = records[-1]
            summary["latest_body_composition"] = {
                key: latest.get(key)
                for key in (
                    "measured_at",
                    "weight_kg",
                    "bmi",
                    "body_fat_pct",
                    "muscle_mass_kg",
                    "bone_mass_kg",
                    "body_water_pct",
                    "basal_metabolism_kcal",
                )
                if latest.get(key) is not None
            }
        return summary

    def _read(self, queries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        results: list[dict[str, Any]] = []
        coverages: list[dict[str, Any]] = []
        for query in queries:
            metric = str(query["metric"])
            start_at = str(query["start_at"])
            end_at = str(query["end_at"])
            mode = str(query.get("mode") or "summary")
            if mode not in QUERY_MODES:
                raise ValueError("mode must be one of summary, series, smart")

            payload = self.reader.query_all(metric, start_at=start_at, end_at=end_at)
            records = payload.get("records") if isinstance(payload, dict) else []
            if not isinstance(records, list):
                records = []
            clean_records = [row for row in records if isinstance(row, dict)]
            coverage = self._coverage(query)
            coverages.append(coverage)

            item: dict[str, Any] = {
                "metric": metric,
                "start_at": start_at,
                "end_at": end_at,
                "mode": mode,
                "coverage": coverage,
            }
            if mode == "series":
                item["count"] = len(clean_records)
                item["records"] = clean_records
            else:
                item["summary"] = self._summary(metric, clean_records)
                if mode == "smart" and coverage.get("status") not in {"complete", "source_empty"}:
                    item["records"] = clean_records
            results.append(item)
        return results, coverages

    def get_bundle(
        self,
        queries: list[dict[str, Any]],
        *,
        sync_policy: str = "if_needed",
    ) -> dict[str, Any]:
        if sync_policy not in SYNC_POLICIES:
            raise ValueError("sync_policy must be one of never, if_needed, always")
        if not queries:
            raise ValueError("queries must contain at least one metric query")

        initial_results, coverages = self._read(queries)
        sync_needed = sync_policy == "always" or (
            sync_policy == "if_needed" and any(self._should_sync(row) for row in coverages)
        )
        if not sync_needed:
            return {
                "sync_policy": sync_policy,
                "sync": {"performed": False, "reason": None},
                "results": initial_results,
            }

        reason = "always" if sync_policy == "always" else "coverage"
        sync_result = self.sync_runner()
        final_results, _ = self._read(queries)
        return {
            "sync_policy": sync_policy,
            "sync": {"performed": True, "reason": reason, "result": sync_result},
            "results": final_results,
        }
