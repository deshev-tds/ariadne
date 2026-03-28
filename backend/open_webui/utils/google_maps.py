import logging
import re
from typing import Any, Optional
from urllib.parse import urlencode

import requests
from fastapi import Request


log = logging.getLogger(__name__)

DEFAULT_GOOGLE_MAPS_BASE_URL = "https://places.googleapis.com"
DEFAULT_GOOGLE_MAPS_TIMEOUT_SECONDS = 10
DEFAULT_GOOGLE_MAPS_MAX_CANDIDATES = 5

LANGUAGE_CODE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?$")
REGION_CODE_RE = re.compile(r"^[A-Za-z]{2}$")


class GoogleMapsError(RuntimeError):
    pass


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def normalize_language_code(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        return None

    normalized = cleaned.replace("_", "-")
    if not LANGUAGE_CODE_RE.match(normalized):
        return None

    parts = normalized.split("-", 1)
    if len(parts) == 2:
        return f"{parts[0].lower()}-{parts[1].upper()}"
    return parts[0].lower()


def normalize_region_code(value: Optional[str]) -> Optional[str]:
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        return None
    if not REGION_CODE_RE.match(cleaned):
        return None
    return cleaned.upper()


def parse_accept_language(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None

    raw_value = request.headers.get("accept-language")
    if not raw_value:
        return None

    primary = raw_value.split(",", 1)[0].split(";", 1)[0].strip()
    return normalize_language_code(primary)


def resolve_runtime_language_code(
    request: Optional[Request],
    override: Optional[str],
    fallback: Optional[str],
) -> Optional[str]:
    return (
        normalize_language_code(override)
        or parse_accept_language(request)
        or normalize_language_code(fallback)
    )


def resolve_runtime_region_code(
    override: Optional[str],
    fallback: Optional[str],
) -> Optional[str]:
    return normalize_region_code(override) or normalize_region_code(fallback)


def build_google_maps_search_url(query: str, place_id: str) -> str:
    encoded = urlencode(
        {
            "api": "1",
            "query": query,
            "query_place_id": place_id,
        }
    )
    return f"https://www.google.com/maps/search/?{encoded}"


def _build_text_query(
    place_name: str,
    location_context: Optional[str] = None,
    query_hint: Optional[str] = None,
) -> str:
    parts = [
        _clean_optional_text(place_name),
        _clean_optional_text(location_context),
        _clean_optional_text(query_hint),
    ]
    return ", ".join(part for part in parts if part)


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
        message = (
            payload.get("error", {}).get("message")
            or payload.get("message")
            or str(payload)
        )
    else:
        message = str(payload)

    raise GoogleMapsError(
        f"{action} failed with {response.status_code}: {message}"
    )


def _raw_response_payload(response: requests.Response) -> dict[str, Any]:
    return {
        "ok": response.ok,
        "upstream_status": response.status_code,
        "body": _json_or_text(response),
    }


def _search_text_place_ids(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
    text_query: str,
    language_code: Optional[str],
    region_code: Optional[str],
    page_size: int,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "textQuery": text_query,
        "pageSize": max(1, min(page_size, 20)),
    }

    if language_code:
        payload["languageCode"] = language_code
    if region_code:
        payload["regionCode"] = region_code

    response = requests.post(
        f"{base_url.rstrip('/')}/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.name",
        },
        json=payload,
        timeout=timeout_seconds,
    )
    _raise_for_status(response, "Google Maps text search")

    data = response.json()
    return data.get("places", []) or []


def _search_text_place_ids_raw(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
    text_query: str,
    language_code: Optional[str],
    region_code: Optional[str],
    page_size: int,
) -> requests.Response:
    payload: dict[str, Any] = {
        "textQuery": text_query,
        "pageSize": max(1, min(page_size, 20)),
    }

    if language_code:
        payload["languageCode"] = language_code
    if region_code:
        payload["regionCode"] = region_code

    return requests.post(
        f"{base_url.rstrip('/')}/v1/places:searchText",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.name",
        },
        json=payload,
        timeout=timeout_seconds,
    )


def _get_place_details_essentials(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
    place_id: str,
    language_code: Optional[str],
    region_code: Optional[str],
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if language_code:
        params["languageCode"] = language_code
    if region_code:
        params["regionCode"] = region_code

    response = requests.get(
        f"{base_url.rstrip('/')}/v1/places/{place_id}",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,name,formattedAddress,shortFormattedAddress,location,types",
        },
        params=params,
        timeout=timeout_seconds,
    )
    _raise_for_status(response, "Google Maps place details")
    return response.json()


def _get_place_details_essentials_raw(
    *,
    api_key: str,
    base_url: str,
    timeout_seconds: int,
    place_id: str,
    language_code: Optional[str],
    region_code: Optional[str],
) -> requests.Response:
    params: dict[str, Any] = {}
    if language_code:
        params["languageCode"] = language_code
    if region_code:
        params["regionCode"] = region_code

    return requests.get(
        f"{base_url.rstrip('/')}/v1/places/{place_id}",
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "id,name,formattedAddress,shortFormattedAddress,location,types",
        },
        params=params,
        timeout=timeout_seconds,
    )


def resolve_place_with_google_maps(
    *,
    config,
    request: Optional[Request],
    place_name: str,
    location_context: Optional[str] = None,
    query_hint: Optional[str] = None,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> dict[str, Any]:
    if not getattr(config, "ENABLE_GOOGLE_MAPS", False):
        raise GoogleMapsError("Google Maps integration is disabled")

    api_key = _clean_optional_text(getattr(config, "GOOGLE_MAPS_API_KEY", None))
    if not api_key:
        raise GoogleMapsError("Google Maps API key is not configured")

    requested_name = _clean_optional_text(place_name)
    if requested_name is None:
        raise GoogleMapsError("place_name is required")

    runtime_language_code = resolve_runtime_language_code(
        request,
        language_code,
        getattr(config, "GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE", None),
    )
    runtime_region_code = resolve_runtime_region_code(
        region_code,
        getattr(config, "GOOGLE_MAPS_DEFAULT_REGION_CODE", None),
    )

    base_url = (
        _clean_optional_text(getattr(config, "GOOGLE_MAPS_BASE_URL", None))
        or DEFAULT_GOOGLE_MAPS_BASE_URL
    )
    timeout_seconds = int(
        getattr(config, "GOOGLE_MAPS_TIMEOUT_SECONDS", DEFAULT_GOOGLE_MAPS_TIMEOUT_SECONDS)
    )
    configured_max_candidates = int(
        getattr(config, "GOOGLE_MAPS_MAX_CANDIDATES", DEFAULT_GOOGLE_MAPS_MAX_CANDIDATES)
    )
    runtime_max_candidates = max_candidates or configured_max_candidates
    runtime_max_candidates = max(1, min(int(runtime_max_candidates), 20))

    text_query = _build_text_query(
        requested_name,
        location_context=location_context,
        query_hint=query_hint,
    )

    candidates = _search_text_place_ids(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        text_query=text_query,
        language_code=runtime_language_code,
        region_code=runtime_region_code,
        page_size=runtime_max_candidates,
    )

    if not candidates:
        return {
            "status": "no_match",
            "requested_name": requested_name,
            "text_query": text_query,
            "language_code_used": runtime_language_code,
            "region_code_used": runtime_region_code,
            "ambiguity_note": "No Google Maps candidates matched the request.",
            "place": None,
        }

    top_candidate = candidates[0]
    place_id = top_candidate.get("id")
    if not place_id:
        raise GoogleMapsError("Google Maps search returned a candidate without a place ID")

    details = _get_place_details_essentials(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        place_id=place_id,
        language_code=runtime_language_code,
        region_code=runtime_region_code,
    )

    formatted_address = details.get("formattedAddress")
    location = details.get("location") or {}
    maps_query = requested_name
    if formatted_address:
        maps_query = f"{requested_name}, {formatted_address}"

    ambiguity_note = None
    if len(candidates) > 1:
        ambiguity_note = (
            f"Multiple Google Maps candidates matched this query; selected the top-ranked "
            f"candidate out of {len(candidates)}."
        )

    return {
        "status": "success",
        "requested_name": requested_name,
        "text_query": text_query,
        "language_code_used": runtime_language_code,
        "region_code_used": runtime_region_code,
        "candidate_count": len(candidates),
        "ambiguity_note": ambiguity_note,
        "place": {
            "place_id": details.get("id") or place_id,
            "resource_name": details.get("name") or top_candidate.get("name"),
            "formatted_address": formatted_address,
            "short_formatted_address": details.get("shortFormattedAddress"),
            "types": details.get("types") or [],
            "coordinates": {
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
            },
            "google_maps_url": build_google_maps_search_url(maps_query, place_id),
        },
        "strategy": "text_search_ids_only_then_place_details_essentials",
    }


def probe_google_maps_integration(
    *,
    config,
    request: Optional[Request],
    place_name: str,
    location_context: Optional[str] = None,
    query_hint: Optional[str] = None,
    language_code: Optional[str] = None,
    region_code: Optional[str] = None,
    max_candidates: Optional[int] = None,
) -> dict[str, Any]:
    api_key = _clean_optional_text(getattr(config, "GOOGLE_MAPS_API_KEY", None))
    if not api_key:
        return {
            "ok": False,
            "local_error": "Google Maps API key is not configured",
        }

    requested_name = _clean_optional_text(place_name)
    if requested_name is None:
        return {
            "ok": False,
            "local_error": "place_name is required",
        }

    runtime_language_code = resolve_runtime_language_code(
        request,
        language_code,
        getattr(config, "GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE", None),
    )
    runtime_region_code = resolve_runtime_region_code(
        region_code,
        getattr(config, "GOOGLE_MAPS_DEFAULT_REGION_CODE", None),
    )
    base_url = (
        _clean_optional_text(getattr(config, "GOOGLE_MAPS_BASE_URL", None))
        or DEFAULT_GOOGLE_MAPS_BASE_URL
    )
    timeout_seconds = int(
        getattr(config, "GOOGLE_MAPS_TIMEOUT_SECONDS", DEFAULT_GOOGLE_MAPS_TIMEOUT_SECONDS)
    )
    configured_max_candidates = int(
        getattr(config, "GOOGLE_MAPS_MAX_CANDIDATES", DEFAULT_GOOGLE_MAPS_MAX_CANDIDATES)
    )
    runtime_max_candidates = max_candidates or configured_max_candidates
    runtime_max_candidates = max(1, min(int(runtime_max_candidates), 20))

    text_query = _build_text_query(
        requested_name,
        location_context=location_context,
        query_hint=query_hint,
    )

    try:
        search_response = _search_text_place_ids_raw(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            text_query=text_query,
            language_code=runtime_language_code,
            region_code=runtime_region_code,
            page_size=runtime_max_candidates,
        )
    except Exception as e:
        return {
            "ok": False,
            "local_error": str(e),
            "stage": "text_search",
        }

    result: dict[str, Any] = {
        "ok": search_response.ok,
        "enabled": bool(getattr(config, "ENABLE_GOOGLE_MAPS", False)),
        "strategy": "text_search_ids_only_then_place_details_essentials",
        "requested_name": requested_name,
        "text_query": text_query,
        "language_code_used": runtime_language_code,
        "region_code_used": runtime_region_code,
        "search": _raw_response_payload(search_response),
    }

    search_body = result["search"]["body"]
    if not search_response.ok:
        return result

    places = []
    if isinstance(search_body, dict):
        places = search_body.get("places", []) or []

    result["candidate_count"] = len(places)
    if not places:
        result["ok"] = False
        result["local_error"] = "Google Maps search returned no candidates"
        return result

    first_place = places[0] or {}
    place_id = first_place.get("id")
    result["selected_place_id"] = place_id

    if not place_id:
        result["ok"] = False
        result["local_error"] = "Google Maps search returned a candidate without a place ID"
        return result

    try:
        details_response = _get_place_details_essentials_raw(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            place_id=place_id,
            language_code=runtime_language_code,
            region_code=runtime_region_code,
        )
    except Exception as e:
        result["ok"] = False
        result["local_error"] = str(e)
        result["stage"] = "place_details"
        return result

    result["details"] = _raw_response_payload(details_response)
    result["ok"] = bool(search_response.ok and details_response.ok)
    return result
