from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math


@dataclass(frozen=True)
class LocationUpdatePayload:
    latitude: float
    longitude: float
    accuracy: float | None
    observed_at: datetime
    source: str


@dataclass(frozen=True)
class UnlockPayload:
    event: str
    device: str
    observed_at: datetime


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _observed_at(value: object, received_at: datetime) -> datetime:
    if value is None:
        return received_at
    try:
        timestamp = _finite_number(value, "timestamp")
        candidate = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return received_at
    received_utc = received_at.astimezone(timezone.utc)
    if timestamp <= 0 or candidate > received_utc + timedelta(minutes=5):
        return received_at
    return candidate.astimezone(received_at.tzinfo or timezone.utc)


def parse_location_update_payload(
    value: object,
    *,
    received_at: datetime,
) -> LocationUpdatePayload:
    if not isinstance(value, dict):
        raise ValueError("request JSON must be an object")
    if value.get("source") != "automate":
        raise ValueError("source must be automate")
    latitude = _finite_number(value.get("latitude"), "latitude")
    longitude = _finite_number(value.get("longitude"), "longitude")
    if not -90 <= latitude <= 90:
        raise ValueError("latitude is out of range")
    if not -180 <= longitude <= 180:
        raise ValueError("longitude is out of range")
    accuracy = None
    if value.get("accuracy") is not None:
        accuracy = _finite_number(value["accuracy"], "accuracy")
        if accuracy < 0:
            raise ValueError("accuracy must not be negative")
    return LocationUpdatePayload(
        latitude=latitude,
        longitude=longitude,
        accuracy=accuracy,
        observed_at=_observed_at(value.get("location_time"), received_at),
        source="automate",
    )


def parse_unlock_payload(
    value: object,
    *,
    received_at: datetime,
    expected_device: str | None = None,
) -> UnlockPayload:
    if not isinstance(value, dict):
        raise ValueError("request JSON must be an object")
    if value.get("event") != "unlock":
        raise ValueError("event must be unlock")
    device = value.get("device")
    if not isinstance(device, str) or not device.strip():
        raise ValueError("device is required")
    device = device.strip()
    if expected_device is not None and device != expected_device:
        raise ValueError("device is not allowed")
    return UnlockPayload(
        event="unlock",
        device=device,
        observed_at=_observed_at(value.get("timestamp"), received_at),
    )
