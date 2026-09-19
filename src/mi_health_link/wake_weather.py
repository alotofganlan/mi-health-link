from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

import httpx


DEFAULT_GEOCODER_URL = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class ResolvedPlace:
    city: str | None = None
    district: str | None = None
    country: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "city": self.city,
            "district": self.district,
            "country": self.country,
        }


@dataclass(frozen=True)
class WeatherContext:
    available: bool
    provider: str = "open_meteo"
    timezone: str | None = None
    current: dict[str, Any] | None = None
    daily: dict[str, Any] | None = None
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": self.available,
            "provider": self.provider,
        }
        if not self.available:
            result["reason"] = self.reason or "weather_unavailable"
            return result
        result["timezone"] = self.timezone
        result["current"] = self.current
        result["daily"] = self.daily
        return result


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(values: object) -> object | None:
    return values[0] if isinstance(values, list) and values else None


class WakeWeather:
    def __init__(
        self,
        *,
        geocoder_url: str | None = None,
        weather_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.geocoder_url = (
            geocoder_url
            or os.getenv("WAKE_GEOCODER_URL")
            or DEFAULT_GEOCODER_URL
        )
        self.weather_url = (
            weather_url
            or os.getenv("WAKE_WEATHER_URL")
            or DEFAULT_WEATHER_URL
        )
        self.transport = transport

    def resolve_place(self, latitude: float, longitude: float) -> ResolvedPlace:
        try:
            with httpx.Client(timeout=10.0, transport=self.transport) as client:
                response = client.get(
                    self.geocoder_url,
                    params={
                        "format": "geocodejson",
                        "lat": str(latitude),
                        "lon": str(longitude),
                        "zoom": "10",
                        "addressdetails": "1",
                        "accept-language": "en,zh-CN;q=0.8",
                    },
                    headers={
                        "User-Agent": "mi-health-link/0.6"
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return ResolvedPlace()

        root = _mapping(payload)
        feature = _mapping(_first(root.get("features")))
        properties = _mapping(feature.get("properties"))
        geocoding = _mapping(properties.get("geocoding"))
        city = next(
            (
                str(geocoding[key])
                for key in ("city", "municipality", "town")
                if geocoding.get(key)
            ),
            None,
        )
        district = next(
            (
                str(geocoding[key])
                for key in ("district", "county")
                if geocoding.get(key)
            ),
            None,
        )
        country = (
            str(geocoding["country"]) if geocoding.get("country") else None
        )
        return ResolvedPlace(city=city, district=district, country=country)

    def fetch_weather(self, latitude: float, longitude: float) -> WeatherContext:
        current_fields = (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "weather_code,wind_speed_10m"
        )
        daily_fields = (
            "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,weather_code"
        )
        try:
            with httpx.Client(timeout=15.0, transport=self.transport) as client:
                response = client.get(
                    self.weather_url,
                    params={
                        "latitude": str(latitude),
                        "longitude": str(longitude),
                        "current": current_fields,
                        "daily": daily_fields,
                        "timezone": "auto",
                        "forecast_days": "1",
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            return WeatherContext(available=False, reason="weather_unavailable")

        root = _mapping(payload)
        current = _mapping(root.get("current"))
        daily = _mapping(root.get("daily"))
        if not current or not daily:
            return WeatherContext(available=False, reason="weather_unavailable")

        return WeatherContext(
            available=True,
            timezone=str(root.get("timezone") or ""),
            current={
                "time": current.get("time"),
                "temperature_c": current.get("temperature_2m"),
                "apparent_temperature_c": current.get("apparent_temperature"),
                "relative_humidity_percent": current.get("relative_humidity_2m"),
                "weather_code": current.get("weather_code"),
                "wind_speed_kmh": current.get("wind_speed_10m"),
            },
            daily={
                "date": _first(daily.get("time")),
                "temperature_max_c": _first(daily.get("temperature_2m_max")),
                "temperature_min_c": _first(daily.get("temperature_2m_min")),
                "precipitation_probability_max_percent": _first(
                    daily.get("precipitation_probability_max")
                ),
                "weather_code": _first(daily.get("weather_code")),
            },
        )
