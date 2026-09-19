from __future__ import annotations

from typing import Any


def _pick(value: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return None


def _mapped(value: dict[str, Any], fields: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    out = {target: _pick(value, *aliases) for target, aliases in fields.items()}
    consumed = {alias for aliases in fields.values() for alias in aliases}
    out["unmapped"] = {k: v for k, v in value.items() if k not in consumed}
    return out


SLEEP_STATE_NAMES: dict[int, str] = {2: "deep", 3: "light", 4: "rem", 5: "awake"}
MENSTRUATION_BOUNDARY_NAMES: dict[int, str] = {1: "period_start", 2: "period_end"}
# Verified against Xiaomi Fitness body-type chart on 2026-08-22.
BODY_SHAPE_NAMES: dict[int, str] = {0: "healthy"}


def _normalize_sleep_segments(value: Any) -> Any:
    if not isinstance(value, list): return value
    normalized = []
    for segment in value:
        if not isinstance(segment, dict):
            normalized.append(segment); continue
        state_code = segment.get("state")
        normalized.append({"start_time": segment.get("start_time"), "end_time": segment.get("end_time"), "state_code": state_code, "state": SLEEP_STATE_NAMES.get(state_code) if isinstance(state_code, int) else None, "raw_segment": dict(segment)})
    return normalized


WEIGHT_FIELDS = {
    "weight_kg": ("weight", "weight_kg"), "bmi": ("bmi",), "body_fat_percent": ("body_fat_rate", "fat_rate", "body_fat_percent"), "fat_mass_kg": ("fat_mass", "body_fat_mass"), "fat_free_mass_kg": ("fat_free_body", "fat_free_mass"), "muscle_mass_kg": ("muscle_mass",), "muscle_percent": ("muscle_rate", "muscle_percent"), "skeletal_muscle_mass_kg": ("skeletal_muscle_mass",), "skeletal_muscle_percent": ("skeletal_muscle_rate", "skeletal_muscle_percent"), "body_water_percent": ("water_rate", "moisture_rate", "body_water_rate"), "body_water_mass_kg": ("body_moisture_mass", "water_mass", "body_water_mass"), "protein_percent": ("protein_rate", "protein_percent"), "protein_mass_kg": ("protein_mass",), "visceral_fat_level": ("visceral_fat", "visceral_fat_level"), "bone_mass_kg": ("bone_mass",), "bone_percent": ("bone_rate", "bone_percent"), "basal_metabolic_rate_kcal": ("basal_metabolism", "bmr", "basal_metabolic_rate"), "body_age": ("body_age",), "body_score": ("body_score",), "body_shape_code": ("somatotype", "body_shape"), "standard_weight_kg": ("standard_weight", "ideal_weight"), "standard_weight_v2_kg": ("standard_weight_v2",), "weight_control_kg": ("weight_control",), "fat_control_kg": ("fat_control",), "muscle_control_kg": ("muscle_control",), "waist_to_hip_ratio": ("whr", "waist_to_hip_ratio"), "heart_rate_bpm": ("bpm", "heart_rate"), "measurement_time": ("time",), "score_standard_type": ("score_standard_type",), "left_arm_fat_percent": ("left_arm_fat_rate",), "right_arm_fat_percent": ("right_arm_fat_rate",), "left_leg_fat_percent": ("left_leg_fat_rate",), "right_leg_fat_percent": ("right_leg_fat_rate",), "trunk_fat_percent": ("trunk_fat_rate",), "left_arm_muscle_kg": ("left_arm_muscle_mass",), "right_arm_muscle_kg": ("right_arm_muscle_mass",), "left_leg_muscle_kg": ("left_leg_muscle_mass",), "right_leg_muscle_kg": ("right_leg_muscle_mass",), "trunk_muscle_kg": ("trunk_muscle_mass",),
}

SLEEP_FIELDS = {
    "total_sleep_seconds": ("duration", "total_duration", "sleep_duration"), "sleep_score": ("sleep_score",), "deep_sleep_seconds": ("sleep_deep_duration", "deep_sleep_duration"), "light_sleep_seconds": ("sleep_light_duration", "light_sleep_duration"), "rem_sleep_seconds": ("sleep_rem_duration", "rem_sleep_duration"), "awake_seconds": ("sleep_awake_duration", "awake_duration"), "awake_count": ("awake_count", "wake_count"), "sleep_stage": ("sleep_stage",), "long_sleep_evaluation": ("long_sleep_evaluation",), "day_sleep_evaluation": ("day_sleep_evaluation",), "avg_sleep_heart_rate_bpm": ("avg_hr",), "min_sleep_heart_rate_bpm": ("min_hr",), "max_sleep_heart_rate_bpm": ("max_hr",), "avg_sleep_spo2_percent": ("avg_spo2",), "min_sleep_spo2_percent": ("min_spo2",), "max_sleep_spo2_percent": ("max_spo2",), "avg_sleep_hrv_ms": ("avg_hrv",), "min_sleep_hrv_ms": ("min_hrv",), "max_sleep_hrv_ms": ("max_hrv",), "median_sleep_hrv_ms": ("median_hrv",), "breathing_rate_per_min": ("avg_breath", "breathing_rate", "avg_breathing_rate"), "breathing_score": ("breathing_score", "breathing_quality"), "sleep_efficiency_percent": ("sleep_efficiency", "efficiency"), "snoring_seconds": ("snoring_duration",), "snoring_count": ("snoring_count",), "dream_talk_seconds": ("dream_talk_duration",), "bedtime": ("bedtime",), "wake_time": ("wake_up_time", "wake_time"), "device_bedtime": ("device_bedtime",), "device_wake_time": ("device_wake_up_time", "device_wake_time"), "sleep_trace_seconds": ("sleep_trace_duration",), "is_incomplete": ("is_uncomplete", "is_incomplete"), "timezone": ("timezone",), "sleep_algorithm_version": ("sleep_algorithm_version",), "record_version": ("version",), "protocol_time": ("protoTime", "proto_time"), "segments": ("segment_details", "segments", "items"),
}
MENSTRUATION_FIELDS = {"date_time": ("date_time",), "status_code": ("status",), "update_time": ("update_time",)}
TEMPERATURE_TREND_FIELDS = {
    "measurement_time": ("time",),
    "status_code": ("status",),
    "baseline_c": ("base_line", "baseline"),
    "base_temperature_c": ("base_temp",),
    "temperature_delta_c": ("diff_temp",),
}
SINGLE_TEMPERATURE_FIELDS = {
    "measurement_time": ("time",),
    "skin_temperature_c": ("skin_temperature",),
}
TEMPERATURE_CHARACTERISTIC_FIELDS = {
    "measurement_time": ("time",),
}


def map_health_value(key: str, value: Any) -> Any:
    if not isinstance(value, dict): return value
    if key == "weight":
        mapped = _mapped(value, WEIGHT_FIELDS)
        code = mapped.get("body_shape_code")
        mapped["body_shape"] = BODY_SHAPE_NAMES.get(code) if isinstance(code, int) else None
        return mapped
    if key == "sleep":
        mapped = _mapped(value, SLEEP_FIELDS); mapped["segments"] = _normalize_sleep_segments(mapped.get("segments")); return mapped
    if key == "menstruation":
        mapped = _mapped(value, MENSTRUATION_FIELDS); code = mapped.get("status_code"); mapped["boundary_type"] = MENSTRUATION_BOUNDARY_NAMES.get(code) if isinstance(code, int) else None; return mapped
    if key == "temperature_trend":
        return _mapped(value, TEMPERATURE_TREND_FIELDS)
    if key == "single_temperature":
        return _mapped(value, SINGLE_TEMPERATURE_FIELDS)
    if key == "temperature_characteristic":
        return _mapped(value, TEMPERATURE_CHARACTERISTIC_FIELDS)
    return dict(value)
