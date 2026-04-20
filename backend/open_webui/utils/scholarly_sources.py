import json
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import urlencode

import aiohttp

from open_webui.retrieval.web.planner import load_source_registry


AuthMode = Literal["none", "optional", "required"]


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


def build_scholarly_source_rows(
    settings_payload: Any,
    *,
    planner_registry: Any | None = None,
) -> list[dict[str, Any]]:
    settings = normalize_scholarly_source_settings(settings_payload)["sources"]
    registry = planner_registry if planner_registry is not None else load_source_registry()
    registry_domains = _collect_registry_domains(registry)

    rows: list[dict[str, Any]] = []
    for definition in SCHOLARLY_SOURCE_DEFINITIONS:
        source_settings = settings[definition.id]
        covered_domains = [
            domain
            for domain in definition.planner_fallback_domains
            if domain.lower() in registry_domains
        ]
        rows.append(
            {
                **asdict(definition),
                "planner_fallback_domains": list(definition.planner_fallback_domains),
                "notes": list(definition.notes),
                "ariadne_status": "Planned native API target",
                "planner_fallback_configured": len(covered_domains) > 0,
                "covered_domains": covered_domains,
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
