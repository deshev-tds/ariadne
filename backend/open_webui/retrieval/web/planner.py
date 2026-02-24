import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field


SOURCE_REGISTRY_PATH = Path(__file__).with_name("source_registry.json")
MAX_QUERY_LENGTH = 256
PLANNER_MODES = ("rules_only", "hybrid_rewriter", "model_only")

FLUFF_PREFIXES = (
    "can you",
    "could you",
    "please",
    "i need",
    "i want",
    "tell me",
    "help me",
)

PRESERVE_TOKEN_PATTERNS = (
    re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Z]{2,}-\d{2,}\b"),
    re.compile(r"--[\w-]+"),
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\b[\w.-]+/[\w./:-]+\b"),
    re.compile(r"\b[\w./:-]*\d[\w./:-]*\b"),
)

QUOTE_TOKEN_PATTERNS = (
    re.compile(r"`([^`]{2,180})`"),
    re.compile(r'"([^"\n]{2,180})"'),
)

INTENT_REQUIREMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "official_guidance": re.compile(
        r"\b(official\s+guidance|official\s+docs?|canonical\s+docs?|source\s+of\s+truth)\b",
        re.IGNORECASE,
    ),
    "github_issues": re.compile(
        r"\b(github\s+issues?|issues?\s+on\s+github|issue\s+tracker|bug\s+reports?)\b",
        re.IGNORECASE,
    ),
    "current_fix": re.compile(
        r"\b(current\s+(?:recommended\s+)?fix|latest\s+fix|current\s+workaround|recent\s+fix|known\s+regression)\b",
        re.IGNORECASE,
    ),
}

FRESHNESS_HIGH = "high"
FRESHNESS_MEDIUM = "medium"
FRESHNESS_STABLE = "stable"

TRUST_TIER_SCORES: dict[str, float] = {
    "A": 1.00,
    "A-": 0.92,
    "B+": 0.82,
    "B": 0.72,
    "C": 0.45,
}

LEGACY_TIER_SCORES: dict[str, float] = {
    "primary": 1.00,
    "secondary": 0.75,
    "community": 0.45,
}

TRUST_FLOOR_UNKNOWN = 0.55

TOPIC_FAMILY_ORDER: dict[str, tuple[str, ...]] = {
    "science_academic": (
        "primary_index",
        "primary_preprint",
        "primary_journal",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "medicine_health": (
        "primary_regulator",
        "primary_index",
        "primary_journal",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "software_devops": (
        "primary_docs",
        "primary_canonical",
        "primary_index",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "software_apis_devops": (
        "primary_docs",
        "primary_canonical",
        "primary_index",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "ai_ml_local_llm": (
        "primary_docs",
        "primary_preprint",
        "primary_journal",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "cybersecurity": (
        "primary_canonical",
        "primary_regulator",
        "primary_docs",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "legal_compliance": (
        "primary_canonical",
        "primary_regulator",
        "primary_docs",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "finance_macro_company": (
        "primary_canonical",
        "primary_regulator",
        "primary_docs",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "hardware_components": (
        "primary_docs",
        "primary_canonical",
        "primary_index",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
    "news_current_events": (
        "primary_canonical",
        "primary_regulator",
        "primary_docs",
        "secondary_news",
        "secondary_analysis",
        "secondary_index",
        "community",
    ),
    "general": (
        "primary_docs",
        "primary_canonical",
        "primary_index",
        "secondary_analysis",
        "secondary_index",
        "secondary_news",
        "community",
    ),
}

INTENT_RULES: dict[str, tuple[str, ...]] = {
    "current_news": (
        "today",
        "latest",
        "breaking",
        "current",
        "recent",
        "this week",
        "this month",
        "news",
    ),
    "technical_debug": (
        "error",
        "exception",
        "stack trace",
        "traceback",
        "debug",
        "failed",
        "failure",
        "bug",
        "issue",
        "segfault",
        "crash",
    ),
    "docs_api": (
        "api",
        "sdk",
        "documentation",
        "docs",
        "endpoint",
        "parameter",
        "reference",
        "spec",
    ),
    "science_medical": (
        "study",
        "clinical",
        "trial",
        "paper",
        "pubmed",
        "medical",
        "medicine",
        "health",
    ),
    "legal_compliance": (
        "law",
        "legal",
        "regulation",
        "gdpr",
        "compliance",
        "statute",
        "directive",
    ),
    "finance_macro": (
        "inflation",
        "interest rate",
        "sec filing",
        "earnings",
        "gdp",
        "macro",
        "finance",
        "revenue",
    ),
    "hardware_components": (
        "cpu",
        "gpu",
        "motherboard",
        "bios",
        "firmware",
        "laptop",
        "hardware",
    ),
    "cybersecurity": (
        "cve",
        "vulnerability",
        "exploit",
        "mitre",
        "cisa",
        "nvd",
        "ransomware",
    ),
    "ai_ml_local_llm": (
        "llm",
        "local model",
        "huggingface",
        "vllm",
        "transformer",
        "inference",
        "quantization",
        "benchmark",
    ),
}

TOPIC_BY_INTENT = {
    "current_news": "news_current_events",
    "technical_debug": "software_apis_devops",
    "docs_api": "software_apis_devops",
    "science_medical": "science_academic",
    "legal_compliance": "legal_compliance",
    "finance_macro": "finance_macro_company",
    "hardware_components": "hardware_components",
    "cybersecurity": "cybersecurity",
    "ai_ml_local_llm": "ai_ml_local_llm",
}


class PlannedQuery(BaseModel):
    kind: str
    query: str
    domain: Optional[str] = None


class SelectedSource(BaseModel):
    domain: str
    tier: str
    trust_tier: str = "B"
    source_type: str = "secondary_analysis"
    access: str = "open"
    freshness_profile: str = FRESHNESS_MEDIUM
    default_priority: int = 50
    prefer_for_time_sensitive: bool = False
    prefer_for_exact_facts: bool = False
    prefer_for_community_signals: bool = False


class WebSearchPlan(BaseModel):
    intent: str
    topic: str
    time_sensitive: bool
    community_requested: bool
    mode: str = "rules_only"
    selected_domains: list[str] = Field(default_factory=list)
    selected_sources: list[SelectedSource] = Field(default_factory=list)
    base_exact_query: str
    base_general_query: str
    preserve_tokens: list[str] = Field(default_factory=list)
    anchors: dict[str, list[str]] = Field(default_factory=dict)
    intent_requirements: list[str] = Field(default_factory=list)
    topic_candidates: list[str] = Field(default_factory=list)
    allowed_domains_ranked: list[str] = Field(default_factory=list)
    planned_queries: list[PlannedQuery] = Field(default_factory=list)
    rewriter_model_used: Optional[str] = None
    rewriter_fallback_used: bool = False
    fallback_reason: Optional[str] = None


class NormalizedSource(BaseModel):
    domain: str
    topic: str
    family: str
    source_type: str
    trust_tier: str
    trust_score: float
    access: str
    freshness_profile: str
    use_for: list[str] = Field(default_factory=list)
    avoid_for: list[str] = Field(default_factory=list)
    default_priority: int = 50
    allow_site_constraint: bool = True
    prefer_for_time_sensitive: bool = False
    prefer_for_exact_facts: bool = False
    prefer_for_community_signals: bool = False


def normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


@lru_cache(maxsize=1)
def load_source_registry() -> dict[str, Any]:
    try:
        with SOURCE_REGISTRY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "topics": {}}


def _normalized_freshness_score(value: str) -> float:
    normalized = (value or FRESHNESS_MEDIUM).strip().lower()
    if normalized == FRESHNESS_HIGH:
        return 1.0
    if normalized == FRESHNESS_STABLE:
        return 0.5
    return 0.75


def _family_rank(topic: str, family: str) -> int:
    ordered = TOPIC_FAMILY_ORDER.get(topic, TOPIC_FAMILY_ORDER["general"])
    try:
        return ordered.index(family)
    except ValueError:
        return len(ordered)


def _normalize_trust_tier(trust_tier: str) -> str:
    normalized = (trust_tier or "").strip().upper()
    if normalized == "B-":
        normalized = "B"
    if normalized not in TRUST_TIER_SCORES:
        raise ValueError(f"Unknown trust tier: {trust_tier}")
    return normalized


def _extract_hint_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return default


def _extract_hint_int(value: Any, default: int = 50) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed >= 0 else default


def _family_from_source_type(source_type: str) -> str:
    if source_type.startswith("primary_"):
        return source_type
    if source_type.startswith("secondary_"):
        return source_type
    if source_type == "community":
        return "community"
    return "secondary_analysis"


def _validate_family_source_type_consistency(family: str, source_type: str) -> None:
    if family == "primary" and not source_type.startswith("primary_"):
        raise ValueError(
            f"Registry family/source_type mismatch: family={family} source_type={source_type}"
        )
    if family == "secondary" and not source_type.startswith("secondary_"):
        raise ValueError(
            f"Registry family/source_type mismatch: family={family} source_type={source_type}"
        )
    if family == "community" and source_type != "community":
        raise ValueError(
            f"Registry family/source_type mismatch: family={family} source_type={source_type}"
        )
    if family.startswith("primary_") and not source_type.startswith("primary_"):
        raise ValueError(
            f"Registry family/source_type mismatch: family={family} source_type={source_type}"
        )
    if family.startswith("secondary_") and not source_type.startswith("secondary_"):
        raise ValueError(
            f"Registry family/source_type mismatch: family={family} source_type={source_type}"
        )


def _normalize_legacy_registry(raw: dict[str, Any]) -> list[NormalizedSource]:
    normalized_sources: list[NormalizedSource] = []
    topics = raw.get("topics", {}) if isinstance(raw.get("topics", {}), dict) else {}

    for topic, topic_data in topics.items():
        if not isinstance(topic_data, dict):
            continue

        for family in ("primary", "secondary", "community"):
            entries = topic_data.get(family, [])
            if not isinstance(entries, list):
                continue

            for idx, entry in enumerate(entries):
                if isinstance(entry, str):
                    domain = normalize_domain(entry)
                elif isinstance(entry, dict):
                    domain = normalize_domain(str(entry.get("domain", "")))
                else:
                    continue

                if not domain:
                    continue

                source_type = (
                    "primary_docs"
                    if family == "primary"
                    else "secondary_analysis"
                    if family == "secondary"
                    else "community"
                )
                trust_tier = (
                    "A" if family == "primary" else "B" if family == "secondary" else "C"
                )
                normalized_sources.append(
                    NormalizedSource(
                        domain=domain,
                        topic=topic,
                        family=_family_from_source_type(source_type),
                        source_type=source_type,
                        trust_tier=trust_tier,
                        trust_score=LEGACY_TIER_SCORES.get(family, TRUST_FLOOR_UNKNOWN),
                        access="open",
                        freshness_profile=FRESHNESS_MEDIUM,
                        use_for=topic_data.get("use_for", [])
                        if isinstance(topic_data.get("use_for", []), list)
                        else [],
                        avoid_for=topic_data.get("avoid_for", [])
                        if isinstance(topic_data.get("avoid_for", []), list)
                        else [],
                        default_priority=idx + 1,
                        allow_site_constraint=True,
                        prefer_for_time_sensitive=False,
                        prefer_for_exact_facts=family == "primary",
                        prefer_for_community_signals=family == "community",
                    )
                )

    return normalized_sources


def _normalize_rich_registry(raw: dict[str, Any]) -> list[NormalizedSource]:
    normalized_sources: list[NormalizedSource] = []
    topics = raw.get("topics", {}) if isinstance(raw.get("topics", {}), dict) else {}

    for topic, topic_data in topics.items():
        if not isinstance(topic_data, dict):
            continue

        for family, entries in topic_data.items():
            if family in {"use_for", "avoid_for"}:
                continue
            if not isinstance(entries, list):
                continue

            for idx, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue

                domain = normalize_domain(str(entry.get("domain", "")))
                if not domain:
                    continue

                source_type = str(entry.get("source_type", family)).strip() or family
                _validate_family_source_type_consistency(family, source_type)
                normalized_family = _family_from_source_type(source_type)

                trust_tier = _normalize_trust_tier(str(entry.get("trust_tier", "B")))
                access = str(entry.get("access", "open")).strip().lower() or "open"
                freshness_profile = (
                    str(entry.get("freshness_profile", FRESHNESS_MEDIUM)).strip().lower()
                    or FRESHNESS_MEDIUM
                )

                planner_hints = entry.get("planner_hints", {})
                if not isinstance(planner_hints, dict):
                    planner_hints = {}

                normalized_sources.append(
                    NormalizedSource(
                        domain=domain,
                        topic=topic,
                        family=normalized_family,
                        source_type=source_type,
                        trust_tier=trust_tier,
                        trust_score=TRUST_TIER_SCORES[trust_tier],
                        access=access,
                        freshness_profile=freshness_profile,
                        use_for=entry.get("use_for", [])
                        if isinstance(entry.get("use_for", []), list)
                        else [],
                        avoid_for=entry.get("avoid_for", [])
                        if isinstance(entry.get("avoid_for", []), list)
                        else [],
                        default_priority=_extract_hint_int(
                            planner_hints.get("default_priority", idx + 1),
                            default=idx + 1,
                        ),
                        allow_site_constraint=_extract_hint_bool(
                            planner_hints.get("allow_site_constraint", True),
                            default=True,
                        ),
                        prefer_for_time_sensitive=_extract_hint_bool(
                            planner_hints.get("prefer_for_time_sensitive", False)
                        ),
                        prefer_for_exact_facts=_extract_hint_bool(
                            planner_hints.get("prefer_for_exact_facts", False)
                        ),
                        prefer_for_community_signals=_extract_hint_bool(
                            planner_hints.get("prefer_for_community_signals", False)
                        ),
                    )
                )

    return normalized_sources


def _dedupe_normalized_sources(
    normalized_sources: list[NormalizedSource],
) -> list[NormalizedSource]:
    by_key: dict[tuple[str, str], NormalizedSource] = {}
    for source in normalized_sources:
        key = (source.topic, source.domain)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = source
            continue

        existing_rank = (
            _family_rank(source.topic, existing.family),
            existing.default_priority,
            -existing.trust_score,
        )
        candidate_rank = (
            _family_rank(source.topic, source.family),
            source.default_priority,
            -source.trust_score,
        )
        if candidate_rank < existing_rank:
            by_key[key] = source

    return list(by_key.values())


@lru_cache(maxsize=1)
def load_normalized_source_registry() -> list[NormalizedSource]:
    raw = load_source_registry()
    topics = raw.get("topics", {}) if isinstance(raw.get("topics", {}), dict) else {}
    if not topics:
        return []

    is_rich_schema = bool(raw.get("schema"))

    if is_rich_schema:
        normalized = _normalize_rich_registry(raw)
    else:
        normalized = _normalize_legacy_registry(raw)

    return _dedupe_normalized_sources(normalized)


def domain_from_url(url: str) -> str:
    try:
        parsed = urlsplit((url or "").strip())
        return normalize_domain(parsed.netloc)
    except Exception:
        return ""


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw

    host = normalize_domain(parsed.netloc)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def sanitize_query(query: str, max_length: int = MAX_QUERY_LENGTH) -> str:
    if not query:
        return ""
    cleaned = re.sub(r"\s+", " ", query).strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip()
    return cleaned


def is_fluff_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    return any(q.startswith(prefix) for prefix in FLUFF_PREFIXES)


def extract_preserve_tokens(text: str, max_tokens: int = 24) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    for pattern in QUOTE_TOKEN_PATTERNS:
        for token in pattern.findall(text or ""):
            t = sanitize_query(token)
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(t)
            if len(found) >= max_tokens:
                return found

    for pattern in PRESERVE_TOKEN_PATTERNS:
        for token in pattern.findall(text or ""):
            t = sanitize_query(token)
            if not t:
                continue
            key = t.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(t)
            if len(found) >= max_tokens:
                return found

    return found


def ensure_preserve_tokens(
    query: str, preserve_tokens: list[str], max_length: int = MAX_QUERY_LENGTH
) -> str:
    normalized = sanitize_query(query, max_length=max_length)
    if not preserve_tokens:
        return normalized

    missing = [token for token in preserve_tokens if token.lower() not in normalized.lower()]
    if not missing:
        return normalized

    append_part = " " + " ".join(missing)
    if len(normalized) + len(append_part) <= max_length:
        return normalized + append_part
    return sanitize_query(normalized, max_length=max_length)


def _match_score(text: str, keywords: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for keyword in keywords if keyword in lowered)


def classify_intent_and_topic(text: str) -> tuple[str, str, bool, bool, float]:
    lowered = (text or "").lower()
    time_sensitive = bool(
        re.search(
            r"\b(today|latest|recent|new|current|breaking|this week|this month|202[0-9])\b",
            lowered,
        )
    )
    community_requested = bool(
        re.search(
            r"\b(reddit|community|forum|opinions?|experience|anecdote|hacker news|hn)\b",
            lowered,
        )
    )

    best_intent = "general_research"
    best_score = 0
    for intent, keywords in INTENT_RULES.items():
        score = _match_score(lowered, keywords)
        if score > best_score:
            best_intent = intent
            best_score = score

    topic = TOPIC_BY_INTENT.get(best_intent, "general")
    confidence = min(1.0, 0.3 + (best_score * 0.2)) if best_score > 0 else 0.4
    return best_intent, topic, time_sensitive, community_requested, confidence


def get_topic_candidates(topic: str) -> list[str]:
    candidates = [topic]
    aliases: dict[str, list[str]] = {
        "software_apis_devops": ["software_devops"],
        "software_devops": ["software_apis_devops"],
        "science_medical": ["science_academic", "medicine_health"],
    }
    candidates.extend(aliases.get(topic, []))
    candidates.append("general")

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return deduped


def extract_query_anchors(user_message: str) -> dict[str, list[str]]:
    text = user_message or ""

    def unique(values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = sanitize_query(value)
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            output.append(normalized)
        return output

    quoted: list[str] = []
    for pattern in QUOTE_TOKEN_PATTERNS:
        quoted.extend(pattern.findall(text))

    versions = re.findall(r"\bv?\d+\.\d+(?:\.\d+)?\b", text)
    flags = re.findall(r"--[\w-]+", text)
    cves = re.findall(r"\bCVE-\d{4}-\d{4,}\b", text, flags=re.IGNORECASE)
    env_vars = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", text)
    paths = re.findall(r"(?:/[\w./:-]+)+", text)
    package_tokens = re.findall(r"\b[\w.-]+/[\w.-]+\b", text)

    product_tokens = re.findall(
        r"\b(?:EKS|kubelet|kubernetes|aws-cni|aws-vpc-cni|vpc-cni|docker|k8s|llama\.cpp|vllm|onnxruntime|pytorch|terraform|helm)\b",
        text,
        flags=re.IGNORECASE,
    )

    anchors = {
        "error_strings": unique(quoted),
        "versions": unique(versions),
        "products": unique(product_tokens),
        "flags": unique(flags),
        "env_vars": unique(env_vars),
        "paths": unique(paths),
        "cves": unique(cves),
        "package_names": unique(package_tokens),
    }

    return anchors


def derive_intent_requirements(user_message: str) -> list[str]:
    requirements: list[str] = []
    for requirement, pattern in INTENT_REQUIREMENT_PATTERNS.items():
        if pattern.search(user_message or ""):
            requirements.append(requirement)
    return requirements


def _source_fit_score(
    source: NormalizedSource,
    *,
    time_sensitive: bool,
    community_requested: bool,
    intent_requirements: list[str],
) -> float:
    score = 0.0
    score -= _family_rank(source.topic, source.family) * 0.20
    score -= source.default_priority * 0.01
    score += source.trust_score * 1.2

    access = source.access.lower()
    if access == "paywalled":
        score -= 0.35
    elif access == "mixed":
        score -= 0.08

    if time_sensitive and source.prefer_for_time_sensitive:
        score += 0.30
    if source.prefer_for_exact_facts:
        score += 0.18

    if community_requested and source.prefer_for_community_signals:
        score += 0.25

    if "official_guidance" in intent_requirements and source.family.startswith("primary"):
        score += 0.35
    if "github_issues" in intent_requirements and source.domain == "github.com":
        score += 0.55
    if "current_fix" in intent_requirements:
        score += _normalized_freshness_score(source.freshness_profile) * 0.22

    return score


def select_sources_for_topic(
    topic: str,
    max_targeted_domains: int,
    *,
    time_sensitive: bool = False,
    community_requested: bool = False,
    intent_requirements: Optional[list[str]] = None,
    topic_candidates: Optional[list[str]] = None,
) -> list[SelectedSource]:
    intent_requirements = intent_requirements or []
    topic_candidates = topic_candidates or get_topic_candidates(topic)
    normalized_sources = load_normalized_source_registry()
    candidate_set = set(topic_candidates)
    topic_sources = [source for source in normalized_sources if source.topic in candidate_set]

    if not topic_sources:
        return []

    ranked = sorted(
        topic_sources,
        key=lambda source: _source_fit_score(
            source,
            time_sensitive=time_sensitive,
            community_requested=community_requested,
            intent_requirements=intent_requirements,
        ),
        reverse=True,
    )

    selected: list[SelectedSource] = []
    seen: set[str] = set()
    for source in ranked:
        if source.family == "community":
            continue
        if not source.allow_site_constraint:
            continue
        if source.domain in seen:
            continue
        seen.add(source.domain)
        selected.append(
            SelectedSource(
                domain=source.domain,
                tier=source.family,
                trust_tier=source.trust_tier,
                source_type=source.source_type,
                access=source.access,
                freshness_profile=source.freshness_profile,
                default_priority=source.default_priority,
                prefer_for_time_sensitive=source.prefer_for_time_sensitive,
                prefer_for_exact_facts=source.prefer_for_exact_facts,
                prefer_for_community_signals=source.prefer_for_community_signals,
            )
        )
        if len(selected) >= max_targeted_domains:
            break

    return selected


def _build_allowed_domains_ranked(
    topic: str,
    *,
    time_sensitive: bool,
    community_requested: bool,
    intent_requirements: list[str],
    topic_candidates: Optional[list[str]] = None,
) -> list[str]:
    topic_candidates = topic_candidates or get_topic_candidates(topic)
    normalized_sources = load_normalized_source_registry()
    candidate_set = set(topic_candidates)
    topic_sources = [source for source in normalized_sources if source.topic in candidate_set]
    ranked = sorted(
        topic_sources,
        key=lambda source: _source_fit_score(
            source,
            time_sensitive=time_sensitive,
            community_requested=community_requested,
            intent_requirements=intent_requirements,
        ),
        reverse=True,
    )

    domains: list[str] = []
    seen: set[str] = set()
    for source in ranked:
        if source.domain in seen:
            continue
        seen.add(source.domain)
        domains.append(source.domain)
    return domains


def build_web_search_plan(user_message: str, max_targeted_domains: int = 4) -> WebSearchPlan:
    exact_query = sanitize_query(user_message)
    if not exact_query:
        exact_query = "web search"

    intent, topic, time_sensitive, community_requested, _ = classify_intent_and_topic(
        user_message
    )
    anchors = extract_query_anchors(user_message)
    intent_requirements = derive_intent_requirements(user_message)
    preserve_tokens = extract_preserve_tokens(user_message)

    for anchor_group in (
        anchors.get("error_strings", []),
        anchors.get("versions", []),
        anchors.get("products", []),
        anchors.get("flags", []),
        anchors.get("cves", []),
        anchors.get("package_names", []),
    ):
        for token in anchor_group:
            if token.lower() not in {item.lower() for item in preserve_tokens}:
                preserve_tokens.append(token)

    preserve_tokens = preserve_tokens[:24]

    general_query = re.sub(
        r"^\s*(can you|could you|please|i need|i want|tell me|help me)\s+",
        "",
        exact_query,
        flags=re.IGNORECASE,
    )
    general_query = sanitize_query(general_query)
    if not general_query:
        general_query = exact_query
    if general_query.lower() == exact_query.lower():
        general_query = sanitize_query(f"{general_query} overview")

    exact_query = ensure_preserve_tokens(exact_query, preserve_tokens)
    general_query = ensure_preserve_tokens(general_query, preserve_tokens)

    topic_candidates = get_topic_candidates(topic)

    selected_sources = select_sources_for_topic(
        topic=topic,
        max_targeted_domains=max(0, max_targeted_domains),
        time_sensitive=time_sensitive,
        community_requested=community_requested,
        intent_requirements=intent_requirements,
        topic_candidates=topic_candidates,
    )
    selected_domains = [source.domain for source in selected_sources]

    allowed_domains_ranked = _build_allowed_domains_ranked(
        topic,
        time_sensitive=time_sensitive,
        community_requested=community_requested,
        intent_requirements=intent_requirements,
        topic_candidates=topic_candidates,
    )

    return WebSearchPlan(
        intent=intent,
        topic=topic,
        time_sensitive=time_sensitive,
        community_requested=community_requested,
        selected_domains=selected_domains,
        selected_sources=selected_sources,
        base_exact_query=exact_query,
        base_general_query=general_query,
        preserve_tokens=preserve_tokens,
        anchors=anchors,
        intent_requirements=intent_requirements,
        topic_candidates=topic_candidates,
        allowed_domains_ranked=allowed_domains_ranked,
    )


def build_rewriter_prompt(
    *,
    user_message: str,
    plan: WebSearchPlan,
    max_queries: int,
) -> str:
    max_queries = max(1, int(max_queries))
    allowed_domains = plan.allowed_domains_ranked[: max(4, max_queries)]

    prompt_payload = {
        "task": "rewrite_search_queries",
        "constraints": {
            "output_format": {
                "queries": [
                    {
                        "kind": "exact|official|issues|current_fix|general|targeted|freshness|community",
                        "query": "string",
                        "domain": "optional domain from allowed_domains",
                    }
                ]
            },
            "json_only": True,
            "no_markdown": True,
            "max_queries": max_queries,
            "preserve_tokens_exactly": plan.preserve_tokens,
            "allowed_domains_only": allowed_domains,
            "do_not_answer_user_question": True,
        },
        "inputs": {
            "user_message": user_message,
            "intent": plan.intent,
            "topic": plan.topic,
            "time_sensitive": plan.time_sensitive,
            "community_requested": plan.community_requested,
            "intent_requirements": plan.intent_requirements,
            "anchors": plan.anchors,
            "base_exact_query": plan.base_exact_query,
            "base_general_query": plan.base_general_query,
        },
        "instructions": [
            "Create concise, search-engine-ready queries only.",
            "Do not drop or alter preserved tokens.",
            "Prefer one exact query first, then targeted/official/issues/current_fix as relevant, then general.",
            "Never invent domains outside allowed_domains.",
            "Return strictly valid JSON with top-level key 'queries'.",
        ],
    }
    return json.dumps(prompt_payload, ensure_ascii=True)


def build_targeted_query(plan: WebSearchPlan, domain: str) -> str:
    return ensure_preserve_tokens(
        sanitize_query(f"{plan.base_exact_query} site:{normalize_domain(domain)}"),
        plan.preserve_tokens,
    )


def build_freshness_query(plan: WebSearchPlan, year: Optional[int] = None) -> str:
    current_year = year or datetime.now().year
    query = f"{plan.base_general_query} latest updates {current_year}"
    return ensure_preserve_tokens(sanitize_query(query), plan.preserve_tokens)


def build_community_query(plan: WebSearchPlan) -> str:
    community_domain = "reddit.com"
    for source in load_normalized_source_registry():
        if source.topic == plan.topic and source.family == "community":
            community_domain = source.domain
            break

    query = f"{plan.base_exact_query} site:{community_domain}"
    return ensure_preserve_tokens(sanitize_query(query), plan.preserve_tokens)


def build_alternate_general_query(plan: WebSearchPlan) -> str:
    if plan.intent in {"technical_debug", "docs_api"}:
        suffix = "troubleshooting guide"
    elif plan.intent in {"science_medical", "legal_compliance", "finance_macro"}:
        suffix = "official guidance"
    else:
        suffix = "overview"
    query = f"{plan.base_general_query} {suffix}"
    return ensure_preserve_tokens(sanitize_query(query), plan.preserve_tokens)


def _finalize_planned_query_candidates(
    queries: list[PlannedQuery],
    preserve_tokens: list[str],
) -> list[PlannedQuery]:
    deduped: list[PlannedQuery] = []
    seen_queries: set[str] = set()

    for query in queries:
        normalized_query = ensure_preserve_tokens(
            sanitize_query(query.query), preserve_tokens
        )
        if not normalized_query or is_fluff_query(normalized_query):
            continue
        if normalized_query in seen_queries:
            continue
        seen_queries.add(normalized_query)
        deduped.append(
            PlannedQuery(kind=query.kind, domain=query.domain, query=normalized_query)
        )

    return deduped


def build_base_planned_queries(plan: WebSearchPlan, targeted_slots: int = 3) -> list[PlannedQuery]:
    planned: list[PlannedQuery] = [PlannedQuery(kind="exact", query=plan.base_exact_query)]

    for domain in plan.selected_domains[:targeted_slots]:
        planned.append(
            PlannedQuery(
                kind="targeted",
                domain=domain,
                query=build_targeted_query(plan, domain),
            )
        )

    planned.append(PlannedQuery(kind="general", query=plan.base_general_query))
    return _finalize_planned_query_candidates(planned, plan.preserve_tokens)


def parse_rewriter_output(raw_text: str) -> list[PlannedQuery]:
    content = (raw_text or "").strip()
    if not content:
        raise ValueError("Rewriter output is empty")

    json_start = content.find("{")
    json_end = content.rfind("}")
    if json_start == -1 or json_end == -1 or json_start >= json_end:
        raise ValueError("Rewriter output does not contain JSON")

    payload = json.loads(content[json_start : json_end + 1])
    queries_raw = payload.get("queries")
    if not isinstance(queries_raw, list):
        raise ValueError("Rewriter payload missing queries array")

    parsed: list[PlannedQuery] = []
    for item in queries_raw:
        if isinstance(item, str):
            parsed.append(PlannedQuery(kind="general", query=item))
            continue

        if not isinstance(item, dict):
            continue

        parsed.append(
            PlannedQuery(
                kind=sanitize_query(str(item.get("kind", "general"))).lower() or "general",
                query=sanitize_query(str(item.get("query", ""))),
                domain=normalize_domain(str(item.get("domain", "")))
                if item.get("domain")
                else None,
            )
        )

    if not parsed:
        raise ValueError("Rewriter output did not contain valid queries")

    return parsed


def _repair_rewriter_queries(
    queries: list[PlannedQuery],
    plan: WebSearchPlan,
    max_queries: int,
) -> list[PlannedQuery]:
    allowed_domains = {normalize_domain(domain) for domain in plan.allowed_domains_ranked}

    repaired: list[PlannedQuery] = []
    for query in queries:
        text = sanitize_query(query.query)
        if not text or is_fluff_query(text):
            continue

        domain = normalize_domain(query.domain or "") if query.domain else None
        if domain and allowed_domains and domain not in allowed_domains:
            domain = None

        if domain and f"site:{domain}" not in text.lower():
            text = sanitize_query(f"{text} site:{domain}")

        text = ensure_preserve_tokens(text, plan.preserve_tokens)

        repaired.append(
            PlannedQuery(kind=query.kind or "general", domain=domain, query=text)
        )

        if len(repaired) >= max_queries:
            break

    return _finalize_planned_query_candidates(repaired, plan.preserve_tokens)


def validate_or_repair_rewriter_queries(
    queries: list[PlannedQuery],
    plan: WebSearchPlan,
    *,
    max_queries: int,
    max_repair_attempts: int = 1,
) -> list[PlannedQuery]:
    repaired = _repair_rewriter_queries(queries, plan, max_queries=max_queries)

    required_tokens = [token.lower() for token in plan.preserve_tokens]

    def _coverage_ok(candidates: list[PlannedQuery]) -> bool:
        if not required_tokens:
            return True
        corpus = " ".join(item.query.lower() for item in candidates)
        return all(token in corpus for token in required_tokens)

    attempts = 0
    while attempts <= max_repair_attempts:
        if repaired and _coverage_ok(repaired):
            return repaired

        if not repaired:
            break

        first = repaired[0]
        repaired[0] = PlannedQuery(
            kind=first.kind,
            domain=first.domain,
            query=ensure_preserve_tokens(first.query, plan.preserve_tokens),
        )
        attempts += 1

    raise ValueError("Rewriter queries failed validation")


def build_planned_queries_from_rewriter(
    plan: WebSearchPlan,
    rewriter_queries: list[PlannedQuery],
    *,
    targeted_slots: int = 3,
) -> list[PlannedQuery]:
    bucket: dict[str, list[PlannedQuery]] = {}
    for query in rewriter_queries:
        kind = (query.kind or "general").lower()
        bucket.setdefault(kind, []).append(query)

    ordered: list[PlannedQuery] = []

    exact = (bucket.get("exact") or [None])[0]
    if exact is None and rewriter_queries:
        exact = rewriter_queries[0]
    if exact is not None:
        ordered.append(PlannedQuery(kind="exact", query=exact.query, domain=exact.domain))

    official_candidate = (bucket.get("official") or bucket.get("targeted") or [None])[0]
    if official_candidate is not None:
        official_domain = official_candidate.domain
        if not official_domain and plan.selected_domains:
            official_domain = plan.selected_domains[0]

        official_query = official_candidate.query
        if official_domain:
            official_query = ensure_preserve_tokens(
                sanitize_query(f"{official_query} site:{official_domain}"),
                plan.preserve_tokens,
            )
        ordered.append(
            PlannedQuery(kind="targeted", domain=official_domain, query=official_query)
        )

    issues_candidate = (bucket.get("issues") or [None])[0]
    if issues_candidate is None:
        for query in rewriter_queries:
            if "github" in query.query.lower() or "issue" in query.query.lower():
                issues_candidate = query
                break

    if issues_candidate is not None:
        issues_domain = issues_candidate.domain or "github.com"
        issues_query = issues_candidate.query
        if issues_domain and f"site:{issues_domain}" not in issues_query.lower():
            issues_query = sanitize_query(f"{issues_query} site:{issues_domain}")
        ordered.append(
            PlannedQuery(kind="targeted", domain=issues_domain, query=issues_query)
        )

    current_fix_candidate = (
        (bucket.get("current_fix") or bucket.get("freshness") or [None])[0]
    )
    if current_fix_candidate is not None:
        ordered.append(
            PlannedQuery(
                kind="freshness",
                domain=current_fix_candidate.domain,
                query=current_fix_candidate.query,
            )
        )

    general_candidate = (bucket.get("general") or [None])[0]
    if general_candidate is not None:
        ordered.append(
            PlannedQuery(kind="general", query=general_candidate.query)
        )
    else:
        ordered.append(PlannedQuery(kind="general", query=plan.base_general_query))

    targeted_domains_used = {
        normalize_domain(query.domain)
        for query in ordered
        if query.kind == "targeted" and query.domain
    }

    for domain in plan.selected_domains:
        normalized_domain = normalize_domain(domain)
        if normalized_domain in targeted_domains_used:
            continue

        ordered.append(
            PlannedQuery(
                kind="targeted",
                domain=normalized_domain,
                query=build_targeted_query(plan, normalized_domain),
            )
        )

        targeted_domains_used.add(normalized_domain)
        if len(targeted_domains_used) >= targeted_slots:
            break

    return _finalize_planned_query_candidates(ordered, plan.preserve_tokens)


def infer_domain_trust_score(domain: str, plan: WebSearchPlan) -> float:
    normalized = normalize_domain(domain)
    if not normalized:
        return TRUST_FLOOR_UNKNOWN

    normalized_sources = load_normalized_source_registry()
    for source in normalized_sources:
        if source.topic == plan.topic and source.domain == normalized:
            return source.trust_score

    return TRUST_FLOOR_UNKNOWN


def evaluate_signal_quality(
    items: list[dict[str, Any]], plan: WebSearchPlan, year: Optional[int] = None
) -> dict[str, Any]:
    current_year = year or datetime.now().year
    scored: list[dict[str, Any]] = []
    trusted_domains: set[str] = set()
    token_count = len(plan.preserve_tokens)

    for item in items:
        title = (item.get("title") or "").strip()
        snippet = (item.get("snippet") or "").strip()
        link = (item.get("link") or "").strip()
        domain = domain_from_url(link)

        trust = infer_domain_trust_score(domain, plan)

        text_blob = f"{title} {snippet}".lower()
        if token_count == 0:
            token_overlap = 0.0
        else:
            matched = sum(
                1 for token in plan.preserve_tokens if token.lower() in text_blob
            )
            token_overlap = matched / token_count

        if plan.time_sensitive:
            years = {int(y) for y in re.findall(r"\b(20\d{2})\b", text_blob)}
            if current_year in years or (current_year - 1) in years:
                recency = 1.0
            else:
                recency = 0.6
        else:
            recency = 0.5

        metadata_quality = 1.0 if title and snippet else 0.5
        quality = (
            (0.40 * trust)
            + (0.35 * token_overlap)
            + (0.20 * recency)
            + (0.05 * metadata_quality)
        )

        scored_item = {
            **item,
            "domain": domain,
            "quality": quality,
            "trust": trust,
            "token_overlap": token_overlap,
            "recency": recency,
            "metadata_quality": metadata_quality,
        }
        scored.append(scored_item)

    scored.sort(key=lambda item: item["quality"], reverse=True)
    top_items = scored[:5]
    avg_score = (
        sum(item["quality"] for item in top_items) / len(top_items) if top_items else 0.0
    )

    for item in top_items:
        if item["trust"] >= 0.75 and item["domain"]:
            trusted_domains.add(item["domain"])

    return {
        "avg_top_score": avg_score,
        "trusted_unique_domains": len(trusted_domains),
        "scored_items": scored,
    }


def evaluate_intent_coverage(
    items: list[dict[str, Any]],
    plan: WebSearchPlan,
    year: Optional[int] = None,
) -> dict[str, Any]:
    current_year = year or datetime.now().year
    requirements = {req.lower() for req in plan.intent_requirements}

    required = {
        "official": "official_guidance" in requirements,
        "issues": "github_issues" in requirements,
        "current_fix": "current_fix" in requirements,
    }

    covered = {"official": False, "issues": False, "current_fix": False}

    for item in items[:10]:
        title = (item.get("title") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        text_blob = f"{title} {snippet}"
        domain = domain_from_url(item.get("link", ""))

        if required["official"] and not covered["official"]:
            if infer_domain_trust_score(domain, plan) >= 0.75:
                covered["official"] = True

        if required["issues"] and not covered["issues"]:
            if domain == "github.com" or any(
                token in text_blob for token in (" issue", "issues", "discussion", "forum")
            ):
                covered["issues"] = True

        if required["current_fix"] and not covered["current_fix"]:
            has_fix_terms = any(
                token in text_blob
                for token in (
                    "current fix",
                    "latest fix",
                    "workaround",
                    "mitigation",
                    "resolved",
                    "recommended",
                    "update",
                    "updates",
                )
            )
            has_recent_year = bool(
                re.search(rf"\b({current_year}|{current_year - 1})\b", text_blob)
            )
            if has_fix_terms or has_recent_year:
                covered["current_fix"] = True

    complete = all((not required[key]) or covered[key] for key in required)

    return {
        "required": required,
        "covered": covered,
        "complete": complete,
    }
