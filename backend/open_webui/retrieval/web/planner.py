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
    re.compile(r"\b[A-Z]{2,}-\d{2,}\b"),
    re.compile(r"--[\w-]+"),
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"),
    re.compile(r"\b[\w.-]+/[\w./:-]+\b"),
    re.compile(r"\b[\w./:-]*\d[\w./:-]*\b"),
)

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


class SelectedSource(BaseModel):
    domain: str
    tier: str


class WebSearchPlan(BaseModel):
    intent: str
    topic: str
    time_sensitive: bool
    community_requested: bool
    selected_domains: list[str] = Field(default_factory=list)
    selected_sources: list[SelectedSource] = Field(default_factory=list)
    base_exact_query: str
    base_general_query: str
    preserve_tokens: list[str] = Field(default_factory=list)


class PlannedQuery(BaseModel):
    kind: str
    query: str
    domain: Optional[str] = None


@lru_cache(maxsize=1)
def load_source_registry() -> dict[str, Any]:
    try:
        with SOURCE_REGISTRY_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"version": 1, "topics": {}}


def normalize_domain(domain: str) -> str:
    normalized = domain.strip().lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


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


def extract_preserve_tokens(text: str, max_tokens: int = 20) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern in PRESERVE_TOKEN_PATTERNS:
        for token in pattern.findall(text or ""):
            t = token.strip()
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


def _iter_ranked_topic_sources(topic_data: dict[str, Any]) -> list[SelectedSource]:
    ranked: list[SelectedSource] = []
    for tier in ("primary", "secondary", "community"):
        for domain in topic_data.get(tier, []):
            normalized = normalize_domain(domain)
            if not normalized:
                continue
            ranked.append(SelectedSource(domain=normalized, tier=tier))
    return ranked


def select_sources_for_topic(topic: str, max_targeted_domains: int) -> list[SelectedSource]:
    registry = load_source_registry()
    topics = registry.get("topics", {})
    topic_data = topics.get(topic, {})
    ranked = _iter_ranked_topic_sources(topic_data)

    selected: list[SelectedSource] = []
    seen: set[str] = set()
    for source in ranked:
        if source.tier == "community":
            continue
        if source.domain in seen:
            continue
        seen.add(source.domain)
        selected.append(source)
        if len(selected) >= max_targeted_domains:
            break
    return selected


def build_web_search_plan(user_message: str, max_targeted_domains: int = 4) -> WebSearchPlan:
    exact_query = sanitize_query(user_message)
    if not exact_query:
        exact_query = "web search"

    intent, topic, time_sensitive, community_requested, _ = classify_intent_and_topic(
        user_message
    )
    preserve_tokens = extract_preserve_tokens(user_message)

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

    selected_sources = select_sources_for_topic(
        topic=topic, max_targeted_domains=max(0, max_targeted_domains)
    )
    selected_domains = [source.domain for source in selected_sources]

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
    )


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
    registry = load_source_registry()
    topic = registry.get("topics", {}).get(plan.topic, {})
    community_domain = normalize_domain(
        (topic.get("community", ["reddit.com"]) or ["reddit.com"])[0]
    )
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

    deduped: list[PlannedQuery] = []
    seen_queries: set[str] = set()
    for query in planned:
        normalized = sanitize_query(query.query)
        if not normalized or is_fluff_query(normalized):
            continue
        if normalized in seen_queries:
            continue
        seen_queries.add(normalized)
        deduped.append(PlannedQuery(kind=query.kind, domain=query.domain, query=normalized))
    return deduped


def infer_domain_trust_score(domain: str, plan: WebSearchPlan) -> float:
    normalized = normalize_domain(domain)
    if not normalized:
        return 0.55

    registry = load_source_registry()
    topic = registry.get("topics", {}).get(plan.topic, {})

    primary = {normalize_domain(item) for item in topic.get("primary", [])}
    secondary = {normalize_domain(item) for item in topic.get("secondary", [])}
    community = {normalize_domain(item) for item in topic.get("community", [])}

    if normalized in primary:
        return 1.0
    if normalized in secondary:
        return 0.75
    if normalized in community:
        return 0.45
    return 0.55


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
