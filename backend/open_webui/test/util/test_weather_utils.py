from types import SimpleNamespace

import pytest

from open_webui.utils.weather import (
    WeatherError,
    _build_forecast_params,
    _fetch_open_meteo_forecast,
    describe_wmo_weather_code,
    get_weather_forecast,
)


def _sample_open_meteo_payload():
    return {
        "timezone": "Europe/Rome",
        "timezone_abbreviation": "CEST",
        "utc_offset_seconds": 7200,
        "current_units": {
            "temperature_2m": "°C",
            "apparent_temperature": "°C",
            "precipitation": "mm",
            "wind_speed_10m": "km/h",
            "wind_gusts_10m": "km/h",
        },
        "current": {
            "time": "2026-04-01T12:00",
            "is_day": 1,
            "weather_code": 2,
            "temperature_2m": 18.2,
            "apparent_temperature": 18.0,
            "precipitation": 0.0,
            "wind_speed_10m": 11.4,
            "wind_gusts_10m": 17.2,
        },
        "daily_units": {
            "temperature_2m_max": "°C",
            "temperature_2m_min": "°C",
            "apparent_temperature_max": "°C",
            "apparent_temperature_min": "°C",
            "precipitation_probability_max": "%",
            "precipitation_sum": "mm",
            "wind_speed_10m_max": "km/h",
            "wind_gusts_10m_max": "km/h",
            "uv_index_max": "",
        },
        "daily": {
            "time": ["2026-04-01", "2026-04-02"],
            "weather_code": [2, 61],
            "temperature_2m_max": [21.1, 17.4],
            "temperature_2m_min": [11.8, 9.1],
            "apparent_temperature_max": [20.3, 16.5],
            "apparent_temperature_min": [11.0, 8.3],
            "precipitation_probability_max": [20, 75],
            "precipitation_sum": [0.0, 4.2],
            "wind_speed_10m_max": [16.1, 24.3],
            "wind_gusts_10m_max": [22.5, 31.0],
            "uv_index_max": [5.4, 3.2],
            "sunrise": ["2026-04-01T06:58", "2026-04-02T06:56"],
            "sunset": ["2026-04-01T19:42", "2026-04-02T19:43"],
        },
    }


def test_describe_wmo_weather_code_returns_known_label():
    assert describe_wmo_weather_code(95) == "Thunderstorm"
    assert describe_wmo_weather_code("2") == "Partly cloudy"


def test_build_forecast_params_defaults_to_seven_days():
    params = _build_forecast_params(
        latitude=43.77,
        longitude=11.25,
        timezone=None,
        temperature_unit=None,
        wind_speed_unit=None,
        precipitation_unit=None,
        start_date=None,
        end_date=None,
    )

    assert params["forecast_days"] == 7
    assert params["timezone"] == "auto"
    assert "start_date" not in params


def test_build_forecast_params_rejects_too_wide_range():
    with pytest.raises(WeatherError, match="at most 16 days"):
        _build_forecast_params(
            latitude=43.77,
            longitude=11.25,
            timezone="Europe/Rome",
            temperature_unit="celsius",
            wind_speed_unit="kmh",
            precipitation_unit="mm",
            start_date="2026-04-01",
            end_date="2026-04-20",
        )


def test_fetch_open_meteo_forecast_surfaces_reason(monkeypatch):
    class FakeResponse:
        ok = False
        status_code = 400

        def json(self):
            return {"reason": "Invalid latitude"}

    monkeypatch.setattr("open_webui.utils.weather.requests.get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(WeatherError, match="Invalid latitude"):
        _fetch_open_meteo_forecast(
            latitude=999,
            longitude=11.25,
            timezone="auto",
            temperature_unit="celsius",
            wind_speed_unit="kmh",
            precipitation_unit="mm",
            start_date=None,
            end_date=None,
        )


def test_get_weather_forecast_with_coordinates(monkeypatch):
    monkeypatch.setattr(
        "open_webui.utils.weather._fetch_open_meteo_forecast",
        lambda **kwargs: _sample_open_meteo_payload(),
    )

    result = get_weather_forecast(
        request=None,
        config=SimpleNamespace(),
        latitude=43.77,
        longitude=11.25,
    )

    assert result["status"] == "success"
    assert result["provider"]["name"] == "Open-Meteo"
    assert result["requested_range"]["forecast_days"] == 7
    assert result["forecast_days"][0]["weather_summary"] == "Partly cloudy"
    assert result["forecast_days"][1]["weather_summary"] == "Slight rain"
    assert result["current"]["weather_summary"] == "Partly cloudy"


def test_get_weather_forecast_resolves_place_when_coordinates_missing(monkeypatch):
    monkeypatch.setattr(
        "open_webui.utils.weather.resolve_place_with_google_maps",
        lambda **kwargs: {
            "status": "success",
            "place": {
                "place_id": "abc123",
                "formatted_address": "Florence, Metropolitan City of Florence, Italy",
                "coordinates": {"latitude": 43.7696, "longitude": 11.2558},
                "google_maps_url": "https://www.google.com/maps/search/?api=1&query=Florence",
            },
        },
    )
    monkeypatch.setattr(
        "open_webui.utils.weather._fetch_open_meteo_forecast",
        lambda **kwargs: _sample_open_meteo_payload(),
    )

    config = SimpleNamespace(
        ENABLE_GOOGLE_MAPS=True,
        GOOGLE_MAPS_API_KEY="test-key",
        GOOGLE_MAPS_BASE_URL="https://places.googleapis.com",
        GOOGLE_MAPS_TIMEOUT_SECONDS=10,
        GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE="",
        GOOGLE_MAPS_DEFAULT_REGION_CODE="",
        GOOGLE_MAPS_MAX_CANDIDATES=5,
    )

    result = get_weather_forecast(
        request=None,
        config=config,
        place_name="Florence",
        location_context="Italy",
    )

    assert result["resolved_place"]["place_id"] == "abc123"
    assert result["requested_location"]["latitude"] == pytest.approx(43.7696)
    assert result["requested_location"]["longitude"] == pytest.approx(11.2558)
