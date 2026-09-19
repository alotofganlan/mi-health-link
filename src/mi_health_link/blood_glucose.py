from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Protocol

MANUAL_BLOOD_GLUCOSE_KEY = "single_blood_sugar"
CONTINUOUS_BLOOD_GLUCOSE_KEY = "blood_sugar"
MANUAL_RECORD_SID = "xiaomiwear_app_manually"
DEFAULT_ZONE_OFFSET_SECONDS = 8 * 60 * 60
DEFAULT_ZONE_NAME = "Asia/Shanghai"
DEFAULT_MEASUREMENT_PERIOD = 2
UPLOAD_FITNESS_DATA_PATH = "/app/v1/data/up_fitness_data"
HISTORY_FITNESS_PATH = "/app/v1/data/get_fitness_data_by_time"


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> Any: ...


class XiaomiBloodGlucoseWriteError(RuntimeError):
    pass


class XiaomiBloodGlucoseConflictError(XiaomiBloodGlucoseWriteError):
    pass


class XiaomiBloodGlucoseVerificationError(XiaomiBloodGlucoseWriteError):
    pass


@dataclass(frozen=True)
class XiaomiUploadRequest:
    path: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class XiaomiGlucoseWriteResult:
    uploaded: bool
    verified: bool
    already_present: bool
    timestamp: int


@dataclass(frozen=True)
class XiaomiGlucoseBatchWriteResult:
    uploaded_count: int
    verified_count: int
    already_present_count: int
    timestamps: tuple[int, ...]


def build_manual_blood_glucose_upload(
    *,
    value_mmol_l: float,
    timestamp: int,
    phone_id: str = MANUAL_RECORD_SID,
    measurement_period: int = DEFAULT_MEASUREMENT_PERIOD,
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
) -> XiaomiUploadRequest:
    value = float(value_mmol_l)
    if value <= 0:
        raise ValueError("value_mmol_l must be positive")
    ts = int(timestamp)
    if ts <= 0:
        raise ValueError("timestamp must be positive")

    encoded_value = json.dumps(
        {
            "time": ts,
            "blood_sugar": value,
            "measurement_period": int(measurement_period),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return XiaomiUploadRequest(
        path=UPLOAD_FITNESS_DATA_PATH,
        payload={
            "phone_id": str(phone_id),
            "data_list": [
                {
                    "sid": MANUAL_RECORD_SID,
                    "key": MANUAL_BLOOD_GLUCOSE_KEY,
                    "time": ts,
                    "value": encoded_value,
                    "zone_offset": int(zone_offset_seconds),
                    "zone_name": str(zone_name),
                }
            ],
        },
    )


def build_continuous_blood_glucose_upload(
    *,
    sid: str,
    phone_id: str,
    samples: list[tuple[int, float]],
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
) -> XiaomiUploadRequest:
    data_list: list[dict[str, Any]] = []
    for timestamp, value_mmol_l in samples:
        ts = int(timestamp)
        value = float(value_mmol_l)
        if ts <= 0:
            raise ValueError("timestamp must be positive")
        if value <= 0:
            raise ValueError("value_mmol_l must be positive")
        data_list.append(
            {
                "sid": str(sid),
                "key": CONTINUOUS_BLOOD_GLUCOSE_KEY,
                "time": ts,
                "value": json.dumps(
                    {"time": ts, "blood_sugar": value},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "zone_offset": int(zone_offset_seconds),
                "zone_name": str(zone_name),
            }
        )
    return XiaomiUploadRequest(
        path=UPLOAD_FITNESS_DATA_PATH,
        payload={"phone_id": str(phone_id), "data_list": data_list},
    )


def upload_response_succeeded(status_code: int, json_data: Any | None) -> bool:
    if not 200 <= int(status_code) < 300:
        return False
    return not isinstance(json_data, dict) or json_data.get("code") in (None, 0)


def _response_rows(response: Any, operation: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not 200 <= int(response.status_code) < 300:
        raise XiaomiBloodGlucoseWriteError(f"{operation} HTTP {response.status_code}")
    body = response.json_data
    if not isinstance(body, dict):
        raise XiaomiBloodGlucoseWriteError(f"{operation} returned non-JSON response")
    if body.get("code") not in (None, 0):
        raise XiaomiBloodGlucoseWriteError(
            f"{operation}: {body.get('message') or f'API code {body.get("code")}' }"
        )
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    data_list = result.get("data_list") if isinstance(result.get("data_list"), list) else []
    rows = [row for row in data_list if isinstance(row, dict)]
    return rows, result


def _read_blood_glucose_key(
    *,
    client: XiaomiClientLike,
    key: str,
    start_time: int,
    end_time: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_key: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        payload: dict[str, Any] = {
            "key": str(key),
            "start_time": int(start_time),
            "end_time": int(end_time),
        }
        if next_key is not None:
            payload["next_key"] = next_key
        response = client.encrypted_post(HISTORY_FITNESS_PATH, payload)
        page, result = _response_rows(response, "read Xiaomi blood glucose")
        rows.extend(page)
        if not result.get("has_more") or not result.get("next_key"):
            break
        candidate = str(result["next_key"])
        if candidate in seen:
            raise XiaomiBloodGlucoseWriteError("Xiaomi glucose pagination cursor loop")
        seen.add(candidate)
        next_key = candidate
    return rows


def read_manual_blood_glucose(
    *,
    client: XiaomiClientLike,
    start_time: int,
    end_time: int,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    return _read_blood_glucose_key(
        client=client,
        key=MANUAL_BLOOD_GLUCOSE_KEY,
        start_time=start_time,
        end_time=end_time,
        max_pages=max_pages,
    )


def read_continuous_blood_glucose(
    *,
    client: XiaomiClientLike,
    start_time: int,
    end_time: int,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    return _read_blood_glucose_key(
        client=client,
        key=CONTINUOUS_BLOOD_GLUCOSE_KEY,
        start_time=start_time,
        end_time=end_time,
        max_pages=max_pages,
    )


def _parse_cloud_glucose(row: dict[str, Any]) -> tuple[int, float] | None:
    raw = row.get("value")
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        value = raw
    if not isinstance(value, dict):
        return None
    try:
        timestamp = int(value.get("time", row.get("time")))
        glucose = float(value["blood_sugar"])
    except (KeyError, TypeError, ValueError):
        return None
    return timestamp, glucose


def _find_timestamp_value(
    rows: list[dict[str, Any]],
    *,
    timestamp: int,
    value_mmol_l: float,
    tolerance: float = 0.01,
) -> tuple[bool, bool]:
    same_timestamp = False
    for row in rows:
        parsed = _parse_cloud_glucose(row)
        if parsed is None:
            continue
        row_timestamp, row_value = parsed
        if row_timestamp != int(timestamp):
            continue
        same_timestamp = True
        if abs(row_value - float(value_mmol_l)) <= tolerance:
            return True, True
    return same_timestamp, False


def write_continuous_blood_glucose_batch(
    *,
    client: XiaomiClientLike,
    sid: str,
    phone_id: str,
    samples: list[tuple[int, float]],
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
    verification_attempts: int = 3,
    verification_delay_seconds: float = 1.0,
) -> XiaomiGlucoseBatchWriteResult:
    if verification_attempts <= 0:
        raise ValueError("verification_attempts must be positive")
    normalized: list[tuple[int, float]] = []
    seen_timestamps: set[int] = set()
    for timestamp, value_mmol_l in samples:
        ts = int(timestamp)
        value = float(value_mmol_l)
        if ts <= 0:
            raise ValueError("timestamp must be positive")
        if value <= 0:
            raise ValueError("value_mmol_l must be positive")
        if ts in seen_timestamps:
            raise ValueError("continuous glucose sample timestamps must be unique")
        seen_timestamps.add(ts)
        normalized.append((ts, value))
    if not normalized:
        raise ValueError("samples must not be empty")

    timestamps = tuple(ts for ts, _ in normalized)
    window_start = max(0, min(timestamps) - 300)
    window_end = max(timestamps) + 300
    existing = read_continuous_blood_glucose(
        client=client,
        start_time=window_start,
        end_time=window_end,
    )

    missing: list[tuple[int, float]] = []
    already_present_count = 0
    for ts, value in normalized:
        same_timestamp, same_value = _find_timestamp_value(
            existing,
            timestamp=ts,
            value_mmol_l=value,
        )
        if same_value:
            already_present_count += 1
        elif same_timestamp:
            raise XiaomiBloodGlucoseConflictError(
                "A different Xiaomi continuous glucose value already exists at a target timestamp"
            )
        else:
            missing.append((ts, value))

    if missing:
        request = build_continuous_blood_glucose_upload(
            sid=sid,
            phone_id=phone_id,
            samples=missing,
            zone_offset_seconds=zone_offset_seconds,
            zone_name=zone_name,
        )
        upload = client.encrypted_post(request.path, request.payload)
        if not upload_response_succeeded(upload.status_code, upload.json_data):
            body = upload.json_data if isinstance(upload.json_data, dict) else {}
            raise XiaomiBloodGlucoseWriteError(
                "Xiaomi continuous glucose upload rejected: "
                f"HTTP {upload.status_code}, code={body.get('code')}, message={body.get('message')}"
            )

    for attempt in range(verification_attempts):
        verified_rows = read_continuous_blood_glucose(
            client=client,
            start_time=window_start,
            end_time=window_end,
        )
        verified_count = sum(
            1
            for ts, value in normalized
            if _find_timestamp_value(
                verified_rows,
                timestamp=ts,
                value_mmol_l=value,
            )[1]
        )
        if verified_count == len(normalized):
            return XiaomiGlucoseBatchWriteResult(
                uploaded_count=len(missing),
                verified_count=verified_count,
                already_present_count=already_present_count,
                timestamps=timestamps,
            )
        if attempt + 1 < verification_attempts:
            time.sleep(max(0.0, float(verification_delay_seconds)))

    raise XiaomiBloodGlucoseVerificationError(
        "Xiaomi accepted the continuous glucose upload but not all records could be read back"
    )


def write_manual_blood_glucose(
    *,
    client: XiaomiClientLike,
    value_mmol_l: float,
    timestamp: int,
    phone_id: str = MANUAL_RECORD_SID,
    measurement_period: int = DEFAULT_MEASUREMENT_PERIOD,
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
    verification_attempts: int = 3,
    verification_delay_seconds: float = 1.0,
) -> XiaomiGlucoseWriteResult:
    if verification_attempts <= 0:
        raise ValueError("verification_attempts must be positive")
    ts = int(timestamp)
    value = float(value_mmol_l)
    window_start = max(0, ts - 300)
    window_end = ts + 300

    existing = read_manual_blood_glucose(
        client=client,
        start_time=window_start,
        end_time=window_end,
    )
    same_timestamp, same_value = _find_timestamp_value(
        existing,
        timestamp=ts,
        value_mmol_l=value,
    )
    if same_value:
        return XiaomiGlucoseWriteResult(
            uploaded=False,
            verified=True,
            already_present=True,
            timestamp=ts,
        )
    if same_timestamp:
        raise XiaomiBloodGlucoseConflictError(
            "A different Xiaomi glucose value already exists at this timestamp"
        )

    request = build_manual_blood_glucose_upload(
        value_mmol_l=value,
        timestamp=ts,
        phone_id=phone_id,
        measurement_period=measurement_period,
        zone_offset_seconds=zone_offset_seconds,
        zone_name=zone_name,
    )
    upload = client.encrypted_post(request.path, request.payload)
    if not upload_response_succeeded(upload.status_code, upload.json_data):
        body = upload.json_data if isinstance(upload.json_data, dict) else {}
        raise XiaomiBloodGlucoseWriteError(
            "Xiaomi glucose upload rejected: "
            f"HTTP {upload.status_code}, code={body.get('code')}, message={body.get('message')}"
        )

    for attempt in range(verification_attempts):
        verified_rows = read_manual_blood_glucose(
            client=client,
            start_time=window_start,
            end_time=window_end,
        )
        _, verified = _find_timestamp_value(
            verified_rows,
            timestamp=ts,
            value_mmol_l=value,
        )
        if verified:
            return XiaomiGlucoseWriteResult(
                uploaded=True,
                verified=True,
                already_present=False,
                timestamp=ts,
            )
        if attempt + 1 < verification_attempts:
            time.sleep(max(0.0, float(verification_delay_seconds)))

    raise XiaomiBloodGlucoseVerificationError(
        "Xiaomi accepted the upload but the record could not be read back"
    )
