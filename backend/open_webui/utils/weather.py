import logging
from datetime import date
from typing import Any, Optional

import requests
from fastapi import Request

from open_webui.utils.google_maps import resolve_place_with_google_maps


log = logging.getLogger(__name__)

DEFAULT_OPEN_METEO_BASE_URL = "https://api.open-meteo.com"
DEFAULT_FORECAST_DAYS = 7
MAX_FORECAST_DAYS = 16

OPEN_METEO_PROVIDER = {
    "name": "Open-Meteo",
    "url": "https://open-meteo.com/",
    "license": "CC BY 4.0",
    "attribution_required": True,
}

DAILY_VARIABLES = [
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_probability_max",
    "precipitation_sum",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "uv_index_max",
    "sunrise",
    "sunset",
]

CURRENT_VARIABLES = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
    "is_day",
]

WMO_WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherError(RuntimeError):
    pass


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def describe_wmo_weather_code(code: Any) -> Optional[str]:
    try:
        numeric_code = int(code)
    except (TypeError, ValueError):
        return None

    return WMO_WEATHER_CODES.get(numeric_code, f"WMO weather code {numeric_code}")


def _parse_iso_date(value: Optional[str], field_name: str) -> Optional[date]:
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        return None

    try:
        return date.fromisoformat(cleaned)
    except ValueError as exc:
        raise WeatherError(f"{field_name} must be in YYYY-MM-DD format") from exc


def _resolve_date_range(
    start_date: Optional[str],
    end_date: Optional[str],
) -> tuple[Optional[date], Optional[date], int]:
    parsed_start = _parse_iso_date(start_date, "start_date")
    parsed_end = _parse_iso_date(end_date, "end_date")

    if parsed_start and not parsed_end:
        parsed_end = parsed_start
    if parsed_end and not parsed_start:
        parsed_start = parsed_end

    if parsed_start and parsed_end:
        if parsed_end < parsed_start:
            raise WeatherError("end_date must be on or after start_date")

        span_days = (parsed_end - parsed_start).days + 1
        if span_days > MAX_FORECAST_DAYS:
            raise WeatherError(
                f"Weather forecast supports at most {MAX_FORECAST_DAYS} days per request"
            )

        return parsed_start, parsed_end, span_days

    return None, None, DEFAULT_FORECAST_DAYS


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _raise_for_status(response: requests.Response, action: str) -> None:
    if response.ok:
        return

    payload = _json_or_text(response)
    if isinstance(payload, dict):
        message = payload.get("reason") or payload.get("message") or str(payload)
    else:
        message = str(payload)

    raise WeatherError(f"{action} failed with {response.status_code}: {message}")


def _build_forecast_params(
    *,
    latitude: float,
    longitude: float,
    timezone: Optional[str],
    temperature_unit: Optional[str],
    wind_speed_unit: Optional[str],
    precipitation_unit: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
) -> dict[str, Any]:
    parsed_start, parsed_end, span_days = _resolve_date_range(start_date, end_date)

    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": ",".join(DAILY_VARIABLES),
        "current": ",".join(CURRENT_VARIABLES),
        "timezone": _clean_optional_text(timezone) or "auto",
        "temperature_unit": _clean_optional_text(temperature_unit) or "celsius",
        "wind_speed_unit": _clean_optional_text(wind_speed_unit) or "kmh",
        "precipitation_unit": _clean_optional_text(precipitation_unit) or "mm",
    }

    if parsed_start and parsed_end:
        params["start_date"] = parsed_start.isoformat()
        params["end_date"] = parsed_end.isoformat()
    else:
        params["forecast_days"] = span_days

    return params


def _fetch_open_meteo_forecast(
    *,
    latitude: float,
    longitude: float,
    timezone: Optional[str],
    temperature_unit: Optional[str],
    wind_speed_unit: Optional[str],
    precipitation_unit: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    timeout_seconds: int = 10,
    base_url: str = DEFAULT_OPEN_METEO_BASE_URL,
) -> dict[str, Any]:
    params = _build_forecast_params(
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
        temperature_unit=temperature_unit,
        wind_speed_unit=wind_speed_unit,
        precipitation_unit=precipitation_unit,
        start_date=start_date,
        end_date=end_date,
    )

    response = requests.get(
        f"{base_url.rstrip('/')}/v1/forecast",
        params=params,
        timeout=timeout_seconds,
    )
    _raise_for_status(response, "Open-Meteo forecast")
    return response.json()


def _build_current_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    current = payload.get("current") or {}
    if not current:
        return None

    weather_code = current.get("weather_code")
    return {
        "time": current.get("time"),
        "is_day": current.get("is_day"),
        "weather_code": weather_code,
        "weather_summary": describe_wmo_weather_code(weather_code),
        "temperature_2m": current.get("temperature_2m"),
        "apparent_temperature": current.get("apparent_temperature"),
        "precipitation": current.get("precipitation"),
        "wind_speed_10m": current.get("wind_speed_10m"),
        "wind_gusts_10m": current.get("wind_gusts_10m"),
    }


def _build_daily_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    daily = payload.get("daily") or {}
    dates = daily.get("time") or []

    items: list[dict[str, Any]] = []
    for idx, forecast_date in enumerate(dates):
        weather_code = (daily.get("weather_code") or [None] * len(dates))[idx]
        items.append(
            {
                "date": forecast_date,
                "weather_code": weather_code,
                "weather_summary": describe_wmo_weather_code(weather_code),
                "temperature_2m_max": (daily.get("temperature_2m_max") or [None] * len(dates))[idx],
                "temperature_2m_min": (daily.get("temperature_2m_min") or [None] * len(dates))[idx],
                "apparent_temperature_max": (
                    (daily.get("apparent_temperature_max") or [None] * len(dates))[idx]
                ),
                "apparent_temperature_min": (
                    (daily.get("apparent_temperature_min") or [None] * len(dates))[idx]
                ),
                "precipitation_probability_max": (
                    (daily.get("precipitation_probability_max") or [None] * len(dates))[idx]
                ),
                "precipitation_sum": (daily.get("precipitation_sum") or [None] * len(dates))[idx],
                "wind_speed_10m_max": (daily.get("wind_speed_10m_max") or [None] * len(dates))[idx],
                "wind_gusts_10m_max": (daily.get("wind_gusts_10m_max") or [None] * len(dates))[idx],
                "uv_index_max": (daily.get("uv_index_max") or [None] * len(dates))[idx],
                "sunrise": (daily.get("sunrise") or [None] * len(dates))[idx],
                "sunset": (daily.get("sunset") or [None] * len(dates))[idx],
            }
        )

    return items


def get_weather_forecast(
    *,
    request: Optional[Request],
    config: Any,
    place_name: Optional[str] = None,
    location_context: Optional[str] = None,
    query_hint: Optional[str] = None,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timezone: Optional[str] = None,
    temperature_unit: Optional[str] = None,
    wind_speed_unit: Optional[str] = None,
    precipitation_unit: Optional[str] = None,
) -> dict[str, Any]:
    resolved_place: Optional[dict[str, Any]] = None

    if latitude is None or longitude is None:
        cleaned_place_name = _clean_optional_text(place_name)
        if cleaned_place_name is None:
            raise WeatherError(
                "Provide either latitude and longitude, or place_name with Google Maps enabled"
            )

        try:
            resolved = resolve_place_with_google_maps(
                config=config,
                request=request,
                place_name=cleaned_place_name,
                location_context=location_context,
                query_hint=query_hint,
                language_code=language_code,
                region_code=region_code,
                max_candidates=3,
            )
        except Exception as exc:
            raise WeatherError(f"Place resolution failed: {exc}") from exc

        if resolved.get("status") != "success" or not resolved.get("place"):
            raise WeatherError("Could not resolve the requested place for weather forecast")

        resolved_place = resolved.get("place")
        coordinates = resolved_place.get("coordinates") or {}
        latitude = coordinates.get("latitude")
        longitude = coordinates.get("longitude")

    if latitude is None or longitude is None:
        raise WeatherError("Weather forecast requires valid latitude and longitude")

    payload = _fetch_open_meteo_forecast(
        latitude=float(latitude),
        longitude=float(longitude),
        timezone=timezone,
        temperature_unit=temperature_unit,
        wind_speed_unit=wind_speed_unit,
        precipitation_unit=precipitation_unit,
        start_date=start_date,
        end_date=end_date,
    )

    params = _build_forecast_params(
        latitude=float(latitude),
        longitude=float(longitude),
        timezone=timezone,
        temperature_unit=temperature_unit,
        wind_speed_unit=wind_speed_unit,
        precipitation_unit=precipitation_unit,
        start_date=start_date,
        end_date=end_date,
    )

    return {
        "status": "success",
        "provider": OPEN_METEO_PROVIDER,
        "requested_location": {
            "place_name": _clean_optional_text(place_name),
            "location_context": _clean_optional_text(location_context),
            "latitude": float(latitude),
            "longitude": float(longitude),
        },
        "resolved_place": resolved_place,
        "timezone": payload.get("timezone"),
        "timezone_abbreviation": payload.get("timezone_abbreviation"),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        "requested_range": {
            "start_date": params.get("start_date"),
            "end_date": params.get("end_date"),
            "forecast_days": params.get("forecast_days"),
        },
        "units": {
            "daily": payload.get("daily_units") or {},
            "current": payload.get("current_units") or {},
        },
        "current": _build_current_payload(payload),
        "forecast_days": _build_daily_payload(payload),
        "strategy": "open_meteo_daily_forecast",
    }
