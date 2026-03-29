from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from open_webui.models.users import UserModel
from open_webui.retrieval.utils import get_content_from_url
from open_webui.routers.retrieval import execute_strong_source_search, search_web
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.google_maps import GoogleMapsError, resolve_place_with_google_maps
from open_webui.utils.misc import get_content_from_message, get_last_user_message, get_system_message
from open_webui.utils.weather import WeatherError, get_weather_forecast


log = logging.getLogger(__name__)

TRAVEL_ORCHESTRATION_CONFIDENCE_THRESHOLD = 0.70
MAX_ASSUMED_DEFAULTS = 4
MAX_UNCERTAINTY_NOTES = 6
MAX_CANDIDATES_PER_BUCKET = 7
MAX_EVIDENCE_NOTES_PER_CANDIDATE = 3
DISCOVERY_SEARCH_ROUNDS = 2
DISCOVERY_FETCH_CAP = 4
DISCOVERY_BUCKETS = (
    "food_drink",
    "nightlife_events",
    "photography_walks",
    "cultural_sites",
    "day_trips",
    "stay_area",
    "mobility",
)
SOURCE_CLASS_PRECEDENCE = (
    "official_events",
    "venue_owned",
    "local_editorial",
    "directory_listing",
    "forum_community",
    "generic_travel_list",
)
WEAK_SOURCE_CLASSES = {"generic_travel_list", "directory_listing", "forum_community"}
PHASE_LABELS = {
    "brief_extract": "Understanding trip brief",
    "weather_context": "Checking weather and hard constraints",
    "trip_skeleton": "Building trip skeleton",
    "discovery_buckets": "Researching trip layers",
    "itinerary_build": "Assembling itinerary",
    "maps_enrichment": "Resolving map links",
}
BUCKET_STATUS_LABELS = {
    "food_drink": "food and bars",
    "nightlife_events": "nightlife and live music",
    "photography_walks": "street photography spots",
    "cultural_sites": "architecture and cultural anchors",
    "day_trips": "day-trip options",
    "stay_area": "best base areas",
    "mobility": "train and driving logistics",
}
SOURCE_PATTERN_TRAITS = {
    "official_events": (
        "eventbrite",
        "ticketmaster",
        "ticketone",
        "bandsintown",
        "songkick",
        "festicket",
        "/events",
        "calendar",
        "festival",
        "programma",
    ),
    "venue_owned": (
        "official site",
        "menu",
        "book",
        "reserve",
        "reservations",
        "our story",
        "about us",
    ),
    "local_editorial": (
        "local guide",
        "city guide",
        "magazine",
        "journal",
        "editorial",
        "news",
        "culture",
        "visit",
        "turismo",
        "turism",
    ),
    "directory_listing": (
        "tripadvisor",
        "yelp",
        "foursquare",
        "wikivoyage",
        "viator",
        "getyourguide",
        "booking",
        "expedia",
        "allevents",
        "maps.google",
    ),
    "forum_community": (
        "reddit",
        "facebook",
        "instagram",
        "tripadvisor forum",
        "quora",
        "x.com",
        "threads",
        "community",
        "forum",
    ),
    "generic_travel_list": (
        "top 10",
        "best things to do",
        "must-see",
        "travel list",
        "itinerary",
        "bucket list",
        "hidden gems",
    ),
}
BUCKET_QUERY_HINTS = {
    "food_drink": "best wine bars enotecas local restaurants food scene",
    "nightlife_events": "live music nightlife bars queer events clubs concerts",
    "photography_walks": "best viewpoints photography walks scenic streets sunset blue hour",
    "cultural_sites": "museums historic churches architecture local culture must-see",
    "day_trips": "best day trips easy by train ferry bus nearby towns",
    "stay_area": "best areas to stay neighborhoods walkable safe lively",
    "mobility": "transport train ferry bus airport transfer walkability logistics",
}


class TravelClassifierSignals(BaseModel):
    has_dates: bool = False
    has_multi_day_scope: bool = False
    has_itinerary_request: bool = False
    has_multiple_locations: bool = False
    has_single_place_lookup: bool = False

    model_config = ConfigDict(extra="ignore")


class TravelClassifierResult(BaseModel):
    classification: Literal["broad_trip", "narrow_trip_lookup", "non_trip"]
    orchestration_confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    signals: TravelClassifierSignals = Field(default_factory=TravelClassifierSignals)

    model_config = ConfigDict(extra="ignore")


class TravelWeatherTarget(BaseModel):
    place_name: str
    location_context: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    timezone: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class TravelDateRange(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_exact: bool = False
    note: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class TravelBrief(BaseModel):
    brief_confidence: float = Field(ge=0.0, le=1.0)
    needs_clarification: bool = False
    missing_but_material_fields: list[str] = Field(default_factory=list)
    assumed_defaults: list[str] = Field(default_factory=list)
    destinations: list[str] = Field(default_factory=list)
    base_candidates: list[str] = Field(default_factory=list)
    date_range: Optional[TravelDateRange] = None
    arrival_departure_constraints: list[str] = Field(default_factory=list)
    mobility_constraints: list[str] = Field(default_factory=list)
    explicit_user_asks: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    output_expectations: list[str] = Field(default_factory=list)
    locale_hints: list[str] = Field(default_factory=list)
    weather_targets: list[TravelWeatherTarget] = Field(default_factory=list)
    research_buckets: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class WeatherFinding(BaseModel):
    finding_type: Literal["hard_constraint", "soft_bias", "uncertain_context"]
    target_label: str
    date: Optional[str] = None
    summary: str
    rationale: str

    model_config = ConfigDict(extra="ignore")


class TravelDayScaffold(BaseModel):
    label: str
    date: Optional[str] = None
    base_city: Optional[str] = None
    focus: str
    transfer_notes: list[str] = Field(default_factory=list)
    weather_flex: bool = False

    model_config = ConfigDict(extra="ignore")


class TravelSkeletonResult(BaseModel):
    hard_constraints: list[str] = Field(default_factory=list)
    scaffold_decisions: list[str] = Field(default_factory=list)
    day_scaffold: list[TravelDayScaffold] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class TravelCandidate(BaseModel):
    place_name: str
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    category: str
    why_it_matters: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence_notes: list[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    source_snippet: Optional[str] = None
    source_class: Optional[str] = None
    needs_map_resolution: bool = True

    model_config = ConfigDict(extra="ignore")


class DiscoveryBucketResult(BaseModel):
    bucket: str
    covered: bool = False
    candidates: list[TravelCandidate] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class TravelFinalPlace(BaseModel):
    place_name: str
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    category: str
    why_it_matters: str
    source_url: Optional[str] = None
    source_snippet: Optional[str] = None
    needs_map_resolution: bool = True

    model_config = ConfigDict(extra="ignore")


class TravelManifestDay(BaseModel):
    label: str
    date: Optional[str] = None
    base_city: Optional[str] = None
    summary: str
    place_names: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class TravelMapTarget(BaseModel):
    place_name: str
    city: Optional[str] = None
    neighborhood: Optional[str] = None
    category: str
    source_url: Optional[str] = None
    source_snippet: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class TravelPlanManifest(BaseModel):
    days: list[TravelManifestDay] = Field(default_factory=list)
    final_places: list[TravelFinalPlace] = Field(default_factory=list)
    map_targets: list[TravelMapTarget] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    validation_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class TravelSynthesisResult(BaseModel):
    assistant_answer: str
    manifest: TravelPlanManifest

    model_config = ConfigDict(extra="ignore")


class TravelSanityIssue(BaseModel):
    issue_type: Literal[
        "contradiction",
        "unresolved_dependency",
        "date_mismatch",
        "assumption_overreach",
        "other",
    ]
    severity: Literal["low", "medium", "high"] = "medium"
    summary: str
    correction_instruction: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class TravelFinalSanityResult(BaseModel):
    passed: bool = True
    issues: list[TravelSanityIssue] = Field(default_factory=list)
    apply_local_correction: bool = False
    validation_notes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class TravelLedger(BaseModel):
    hard_constraints: list[str] = Field(default_factory=list)
    assumed_defaults: list[str] = Field(default_factory=list)
    covered_buckets: list[str] = Field(default_factory=list)
    weather_findings: list[WeatherFinding] = Field(default_factory=list)
    scaffold_decisions: list[str] = Field(default_factory=list)
    ranked_candidates: dict[str, list[TravelCandidate]] = Field(default_factory=dict)
    weak_source_warnings: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    source_debug: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


@dataclass
class SearchEvidence:
    title: str
    url: str
    snippet: str
    source_class: str
    matched_traits: list[str]
    content_excerpt: str


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clip_text(value: Any, limit: int = 1200) -> str:
    text = _clean_text(value) or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _dedupe_strings(values: list[str], limit: Optional[int] = None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
        if limit and len(result) >= limit:
            break
    return result


def _normalize_domain(url: Optional[str]) -> Optional[str]:
    cleaned = _clean_text(url)
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    hostname = parsed.netloc or parsed.path
    hostname = hostname.lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname or None


def _is_cc_tld(domain: str) -> bool:
    suffix = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return len(suffix) == 2 and suffix.isalpha()


def classify_source(
    *,
    url: Optional[str],
    title: Optional[str] = None,
    snippet: Optional[str] = None,
) -> tuple[str, list[str]]:
    domain = _normalize_domain(url) or ""
    text = " ".join(filter(None, [_clean_text(title), _clean_text(snippet), domain])).lower()
    matched: dict[str, list[str]] = {key: [] for key in SOURCE_CLASS_PRECEDENCE}

    for source_class, traits in SOURCE_PATTERN_TRAITS.items():
        for trait in traits:
            if trait in text:
                matched[source_class].append(trait)

    if domain.endswith(".gov") or ".gov." in domain or ".municipio." in domain or ".comune." in domain:
        matched["official_events"].append("official-domain")
    if domain.endswith(".edu") or domain.endswith(".ac.uk") or ".museum" in domain:
        matched["local_editorial"].append("institutional-domain")
    if any(token in domain for token in ("facebook.com", "reddit.com", "instagram.com", "threads.net", "x.com")):
        matched["forum_community"].append("community-domain")
    if any(token in domain for token in ("tripadvisor.", "eventbrite.", "allevents.", "booking.", "viator.", "foursquare.", "wikivoyage.")):
        matched["directory_listing"].append("directory-domain")
    if any(token in domain for token in ("ticketmaster.", "ticketone.", "songkick.", "bandsintown.")):
        matched["official_events"].append("ticketing-domain")
    if "visit" in domain or "turismo" in domain or "turism" in domain or domain.endswith(".travel"):
        matched["local_editorial"].append("tourism-domain")
    if _is_cc_tld(domain) and not matched["directory_listing"] and not matched["forum_community"]:
        matched["local_editorial"].append("country-code-domain")

    for source_class in SOURCE_CLASS_PRECEDENCE:
        traits = _dedupe_strings(matched[source_class])
        if traits:
            return source_class, traits

    return "generic_travel_list", ["fallback"]


def _candidate_key(place_name: Optional[str], city: Optional[str], category: Optional[str]) -> str:
    return "::".join(
        [
            (_clean_text(place_name) or "").casefold(),
            (_clean_text(city) or "").casefold(),
            (_clean_text(category) or "").casefold(),
        ]
    )


def derive_map_targets(final_places: list[TravelFinalPlace]) -> list[TravelMapTarget]:
    targets: list[TravelMapTarget] = []
    seen: set[str] = set()
    for place in final_places:
        if not place.needs_map_resolution:
            continue
        key = _candidate_key(place.place_name, place.city, place.category)
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            TravelMapTarget(
                place_name=place.place_name,
                city=place.city,
                neighborhood=place.neighborhood,
                category=place.category,
                source_url=place.source_url,
                source_snippet=place.source_snippet,
            )
        )
    return targets


def _cap_assumed_defaults(values: list[str]) -> list[str]:
    return [_clip_text(value, 160) for value in _dedupe_strings(values, MAX_ASSUMED_DEFAULTS)]


def _cap_uncertainty_notes(values: list[str]) -> list[str]:
    return [_clip_text(value, 220) for value in _dedupe_strings(values, MAX_UNCERTAINTY_NOTES)]


def _cap_candidates(candidates: list[TravelCandidate]) -> list[TravelCandidate]:
    deduped: list[TravelCandidate] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
        key = _candidate_key(candidate.place_name, candidate.city, candidate.category)
        if key in seen:
            continue
        seen.add(key)
        candidate.evidence_notes = _dedupe_strings(candidate.evidence_notes, MAX_EVIDENCE_NOTES_PER_CANDIDATE)
        deduped.append(candidate)
        if len(deduped) >= MAX_CANDIDATES_PER_BUCKET:
            break
    return deduped


def _maybe_parse_date(value: Optional[str]) -> Optional[date]:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def _dates_within_forecast_horizon(brief: TravelBrief) -> bool:
    if not brief.date_range:
        return False
    end_date = _maybe_parse_date(brief.date_range.end_date) or _maybe_parse_date(brief.date_range.start_date)
    if end_date is None:
        return False
    return (end_date - date.today()).days <= 16


def _weather_is_outdoor_sensitive(brief: TravelBrief) -> bool:
    interests = " ".join(brief.interests + brief.explicit_user_asks).lower()
    return any(token in interests for token in ("photo", "walk", "walking", "hike", "beach", "sunset", "outdoor", "viewpoint"))


def build_synthetic_chat_response(content: str, model_id: str) -> dict[str, Any]:
    return {
        "id": f"travel-orchestration-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }


def should_activate_travel_orchestration(model: dict[str, Any], metadata: dict[str, Any]) -> bool:
    capability_enabled: Optional[bool] = None
    metadata_capability_sources = (
        metadata.get("persona_effective_capabilities"),
        (metadata.get("persona_requested_defaults") or {}).get("capabilities"),
        (metadata.get("persona_snapshot") or {}).get("capabilities"),
    )
    for capabilities in metadata_capability_sources:
        if isinstance(capabilities, dict) and "travel_orchestration" in capabilities:
            capability_enabled = bool(capabilities.get("travel_orchestration"))
            break

    if capability_enabled is None:
        capabilities = (
            (model.get("info", {}).get("meta", {}).get("capabilities") or {})
            if isinstance(model, dict)
            else {}
        )
        capability_enabled = bool(capabilities.get("travel_orchestration"))

    return bool(
        capability_enabled
        and (metadata.get("params", {}) or {}).get("function_calling") == "native"
    )


def _extract_response_text(response: Any) -> str:
    if isinstance(response, list) and len(response) == 1:
        response = response[0]

    payload = response
    if hasattr(response, "body"):
        body = getattr(response, "body")
        if isinstance(body, bytes):
            payload = json.loads(body.decode("utf-8", "replace"))

    if not isinstance(payload, dict):
        raise ValueError("Internal model call did not return a JSON object payload")

    choices = payload.get("choices", [])
    if not choices:
        raise ValueError("Internal model call returned no choices")

    message = choices[0].get("message", {}) or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)

    if not isinstance(content, str) or not content.strip():
        raise ValueError("Internal model call returned empty content")
    return content.strip()


def _extract_json_string(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    for opening, closing in (("{", "}"), ("[", "]")):
        start = text.find(opening)
        end = text.rfind(closing)
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                json.loads(candidate)
                return candidate
            except Exception:
                continue
    return text


async def _call_structured_model(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    schema_model: type[BaseModel],
    system_prompt: str,
    user_prompt: str,
    metadata_label: str,
) -> BaseModel:
    payload = {
        "model": model_id,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "metadata": {
            "travel_orchestration_internal": metadata_label,
        },
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_model.__name__,
                "schema": schema_model.model_json_schema(),
            },
        },
    }

    try:
        response = await generate_chat_completion(
            request,
            payload,
            user,
            bypass_system_prompt=True,
        )
        raw_text = _extract_response_text(response)
        return schema_model.model_validate_json(_extract_json_string(raw_text))
    except Exception as first_exc:
        fallback_payload = {
            "model": model_id,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": f"{system_prompt}\n\nReturn JSON only. Do not include markdown fences or explanatory text.",
                },
                {"role": "user", "content": user_prompt},
            ],
            "metadata": {
                "travel_orchestration_internal": f"{metadata_label}:fallback",
            },
        }
        response = await generate_chat_completion(
            request,
            fallback_payload,
            user,
            bypass_system_prompt=True,
        )
        raw_text = _extract_response_text(response)
        try:
            return schema_model.model_validate_json(_extract_json_string(raw_text))
        except ValidationError:
            raise
        except Exception as second_exc:
            raise RuntimeError(
                f"Structured travel orchestration call failed for {metadata_label}: {first_exc}; fallback: {second_exc}"
            ) from second_exc


def _conversation_excerpt(messages: list[dict[str, Any]], limit: int = 6, max_chars: int = 6000) -> str:
    selected = [message for message in messages if message.get("role") in {"user", "assistant"}][-limit:]
    lines: list[str] = []
    total = 0
    for message in selected:
        role = str(message.get("role", "user")).upper()
        content = _clip_text(get_content_from_message(message) or "", 1200)
        line = f"{role}: {content}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)


async def _emit_phase_status(
    event_emitter,
    phase: str,
    *,
    done: bool,
    detail: Optional[str] = None,
    step: Optional[int] = None,
    total: Optional[int] = None,
) -> None:
    if not event_emitter:
        return
    try:
        payload: dict[str, Any] = {
            "action": "travel_orchestration",
            "phase": phase,
            "description": PHASE_LABELS.get(phase, phase),
            "done": done,
        }
        if detail:
            payload["detail"] = detail
        if step is not None:
            payload["step"] = step
        if total is not None:
            payload["total"] = total
        await event_emitter(
            {
                "type": "status",
                "data": payload,
            }
        )
    except Exception:
        log.debug("Failed to emit travel orchestration status for %s", phase, exc_info=True)


def _bucket_status_label(bucket: str) -> str:
    return BUCKET_STATUS_LABELS.get(bucket, bucket.replace("_", " "))


def _select_utility_model_id(model: dict[str, Any], task_model: Optional[dict[str, Any]]) -> str:
    if isinstance(task_model, dict) and task_model.get("id"):
        return task_model["id"]
    return model["id"]


async def _classify_request(
    *,
    request: Request,
    user: UserModel,
    utility_model_id: str,
    messages: list[dict[str, Any]],
) -> TravelClassifierResult:
    prompt = (
        "Classify the travel intent for this conversation.\n"
        "Use broad_trip only for multi-step trip planning, itinerary building, multi-day travel research, or destination planning.\n"
        "Use narrow_trip_lookup for a single venue/address/weather/transport lookup.\n"
        "Use non_trip for everything else.\n\n"
        f"Conversation:\n{_conversation_excerpt(messages)}"
    )
    return await _call_structured_model(
        request=request,
        user=user,
        model_id=utility_model_id,
        schema_model=TravelClassifierResult,
        system_prompt=(
            "You are a conservative travel-orchestration gate. "
            "Classify only from the conversation. Prefer false negatives over false positives."
        ),
        user_prompt=prompt,
        metadata_label="classifier",
    )


def _normalize_brief(brief: TravelBrief) -> TravelBrief:
    brief.assumed_defaults = _cap_assumed_defaults(brief.assumed_defaults)
    brief.missing_but_material_fields = _dedupe_strings(brief.missing_but_material_fields, 6)
    brief.destinations = _dedupe_strings(brief.destinations, 6)
    brief.base_candidates = _dedupe_strings(brief.base_candidates, 4)
    brief.arrival_departure_constraints = _dedupe_strings(brief.arrival_departure_constraints, 8)
    brief.mobility_constraints = _dedupe_strings(brief.mobility_constraints, 8)
    brief.explicit_user_asks = _dedupe_strings(brief.explicit_user_asks, 12)
    brief.interests = _dedupe_strings(brief.interests, 12)
    brief.output_expectations = _dedupe_strings(brief.output_expectations, 8)
    brief.locale_hints = _dedupe_strings(brief.locale_hints, 6)
    bucket_candidates = [bucket for bucket in brief.research_buckets if bucket in DISCOVERY_BUCKETS]
    if not bucket_candidates:
        bucket_candidates = ["cultural_sites", "food_drink"]
    brief.research_buckets = _dedupe_strings(bucket_candidates, 5)
    normalized_targets: list[TravelWeatherTarget] = []
    for target in brief.weather_targets:
        place_name = _clean_text(target.place_name)
        if not place_name:
            continue
        normalized_targets.append(
            TravelWeatherTarget(
                place_name=place_name,
                location_context=_clean_text(target.location_context),
                start_date=_clean_text(target.start_date),
                end_date=_clean_text(target.end_date),
                timezone=_clean_text(target.timezone),
            )
        )
    brief.weather_targets = normalized_targets[:4]
    return brief


async def _extract_brief(
    *,
    request: Request,
    user: UserModel,
    utility_model_id: str,
    messages: list[dict[str, Any]],
) -> TravelBrief:
    prompt = (
        "Extract a normalized travel brief from the conversation.\n"
        f"Allowed research_buckets: {', '.join(DISCOVERY_BUCKETS)}.\n"
        "Assumed defaults must be short provisional planning assumptions, not personality inferences.\n"
        "Only request clarification when missing fields would materially block a usable plan.\n\n"
        f"Conversation:\n{_conversation_excerpt(messages, limit=8, max_chars=8000)}"
    )
    brief = await _call_structured_model(
        request=request,
        user=user,
        model_id=utility_model_id,
        schema_model=TravelBrief,
        system_prompt=(
            "You normalize broad travel planning asks into structured planning briefs. "
            "Keep assumptions explicit and conservative."
        ),
        user_prompt=prompt,
        metadata_label="brief_extract",
    )
    return _normalize_brief(brief)


def _build_clarification_response(brief: TravelBrief) -> str:
    missing = brief.missing_but_material_fields[:3]
    if not missing:
        return "Преди да подредя смислен маршрут, ми трябва още малко контекст. Кажи ми най-важното, което още не е уточнено."

    questions = "\n".join(f"- {item}" for item in missing)
    return (
        "За да направя usable itinerary вместо да дописвам на сляпо, ми трябват още няколко уточнения:\n"
        f"{questions}"
    )


def _summarize_weather_findings(forecast: dict[str, Any], brief: TravelBrief) -> list[WeatherFinding]:
    findings: list[WeatherFinding] = []
    outdoor_sensitive = _weather_is_outdoor_sensitive(brief)
    for day_payload in forecast.get("forecast_days", []) or []:
        weather_code = day_payload.get("weather_code")
        precip_probability = day_payload.get("precipitation_probability_max") or 0
        wind_gusts = day_payload.get("wind_gusts_10m_max") or 0
        temp_max = day_payload.get("temperature_2m_max") or 0
        summary = _clean_text(day_payload.get("weather_summary")) or "Weather forecast available"
        date_value = _clean_text(day_payload.get("date"))
        place_label = (
            _clean_text((forecast.get("resolved_place") or {}).get("short_formatted_address"))
            or _clean_text((forecast.get("requested_location") or {}).get("place_name"))
            or "trip target"
        )

        severe_code = weather_code in {82, 86, 95, 96, 99}
        closure_like = severe_code or wind_gusts >= 60
        extreme_heat = outdoor_sensitive and temp_max >= 37

        if closure_like or extreme_heat:
            findings.append(
                WeatherFinding(
                    finding_type="hard_constraint",
                    target_label=place_label,
                    date=date_value,
                    summary=f"{summary} on {date_value} may materially break outdoor-first planning.",
                    rationale="Treat this as a structural constraint only because the forecast indicates severe disruption or extreme heat for outdoor-dependent activity.",
                )
            )
            continue

        if precip_probability >= 60 or (weather_code in {63, 65, 71, 73, 75, 80, 81}):
            findings.append(
                WeatherFinding(
                    finding_type="soft_bias",
                    target_label=place_label,
                    date=date_value,
                    summary=f"{summary} on {date_value} suggests indoor-flex planning.",
                    rationale="Use as a planning bias, not a hard blocker.",
                )
            )
            continue

        findings.append(
            WeatherFinding(
                finding_type="uncertain_context",
                target_label=place_label,
                date=date_value,
                summary=f"{summary} on {date_value}.",
                rationale="Forecast is informative context only.",
            )
        )
    return findings


async def _run_weather_context(
    *,
    request: Request,
    brief: TravelBrief,
) -> tuple[list[WeatherFinding], list[str], list[dict[str, Any]]]:
    findings: list[WeatherFinding] = []
    hard_constraints: list[str] = []
    raw_results: list[dict[str, Any]] = []

    if not brief.weather_targets:
        return findings, hard_constraints, raw_results

    if not _dates_within_forecast_horizon(brief):
        for target in brief.weather_targets:
            findings.append(
                WeatherFinding(
                    finding_type="uncertain_context",
                    target_label=target.place_name,
                    summary="Exact forecast is outside the deterministic forecast horizon; use seasonal expectations only.",
                    rationale="No exact weather call was made because the trip is beyond the forecast horizon.",
                )
            )
        return findings, hard_constraints, raw_results

    for target in brief.weather_targets:
        try:
            forecast = get_weather_forecast(
                request=request,
                config=request.app.state.config,
                place_name=target.place_name,
                location_context=target.location_context,
                start_date=target.start_date,
                end_date=target.end_date,
                timezone=target.timezone,
            )
            raw_results.append(forecast)
            target_findings = _summarize_weather_findings(forecast, brief)
            findings.extend(target_findings)
        except (WeatherError, GoogleMapsError) as exc:
            findings.append(
                WeatherFinding(
                    finding_type="uncertain_context",
                    target_label=target.place_name,
                    summary=f"Could not fetch deterministic weather for {target.place_name}.",
                    rationale=str(exc),
                )
            )

    hard_constraints = [
        finding.summary
        for finding in findings
        if finding.finding_type == "hard_constraint"
    ]
    return findings, _dedupe_strings(hard_constraints, 8), raw_results


async def _build_trip_skeleton(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    brief: TravelBrief,
    weather_findings: list[WeatherFinding],
    hard_constraints: list[str],
) -> TravelSkeletonResult:
    payload = {
        "brief": brief.model_dump(),
        "weather_findings": [finding.model_dump() for finding in weather_findings[:12]],
        "hard_constraints": hard_constraints[:8],
    }
    skeleton = await _call_structured_model(
        request=request,
        user=user,
        model_id=model_id,
        schema_model=TravelSkeletonResult,
        system_prompt=(
            "You build only the temporal and logistical scaffold for a trip. "
            "Do not rank venues. Do not produce destination listicles."
        ),
        user_prompt=json.dumps(payload, ensure_ascii=False),
        metadata_label="trip_skeleton",
    )
    skeleton.hard_constraints = _dedupe_strings(
        [*hard_constraints, *skeleton.hard_constraints],
        10,
    )
    skeleton.scaffold_decisions = _dedupe_strings(skeleton.scaffold_decisions, 10)
    skeleton.open_questions = _cap_uncertainty_notes(skeleton.open_questions)
    skeleton.day_scaffold = skeleton.day_scaffold[:12]
    return skeleton


def _build_bucket_queries(brief: TravelBrief, skeleton: TravelSkeletonResult, bucket: str) -> list[str]:
    primary_place = brief.base_candidates[0] if brief.base_candidates else (brief.destinations[0] if brief.destinations else "")
    date_hint = None
    if brief.date_range and brief.date_range.start_date:
        date_hint = brief.date_range.start_date[:4]
    query_suffix = BUCKET_QUERY_HINTS.get(bucket, bucket.replace("_", " "))
    queries = [", ".join(part for part in [primary_place, query_suffix, date_hint] if part)]
    if bucket == "day_trips" and primary_place:
        queries.append(f"{primary_place} easy day trips by train ferry bus")
    elif bucket == "mobility" and primary_place:
        queries.append(f"{primary_place} airport train ferry bus logistics")
    elif bucket == "nightlife_events" and primary_place:
        queries.append(f"{primary_place} live music queer bars events {date_hint or ''}".strip())
    else:
        queries.append(f"{primary_place} local {query_suffix}")
    return _dedupe_strings(queries, DISCOVERY_SEARCH_ROUNDS)


async def _strong_or_generic_search(
    *,
    request: Request,
    user: UserModel,
    query: str,
    features: dict[str, Any],
    event_emitter,
) -> list[dict[str, Any]]:
    if features.get("focused_search"):
        try:
            result = await execute_strong_source_search(
                request,
                query=query,
                user=user,
                max_queries=2,
                max_domains=4,
                topic_hint="travel trip planning",
                event_emitter=event_emitter,
                metadata={"params": {}},
            )
            if result.get("phase") == "completed":
                items = result.get("citation_items") or result.get("evidence_items") or result.get("items") or []
                normalized: list[dict[str, Any]] = []
                for item in items:
                    normalized.append(
                        {
                            "title": _clean_text(item.get("title")) or "",
                            "url": _clean_text(item.get("link")) or "",
                            "snippet": _clean_text(item.get("snippet")) or "",
                        }
                    )
                if normalized:
                    return normalized
        except Exception:
            log.debug("Focused search failed for query %s", query, exc_info=True)

    if not getattr(request.app.state.config, "ENABLE_WEB_SEARCH", False):
        return []

    engine = request.app.state.config.WEB_SEARCH_ENGINE
    results = await asyncio.to_thread(search_web, request, engine, query, user)
    normalized = []
    for result in results[:6]:
        normalized.append(
            {
                "title": _clean_text(getattr(result, "title", "")) or "",
                "url": _clean_text(getattr(result, "link", "")) or "",
                "snippet": _clean_text(getattr(result, "snippet", "")) or "",
            }
        )
    return normalized


async def _fetch_evidence_item(request: Request, item: dict[str, Any]) -> Optional[SearchEvidence]:
    url = _clean_text(item.get("url"))
    if not url:
        return None
    title = _clean_text(item.get("title")) or url
    snippet = _clean_text(item.get("snippet")) or ""
    source_class, matched_traits = classify_source(url=url, title=title, snippet=snippet)
    try:
        content, _ = await asyncio.to_thread(get_content_from_url, request, url)
    except Exception:
        content = ""
    return SearchEvidence(
        title=title,
        url=url,
        snippet=snippet,
        source_class=source_class,
        matched_traits=matched_traits,
        content_excerpt=_clip_text(content or snippet or title, 2200),
    )


async def _extract_bucket_candidates(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    bucket: str,
    brief: TravelBrief,
    skeleton: TravelSkeletonResult,
    evidence: list[SearchEvidence],
) -> DiscoveryBucketResult:
    payload = {
        "bucket": bucket,
        "brief": brief.model_dump(),
        "skeleton": skeleton.model_dump(),
        "evidence": [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "source_class": item.source_class,
                "content_excerpt": item.content_excerpt,
            }
            for item in evidence
        ],
    }
    result = await _call_structured_model(
        request=request,
        user=user,
        model_id=model_id,
        schema_model=DiscoveryBucketResult,
        system_prompt=(
            "You are extracting trip candidates for one research bucket. "
            "Collect and rank candidates only. Do not assemble the itinerary."
        ),
        user_prompt=json.dumps(payload, ensure_ascii=False),
        metadata_label=f"discovery:{bucket}",
    )
    result.bucket = bucket
    result.candidates = _cap_candidates(result.candidates)
    result.uncertainty_notes = _cap_uncertainty_notes(result.uncertainty_notes)
    result.covered = bool(result.covered or result.candidates)
    return result


async def _run_discovery_buckets(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    brief: TravelBrief,
    skeleton: TravelSkeletonResult,
    features: dict[str, Any],
    event_emitter,
) -> tuple[dict[str, list[TravelCandidate]], list[str], list[dict[str, Any]]]:
    ranked_candidates: dict[str, list[TravelCandidate]] = {}
    weak_source_warnings: list[str] = []
    source_debug: list[dict[str, Any]] = []
    total_buckets = len(brief.research_buckets)

    for bucket_idx, bucket in enumerate(brief.research_buckets, start=1):
        bucket_label = _bucket_status_label(bucket)
        await _emit_phase_status(
            event_emitter,
            "discovery_buckets",
            done=False,
            detail=f"Layer {bucket_idx}/{total_buckets}: researching {bucket_label}",
            step=bucket_idx,
            total=total_buckets,
        )
        queries = _build_bucket_queries(brief, skeleton, bucket)
        bucket_candidates: list[TravelCandidate] = []
        weak_counter: Counter[str] = Counter()
        rounds_without_new = 0

        for round_idx, query in enumerate(queries[:DISCOVERY_SEARCH_ROUNDS], start=1):
            await _emit_phase_status(
                event_emitter,
                "discovery_buckets",
                done=False,
                detail=(
                    f"Layer {bucket_idx}/{total_buckets}: checking {bucket_label} "
                    f"(search {round_idx}/{min(len(queries), DISCOVERY_SEARCH_ROUNDS)})"
                ),
                step=bucket_idx,
                total=total_buckets,
            )
            search_items = await _strong_or_generic_search(
                request=request,
                user=user,
                query=query,
                features=features,
                event_emitter=event_emitter,
            )
            fetched: list[SearchEvidence] = []
            for item in search_items[:DISCOVERY_FETCH_CAP]:
                evidence = await _fetch_evidence_item(request, item)
                if evidence is None:
                    continue
                fetched.append(evidence)
                source_debug.append(
                    {
                        "bucket": bucket,
                        "query": query,
                        "url": evidence.url,
                        "source_class": evidence.source_class,
                        "matched_traits": evidence.matched_traits,
                    }
                )
                if evidence.source_class in WEAK_SOURCE_CLASSES:
                    weak_counter[evidence.source_class] += 1

            if not fetched:
                rounds_without_new += 1
                if rounds_without_new >= 2:
                    break
                continue

            await _emit_phase_status(
                event_emitter,
                "discovery_buckets",
                done=False,
                detail=f"Layer {bucket_idx}/{total_buckets}: extracting candidates for {bucket_label}",
                step=bucket_idx,
                total=total_buckets,
            )
            bucket_result = await _extract_bucket_candidates(
                request=request,
                user=user,
                model_id=model_id,
                bucket=bucket,
                brief=brief,
                skeleton=skeleton,
                evidence=fetched,
            )

            previous_count = len(bucket_candidates)
            bucket_candidates = _cap_candidates([*bucket_candidates, *bucket_result.candidates])
            if len(bucket_candidates) == previous_count:
                rounds_without_new += 1
            else:
                rounds_without_new = 0

            weak_repetition = any(count >= 3 for source_class, count in weak_counter.items() if source_class in WEAK_SOURCE_CLASSES)
            if weak_repetition:
                repeated = [source_class for source_class, count in weak_counter.items() if count >= 3]
                weak_source_warnings.append(
                    f"{bucket}: weak-source saturation on {', '.join(sorted(repeated))}; stopping early."
                )
                break
            if rounds_without_new >= 2:
                break

        ranked_candidates[bucket] = bucket_candidates[:MAX_CANDIDATES_PER_BUCKET]

    return ranked_candidates, _dedupe_strings(weak_source_warnings, 6), source_debug


async def _build_itinerary(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    brief: TravelBrief,
    ledger: TravelLedger,
    skeleton: TravelSkeletonResult,
) -> TravelSynthesisResult:
    payload = {
        "brief": brief.model_dump(),
        "ledger": ledger.model_dump(),
        "skeleton": skeleton.model_dump(),
    }
    synthesis = await _call_structured_model(
        request=request,
        user=user,
        model_id=model_id,
        schema_model=TravelSynthesisResult,
        system_prompt=(
            "You are building the final trip answer from a bounded ledger. "
            "Return one human-usable answer and one faithful manifest. "
            "Do not invent new places beyond the ranked candidates."
        ),
        user_prompt=json.dumps(payload, ensure_ascii=False),
        metadata_label="itinerary_build",
    )
    synthesis.manifest.final_places = synthesis.manifest.final_places[:12]
    synthesis.manifest.validation_notes = _dedupe_strings(synthesis.manifest.validation_notes, 8)
    synthesis.manifest.unresolved_items = _dedupe_strings(synthesis.manifest.unresolved_items, 8)
    synthesis.manifest.map_targets = derive_map_targets(synthesis.manifest.final_places)
    return synthesis


def _build_maps_query_hint(target: TravelMapTarget) -> Optional[str]:
    hints = [_clean_text(target.category)]
    if target.source_snippet:
        hints.append(_clip_text(target.source_snippet, 80))
    return ", ".join(part for part in hints if part) or None


async def _run_maps_enrichment(
    *,
    request: Request,
    manifest: TravelPlanManifest,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    manifest.map_targets = derive_map_targets(manifest.final_places)
    for target in manifest.map_targets:
        try:
            resolved = resolve_place_with_google_maps(
                config=request.app.state.config,
                request=request,
                place_name=target.place_name,
                location_context=", ".join(
                    part for part in [_clean_text(target.neighborhood), _clean_text(target.city)] if part
                )
                or _clean_text(target.city),
                query_hint=_build_maps_query_hint(target),
                max_candidates=3,
            )
            results.append(
                {
                    "target": target.model_dump(),
                    "resolution": resolved,
                }
            )
        except GoogleMapsError as exc:
            results.append(
                {
                    "target": target.model_dump(),
                    "resolution": {
                        "status": "error",
                        "error": str(exc),
                    },
                }
            )
    return results


def _render_map_ready_appendix(map_results: list[dict[str, Any]]) -> str:
    if not map_results:
        return ""
    lines = ["Map-ready picks:"]
    for item in map_results:
        target = item.get("target") or {}
        resolution = item.get("resolution") or {}
        target_name = _clean_text(target.get("place_name")) or "Place"
        if resolution.get("status") == "success" and resolution.get("place"):
            place = resolution["place"]
            address = _clean_text(place.get("formatted_address")) or "Address unavailable"
            maps_url = _clean_text(place.get("google_maps_url")) or ""
            if maps_url:
                lines.append(f"- {target_name}: {address} | {maps_url}")
            else:
                lines.append(f"- {target_name}: {address}")
        else:
            note = (
                _clean_text(resolution.get("ambiguity_note"))
                or _clean_text(resolution.get("error"))
                or "Map resolution unavailable"
            )
            lines.append(f"- {target_name}: unresolved | {note}")
    return "\n".join(lines)


async def _run_final_sanity(
    *,
    request: Request,
    user: UserModel,
    utility_model_id: str,
    brief: TravelBrief,
    ledger: TravelLedger,
    synthesis: TravelSynthesisResult,
    map_results: list[dict[str, Any]],
) -> TravelFinalSanityResult:
    payload = {
        "brief": brief.model_dump(),
        "assumed_defaults": ledger.assumed_defaults,
        "weather_findings": [finding.model_dump() for finding in ledger.weather_findings[:12]],
        "assistant_answer": synthesis.assistant_answer,
        "manifest": synthesis.manifest.model_dump(),
        "map_results": map_results,
        "allowed_local_repairs": [
            "contradiction fix",
            "unresolved dependency handling",
            "date/time repair",
        ],
        "forbidden_actions": [
            "reopen discovery",
            "add new candidates",
            "redesign day allocation",
            "rewrite the itinerary wholesale",
        ],
    }
    return await _call_structured_model(
        request=request,
        user=user,
        model_id=utility_model_id,
        schema_model=TravelFinalSanityResult,
        system_prompt=(
            "You are a final travel plan checker. "
            "You may validate and request at most one local correction. "
            "You are not a planner and you may not redesign the itinerary."
        ),
        user_prompt=json.dumps(payload, ensure_ascii=False),
        metadata_label="final_sanity",
    )


async def _apply_local_correction(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    brief: TravelBrief,
    ledger: TravelLedger,
    synthesis: TravelSynthesisResult,
    sanity: TravelFinalSanityResult,
) -> TravelSynthesisResult:
    correction_issues = [
        issue.model_dump()
        for issue in sanity.issues
        if issue.issue_type in {"contradiction", "unresolved_dependency", "date_mismatch"}
    ][:3]
    payload = {
        "brief": brief.model_dump(),
        "ledger": ledger.model_dump(),
        "current_synthesis": synthesis.model_dump(),
        "issues": correction_issues,
        "instructions": "Apply only local fixes. Do not reopen discovery, add new places, redesign the plan, or rewrite wholesale.",
    }
    corrected = await _call_structured_model(
        request=request,
        user=user,
        model_id=model_id,
        schema_model=TravelSynthesisResult,
        system_prompt=(
            "Apply only local repairs to a travel itinerary. "
            "You may fix contradictions, date/time mismatches, or unresolved dependencies. "
            "Keep the overall structure and candidate set intact."
        ),
        user_prompt=json.dumps(payload, ensure_ascii=False),
        metadata_label="local_correction",
    )
    corrected.manifest.map_targets = derive_map_targets(corrected.manifest.final_places)
    return corrected


async def maybe_run_travel_orchestration(
    *,
    request: Request,
    form_data: dict[str, Any],
    user: UserModel,
    metadata: dict[str, Any],
    model: dict[str, Any],
    task_model: Optional[dict[str, Any]],
    features: dict[str, Any],
    event_emitter,
) -> Optional[dict[str, Any]]:
    if not should_activate_travel_orchestration(model, metadata):
        return None

    messages = form_data.get("messages", []) or []
    if not get_last_user_message(messages):
        return None

    utility_model_id = _select_utility_model_id(model, task_model)
    telemetry: dict[str, Any] = {
        "active": False,
        "orchestration_confidence": None,
        "brief_confidence": None,
        "classifier": None,
        "covered_buckets": [],
        "phase_timings_ms": {},
        "stop_reason": None,
        "weather_calls": 0,
        "maps_calls": 0,
    }
    debug_artifacts: dict[str, Any] = {}

    try:
        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "brief_extract",
            done=False,
            detail="Classifying whether this needs the full trip-planning harness",
        )
        classifier = await _classify_request(
            request=request,
            user=user,
            utility_model_id=utility_model_id,
            messages=messages,
        )
        telemetry["classifier"] = classifier.model_dump()
        telemetry["orchestration_confidence"] = classifier.orchestration_confidence
        if (
            classifier.classification != "broad_trip"
            or classifier.orchestration_confidence < TRAVEL_ORCHESTRATION_CONFIDENCE_THRESHOLD
        ):
            telemetry["stop_reason"] = "classifier_fallback"
            metadata["travel_orchestration_telemetry"] = telemetry
            return None

        telemetry["active"] = True
        await _emit_phase_status(
            event_emitter,
            "brief_extract",
            done=False,
            detail="Turning the prompt into dates, bases, transport limits, and research buckets",
        )
        brief = await _extract_brief(
            request=request,
            user=user,
            utility_model_id=utility_model_id,
            messages=messages,
        )
        telemetry["brief_confidence"] = brief.brief_confidence
        telemetry["phase_timings_ms"]["brief_extract"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["brief"] = brief.model_dump()
        await _emit_phase_status(
            event_emitter,
            "brief_extract",
            done=True,
            detail="Trip brief captured",
        )

        if brief.needs_clarification:
            response = build_synthetic_chat_response(
                _build_clarification_response(brief),
                model["id"],
            )
            metadata["travel_orchestration_telemetry"] = telemetry
            metadata["travel_orchestration_artifacts"] = debug_artifacts
            return {"response": response, "events": []}

        ledger = TravelLedger(assumed_defaults=brief.assumed_defaults)

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "weather_context",
            done=False,
            detail="Checking forecast constraints for the travel window",
        )
        weather_findings, hard_constraints, weather_raw = await _run_weather_context(
            request=request,
            brief=brief,
        )
        ledger.weather_findings = weather_findings[:16]
        ledger.hard_constraints = _dedupe_strings(hard_constraints, 10)
        telemetry["weather_calls"] = len(weather_raw)
        telemetry["phase_timings_ms"]["weather_context"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["weather"] = weather_raw
        await _emit_phase_status(
            event_emitter,
            "weather_context",
            done=True,
            detail="Weather constraints folded into the plan",
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "trip_skeleton",
            done=False,
            detail="Allocating bases, transfers, and day-level focus",
        )
        skeleton = await _build_trip_skeleton(
            request=request,
            user=user,
            model_id=model["id"],
            brief=brief,
            weather_findings=ledger.weather_findings,
            hard_constraints=ledger.hard_constraints,
        )
        ledger.hard_constraints = _dedupe_strings([*ledger.hard_constraints, *skeleton.hard_constraints], 10)
        ledger.scaffold_decisions = _dedupe_strings(skeleton.scaffold_decisions, 12)
        ledger.unresolved_questions = _cap_uncertainty_notes(skeleton.open_questions)
        telemetry["phase_timings_ms"]["trip_skeleton"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["skeleton"] = skeleton.model_dump()
        await _emit_phase_status(
            event_emitter,
            "trip_skeleton",
            done=True,
            detail="Trip skeleton ready",
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "discovery_buckets",
            done=False,
            detail="Starting layered source research",
        )
        ranked_candidates, weak_source_warnings, source_debug = await _run_discovery_buckets(
            request=request,
            user=user,
            model_id=model["id"],
            brief=brief,
            skeleton=skeleton,
            features=features,
            event_emitter=event_emitter,
        )
        ledger.ranked_candidates = ranked_candidates
        ledger.weak_source_warnings = weak_source_warnings
        ledger.covered_buckets = [bucket for bucket, candidates in ranked_candidates.items() if candidates]
        ledger.source_debug = source_debug
        telemetry["covered_buckets"] = ledger.covered_buckets
        telemetry["phase_timings_ms"]["discovery_buckets"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["discovery"] = {
            "weak_source_warnings": weak_source_warnings,
            "source_debug": source_debug,
        }
        await _emit_phase_status(
            event_emitter,
            "discovery_buckets",
            done=True,
            detail=(
                "Research complete"
                if not ledger.covered_buckets
                else f"Research complete: covered {len(ledger.covered_buckets)} layer(s)"
            ),
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "itinerary_build",
            done=False,
            detail="Turning the ledger into a day-by-day itinerary",
        )
        synthesis = await _build_itinerary(
            request=request,
            user=user,
            model_id=model["id"],
            brief=brief,
            ledger=ledger,
            skeleton=skeleton,
        )
        telemetry["phase_timings_ms"]["itinerary_build"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["synthesis_initial"] = synthesis.model_dump()
        await _emit_phase_status(
            event_emitter,
            "itinerary_build",
            done=True,
            detail="Draft itinerary assembled",
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "maps_enrichment",
            done=False,
            detail="Resolving places into map-ready links",
        )
        map_results = await _run_maps_enrichment(request=request, manifest=synthesis.manifest)
        telemetry["maps_calls"] = len(map_results)
        telemetry["phase_timings_ms"]["maps_enrichment"] = int((time.perf_counter() - started_at) * 1000)
        debug_artifacts["maps"] = map_results
        await _emit_phase_status(
            event_emitter,
            "maps_enrichment",
            done=True,
            detail="Map links resolved",
        )

        sanity = await _run_final_sanity(
            request=request,
            user=user,
            utility_model_id=utility_model_id,
            brief=brief,
            ledger=ledger,
            synthesis=synthesis,
            map_results=map_results,
        )
        debug_artifacts["sanity"] = sanity.model_dump()

        if sanity.apply_local_correction:
            synthesis = await _apply_local_correction(
                request=request,
                user=user,
                model_id=model["id"],
                brief=brief,
                ledger=ledger,
                synthesis=synthesis,
                sanity=sanity,
            )
            map_results = await _run_maps_enrichment(request=request, manifest=synthesis.manifest)
            telemetry["maps_calls"] = len(map_results)
            debug_artifacts["synthesis_corrected"] = synthesis.model_dump()
            debug_artifacts["maps_corrected"] = map_results

        if sanity.validation_notes:
            synthesis.manifest.validation_notes = _dedupe_strings(
                [*synthesis.manifest.validation_notes, *sanity.validation_notes],
                8,
            )

        appendix = _render_map_ready_appendix(map_results)
        final_answer = synthesis.assistant_answer.strip()
        if synthesis.manifest.validation_notes:
            notes = "\n".join(f"- {note}" for note in synthesis.manifest.validation_notes[:4])
            final_answer = f"{final_answer}\n\nValidation notes:\n{notes}"
        if appendix:
            final_answer = f"{final_answer}\n\n{appendix}"

        response = build_synthetic_chat_response(final_answer, model["id"])
        metadata["travel_orchestration_telemetry"] = telemetry
        metadata["travel_orchestration_artifacts"] = debug_artifacts
        telemetry["stop_reason"] = "synthesized"
        return {"response": response, "events": []}
    except Exception:
        log.exception("Travel orchestration failed; falling back to default path")
        metadata["travel_orchestration_telemetry"] = {
            **telemetry,
            "stop_reason": "error_fallback",
        }
        return None
