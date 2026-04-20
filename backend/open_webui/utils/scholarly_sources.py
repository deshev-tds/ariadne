import json
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlencode
from xml.etree import ElementTree

import aiohttp

from open_webui.retrieval.web.planner import load_source_registry


AuthMode = Literal["none", "optional", "required"]
ProtocolLevel = Literal["required", "advisory"]
GroundingLevel = Literal["metadata", "abstract", "full_text", "doi_landing"]
AccessStatus = Literal["open", "restricted", "landing_only", "unresolved", "unknown"]
ReasoningScope = Literal[
    "bibliographic_discovery", "abstract_grounded", "full_text_grounded"
]
StudySignal = Literal[
    "unknown",
    "preprint",
    "case_report",
    "observational",
    "trial",
    "review",
    "systematic_review",
    "meta_analysis",
    "guideline",
]


@dataclass(frozen=True)
class ScholarlySourceDefinition:
    id: str
    label: str
    auth_mode: AuthMode
    purpose: str
    planner_fallback_domains: tuple[str, ...]
    auth_detail: str
    api_key_label: str | None = None
    api_key_placeholder: str | None = None
    notes: tuple[str, ...] = ()
    uses_contact_email: bool = False


SCHOLARLY_SOURCE_DEFINITIONS: tuple[ScholarlySourceDefinition, ...] = (
    ScholarlySourceDefinition(
        id="pubmed",
        label="PubMed / NCBI E-utilities",
        auth_mode="optional",
        api_key_label="NCBI API Key",
        api_key_placeholder="Enter NCBI API Key",
        auth_detail="Works without a key. An NCBI API key mainly increases rate limits.",
        purpose="Biomedical literature search, metadata lookups, and PMID or PMCID workflows.",
        planner_fallback_domains=("pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"),
        notes=(
            "Save does not enforce the optional key. Native calls can still run without it.",
        ),
        uses_contact_email=True,
    ),
    ScholarlySourceDefinition(
        id="openalex",
        label="OpenAlex",
        auth_mode="required",
        api_key_label="OpenAlex API Key",
        api_key_placeholder="Enter OpenAlex API Key",
        auth_detail="Native API access should assume a free OpenAlex key.",
        purpose="Cross-disciplinary works, authors, institutions, concepts, and citation graphs.",
        planner_fallback_domains=("openalex.org",),
        notes=(
            "OpenAlex native integration expects an API key. The save action does not block an empty field.",
        ),
    ),
    ScholarlySourceDefinition(
        id="crossref",
        label="Crossref",
        auth_mode="none",
        auth_detail="No key is required for public or polite-pool usage.",
        purpose="DOI metadata, references, funders, journal records, and citation normalization.",
        planner_fallback_domains=("crossref.org",),
        notes=(
            "Crossref polite-pool requests use the email of the admin who last saved these settings.",
        ),
        uses_contact_email=True,
    ),
    ScholarlySourceDefinition(
        id="europe-pmc",
        label="Europe PMC",
        auth_mode="none",
        auth_detail="Public REST APIs are available without auth.",
        purpose="Life-sciences literature, references, annotations, and open-access full-text links.",
        planner_fallback_domains=("europepmc.org",),
    ),
    ScholarlySourceDefinition(
        id="doi",
        label="DOI resolver / content negotiation",
        auth_mode="none",
        auth_detail="Resolution and content negotiation via doi.org work without auth.",
        purpose="Resolve DOI targets and request registry-backed metadata formats.",
        planner_fallback_domains=("doi.org",),
    ),
)


SCHOLARLY_SOURCE_DEFINITION_MAP = {
    definition.id: definition for definition in SCHOLARLY_SOURCE_DEFINITIONS
}


SCHOLARLY_SOURCE_CODEBACKED_SKILL_HINTS: dict[str, tuple[str, ...]] = {
    "pubmed": (
        "kdense-paper-lookup",
        "kdense-literature-review",
        "kdense-citation-management",
    ),
    "openalex": (
        "kdense-paper-lookup",
        "kdense-literature-review",
        "kdense-citation-management",
    ),
    "crossref": (
        "kdense-paper-lookup",
        "kdense-citation-management",
    ),
    "europe-pmc": (
        "kdense-paper-lookup",
        "kdense-literature-review",
        "kdense-citation-management",
    ),
    "doi": (
        "kdense-paper-lookup",
        "kdense-citation-management",
    ),
}


def get_scholarly_source_settings(config_or_path: Any = None) -> dict[str, Any]:
    raw = (
        getattr(config_or_path, "SCHOLARLY_API_SOURCES", None)
        if config_or_path is not None
        else None
    )
    return normalize_scholarly_source_settings(raw)


def is_scholarly_source_runtime_enabled(
    source_id: str,
    config_or_path: Any = None,
) -> bool:
    if source_id not in SCHOLARLY_SOURCE_DEFINITION_MAP:
        return False
    settings = get_scholarly_source_settings(config_or_path)["sources"][source_id]
    definition = SCHOLARLY_SOURCE_DEFINITION_MAP[source_id]
    if not settings.get("enabled"):
        return False
    if definition.auth_mode == "required" and not str(settings.get("api_key") or "").strip():
        return False
    return True


def list_enabled_scholarly_runtime_sources(config_or_path: Any = None) -> list[str]:
    return [
        definition.id
        for definition in SCHOLARLY_SOURCE_DEFINITIONS
        if is_scholarly_source_runtime_enabled(definition.id, config_or_path)
    ]


def default_scholarly_source_settings() -> dict[str, Any]:
    return {
        "sources": {
            definition.id: {
                "enabled": False,
                "api_key": "",
                "contact_email": "",
            }
            for definition in SCHOLARLY_SOURCE_DEFINITIONS
        }
    }


def normalize_scholarly_source_settings(payload: Any) -> dict[str, Any]:
    normalized = default_scholarly_source_settings()
    if not isinstance(payload, dict):
        return normalized

    sources = payload.get("sources")
    if not isinstance(sources, dict):
        return normalized

    for source_id, definition in SCHOLARLY_SOURCE_DEFINITION_MAP.items():
        raw = sources.get(source_id, {})
        if not isinstance(raw, dict):
            continue

        normalized["sources"][source_id] = {
            "enabled": bool(raw.get("enabled", False)),
            "api_key": str(raw.get("api_key", "") or "").strip(),
            "contact_email": str(raw.get("contact_email", "") or "").strip(),
        }

    return normalized


def merge_scholarly_source_settings(
    current_payload: Any,
    updates: dict[str, Any] | None,
    *,
    configured_by_email: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_scholarly_source_settings(current_payload)
    raw_updates = updates or {}

    contact_email = (configured_by_email or "").strip().lower()

    for source_id, update in raw_updates.items():
        if source_id not in SCHOLARLY_SOURCE_DEFINITION_MAP or not isinstance(update, dict):
            continue
        current = normalized["sources"][source_id]
        if "enabled" in update:
            current["enabled"] = bool(update.get("enabled"))
        if "api_key" in update:
            current["api_key"] = str(update.get("api_key", "") or "").strip()

        definition = SCHOLARLY_SOURCE_DEFINITION_MAP[source_id]
        if definition.uses_contact_email and contact_email:
            current["contact_email"] = contact_email
        elif "contact_email" in update:
            current["contact_email"] = str(update.get("contact_email", "") or "").strip()

    return normalized


def _collect_registry_domains(value: Any, acc: set[str] | None = None) -> set[str]:
    if acc is None:
        acc = set()

    if isinstance(value, list):
        for item in value:
            _collect_registry_domains(item, acc)
        return acc

    if isinstance(value, dict):
        domain = value.get("domain")
        if isinstance(domain, str) and domain.strip():
            acc.add(domain.strip().lower())

        for nested in value.values():
            _collect_registry_domains(nested, acc)

    return acc


def resolve_scholarly_source_covered_domains(
    source_id: str,
    *,
    planner_registry: Any | None = None,
) -> list[str]:
    definition = SCHOLARLY_SOURCE_DEFINITION_MAP[source_id]
    registry = planner_registry if planner_registry is not None else load_source_registry()
    registry_domains = _collect_registry_domains(registry)
    return [
        domain
        for domain in definition.planner_fallback_domains
        if domain.lower() in registry_domains
    ]


def build_scholarly_source_runtime_coverage(
    source_id: str,
    *,
    planner_registry: Any | None = None,
) -> dict[str, Any]:
    covered_domains = resolve_scholarly_source_covered_domains(
        source_id,
        planner_registry=planner_registry,
    )
    seeded_skill_ids = list(SCHOLARLY_SOURCE_CODEBACKED_SKILL_HINTS.get(source_id, ()))
    return {
        "admin_probe_ready": True,
        "planner_fallback_configured": len(covered_domains) > 0,
        "covered_domains": covered_domains,
        "native_tool_adapter_ready": True,
        "native_tool_adapter_status": "Available",
        "skill_support_status": "Guidance + runtime tools",
        "seeded_skill_ids": seeded_skill_ids,
        "inventory_scope_note": (
            "This reflects code-backed Science lane defaults and known builtin runtime paths. "
            "Live-only custom skills are not introspected here."
        ),
        "ariadne_status": "Admin probe ready; native tool adapter available",
    }


def build_scholarly_source_rows(
    settings_payload: Any,
    *,
    planner_registry: Any | None = None,
) -> list[dict[str, Any]]:
    settings = normalize_scholarly_source_settings(settings_payload)["sources"]
    registry = planner_registry if planner_registry is not None else load_source_registry()

    rows: list[dict[str, Any]] = []
    for definition in SCHOLARLY_SOURCE_DEFINITIONS:
        source_settings = settings[definition.id]
        coverage = build_scholarly_source_runtime_coverage(
            definition.id,
            planner_registry=registry,
        )
        rows.append(
            {
                **asdict(definition),
                "planner_fallback_domains": list(definition.planner_fallback_domains),
                "notes": list(definition.notes),
                **coverage,
                "settings": {
                    "enabled": bool(source_settings.get("enabled", False)),
                    "api_key": str(source_settings.get("api_key", "") or ""),
                },
                "effective_contact_email": str(
                    source_settings.get("contact_email", "") or ""
                ),
            }
        )

    return rows


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}***{value[-2:]}"


def _sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"authorization", "crossref-api-key", "crossref-plus-api-token"}:
            sanitized[key] = _mask_secret(value)
        else:
            sanitized[key] = value
    return sanitized


def _sanitize_params(params: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in params.items():
        if "key" in key.lower() or "token" in key.lower():
            sanitized[key] = _mask_secret(str(value))
        else:
            sanitized[key] = value
    return sanitized


def _protocol_check(
    check_id: str,
    label: str,
    *,
    ok: bool,
    level: ProtocolLevel,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "ok": bool(ok),
        "level": level,
        "detail": detail,
    }


def build_scholarly_probe_request(
    source_id: str,
    settings: dict[str, Any] | None = None,
    *,
    fallback_contact_email: str | None = None,
) -> dict[str, Any]:
    if source_id not in SCHOLARLY_SOURCE_DEFINITION_MAP:
        raise ValueError(f"Unknown scholarly source '{source_id}'")

    normalized_settings = normalize_scholarly_source_settings({"sources": {source_id: settings or {}}})[
        "sources"
    ][source_id]
    contact_email = (
        normalized_settings.get("contact_email") or fallback_contact_email or ""
    ).strip().lower()

    user_agent = "OpenWebUI-ScholarlyProbe/1.0"
    if contact_email:
        user_agent = f"{user_agent} ({contact_email})"

    if source_id == "pubmed":
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": "CRISPR",
            "retmode": "json",
            "retmax": 1,
            "tool": "open-webui",
        }
        if contact_email:
            params["email"] = contact_email
        if normalized_settings.get("api_key"):
            params["api_key"] = normalized_settings["api_key"]
        return {
            "method": "GET",
            "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "openalex":
        params = {
            "search": "CRISPR",
            "per-page": 1,
        }
        if normalized_settings.get("api_key"):
            params["api_key"] = normalized_settings["api_key"]
        return {
            "method": "GET",
            "url": "https://api.openalex.org/works",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "crossref":
        params = {
            "query.title": "CRISPR",
            "rows": 1,
        }
        if contact_email:
            params["mailto"] = contact_email
        return {
            "method": "GET",
            "url": "https://api.crossref.org/works",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "europe-pmc":
        return {
            "method": "GET",
            "url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            "params": {
                "query": "CRISPR",
                "pageSize": 1,
                "format": "json",
            },
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "doi":
        return {
            "method": "GET",
            "url": "https://doi.org/10.1126/science.169.3946.635",
            "params": {},
            "headers": {
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": user_agent,
            },
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    raise ValueError(f"Unhandled scholarly source '{source_id}'")


def _validate_scholarly_probe_payload_shape(
    source_id: str,
    payload: Any,
) -> tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "Expected a JSON object in the upstream response body."

    if source_id == "pubmed":
        if isinstance(payload.get("esearchresult"), dict):
            return True, "PubMed response contains an esearchresult object."
        return False, "PubMed response is missing the esearchresult object."

    if source_id == "openalex":
        if isinstance(payload.get("results"), list):
            return True, "OpenAlex response contains a results list."
        return False, "OpenAlex response is missing the results list."

    if source_id == "crossref":
        if isinstance(payload.get("message"), dict):
            return True, "Crossref response contains a message object."
        return False, "Crossref response is missing the message object."

    if source_id == "europe-pmc":
        if isinstance(payload.get("resultList"), dict) or "hitCount" in payload:
            return True, "Europe PMC response contains expected search result fields."
        return False, "Europe PMC response is missing resultList or hitCount."

    if source_id == "doi":
        if any(key in payload for key in ("DOI", "title", "type", "URL")):
            return True, "DOI resolver response contains citation-style metadata keys."
        return False, "DOI resolver response is missing citation-style metadata fields."

    return False, f"No payload validator is defined for source '{source_id}'."


def assess_scholarly_probe_protocol(
    source_id: str,
    probe_result: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    fallback_contact_email: str | None = None,
    planner_registry: Any | None = None,
) -> dict[str, Any]:
    if source_id not in SCHOLARLY_SOURCE_DEFINITION_MAP:
        raise ValueError(f"Unknown scholarly source '{source_id}'")

    definition = SCHOLARLY_SOURCE_DEFINITION_MAP[source_id]
    normalized_settings = normalize_scholarly_source_settings(
        {"sources": {source_id: settings or {}}}
    )["sources"][source_id]
    contact_email = (
        normalized_settings.get("contact_email") or fallback_contact_email or ""
    ).strip().lower()
    request_spec = build_scholarly_probe_request(
        source_id,
        normalized_settings,
        fallback_contact_email=fallback_contact_email,
    )
    coverage = build_scholarly_source_runtime_coverage(
        source_id,
        planner_registry=planner_registry,
    )

    checks: list[dict[str, Any]] = []
    api_key = str(normalized_settings.get("api_key") or "").strip()
    if definition.auth_mode == "required":
        checks.append(
            _protocol_check(
                "auth-configured",
                "Required API key configured",
                ok=bool(api_key),
                level="required",
                detail=(
                    "A required API key is configured."
                    if api_key
                    else f"{definition.label} expects an API key for native Ariadne use."
                ),
            )
        )
    elif definition.auth_mode == "optional":
        checks.append(
            _protocol_check(
                "auth-configured",
                "Optional API key configured",
                ok=bool(api_key),
                level="advisory",
                detail=(
                    "Optional API key is configured."
                    if api_key
                    else f"{definition.label} can run without a key, but throughput will be lower."
                ),
            )
        )

    if definition.uses_contact_email:
        checks.append(
            _protocol_check(
                "contact-email",
                "Contact email available",
                ok=bool(contact_email),
                level="advisory",
                detail=(
                    f"Contact email {contact_email} will be sent with polite-pool requests."
                    if contact_email
                    else "No contact email is configured for polite-pool requests."
                ),
            )
        )

    response_request = (probe_result or {}).get("request") or {}
    request_url = str(response_request.get("url") or "")
    checks.append(
        _protocol_check(
            "canonical-target",
            "Canonical request target",
            ok=request_url.startswith(request_spec["url"]),
            level="required",
            detail=(
                f"Request targets {request_spec['url']}."
                if request_url.startswith(request_spec["url"])
                else f"Expected request target {request_spec['url']}, got {request_url or 'nothing'}."
            ),
        )
    )

    checks.append(
        _protocol_check(
            "planner-fallback",
            "Planner fallback coverage",
            ok=coverage["planner_fallback_configured"],
            level="advisory",
            detail=(
                "Web planner fallback is configured for this source family."
                if coverage["planner_fallback_configured"]
                else "Web planner fallback is not configured for this source family yet."
            ),
        )
    )
    checks.append(
        _protocol_check(
            "native-tool-adapter",
            "Native Ariadne tool adapter",
            ok=coverage["native_tool_adapter_ready"],
            level="advisory",
            detail=(
                "A dedicated Ariadne tool adapter is available."
                if coverage["native_tool_adapter_ready"]
                else "Admin probe exists, but there is no dedicated Ariadne runtime tool adapter yet."
            ),
        )
    )
    checks.append(
        _protocol_check(
            "science-skill-coverage",
            "Code-backed science skill coverage",
            ok=len(coverage["seeded_skill_ids"]) > 0,
            level="advisory",
            detail=(
                f"Code-backed science skills reference this source family: {', '.join(coverage['seeded_skill_ids'])}."
                if coverage["seeded_skill_ids"]
                else "No code-backed science skills currently reference this source family."
            ),
        )
    )

    if not probe_result.get("status"):
        checks.append(
            _protocol_check(
                "probe-completed",
                "Probe request completed",
                ok=False,
                level="required",
                detail=str(probe_result.get("error") or "Probe request failed."),
            )
        )
    else:
        response_payload = (probe_result.get("response") or {})
        response_status = int(response_payload.get("status") or 0)
        status_ok = 200 <= response_status < 300
        auth_hint = ""
        if response_status in {401, 403} and definition.auth_mode == "required":
            auth_hint = " This usually means the configured API key is missing or invalid."
        checks.append(
            _protocol_check(
                "upstream-status",
                "Upstream HTTP status",
                ok=status_ok,
                level="required",
                detail=(
                    f"Upstream returned {response_status}."
                    if status_ok
                    else f"Upstream returned {response_status}.{auth_hint}"
                ),
            )
        )

        if status_ok:
            payload_ok, payload_detail = _validate_scholarly_probe_payload_shape(
                source_id,
                response_payload.get("body_json"),
            )
            checks.append(
                _protocol_check(
                    "payload-shape",
                    "Expected payload shape",
                    ok=payload_ok,
                    level="required",
                    detail=payload_detail,
                )
            )

    failed_required = [
        check for check in checks if check["level"] == "required" and not check["ok"]
    ]
    failed_advisory = [
        check for check in checks if check["level"] == "advisory" and not check["ok"]
    ]
    if failed_required:
        status = "fail"
        summary = f"Required checks failed: {failed_required[0]['detail']}"
    elif failed_advisory:
        status = "warn"
        summary = f"Probe contract passed, but runtime coverage is still partial: {failed_advisory[0]['detail']}"
    else:
        status = "pass"
        summary = "Probe contract passed and the expected response shape was returned."

    return {
        "status": status,
        "summary": summary,
        "checks": checks,
        "coverage": coverage,
    }


async def probe_scholarly_source(
    source_id: str,
    settings: dict[str, Any] | None = None,
    *,
    fallback_contact_email: str | None = None,
) -> dict[str, Any]:
    request_spec = build_scholarly_probe_request(
        source_id,
        settings,
        fallback_contact_email=fallback_contact_email,
    )

    timeout = aiohttp.ClientTimeout(total=request_spec["timeout_seconds"])
    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.request(
                request_spec["method"],
                request_spec["url"],
                params=request_spec.get("params"),
                headers=request_spec.get("headers"),
                allow_redirects=request_spec.get("allow_redirects", True),
            ) as response:
                body_text = await response.text()
                body_json = None
                try:
                    body_json = json.loads(body_text)
                except Exception:
                    body_json = None

                sanitized_params = _sanitize_params(
                    {key: value for key, value in request_spec.get("params", {}).items()}
                )
                query_string = urlencode(
                    [(key, value) for key, value in sanitized_params.items() if value not in (None, "")]
                )
                request_url = request_spec["url"]
                if query_string:
                    request_url = f"{request_url}?{query_string}"

                return {
                    "status": True,
                    "source_id": source_id,
                    "request": {
                        "method": request_spec["method"],
                        "url": request_url,
                        "headers": _sanitize_headers(
                            {
                                key: value
                                for key, value in request_spec.get("headers", {}).items()
                            }
                        ),
                    },
                    "response": {
                        "status": response.status,
                        "reason": response.reason,
                        "url": str(response.url),
                        "history": [
                            {"status": item.status, "url": str(item.url)}
                            for item in response.history
                        ],
                        "headers": dict(response.headers),
                        "body_text": body_text,
                        "body_json": body_json,
                    },
                }
    except Exception as exc:
        sanitized_params = _sanitize_params(
            {key: value for key, value in request_spec.get("params", {}).items()}
        )
        query_string = urlencode(
            [(key, value) for key, value in sanitized_params.items() if value not in (None, "")]
        )
        request_url = request_spec["url"]
        if query_string:
            request_url = f"{request_url}?{query_string}"

        return {
            "status": False,
            "source_id": source_id,
            "request": {
                "method": request_spec["method"],
                "url": request_url,
                "headers": _sanitize_headers(
                    {key: value for key, value in request_spec.get("headers", {}).items()}
                ),
            },
            "error": str(exc),
        }


def _reconstruct_openalex_abstract(abstract_index: Any) -> str | None:
    if not isinstance(abstract_index, dict) or not abstract_index:
        return None
    positions: list[tuple[int, str]] = []
    for token, indexes in abstract_index.items():
        if not isinstance(token, str) or not isinstance(indexes, list):
            continue
        for idx in indexes:
            if isinstance(idx, int):
                positions.append((idx, token))
    if not positions:
        return None
    positions.sort(key=lambda item: item[0])
    return " ".join(token for _, token in positions).strip() or None


def _extract_first_year(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    for idx in range(max(0, len(text) - 3)):
        candidate = text[idx : idx + 4]
        if candidate.isdigit():
            return candidate
    return None


def _normalize_doi(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower().startswith("https://doi.org/"):
        text = text[16:]
    if text.lower().startswith("http://doi.org/"):
        text = text[15:]
    if text.lower().startswith("doi:"):
        text = text[4:]
    return text.strip() or None


def _infer_study_signal(values: list[str]) -> StudySignal:
    haystack = " ".join(value.strip().lower() for value in values if value).strip()
    if not haystack:
        return "unknown"
    if "guideline" in haystack or "practice guideline" in haystack:
        return "guideline"
    if "meta-analysis" in haystack or "meta analysis" in haystack:
        return "meta_analysis"
    if "systematic review" in haystack:
        return "systematic_review"
    if "review" in haystack:
        return "review"
    if "preprint" in haystack:
        return "preprint"
    if "case report" in haystack or "case series" in haystack:
        return "case_report"
    if (
        "randomized" in haystack
        or "clinical trial" in haystack
        or "trial" in haystack
        or "controlled study" in haystack
    ):
        return "trial"
    if (
        "observational" in haystack
        or "cohort" in haystack
        or "case-control" in haystack
        or "cross-sectional" in haystack
    ):
        return "observational"
    return "unknown"


def _reasoning_scope_for_grounding(grounding_level: GroundingLevel) -> ReasoningScope:
    if grounding_level == "abstract":
        return "abstract_grounded"
    if grounding_level == "full_text":
        return "full_text_grounded"
    return "bibliographic_discovery"


def _summarize_evidence_profile(items: list[dict[str, Any]]) -> dict[str, Any]:
    grounding_counts: dict[str, int] = {}
    study_signal_counts: dict[str, int] = {}
    for item in items:
        grounding = str(item.get("grounding_level") or "metadata")
        grounding_counts[grounding] = grounding_counts.get(grounding, 0) + 1
        study_signal = str(item.get("study_signal") or "unknown")
        study_signal_counts[study_signal] = study_signal_counts.get(study_signal, 0) + 1
    return {
        "grounding_levels": grounding_counts,
        "study_signals": study_signal_counts,
    }


def _build_result_item(
    *,
    source_id: str,
    title: Any,
    authors: list[str] | None = None,
    year: Any = None,
    venue: Any = None,
    doi: Any = None,
    pmid: Any = None,
    pmcid: Any = None,
    arxiv_id: Any = None,
    url: Any = None,
    abstract_text: Any = None,
    full_text: Any = None,
    full_text_available: bool = False,
    access_status: AccessStatus = "unknown",
    study_signal_inputs: list[str] | None = None,
    warnings: list[str] | None = None,
    why_matched: str | None = None,
) -> dict[str, Any]:
    normalized_abstract = str(abstract_text or "").strip()
    normalized_full_text = str(full_text or "").strip()
    grounding_level: GroundingLevel = "metadata"
    if normalized_full_text:
        grounding_level = "full_text"
    elif normalized_abstract:
        grounding_level = "abstract"
    elif source_id == "doi":
        grounding_level = "doi_landing"

    anchor_fields = {
        "title": str(title or "").strip() or None,
        "authors": [value for value in (authors or []) if value],
        "venue_or_journal": str(venue or "").strip() or None,
        "year": _extract_first_year(year),
        "doi": _normalize_doi(doi),
        "pmid": str(pmid or "").strip() or None,
        "pmcid": str(pmcid or "").strip() or None,
        "arxiv_id": str(arxiv_id or "").strip() or None,
        "url": str(url or "").strip() or None,
    }
    return {
        "source_id": source_id,
        "title": anchor_fields["title"],
        "authors": anchor_fields["authors"],
        "year": anchor_fields["year"],
        "venue": anchor_fields["venue_or_journal"],
        "doi": anchor_fields["doi"],
        "pmid": anchor_fields["pmid"],
        "pmcid": anchor_fields["pmcid"],
        "arxiv_id": anchor_fields["arxiv_id"],
        "url": anchor_fields["url"],
        "grounding_level": grounding_level,
        "access_status": access_status,
        "reasoning_scope": _reasoning_scope_for_grounding(grounding_level),
        "study_signal": _infer_study_signal(study_signal_inputs or []),
        "anchor_fields": anchor_fields,
        "abstract_available": bool(normalized_abstract),
        "full_text_available": bool(full_text_available or normalized_full_text),
        "warnings": list(warnings or []),
        "why_matched": why_matched,
        **({"abstract_text": normalized_abstract} if normalized_abstract else {}),
        **({"full_text_excerpt": normalized_full_text[:4000]} if normalized_full_text else {}),
    }


async def _request_json(
    request_spec: dict[str, Any],
) -> tuple[int, str, str, dict[str, str], Any]:
    timeout = aiohttp.ClientTimeout(total=request_spec["timeout_seconds"])
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        async with session.request(
            request_spec["method"],
            request_spec["url"],
            params=request_spec.get("params"),
            headers=request_spec.get("headers"),
            allow_redirects=request_spec.get("allow_redirects", True),
        ) as response:
            body_text = await response.text()
            body_json = None
            try:
                body_json = json.loads(body_text)
            except Exception:
                body_json = None
            return response.status, response.reason, str(response.url), dict(response.headers), (
                body_json if body_json is not None else body_text
            )


def _build_runtime_user_agent(contact_email: str | None = None) -> str:
    base = "OpenWebUI-ScholarlyRuntime/1.0"
    email = str(contact_email or "").strip().lower()
    if email:
        return f"{base} ({email})"
    return base


def build_scholarly_runtime_request(
    source_id: str,
    *,
    query: str | None = None,
    max_results: int = 5,
    doi: str | None = None,
    settings: dict[str, Any] | None = None,
    fallback_contact_email: str | None = None,
) -> dict[str, Any]:
    if source_id not in SCHOLARLY_SOURCE_DEFINITION_MAP:
        raise ValueError(f"Unknown scholarly source '{source_id}'")

    normalized_settings = normalize_scholarly_source_settings(
        {"sources": {source_id: settings or {}}}
    )["sources"][source_id]
    definition = SCHOLARLY_SOURCE_DEFINITION_MAP[source_id]
    api_key = str(normalized_settings.get("api_key") or "").strip()
    contact_email = (
        normalized_settings.get("contact_email") or fallback_contact_email or ""
    ).strip().lower()

    if definition.auth_mode == "required" and not api_key:
        raise ValueError(f"{definition.label} requires an API key for runtime use")

    bounded_max_results = max(1, min(int(max_results or 5), 10))
    user_agent = _build_runtime_user_agent(contact_email)

    if source_id == "pubmed":
        params: dict[str, Any] = {
            "db": "pubmed",
            "term": str(query or "").strip(),
            "retmode": "json",
            "retmax": bounded_max_results,
            "sort": "relevance",
            "tool": "open-webui",
        }
        if contact_email:
            params["email"] = contact_email
        if api_key:
            params["api_key"] = api_key
        return {
            "method": "GET",
            "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "openalex":
        params = {
            "search": str(query or "").strip(),
            "per-page": bounded_max_results,
        }
        if api_key:
            params["api_key"] = api_key
        return {
            "method": "GET",
            "url": "https://api.openalex.org/works",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "crossref":
        params = {
            "query": str(query or "").strip(),
            "rows": bounded_max_results,
        }
        if contact_email:
            params["mailto"] = contact_email
        return {
            "method": "GET",
            "url": "https://api.crossref.org/works",
            "params": params,
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "europe-pmc":
        return {
            "method": "GET",
            "url": "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            "params": {
                "query": str(query or "").strip(),
                "pageSize": bounded_max_results,
                "format": "json",
                "resultType": "core",
            },
            "headers": {"Accept": "application/json", "User-Agent": user_agent},
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    if source_id == "doi":
        normalized_doi = _normalize_doi(doi)
        if not normalized_doi:
            raise ValueError("A DOI is required for scholarly DOI resolution")
        return {
            "method": "GET",
            "url": f"https://doi.org/{normalized_doi}",
            "params": {},
            "headers": {
                "Accept": "application/vnd.citationstyles.csl+json",
                "User-Agent": user_agent,
            },
            "allow_redirects": True,
            "timeout_seconds": 20,
        }

    raise ValueError(f"Unhandled scholarly source '{source_id}'")


async def _build_pubmed_items(
    pmids: list[str],
    *,
    settings: dict[str, Any],
    fallback_contact_email: str | None = None,
) -> list[dict[str, Any]]:
    if not pmids:
        return []

    source_settings = normalize_scholarly_source_settings({"sources": {"pubmed": settings}})["sources"][
        "pubmed"
    ]
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "json",
        "version": "2.0",
        "tool": "open-webui",
    }
    contact_email = (
        source_settings.get("contact_email") or fallback_contact_email or ""
    ).strip().lower()
    if contact_email:
        params["email"] = contact_email
    api_key = str(source_settings.get("api_key") or "").strip()
    if api_key:
        params["api_key"] = api_key

    summary_spec = {
        "method": "GET",
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
        "params": params,
        "headers": {
            "Accept": "application/json",
            "User-Agent": _build_runtime_user_agent(contact_email),
        },
        "allow_redirects": True,
        "timeout_seconds": 20,
    }
    _, _, _, _, summary_payload = await _request_json(summary_spec)
    summary_result = summary_payload.get("result") if isinstance(summary_payload, dict) else {}

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
        "tool": "open-webui",
    }
    if contact_email:
        fetch_params["email"] = contact_email
    if api_key:
        fetch_params["api_key"] = api_key
    fetch_spec = {
        "method": "GET",
        "url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
        "params": fetch_params,
        "headers": {
            "Accept": "application/xml",
            "User-Agent": _build_runtime_user_agent(contact_email),
        },
        "allow_redirects": True,
        "timeout_seconds": 20,
    }
    _, _, _, _, fetch_payload = await _request_json(fetch_spec)
    abstract_map: dict[str, str] = {}
    pub_type_map: dict[str, list[str]] = {}
    try:
        root = ElementTree.fromstring(fetch_payload if isinstance(fetch_payload, str) else "")
        for article in root.findall(".//PubmedArticle"):
            pmid = (article.findtext(".//PMID") or "").strip()
            abstract_parts = [
                "".join(node.itertext()).strip()
                for node in article.findall(".//Abstract/AbstractText")
                if "".join(node.itertext()).strip()
            ]
            if pmid and abstract_parts:
                abstract_map[pmid] = "\n".join(abstract_parts).strip()
            pub_types = [
                "".join(node.itertext()).strip()
                for node in article.findall(".//PublicationTypeList/PublicationType")
                if "".join(node.itertext()).strip()
            ]
            if pmid and pub_types:
                pub_type_map[pmid] = pub_types
    except Exception:
        pass

    items: list[dict[str, Any]] = []
    for pmid in pmids:
        summary = summary_result.get(pmid) if isinstance(summary_result, dict) else {}
        article_ids = summary.get("articleids") or []
        doi = None
        pmcid = None
        for article_id in article_ids:
            id_type = str((article_id or {}).get("idtype") or "").strip().lower()
            value = str((article_id or {}).get("value") or "").strip()
            if id_type == "doi" and value:
                doi = value
            if id_type == "pmc" and value:
                pmcid = value
        authors = [
            str(author.get("name") or "").strip()
            for author in (summary.get("authors") or [])
            if isinstance(author, dict) and str(author.get("name") or "").strip()
        ]
        items.append(
            _build_result_item(
                source_id="pubmed",
                title=summary.get("title"),
                authors=authors,
                year=summary.get("pubdate"),
                venue=summary.get("fulljournalname") or summary.get("source"),
                doi=doi,
                pmid=pmid,
                pmcid=pmcid,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                abstract_text=abstract_map.get(pmid),
                full_text_available=bool(pmcid),
                access_status="open" if pmcid else "unknown",
                study_signal_inputs=[
                    str(summary.get("pubtype") or ""),
                    *(pub_type_map.get(pmid) or []),
                ],
                warnings=(
                    ["Open access full text may exist in PMC, but it was not loaded directly."]
                    if pmcid
                    else []
                ),
            )
        )
    return items


async def execute_scholarly_runtime_query(
    source_id: str,
    *,
    query: str | None = None,
    doi: str | None = None,
    max_results: int = 5,
    settings: dict[str, Any] | None = None,
    fallback_contact_email: str | None = None,
) -> dict[str, Any]:
    request_spec = build_scholarly_runtime_request(
        source_id,
        query=query,
        doi=doi,
        max_results=max_results,
        settings=settings,
        fallback_contact_email=fallback_contact_email,
    )
    response_status, response_reason, response_url, _headers, payload = await _request_json(
        request_spec
    )
    if response_status < 200 or response_status >= 300:
        return {
            "status": "error",
            "source_id": source_id,
            "query": query,
            "doi": _normalize_doi(doi),
            "error": f"{source_id} upstream returned {response_status} {response_reason}".strip(),
            "upstream_status": response_status,
            "items": [],
            "warnings": [],
        }

    items: list[dict[str, Any]] = []
    warnings: list[str] = []

    if source_id == "pubmed":
        esearch = payload.get("esearchresult") if isinstance(payload, dict) else {}
        pmids = [str(value).strip() for value in (esearch.get("idlist") or []) if str(value).strip()]
        source_settings = normalize_scholarly_source_settings({"sources": {"pubmed": settings or {}}})[
            "sources"
        ]["pubmed"]
        items = await _build_pubmed_items(
            pmids[: max(1, min(int(max_results or 5), 10))],
            settings=source_settings,
            fallback_contact_email=fallback_contact_email,
        )
    elif source_id == "openalex":
        for work in (payload.get("results") or [])[: max(1, min(int(max_results or 5), 10))]:
            if not isinstance(work, dict):
                continue
            abstract_text = _reconstruct_openalex_abstract(work.get("abstract_inverted_index"))
            authors = [
                str((authorship.get("author") or {}).get("display_name") or "").strip()
                for authorship in (work.get("authorships") or [])
                if isinstance(authorship, dict)
                and str((authorship.get("author") or {}).get("display_name") or "").strip()
            ]
            open_access = work.get("open_access") or {}
            primary_location = work.get("primary_location") or {}
            best_location = work.get("best_oa_location") or {}
            url = (
                best_location.get("landing_page_url")
                or primary_location.get("landing_page_url")
                or work.get("id")
            )
            study_signal_inputs = [
                str(work.get("type") or ""),
                str(work.get("type_crossref") or ""),
            ]
            if work.get("is_preprint"):
                study_signal_inputs.append("preprint")
            items.append(
                _build_result_item(
                    source_id="openalex",
                    title=work.get("display_name"),
                    authors=authors,
                    year=work.get("publication_year"),
                    venue=((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
                    doi=work.get("doi"),
                    pmid=(work.get("ids") or {}).get("pmid"),
                    pmcid=(work.get("ids") or {}).get("pmcid"),
                    arxiv_id=(work.get("ids") or {}).get("arxiv"),
                    url=url,
                    abstract_text=abstract_text,
                    full_text_available=bool(open_access.get("is_oa") or work.get("has_fulltext")),
                    access_status="open" if open_access.get("is_oa") else "restricted",
                    study_signal_inputs=study_signal_inputs,
                    warnings=(
                        ["OpenAlex metadata may point to full text, but the full text itself was not loaded."]
                        if open_access.get("is_oa") or work.get("has_fulltext")
                        else []
                    ),
                )
            )
    elif source_id == "crossref":
        message = payload.get("message") if isinstance(payload, dict) else {}
        for work in (message.get("items") or [])[: max(1, min(int(max_results or 5), 10))]:
            if not isinstance(work, dict):
                continue
            title_values = work.get("title") or []
            author_values = []
            for author in work.get("author") or []:
                if not isinstance(author, dict):
                    continue
                given = str(author.get("given") or "").strip()
                family = str(author.get("family") or "").strip()
                name = " ".join(value for value in (given, family) if value).strip()
                if name:
                    author_values.append(name)
            items.append(
                _build_result_item(
                    source_id="crossref",
                    title=title_values[0] if title_values else None,
                    authors=author_values,
                    year=((work.get("published-print") or {}).get("date-parts") or [[None]])[0][0]
                    or ((work.get("published-online") or {}).get("date-parts") or [[None]])[0][0],
                    venue=(work.get("container-title") or [None])[0],
                    doi=work.get("DOI"),
                    url=(work.get("URL") or f"https://doi.org/{work.get('DOI')}") if work.get("DOI") else work.get("URL"),
                    study_signal_inputs=[str(work.get("type") or "")],
                )
            )
    elif source_id == "europe-pmc":
        result_list = (payload.get("resultList") or {}).get("result") or []
        for work in result_list[: max(1, min(int(max_results or 5), 10))]:
            if not isinstance(work, dict):
                continue
            pmcid = str(work.get("pmcid") or "").strip() or None
            is_open = str(work.get("isOpenAccess") or "").strip().upper() == "Y"
            full_text = None
            if is_open and pmcid:
                try:
                    full_text_spec = {
                        "method": "GET",
                        "url": f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML",
                        "params": {},
                        "headers": {
                            "Accept": "application/xml",
                            "User-Agent": _build_runtime_user_agent(
                                fallback_contact_email
                            ),
                        },
                        "allow_redirects": True,
                        "timeout_seconds": 20,
                    }
                    full_text_status, _, _, _, full_text_payload = await _request_json(full_text_spec)
                    if 200 <= full_text_status < 300 and isinstance(full_text_payload, str):
                        full_text = " ".join(full_text_payload.split())[:12000]
                except Exception:
                    warnings.append(
                        f"Europe PMC full text could not be loaded for {pmcid}; falling back to abstract-grounded output."
                    )
            author_string = str(work.get("authorString") or "").strip()
            authors = [part.strip() for part in author_string.split(",") if part.strip()]
            items.append(
                _build_result_item(
                    source_id="europe-pmc",
                    title=work.get("title"),
                    authors=authors,
                    year=work.get("pubYear"),
                    venue=work.get("journalTitle"),
                    doi=work.get("doi"),
                    pmid=work.get("pmid"),
                    pmcid=pmcid,
                    url=(
                        f"https://europepmc.org/article/{work.get('source')}/{work.get('id')}"
                        if work.get("source") and work.get("id")
                        else None
                    ),
                    abstract_text=work.get("abstractText"),
                    full_text=full_text,
                    full_text_available=bool(is_open and pmcid),
                    access_status="open" if is_open else "restricted",
                    study_signal_inputs=[
                        *(work.get("pubTypeList") or []),
                        str(work.get("pubType") or ""),
                    ],
                    warnings=(
                        ["Europe PMC record is open access, but only abstract-level grounding was available."]
                        if is_open and pmcid and not full_text
                        else []
                    ),
                )
            )
    elif source_id == "doi":
        payload_dict = payload if isinstance(payload, dict) else {}
        items = [
            _build_result_item(
                source_id="doi",
                title=(payload_dict.get("title") or [None])[0]
                if isinstance(payload_dict.get("title"), list)
                else payload_dict.get("title"),
                authors=[
                    " ".join(
                        part
                        for part in (
                            str(author.get("given") or "").strip(),
                            str(author.get("family") or "").strip(),
                        )
                        if part
                    ).strip()
                    for author in (payload_dict.get("author") or [])
                    if isinstance(author, dict)
                ],
                year=((payload_dict.get("issued") or {}).get("date-parts") or [[None]])[0][0],
                venue=(payload_dict.get("container-title") or [None])[0]
                if isinstance(payload_dict.get("container-title"), list)
                else payload_dict.get("container-title"),
                doi=payload_dict.get("DOI") or doi,
                url=payload_dict.get("URL") or (f"https://doi.org/{_normalize_doi(doi)}" if doi else None),
                study_signal_inputs=[str(payload_dict.get("type") or "")],
                access_status="landing_only",
                warnings=["DOI resolution gives registry-backed metadata, not full text."],
            )
        ]
    else:
        raise ValueError(f"Unhandled scholarly source '{source_id}'")

    return {
        "status": "ok",
        "source_id": source_id,
        "query": str(query or "").strip() or None,
        "doi": _normalize_doi(doi),
        "items": items,
        "item_count": len(items),
        "warnings": warnings,
        "upstream_status": response_status,
        "upstream_url": response_url,
        "evidence_profile": _summarize_evidence_profile(items),
        "next_step_hint": (
            "Use exact identifiers or a source with richer access if you need stronger grounding."
            if any(item.get("reasoning_scope") != "full_text_grounded" for item in items)
            else "You have at least one full-text-grounded record available."
        ),
    }
