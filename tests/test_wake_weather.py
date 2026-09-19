from __future__ import annotations

import json

import httpx

from xiaomi_health_sync.wake_weather import WakeWeather


def test_resolve_place_returns_city_district_country_without_state_or_address() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "properties": {
                            "geocoding": {
                                "city": "Test City",
                                "district": "Test District",
                                "state": "Test Region",
                                "country": "Test Country",
                                "street": "Test Road",
                                "housenumber": "88",
                                "label": "Test road and full address",
                            }
                        }
                    }
                ],
            },
        )

    weather = WakeWeather(transport=httpx.MockTransport(handler))
    place = weather.resolve_place(12.346, 67.890)

    assert place.as_dict() == {
        "city": "Test City",
        "district": "Test District",
        "country": "Test Country",
    }
    assert len(seen) == 1
    assert seen[0].url.params["lat"] == "12.346"
    assert seen[0].url.params["lon"] == "67.89"
    assert seen[0].url.params["format"] == "geocodejson"
    assert seen[0].url.params["zoom"] == "10"
    assert seen[0].headers["user-agent"] == "xiaomi-health-sync/0.6"
    assert "region" not in place.as_dict()
    assert "street" not in place.as_dict()


def test_fetch_weather_uses_coordinates_but_returns_no_coordinates() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "latitude": 12.340,
                "longitude": 67.880,
                "timezone": "Asia/Shanghai",
                "current": {
                    "time": "2026-09-14T08:00",
                    "temperature_2m": 28.4,
                    "apparent_temperature": 31.2,
                    "relative_humidity_2m": 74,
                    "weather_code": 2,
                    "wind_speed_10m": 8.1,
                },
                "daily": {
                    "time": ["2026-09-14"],
                    "temperature_2m_max": [33.0],
                    "temperature_2m_min": [25.0],
                    "precipitation_probability_max": [45],
                    "weather_code": [61],
                },
            },
        )

    weather = WakeWeather(transport=httpx.MockTransport(handler))
    result = weather.fetch_weather(12.346, 67.890).as_dict()

    assert result == {
        "available": True,
        "provider": "open_meteo",
        "timezone": "Asia/Shanghai",
        "current": {
            "time": "2026-09-14T08:00",
            "temperature_c": 28.4,
            "apparent_temperature_c": 31.2,
            "relative_humidity_percent": 74,
            "weather_code": 2,
            "wind_speed_kmh": 8.1,
        },
        "daily": {
            "date": "2026-09-14",
            "temperature_max_c": 33.0,
            "temperature_min_c": 25.0,
            "precipitation_probability_max_percent": 45,
            "weather_code": 61,
        },
    }
    params = seen[0].url.params
    assert params["latitude"] == "12.346"
    assert params["longitude"] == "67.89"
    assert params["timezone"] == "auto"
    encoded = json.dumps(result)
    assert "latitude" not in encoded
    assert "longitude" not in encoded


def test_provider_failures_return_safe_unavailable_values() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="88 Test Road, secret provider body")

    weather = WakeWeather(transport=httpx.MockTransport(handler))

    assert weather.resolve_place(12.346, 67.890).as_dict() == {
        "city": None,
        "district": None,
        "country": None,
    }
    result = weather.fetch_weather(12.346, 67.890).as_dict()
    assert result == {
        "available": False,
        "provider": "open_meteo",
        "reason": "weather_unavailable",
    }
    assert "Test Road" not in json.dumps(result)
