from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from typing import Any, Iterable, Protocol

from .config import load_credentials, load_settings
from .xiaomi import XiaomiHealthClient


INTENSITY_KEY = "intensity"
UPLOAD_FITNESS_DATA_PATH = "/app/v1/data/up_fitness_data"
HISTORY_FITNESS_DATA_PATH = "/app/v1/data/get_fitness_data_by_time"
DEFAULT_ZONE_OFFSET_SECONDS = 8 * 60 * 60
DEFAULT_ZONE_NAME = "Asia/Shanghai"
MANUAL_RECORD_SID = "xiaomiwear_app_manually"


@dataclass(frozen=True)
class XiaomiIntensityUploadRequest:
    path: str
    payload: dict[str, object]


def _minute(timestamp: int) -> int:
    return int(timestamp) // 60 * 60


def qualifying_intensity_minutes(
    samples: Iterable[tuple[int, int | float]],
    *,
    start_time: int,
    end_time: int,
    maximum_heart_rate: int | float,
    existing_minutes: set[int] | None = None,
) -> tuple[int, ...]:
    start = int(start_time)
    end = int(end_time)
    maximum = float(maximum_heart_rate)
    if end < start:
        raise ValueError("end_time must not be before start_time")
    if maximum <= 0:
        raise ValueError("maximum_heart_rate must be positive")

    existing = {_minute(value) for value in (existing_minutes or set())}
    threshold = maximum * 0.64
    qualifying = {
        _minute(timestamp)
        for timestamp, bpm in samples
        if start <= int(timestamp) <= end
        and isinstance(bpm, (int, float))
        and not isinstance(bpm, bool)
        and float(bpm) > threshold
    }
    return tuple(sorted(qualifying - existing))


def build_intensity_upload(
    *,
    timestamps: Iterable[int],
    sid: str = MANUAL_RECORD_SID,
    phone_id: str = MANUAL_RECORD_SID,
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
) -> XiaomiIntensityUploadRequest:
    minutes = sorted({_minute(timestamp) for timestamp in timestamps})
    if not minutes:
        raise ValueError("timestamps must contain at least one minute")
    if any(timestamp <= 0 for timestamp in minutes):
        raise ValueError("timestamps must be positive")

    data_list = [
        {
            "sid": str(sid),
            "key": INTENSITY_KEY,
            "time": timestamp,
            "value": json.dumps(
                {"time": timestamp},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "zone_offset": int(zone_offset_seconds),
            "zone_name": str(zone_name),
        }
        for timestamp in minutes
    ]
    return XiaomiIntensityUploadRequest(
        path=UPLOAD_FITNESS_DATA_PATH,
        payload={"phone_id": str(phone_id), "data_list": data_list},
    )


class XiaomiClientLike(Protocol):
    def encrypted_post(self, path: str, payload: dict[str, Any]) -> Any: ...


class XiaomiIntensityWriteError(RuntimeError):
    pass


class XiaomiIntensityVerificationError(XiaomiIntensityWriteError):
    pass


@dataclass(frozen=True)
class XiaomiIntensityWriteResult:
    uploaded_count: int
    verified_count: int
    already_present_count: int
    timestamps: tuple[int, ...]


def _response_result(response: Any, operation: str) -> dict[str, Any]:
    if not 200 <= int(response.status_code) < 300:
        raise XiaomiIntensityWriteError(f"{operation} HTTP {response.status_code}")
    body = response.json_data
    if not isinstance(body, dict):
        raise XiaomiIntensityWriteError(f"{operation} returned non-JSON response")
    if body.get("code") not in (None, 0):
        raise XiaomiIntensityWriteError(
            f"{operation}: {body.get('message') or f'API code {body.get("code")}' }"
        )
    return body.get("result") if isinstance(body.get("result"), dict) else {}


def _response_rows(response: Any, operation: str) -> list[dict[str, Any]]:
    result = _response_result(response, operation)
    data_list = result.get("data_list") if isinstance(result.get("data_list"), list) else []
    return [row for row in data_list if isinstance(row, dict)]


def _read_metric_rows(
    *,
    client: XiaomiClientLike,
    key: str,
    start_time: int,
    end_time: int,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_key: str | None = None
    seen: set[str] = set()
    for _ in range(max_pages):
        payload: dict[str, Any] = {
            "key": key,
            "start_time": int(start_time),
            "end_time": int(end_time),
        }
        if next_key is not None:
            payload["next_key"] = next_key
        response = client.encrypted_post(HISTORY_FITNESS_DATA_PATH, payload)
        result = _response_result(response, f"read Xiaomi {key}")
        data_list = result.get("data_list") if isinstance(result.get("data_list"), list) else []
        rows.extend(row for row in data_list if isinstance(row, dict))
        candidate = result.get("next_key")
        if not result.get("has_more") or not candidate:
            return rows
        candidate = str(candidate)
        if candidate in seen:
            raise XiaomiIntensityWriteError(f"Xiaomi {key} pagination cursor loop")
        seen.add(candidate)
        next_key = candidate
    raise XiaomiIntensityWriteError(f"Xiaomi {key} exceeded {max_pages} pages")


def _read_intensity_minutes(
    *,
    client: XiaomiClientLike,
    start_time: int,
    end_time: int,
) -> set[int]:
    rows = _read_metric_rows(
        client=client,
        key=INTENSITY_KEY,
        start_time=start_time,
        end_time=end_time,
    )
    minutes: set[int] = set()
    for row in rows:
        try:
            timestamp = int(row["time"])
        except (KeyError, TypeError, ValueError):
            continue
        minutes.add(_minute(timestamp))
    return minutes


def write_intensity_minutes(
    *,
    client: XiaomiClientLike,
    timestamps: Iterable[int],
    start_time: int,
    end_time: int,
    sid: str = MANUAL_RECORD_SID,
    phone_id: str = MANUAL_RECORD_SID,
    zone_offset_seconds: int = DEFAULT_ZONE_OFFSET_SECONDS,
    zone_name: str = DEFAULT_ZONE_NAME,
) -> XiaomiIntensityWriteResult:
    requested = tuple(sorted({_minute(timestamp) for timestamp in timestamps}))
    if not requested:
        raise ValueError("timestamps must contain at least one minute")

    before = _read_intensity_minutes(
        client=client,
        start_time=start_time,
        end_time=end_time,
    )
    pending = tuple(timestamp for timestamp in requested if timestamp not in before)
    if pending:
        request = build_intensity_upload(
            timestamps=pending,
            sid=sid,
            phone_id=phone_id,
            zone_offset_seconds=zone_offset_seconds,
            zone_name=zone_name,
        )
        response = client.encrypted_post(request.path, request.payload)
        _response_rows(response, "upload Xiaomi intensity")

    after = _read_intensity_minutes(
        client=client,
        start_time=start_time,
        end_time=end_time,
    )
    missing = tuple(timestamp for timestamp in pending if timestamp not in after)
    if missing:
        rendered = ", ".join(str(timestamp) for timestamp in missing)
        raise XiaomiIntensityVerificationError(
            f"uploaded Xiaomi intensity minutes were not readable: {rendered}"
        )
    return XiaomiIntensityWriteResult(
        uploaded_count=len(pending),
        verified_count=len(pending),
        already_present_count=len(requested) - len(pending),
        timestamps=requested,
    )


def _heart_rate_samples(rows: Iterable[dict[str, Any]]) -> list[tuple[int, float]]:
    samples: list[tuple[int, float]] = []
    for row in rows:
        value = row.get("value")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                continue
        if not isinstance(value, dict):
            continue
        bpm = value.get("bpm", value.get("heart_rate", value.get("value")))
        try:
            timestamp = int(row["time"])
            numeric_bpm = float(bpm)
        except (KeyError, TypeError, ValueError):
            continue
        samples.append((timestamp, numeric_bpm))
    return samples


def repair_intensity_from_heart_rate(
    *,
    client: XiaomiClientLike,
    start_time: int,
    end_time: int,
    read_start_time: int,
    read_end_time: int,
    maximum_heart_rate: int | float,
    confirm_write: bool = False,
) -> dict[str, object]:
    """Preview or repair missing Xiaomi intensity minutes from minute heart-rate samples."""
    heart_rows = _read_metric_rows(
        client=client,
        key="heart_rate",
        start_time=read_start_time,
        end_time=read_end_time,
    )
    existing = _read_intensity_minutes(
        client=client,
        start_time=read_start_time,
        end_time=read_end_time,
    )
    all_qualifying = qualifying_intensity_minutes(
        _heart_rate_samples(heart_rows),
        start_time=start_time,
        end_time=end_time,
        maximum_heart_rate=maximum_heart_rate,
    )
    missing = tuple(timestamp for timestamp in all_qualifying if timestamp not in existing)
    result: dict[str, object] = {
        "status": "preview",
        "threshold_bpm": float(maximum_heart_rate) * 0.64,
        "qualifying_count": len(all_qualifying),
        "already_present_count": len(all_qualifying) - len(missing),
        "missing_count": len(missing),
        "missing_timestamps": list(missing),
    }
    if not confirm_write or not missing:
        if confirm_write:
            result["status"] = "completed"
            result["uploaded_count"] = 0
            result["verified_count"] = 0
        return result

    written = write_intensity_minutes(
        client=client,
        timestamps=missing,
        start_time=read_start_time,
        end_time=read_end_time,
    )
    result.update({
        "status": "completed",
        "uploaded_count": written.uploaded_count,
        "verified_count": written.verified_count,
    })
    return result


def repair_intensity_window(
    *,
    start_at: str,
    end_at: str,
    maximum_heart_rate: int | float,
    confirm_write: bool = False,
) -> dict[str, object]:
    """Public entry point using ISO-8601 timestamps; preview-only unless confirmed."""
    try:
        start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("start_at and end_at must be valid ISO-8601 timestamps") from exc
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start_at and end_at must include timezone offsets")
    if end < start:
        raise ValueError("end_at must not be before start_at")
    read_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    read_end = end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    settings = load_settings()
    credentials = load_credentials(settings.credentials_file, settings.region)
    with XiaomiHealthClient(settings, credentials) as client:
        result = repair_intensity_from_heart_rate(
            client=client,
            start_time=int(start.timestamp()),
            end_time=int(end.timestamp()),
            read_start_time=int(read_start.timestamp()),
            read_end_time=int(read_end.timestamp()) - 1,
            maximum_heart_rate=maximum_heart_rate,
            confirm_write=confirm_write,
        )
    result["start_at"] = start.isoformat()
    result["end_at"] = end.isoformat()
    result["missing_times"] = [
        datetime.fromtimestamp(timestamp, tz=start.tzinfo).isoformat()
        for timestamp in result["missing_timestamps"]
    ]
    return result
