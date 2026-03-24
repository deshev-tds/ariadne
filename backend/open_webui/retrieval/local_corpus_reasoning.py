import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from open_webui.retrieval import local_corpus as corpus

log = logging.getLogger(__name__)

LOCAL_CORPUS_PACKS_DIR = Path(__file__).resolve().parent / "local_corpus_packs"
LOCAL_CORPUS_REASONING_SCHEMA_VERSION = 1
LOCAL_CORPUS_REASONING_HARD_MAX_AXES = 6
LOCAL_CORPUS_MODES = {"off", "auto", "prefer"}
MATURITY_LABELS = {1: "tier_1", 2: "tier_2", 3: "tier_3"}

_GENERIC_ORIENTATION_TERMS = {
    "approach",
    "bucket",
    "buckets",
    "cause",
    "causes",
    "consider",
    "differential",
    "framework",
    "hypothesis",
    "hypotheses",
    "interpret",
    "orientation",
    "possible",
    "regime",
    "strategy",
    "think",
    "vectors",
}
_TIME_SENSITIVE_TERMS = {
    "current",
    "latest",
    "new",
    "newest",
    "recent",
    "today",
    "updated",
}
_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:%|[a-zA-Z]+(?:/[a-zA-Z]+)?|mmhg|mg/dl|mmol/l|g/dl|bpm)\b",
    flags=re.IGNORECASE,
)
_DOMAIN_CONTEXT_GAPS = {
    "medicine": [
        ("tempo", "time course / onset"),
        ("demographics", "age and relevant background"),
    ],
    "chemistry": [
        ("conditions", "reaction conditions such as solvent, temperature, or pH"),
    ],
    "physics": [
        ("quantities", "relevant quantities, scales, or boundary conditions"),
    ],
    "mathematics": [
        ("assumptions", "the exact assumptions or domain restrictions"),
    ],
    "quantum_mechanics": [
        ("formalism", "the basis, Hamiltonian, or approximation regime"),
    ],
    "biology": [
        ("context", "organism, system, or experimental context"),
    ],
    "computer_science": [
        ("constraints", "the workload, constraints, or target environment"),
    ],
    "offensive_security": [
        ("scope", "authorized scope, target assumptions, and environment"),
    ],
}
_GENERIC_CONTEXT_TERMS = {
    "about",
    "appointment",
    "best",
    "brief",
    "briefly",
    "broad",
    "currently",
    "general",
    "gp",
    "high",
    "interview",
    "level",
    "looking",
    "need",
    "overview",
    "please",
    "plus",
    "quick",
    "quickly",
    "should",
    "think",
    "tomorrow",
    "while",
    "waiting",
    "want",
    "meeting",
}
_QUERY_SPINE_GLUE_TERMS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "if",
    "in",
    "into",
    "is",
    "may",
    "of",
    "on",
    "or",
    "the",
    "their",
    "these",
    "this",
    "those",
    "to",
    "what",
    "when",
    "whether",
    "which",
    "who",
    "why",
    "with",
}
_QUERY_SPINE_HUMAN_REFERENCE_TERMS = {
    "certain",
    "individual",
    "individuals",
    "people",
    "person",
    "persons",
    "someone",
    "somebody",
}
_RELATION_OR_MECHANISM_CORE_STEMS = {
    "associat",
    "cause",
    "contribut",
    "explain",
    "induc",
    "link",
    "mechanism",
    "mediat",
    "modulat",
    "pathway",
    "precipit",
    "provok",
    "relation",
    "trigger",
    "underly",
}
_SELECTOR_TERMS_BY_DOMAIN = {
    "medicine": {
        "adult",
        "adults",
        "child",
        "children",
        "female",
        "infant",
        "male",
        "pediatric",
        "pregnant",
        "postpartum",
        "renal",
        "hepatic",
        "elderly",
    },
    "chemistry": {
        "aqueous",
        "anhydrous",
        "catalyst",
        "ph",
        "pressure",
        "solvent",
        "temperature",
    },
    "physics": {
        "approximate",
        "asymptotic",
        "boundary",
        "exact",
        "limit",
        "regime",
    },
    "mathematics": {
        "constructive",
        "intuition",
        "proof",
        "rigorous",
    },
    "quantum_mechanics": {
        "approximation",
        "basis",
        "hamiltonian",
        "operator",
        "state",
        "wavefunction",
    },
    "biology": {
        "cell",
        "human",
        "mouse",
        "organism",
        "tissue",
        "vitro",
        "vivo",
    },
    "computer_science": {
        "environment",
        "latency",
        "memory",
        "production",
        "throughput",
    },
    "offensive_security": {
        "air",
        "airgapped",
        "authorized",
        "engagement",
        "environment",
        "lab",
        "scope",
        "target",
    },
}


def clear_local_corpus_reasoning_caches() -> None:
    load_local_corpus_pack.cache_clear()


def normalize_local_corpus_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in LOCAL_CORPUS_MODES:
        return normalized
    return "auto"


def _pack_path(domain: str) -> Path:
    return LOCAL_CORPUS_PACKS_DIR / f"{corpus._normalize_name(domain)}.json"


def _validate_pack(data: dict[str, Any]) -> dict[str, Any]:
    required_top_level = {
        "domain",
        "version",
        "maturity_tier",
        "default_task_type",
        "default_axis_budget",
        "max_axis_budget",
        "task_types",
        "axes",
        "answer_modes",
        "ranking_profiles",
        "risk_profiles",
        "insufficiency_profiles",
    }
    missing = sorted(required_top_level - set(data.keys()))
    if missing:
        raise ValueError(f"Local corpus pack missing required keys: {', '.join(missing)}")

    if int(data.get("maturity_tier") or 0) not in {1, 2, 3}:
        raise ValueError("Local corpus pack maturity_tier must be 1, 2, or 3")

    default_axis_budget = int(data.get("default_axis_budget") or 0)
    max_axis_budget = int(data.get("max_axis_budget") or 0)
    if default_axis_budget < 1 or max_axis_budget < 1:
        raise ValueError("Local corpus pack axis budgets must be positive")
    if default_axis_budget > max_axis_budget:
        raise ValueError("Local corpus pack default_axis_budget cannot exceed max_axis_budget")
    if max_axis_budget > LOCAL_CORPUS_REASONING_HARD_MAX_AXES:
        raise ValueError("Local corpus pack max_axis_budget exceeds hard backend cap")

    axes = data.get("axes") or {}
    if not isinstance(axes, dict) or not axes:
        raise ValueError("Local corpus pack axes must be a non-empty object")

    for axis_id, axis in axes.items():
        if not isinstance(axis, dict):
            raise ValueError(f"Axis {axis_id} must be an object")
        for field in (
            "intent",
            "slot_sources",
            "literal_terms",
            "preferred_resource_types",
            "preferred_evidence_tiers",
            "boost_table_like",
            "max_books",
            "top_k",
        ):
            if field not in axis:
                raise ValueError(f"Axis {axis_id} missing field: {field}")

    answer_modes = data.get("answer_modes") or []
    if not isinstance(answer_modes, list) or not answer_modes:
        raise ValueError("Local corpus pack answer_modes must be a non-empty list")

    ranking_profiles = data.get("ranking_profiles") or {}
    risk_profiles = data.get("risk_profiles") or {}
    insufficiency_profiles = data.get("insufficiency_profiles") or {}
    task_types = data.get("task_types") or {}

    if not isinstance(task_types, dict) or not task_types:
        raise ValueError("Local corpus pack task_types must be a non-empty object")

    for task_type, config in task_types.items():
        if not isinstance(config, dict):
            raise ValueError(f"Task type {task_type} must be an object")
        for field in (
            "answer_mode",
            "required_axis_ids",
            "optional_axis_ids",
            "frame_slots",
            "ranking_profile",
            "risk_profile",
            "insufficiency_profile",
        ):
            if field not in config:
                raise ValueError(f"Task type {task_type} missing field: {field}")
        if config["answer_mode"] not in answer_modes:
            raise ValueError(f"Task type {task_type} references unknown answer mode")
        for axis_id in list(config.get("required_axis_ids") or []) + list(
            config.get("optional_axis_ids") or []
        ):
            if axis_id not in axes:
                raise ValueError(f"Task type {task_type} references unknown axis {axis_id}")
        if config["ranking_profile"] not in ranking_profiles:
            raise ValueError(f"Task type {task_type} references unknown ranking profile")
        if config["risk_profile"] not in risk_profiles:
            raise ValueError(f"Task type {task_type} references unknown risk profile")
        if config["insufficiency_profile"] not in insufficiency_profiles:
            raise ValueError(
                f"Task type {task_type} references unknown insufficiency profile"
            )

    if data["default_task_type"] not in task_types:
        raise ValueError("Local corpus pack default_task_type must exist in task_types")

    data["version"] = int(data["version"])
    data["maturity_tier"] = int(data["maturity_tier"])
    data["default_axis_budget"] = default_axis_budget
    data["max_axis_budget"] = max_axis_budget
    data["schema_version"] = LOCAL_CORPUS_REASONING_SCHEMA_VERSION
    return data


@lru_cache(maxsize=32)
def load_local_corpus_pack(domain: str) -> dict[str, Any]:
    path = _pack_path(domain)
    if not path.exists():
        raise ValueError(f"Local corpus pack not found for domain: {domain}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return _validate_pack(data)


def load_local_corpus_pack_for_domain(domain: str, config_or_path: Any = None) -> dict[str, Any]:
    registry = corpus.load_local_corpus_registry(config_or_path)
    normalized_domain = corpus._normalize_name(domain)
    if normalized_domain not in registry.usable_books_by_domain:
        raise ValueError(f"Unknown or unavailable domain: {domain}")
    return load_local_corpus_pack(normalized_domain)


def _extract_segments(query: str) -> list[str]:
    segments = []
    for piece in re.split(r"[;,\n]+", query):
        normalized = corpus._normalize_text(piece)
        if normalized:
            segments.append(normalized)
    return segments


def _extract_measurements(query: str) -> list[str]:
    seen = []
    for match in _MEASUREMENT_RE.findall(query or ""):
        normalized = corpus._normalize_text(match)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def _span_tokens(value: str) -> list[str]:
    return corpus._query_terms(value)


def _normalized_token_set(value: str) -> set[str]:
    return {token for token in _span_tokens(value) if token}


def _content_tokens(tokens: list[str]) -> list[str]:
    return [
        token
        for token in tokens
        if token not in corpus.SALIENT_QUERY_STOP_TERMS and token not in _GENERIC_CONTEXT_TERMS
    ]


def _trim_context_edges(value: str) -> str:
    tokens = _span_tokens(value)
    if not tokens:
        return ""

    start = 0
    end = len(tokens)
    edge_terms = set(corpus.SALIENT_QUERY_STOP_TERMS) | _GENERIC_CONTEXT_TERMS
    while start < end and tokens[start] in edge_terms:
        start += 1
    while end > start and tokens[end - 1] in edge_terms:
        end -= 1
    return " ".join(tokens[start:end]).strip()


def _compact_projection_span(value: str) -> str:
    tokens = _span_tokens(value)
    if not tokens:
        return ""

    edge_terms = (
        set(corpus.SALIENT_QUERY_STOP_TERMS)
        | _GENERIC_CONTEXT_TERMS
        | _QUERY_SPINE_GLUE_TERMS
        | _QUERY_SPINE_HUMAN_REFERENCE_TERMS
    )

    start = 0
    end = len(tokens)
    while start < end and tokens[start] in edge_terms:
        start += 1
    while end > start and tokens[end - 1] in edge_terms:
        end -= 1

    compacted = tokens[start:end]
    if not compacted:
        return ""

    compacted = [
        token
        for token in compacted
        if token not in _QUERY_SPINE_GLUE_TERMS
        and token not in _QUERY_SPINE_HUMAN_REFERENCE_TERMS
    ]
    if not compacted:
        return ""

    compacted = compacted[:5]
    return corpus._normalize_text(" ".join(compacted))


def _stem_like(token: str) -> str:
    token = token.lower().strip()
    for suffix in ("ing", "ers", "ies", "ied", "ed", "es", "s"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            if suffix in {"ies", "ied"}:
                return token[: -len(suffix)] + "y"
            return token[: -len(suffix)]
    return token


def _canonicalize_span(value: str) -> str:
    tokens = _content_tokens(_span_tokens(value))
    if not tokens:
        return ""
    stems: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        stemmed = _stem_like(token)
        if stemmed and stemmed not in seen:
            seen.add(stemmed)
            stems.append(stemmed)
    return " ".join(stems[:4]).strip()


def _spine_tokens(value: str) -> list[str]:
    tokens = _span_tokens(value)
    filtered: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        stem = _stem_like(token)
        if (
            (token in corpus.SALIENT_QUERY_STOP_TERMS and stem not in _RELATION_OR_MECHANISM_CORE_STEMS)
            or token in _GENERIC_CONTEXT_TERMS
            or token in _QUERY_SPINE_GLUE_TERMS
            or token in _QUERY_SPINE_HUMAN_REFERENCE_TERMS
        ):
            continue
        if token not in seen:
            seen.add(token)
            filtered.append(token)
    return filtered


def _surface_spine_phrase(value: str, *, limit: int = 3) -> str:
    tokens = _spine_tokens(value)
    if not tokens:
        return ""
    return " ".join(tokens[:limit]).strip()


def _build_retrieval_spine(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    salient_phrases: list[str],
    specific_terms: list[str],
    measurements: list[str],
) -> dict[str, list[str] | int]:
    surface_meta: dict[str, dict[str, float | int]] = {}
    canonical_meta: dict[str, dict[str, float | int]] = {}
    source_count = 0

    def register_surface(term: str, *, weight: float, source_priority: int) -> None:
        nonlocal source_count
        normalized = corpus._normalize_text(term)
        if not normalized:
            return
        source_count += 1
        current = surface_meta.get(normalized)
        if not current or (weight, source_priority, -len(normalized)) > (
            current["weight"],
            current["source_priority"],
            -len(normalized),
        ):
            surface_meta[normalized] = {
                "weight": weight,
                "source_priority": source_priority,
            }

        canonical = _canonicalize_span(normalized)
        if not canonical:
            return
        current_canonical = canonical_meta.get(canonical)
        if not current_canonical or (weight, source_priority, -len(canonical)) > (
            current_canonical["weight"],
            current_canonical["source_priority"],
            -len(canonical),
        ):
            canonical_meta[canonical] = {
                "weight": weight,
                "source_priority": source_priority,
            }

    quoted = corpus._quoted_phrases(query)
    for phrase in quoted:
        cleaned = _surface_spine_phrase(phrase, limit=4)
        if cleaned:
            register_surface(cleaned, weight=5.0, source_priority=5)

    for phrase in specific_terms:
        cleaned = _surface_spine_phrase(phrase, limit=4)
        if cleaned:
            register_surface(cleaned, weight=4.4, source_priority=4)

    for phrase in salient_phrases:
        cleaned = _surface_spine_phrase(phrase, limit=4)
        if cleaned:
            register_surface(cleaned, weight=3.8, source_priority=3)

    for measurement in measurements:
        cleaned = _surface_spine_phrase(measurement, limit=4)
        if cleaned:
            register_surface(cleaned, weight=3.4, source_priority=3)

    normalized_query = corpus._phrase_ready_text(query)
    if re.search(r"\bmechanis\w*\b", normalized_query):
        register_surface("mechanism", weight=4.6, source_priority=4)
    if re.search(r"\bpathway\w*\b", normalized_query):
        register_surface("pathway", weight=4.5, source_priority=4)

    cleaned_query_tokens = _spine_tokens(query)
    for idx, token in enumerate(cleaned_query_tokens):
        stem = _stem_like(token)
        if stem not in _RELATION_OR_MECHANISM_CORE_STEMS:
            continue
        core_weight = 3.2
        if stem in {"mechanism", "pathway"}:
            core_weight = 3.9
        elif stem in {"trigger", "cause", "induc", "provok"}:
            core_weight = 3.5
        register_surface(token, weight=core_weight, source_priority=2)
        if idx >= 2:
            register_surface(
                " ".join(cleaned_query_tokens[idx - 2 : idx + 1]),
                weight=core_weight - 0.1,
                source_priority=2,
            )
        if idx + 3 <= len(cleaned_query_tokens):
            register_surface(
                " ".join(cleaned_query_tokens[idx : idx + 3]),
                weight=core_weight - 0.1,
                source_priority=2,
            )

    for candidate in candidates:
        if candidate["content_signal_score"] < 2.75 and candidate["selector_signal_score"] < 1.2:
            continue
        cleaned = _surface_spine_phrase(candidate["value"], limit=4)
        if not cleaned:
            continue
        register_surface(
            cleaned,
            weight=float(candidate["content_signal_score"]) + (0.6 * float(candidate["selector_signal_score"])),
            source_priority=1,
        )

    retrieval_spine_surface = [
        key
        for key, _ in sorted(
            surface_meta.items(),
            key=lambda item: (-float(item[1]["weight"]), -int(item[1]["source_priority"]), len(item[0]), item[0]),
        )[:6]
    ]
    retrieval_spine_canonical = [
        key
        for key, _ in sorted(
            canonical_meta.items(),
            key=lambda item: (-float(item[1]["weight"]), -int(item[1]["source_priority"]), len(item[0]), item[0]),
        )[:6]
    ]

    required_surface_terms: list[str] = []
    required_canonical_terms: list[str] = []
    for token in cleaned_query_tokens:
        stem = _stem_like(token)
        if stem not in _RELATION_OR_MECHANISM_CORE_STEMS:
            continue
        if stem in {"mechanism", "pathway", "trigger", "cause", "induc", "provok"}:
            if token not in required_surface_terms:
                required_surface_terms.append(token)
            canonical = _canonicalize_span(token)
            if canonical and canonical not in required_canonical_terms:
                required_canonical_terms.append(canonical)

    def ensure_required_terms(target: list[str], required_terms: list[str]) -> list[str]:
        terms = list(target)
        for required in required_terms:
            required_stem = _stem_like(required)
            if any(required_stem == _stem_like(token) for token in terms):
                continue
            if len(terms) < 6:
                terms.append(required)
            elif terms:
                terms[-1] = required
        return terms[:6]

    retrieval_spine_surface = ensure_required_terms(
        retrieval_spine_surface, required_surface_terms
    )
    retrieval_spine_canonical = ensure_required_terms(
        retrieval_spine_canonical, required_canonical_terms
    )

    if not retrieval_spine_surface:
        retrieval_spine_surface = [
            term for term in (_surface_spine_phrase(query, limit=4),) if term
        ][:1]
    if not retrieval_spine_canonical:
        retrieval_spine_canonical = [
            term for term in (_canonicalize_span(value) for value in retrieval_spine_surface) if term
        ][:6]

    return {
        "retrieval_spine_surface": retrieval_spine_surface,
        "retrieval_spine_canonical": retrieval_spine_canonical,
        "spine_source_count": source_count,
    }


def _candidate_content_signal(
    value: str,
    *,
    domain_term_set: set[str],
    salient_terms: set[str],
    measurements: set[str],
    support_count: int,
    sources: set[str],
) -> float:
    tokens = _span_tokens(value)
    token_set = set(tokens)
    if not tokens:
        return 0.0

    content_tokens = _content_tokens(tokens)
    context_hits = sum(1 for token in tokens if token in _GENERIC_CONTEXT_TERMS)
    score = 0.0
    score += min(2.0, 0.55 * len(content_tokens))
    score += min(2.0, 0.7 * len(token_set & domain_term_set))
    score += min(1.6, 0.45 * len(token_set & salient_terms))
    if "quoted" in sources:
        score += 0.8
    if "salient" in sources:
        score += 0.5
    if "specific" in sources:
        score += 0.35
    if any(measurement in value.lower() for measurement in measurements):
        score += 1.6
    if support_count > 1:
        score += min(1.2, 0.5 * (support_count - 1))
    if context_hits:
        score -= min(2.4, 0.8 * context_hits)
    if tokens and tokens[0] in _GENERIC_CONTEXT_TERMS:
        score -= 0.65
    if tokens and tokens[-1] in _GENERIC_CONTEXT_TERMS:
        score -= 0.65
    if len(content_tokens) < 2 and not any(measurement in value.lower() for measurement in measurements):
        score -= 1.25
    if sources <= {"segment"} or sources <= {"constraint"}:
        score -= 0.8
    if len(tokens) > 6 and (len(content_tokens) / max(1, len(tokens))) < 0.6:
        score -= 1.2
    if len(tokens) > 10:
        score -= 1.0
    return round(score, 4)


def _candidate_selector_signal(value: str, *, domain: str, is_constraint: bool) -> float:
    tokens = _span_tokens(value)
    token_set = set(tokens)
    if not token_set:
        return 0.0

    score = 0.0
    selector_terms = {_stem_like(term) for term in _SELECTOR_TERMS_BY_DOMAIN.get(domain, set())}
    selector_hits = sum(1 for token in token_set if _stem_like(token) in selector_terms)
    score += min(2.0, 1.0 * selector_hits)
    if selector_hits and len(tokens) <= 3:
        score += 0.35
    if is_constraint and len(tokens) <= 5:
        score += 0.45
    if is_constraint and len(_content_tokens(tokens)) >= 2:
        score += 0.3
    return round(score, 4)


def _build_retrieval_projection(
    query: str,
    *,
    domain: str,
    constraints: list[str],
    measurements: list[str],
) -> dict[str, Any]:
    quoted = corpus._quoted_phrases(query)
    salient_phrases = corpus._salient_query_phrases(query)
    segments = _extract_segments(query)
    specific_terms = corpus._specific_query_terms(query, domain)
    salient_terms = {
        token
        for phrase in salient_phrases
        for token in _content_tokens(_span_tokens(phrase))
    }
    domain_term_set = set(specific_terms)
    measurement_set = {measurement.lower() for measurement in measurements}
    constraint_set = {corpus._normalize_text(item) for item in constraints}

    candidate_sources: dict[str, set[str]] = {}
    for source, values in (
        ("quoted", quoted),
        ("salient", salient_phrases),
        ("segment", segments),
        ("specific", specific_terms),
        ("measurement", measurements),
        ("constraint", constraints),
    ):
        for value in values:
            normalized = corpus._normalize_text(_trim_context_edges(value) or value)
            if normalized:
                candidate_sources.setdefault(normalized, set()).add(source)

    candidates: list[dict[str, Any]] = []
    for value, sources in candidate_sources.items():
        content_score = _candidate_content_signal(
            value,
            domain_term_set=domain_term_set,
            salient_terms=salient_terms,
            measurements=measurement_set,
            support_count=len(sources),
            sources=sources,
        )
        selector_score = _candidate_selector_signal(
            value,
            domain=domain,
            is_constraint=value in constraint_set or "constraint" in sources,
        )
        canonical = _canonicalize_span(value)
        tokens = _span_tokens(value)
        candidate = {
            "value": value,
            "canonical": canonical,
            "tokens": tokens,
            "sources": sorted(sources),
            "content_signal_score": content_score,
            "selector_signal_score": selector_score,
            "salience_weight": max(1, len(sources)),
            "is_constraint_like": value in constraint_set or "constraint" in sources,
            "is_measurement_like": "measurement" in sources,
            "is_selector_promoted": False,
        }
        candidates.append(candidate)

    candidates.sort(
        key=lambda item: (
            -item["content_signal_score"],
            -item["selector_signal_score"],
            -item["salience_weight"],
            len(item["tokens"]),
            item["value"],
        )
    )

    retrieval_entities: list[str] = []
    retrieval_observations: list[str] = []
    answer_context: list[str] = []
    promoted_selectors: list[str] = []
    retrieval_term_surface_meta: dict[str, dict[str, float]] = {}
    retrieval_term_canonical_meta: dict[str, dict[str, float]] = {}

    for candidate in candidates:
        normalized = candidate["value"]
        compacted = _compact_projection_span(normalized)
        content_score = candidate["content_signal_score"]
        selector_score = candidate["selector_signal_score"]
        qualifies_content = content_score >= 2.75
        qualifies_selector = selector_score >= 1.2

        if qualifies_selector:
            candidate["is_selector_promoted"] = True
            if normalized not in promoted_selectors:
                promoted_selectors.append(normalized)

        if qualifies_content or qualifies_selector or candidate["is_measurement_like"]:
            entity_value = compacted or (
                normalized if candidate["is_measurement_like"] else ""
            )
            if entity_value and entity_value not in retrieval_entities and len(retrieval_entities) < 8:
                retrieval_entities.append(entity_value)
            if (
                (candidate["is_measurement_like"] or len(candidate["tokens"]) >= 2)
                and entity_value
                and entity_value not in retrieval_observations
                and len(retrieval_observations) < 6
            ):
                retrieval_observations.append(entity_value)

            surface_weight = content_score + (0.75 * selector_score) + (0.2 * candidate["salience_weight"])
            current_surface = retrieval_term_surface_meta.get(normalized)
            if not current_surface or surface_weight > current_surface["weight"]:
                retrieval_term_surface_meta[normalized] = {
                    "weight": surface_weight,
                    "salience": candidate["salience_weight"],
                }

            canonical = candidate["canonical"]
            if canonical:
                canonical_weight = content_score + selector_score + (0.15 * candidate["salience_weight"])
                current_canonical = retrieval_term_canonical_meta.get(canonical)
                if not current_canonical or canonical_weight > current_canonical["weight"]:
                    retrieval_term_canonical_meta[canonical] = {
                        "weight": canonical_weight,
                        "salience": candidate["salience_weight"],
                    }
        else:
            if normalized not in answer_context and len(answer_context) < 6:
                answer_context.append(normalized)

    if not retrieval_entities:
        top_candidates = [
            _compact_projection_span(candidate["value"])
            for candidate in candidates
            if candidate["content_signal_score"] > 0 or candidate["selector_signal_score"] > 0
        ]
        top_candidates = [value for value in top_candidates if value][:4]
        retrieval_entities.extend(top_candidates)
        for value in top_candidates:
            if value not in retrieval_observations and len(value.split()) >= 2:
                retrieval_observations.append(value)

    retrieval_terms_surface = [
        key
        for key, _ in sorted(
            retrieval_term_surface_meta.items(),
            key=lambda item: (-item[1]["weight"], -item[1]["salience"], len(item[0]), item[0]),
        )[:10]
    ]
    retrieval_terms_canonical = [
        key
        for key, _ in sorted(
            retrieval_term_canonical_meta.items(),
            key=lambda item: (-item[1]["weight"], -item[1]["salience"], len(item[0]), item[0]),
        )[:10]
    ]

    if not retrieval_terms_surface:
        retrieval_terms_surface = retrieval_entities[:6]
    if not retrieval_terms_canonical:
        retrieval_terms_canonical = [
            term for term in (_canonicalize_span(value) for value in retrieval_entities[:6]) if term
        ][:6]

    spine = _build_retrieval_spine(
        query=query,
        candidates=candidates,
        salient_phrases=salient_phrases,
        specific_terms=specific_terms,
        measurements=measurements,
    )

    return {
        "retrieval_entities": retrieval_entities[:8],
        "retrieval_observations": retrieval_observations[:6],
        "retrieval_terms_surface": retrieval_terms_surface[:10],
        "retrieval_terms_canonical": retrieval_terms_canonical[:10],
        "retrieval_spine_surface": spine["retrieval_spine_surface"][:6],
        "retrieval_spine_canonical": spine["retrieval_spine_canonical"][:6],
        "answer_context": answer_context[:6],
        "control_constraints": constraints[:6],
        "promoted_selectors": promoted_selectors[:6],
        "normalization_applied": True,
        "retrieval_projection_summary": {
            "candidate_count": len(candidates),
            "retrieval_entity_count": len(retrieval_entities[:8]),
            "retrieval_term_count": len(retrieval_terms_surface[:10]),
            "retrieval_spine_count": len(spine["retrieval_spine_surface"][:6]),
            "spine_source_count": int(spine["spine_source_count"]),
            "promoted_selector_count": len(promoted_selectors[:6]),
        },
    }


def _extract_entities(query: str, domain: str) -> list[str]:
    phrases = corpus._quoted_phrases(query)
    salient = corpus._salient_query_phrases(query)
    segments = _extract_segments(query)
    specific_terms = corpus._specific_query_terms(query, domain)
    entities = []
    for value in phrases + salient + segments + specific_terms:
        normalized = corpus._normalize_text(value)
        lowered = normalized.lower()
        if (
            normalized
            and lowered not in {item.lower() for item in entities}
            and len(lowered) > 2
        ):
            entities.append(normalized)
        if len(entities) >= 8:
            break
    return entities


def _extract_constraints(query: str) -> list[str]:
    constraints = []
    lowered = query.lower()
    patterns = [
        r"\bwhile waiting[^,.!?;]*",
        r"\bnot looking for[^,.!?;]*",
        r"\bwithout[^,.!?;]*",
        r"\bonly[^,.!?;]*",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, lowered):
            normalized = corpus._normalize_text(match)
            if normalized and normalized not in constraints:
                constraints.append(normalized)
    return constraints


def _extract_risk_flags(query: str, risk_profile: dict[str, Any]) -> list[str]:
    risk_terms = risk_profile.get("red_flag_terms") or []
    lowered = corpus._phrase_ready_text(query)
    flags = []
    for term in risk_terms:
        normalized = corpus._phrase_ready_text(term)
        if normalized and normalized in lowered and term not in flags:
            flags.append(term)
    return flags


def _infer_unknowns(
    domain: str,
    query: str,
    measurements: list[str],
    constraints: list[str],
) -> list[str]:
    lowered = corpus._phrase_ready_text(query)
    gaps = []
    for gap_type, label in _DOMAIN_CONTEXT_GAPS.get(domain, []):
        if gap_type == "tempo":
            if not any(term in lowered for term in ("day", "days", "week", "weeks", "month", "months", "acute", "chronic")):
                gaps.append(label)
        elif gap_type == "demographics":
            if not any(term in lowered for term in ("year old", "male", "female", "pregnan", "adult", "child")):
                gaps.append(label)
        elif gap_type == "conditions":
            if not any(term in lowered for term in ("temperature", "solvent", "ph", "pressure", "catalyst")):
                gaps.append(label)
        elif gap_type == "quantities":
            if not measurements:
                gaps.append(label)
        elif gap_type == "assumptions":
            if not any(term in lowered for term in ("assume", "given", "for all", "for any", "bounded", "continuous")):
                gaps.append(label)
        elif gap_type == "formalism":
            if not any(term in lowered for term in ("hamiltonian", "basis", "operator", "state", "wavefunction")):
                gaps.append(label)
        elif gap_type == "context":
            if not any(term in lowered for term in ("cell", "tissue", "mouse", "human", "organism", "in vitro", "in vivo")):
                gaps.append(label)
        elif gap_type == "constraints":
            if not constraints and not any(term in lowered for term in ("latency", "throughput", "memory", "time complexity", "space complexity")):
                gaps.append(label)
        elif gap_type == "scope":
            if not any(term in lowered for term in ("authorized", "scope", "target", "lab", "engagement", "environment")):
                gaps.append(label)
    return gaps[:4]


def _score_task_type(query: str, task_type: str, task_config: dict[str, Any], pack: dict[str, Any]) -> float:
    terms = set(corpus._query_terms(query))
    normalized_query = corpus._phrase_ready_text(query)
    score = 0.0

    for token in task_type.split("_"):
        if token and token in terms:
            score += 0.45

    for hint in task_config.get("classification_terms") or []:
        normalized_hint = corpus._phrase_ready_text(hint)
        if normalized_hint and normalized_hint in normalized_query:
            score += 1.3 if " " in normalized_hint else 0.75

    for axis_id in list(task_config.get("required_axis_ids") or []) + list(
        task_config.get("optional_axis_ids") or []
    ):
        axis = (pack.get("axes") or {}).get(axis_id, {})
        for hint in axis.get("literal_terms") or []:
            normalized_hint = corpus._phrase_ready_text(hint)
            if normalized_hint and normalized_hint in normalized_query:
                score += 0.35

    ranking_profile = (pack.get("ranking_profiles") or {}).get(
        task_config.get("ranking_profile"), {}
    )
    for hint in ranking_profile.get("classification_terms") or []:
        normalized_hint = corpus._phrase_ready_text(hint)
        if normalized_hint and normalized_hint in normalized_query:
            score += 0.5

    if terms & _GENERIC_ORIENTATION_TERMS and "orientation" in task_type:
        score += 0.8
    if "reference" in task_type and any(term in terms for term in ("page", "book", "source", "reference", "where")):
        score += 0.9
    if "mechanism" in task_type and any(term in terms for term in ("why", "how", "mechanism", "pathway", "regime")):
        score += 0.7
    if "proof" in task_type and any(term in terms for term in ("prove", "proof", "show", "theorem")):
        score += 0.9

    return score


def _sorted_task_candidates(query: str, pack: dict[str, Any]) -> list[tuple[str, float]]:
    ranked = []
    for task_type, task_config in (pack.get("task_types") or {}).items():
        ranked.append((task_type, _score_task_type(query, task_type, task_config, pack)))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _resolve_domain(query: str, domain_hint: Optional[str], config_or_path: Any) -> dict[str, Any]:
    registry = corpus.load_local_corpus_registry(config_or_path)
    if domain_hint:
        normalized_hint = corpus._normalize_name(domain_hint)
        if normalized_hint in registry.usable_books_by_domain:
            return {
                "domain": normalized_hint,
                "confidence": 1.0,
                "routing_notes": ["explicit_domain_hint"],
                "needs_clarification": False,
            }

    routed = corpus._route_domain(query, registry)
    if routed.get("selected_domain"):
        notes = ["domain_router"]
        if routed.get("ambiguous"):
            notes.append("ambiguous_domain")
        return {
            "domain": routed.get("selected_domain"),
            "confidence": float(routed.get("confidence") or 0.0),
            "routing_notes": notes,
            "needs_clarification": False,
        }

    domains = sorted(registry.usable_books_by_domain.keys())
    fallback_domain = domains[0] if domains else None
    return {
        "domain": fallback_domain,
        "confidence": float(routed.get("confidence") or 0.0),
        "routing_notes": ["domain_router_ambiguous"],
        "needs_clarification": True,
    }


def frame_local_corpus_problem(
    *,
    query: str,
    domain_hint: Optional[str] = None,
    config_or_path: Any = None,
) -> dict[str, Any]:
    domain_resolution = _resolve_domain(query, domain_hint, config_or_path)
    domain = domain_resolution.get("domain")
    if not domain:
        return {
            "status": "error",
            "error": "No compatible local corpus domain is available",
        }

    pack = load_local_corpus_pack_for_domain(domain, config_or_path)
    task_candidates = _sorted_task_candidates(query, pack)
    primary_task_type = (
        task_candidates[0][0] if task_candidates else pack.get("default_task_type")
    )
    primary_score = task_candidates[0][1] if task_candidates else 0.0
    secondary_task_types = [
        {"task_type": task_type, "score": round(score, 4)}
        for task_type, score in task_candidates[1:3]
        if score > 0.0
    ]
    if primary_score <= 0.55:
        primary_task_type = pack.get("default_task_type")
    task_config = (pack.get("task_types") or {}).get(primary_task_type, {})
    risk_profile = (pack.get("risk_profiles") or {}).get(task_config.get("risk_profile"), {})

    measurements = _extract_measurements(query)
    constraints = _extract_constraints(query)
    projection = _build_retrieval_projection(
        query,
        domain=domain,
        constraints=constraints,
        measurements=measurements,
    )
    observations = list(dict.fromkeys(projection["retrieval_observations"]))[:6]

    task_confidence = min(1.0, round(primary_score, 4))
    if secondary_task_types and (primary_score - secondary_task_types[0]["score"]) < 0.35:
        routing_notes = list(domain_resolution.get("routing_notes") or []) + ["blended_task_fallback"]
    else:
        routing_notes = list(domain_resolution.get("routing_notes") or [])

    if task_confidence < 0.6:
        routing_notes.append("low_task_confidence")

    return {
        "status": "ok",
        "phase": "completed",
        "query": query,
        "domain": domain,
        "domain_confidence": round(float(domain_resolution.get("confidence") or 0.0), 4),
        "primary_task_type": primary_task_type,
        "secondary_task_types": secondary_task_types,
        "task_type_confidence": task_confidence,
        "entities": list(dict.fromkeys(projection["retrieval_entities"]))[:8],
        "observations": observations,
        "constraints": constraints,
        "risk_flags": _extract_risk_flags(query, risk_profile),
        "unknowns": _infer_unknowns(domain, query, measurements, constraints),
        "retrieval_entities": projection["retrieval_entities"],
        "retrieval_observations": projection["retrieval_observations"],
        "retrieval_terms_surface": projection["retrieval_terms_surface"],
        "retrieval_terms_canonical": projection["retrieval_terms_canonical"],
        "retrieval_spine_surface": projection["retrieval_spine_surface"],
        "retrieval_spine_canonical": projection["retrieval_spine_canonical"],
        "answer_context": projection["answer_context"],
        "control_constraints": projection["control_constraints"],
        "normalization_applied": projection["normalization_applied"],
        "retrieval_projection_summary": projection["retrieval_projection_summary"],
        "promoted_selectors": projection["promoted_selectors"],
        "answer_mode": task_config.get("answer_mode"),
        "pack_version": pack.get("version"),
        "maturity_tier": MATURITY_LABELS.get(pack.get("maturity_tier"), "tier_3"),
        "needs_clarification": bool(domain_resolution.get("needs_clarification")) or task_confidence < 0.5,
        "routing_notes": routing_notes,
        "coverage_is_scaffold_not_exhaustive": True,
    }


def _axis_score(problem_frame: dict[str, Any], axis_config: dict[str, Any]) -> float:
    combined = " ".join(
        [
            problem_frame.get("query", ""),
            " ".join(problem_frame.get("retrieval_spine_surface") or []),
            " ".join(problem_frame.get("retrieval_spine_canonical") or []),
            " ".join(problem_frame.get("retrieval_entities") or problem_frame.get("entities") or []),
            " ".join(
                problem_frame.get("retrieval_observations") or problem_frame.get("observations") or []
            ),
            " ".join(problem_frame.get("retrieval_terms_surface") or []),
            " ".join(problem_frame.get("retrieval_terms_canonical") or []),
            " ".join(problem_frame.get("risk_flags") or []),
        ]
    )
    normalized = corpus._phrase_ready_text(combined)
    score = 0.0
    for term in axis_config.get("literal_terms") or []:
        normalized_term = corpus._phrase_ready_text(term)
        if normalized_term and normalized_term in normalized:
            score += 1.0 if " " in normalized_term else 0.45
    if axis_config.get("boost_table_like") and any(
        term in normalized for term in ("criteria", "threshold", "score", "staging", "dose", "regimen")
    ):
        score += 0.5
    return score


def _build_axis_query(problem_frame: dict[str, Any], axis_id: str, axis_config: dict[str, Any]) -> str:
    def _has_relation_core(value: str) -> bool:
        return any(
            _stem_like(token) in _RELATION_OR_MECHANISM_CORE_STEMS
            for token in _content_tokens(_span_tokens(corpus._normalize_text(value)))
        )

    def _candidate_phrases() -> list[str]:
        spine_surface = list(problem_frame.get("retrieval_spine_surface") or [])[:6]
        spine_canonical = list(problem_frame.get("retrieval_spine_canonical") or [])[:6]
        if spine_surface or spine_canonical:
            candidates = spine_surface + spine_canonical
        else:
            fallback: list[str] = []
            fallback.extend((problem_frame.get("retrieval_terms_surface") or [])[:4])
            fallback.extend((problem_frame.get("retrieval_terms_canonical") or [])[:4])
            fallback.extend((problem_frame.get("retrieval_entities") or problem_frame.get("entities") or [])[:3])
            fallback.extend(
                (problem_frame.get("retrieval_observations") or problem_frame.get("observations") or [])[:2]
            )
            candidates = fallback
        relation_first = [value for value in candidates if _has_relation_core(value)]
        non_relation = [value for value in candidates if value not in relation_first]
        return relation_first + non_relation

    def _compact_phrase(value: str) -> str:
        normalized = corpus._normalize_text(value)
        compacted = _compact_projection_span(normalized) or _trim_context_edges(normalized)
        if not compacted:
            compacted = normalized
        content_tokens = _content_tokens(_span_tokens(compacted))
        if not content_tokens:
            return ""
        return corpus._normalize_text(" ".join(content_tokens[:5]))

    phrases: list[str] = []
    phrases.extend(_candidate_phrases())
    phrases.extend((axis_config.get("literal_terms") or [])[:2])
    if problem_frame.get("risk_flags") and axis_id in {"dangerous_causes", "red_flags", "safety_risks"}:
        phrases.extend((problem_frame.get("risk_flags") or [])[:2])

    seen_canonical: set[str] = set()
    seen_stem_sets: list[set[str]] = []
    selected_phrases: list[str] = []

    for phrase in phrases:
        compacted = _compact_phrase(phrase)
        if not compacted:
            continue
        canonical = _canonicalize_span(compacted)
        if not canonical or canonical in seen_canonical:
            continue
        stems = {
            _stem_like(token)
            for token in _content_tokens(_span_tokens(compacted))
            if _stem_like(token)
        }
        if not stems:
            continue
        if any(
            len(stems & existing) / max(len(stems | existing), 1) >= 0.75
            for existing in seen_stem_sets
        ):
            continue
        seen_canonical.add(canonical)
        seen_stem_sets.append(stems)
        selected_phrases.append(compacted)
        if len(selected_phrases) >= 5:
            break

    if selected_phrases and not any(_has_relation_core(value) for value in selected_phrases):
        for phrase in phrases:
            compacted = _compact_phrase(phrase)
            if compacted and _has_relation_core(compacted):
                if compacted in selected_phrases:
                    selected_phrases = [compacted] + [
                        value for value in selected_phrases if value != compacted
                    ]
                else:
                    selected_phrases = [compacted, *selected_phrases[:4]]
                break

    if selected_phrases:
        query_phrases: list[str] = []
        token_count = 0
        for phrase in selected_phrases:
            phrase_tokens = _content_tokens(_span_tokens(phrase))
            if not phrase_tokens:
                continue
            remaining = 14 - token_count
            if remaining <= 0:
                break
            if len(phrase_tokens) > remaining:
                if not query_phrases:
                    query_phrases.append(" ".join(phrase_tokens[:remaining]).strip())
                break
            query_phrases.append(" ".join(phrase_tokens).strip())
            token_count += len(phrase_tokens)
        if query_phrases:
            return " ".join(query_phrases).strip()

    fallback_parts = (
        problem_frame.get("retrieval_spine_surface")
        or problem_frame.get("retrieval_spine_canonical")
        or problem_frame.get("retrieval_terms_surface")
        or problem_frame.get("retrieval_terms_canonical")
        or problem_frame.get("retrieval_entities")
        or problem_frame.get("retrieval_observations")
        or problem_frame.get("entities")
        or []
    )
    if fallback_parts:
        compacted_fallback = [_compact_phrase(value) for value in fallback_parts[:4]]
        compacted_fallback = [value for value in compacted_fallback if value]
        if compacted_fallback:
            return " ".join(
                dict.fromkeys(
                    token
                    for phrase in compacted_fallback
                    for token in _content_tokens(_span_tokens(phrase))
                )
            ).strip()
    return _compact_phrase(problem_frame.get("query", "")) or problem_frame.get("query", "")


def plan_local_corpus_axes(
    *,
    problem_frame: dict[str, Any],
    config_or_path: Any = None,
) -> dict[str, Any]:
    if not isinstance(problem_frame, dict) or problem_frame.get("status") == "error":
        return {"status": "error", "error": "A valid problem_frame is required"}

    domain = problem_frame.get("domain")
    pack = load_local_corpus_pack_for_domain(str(domain), config_or_path)
    task_type = str(problem_frame.get("primary_task_type") or pack.get("default_task_type"))
    task_config = (pack.get("task_types") or {}).get(task_type) or (
        pack.get("task_types") or {}
    ).get(pack.get("default_task_type"), {})

    required_axis_ids = list(task_config.get("required_axis_ids") or [])
    optional_axis_ids = list(task_config.get("optional_axis_ids") or [])
    default_budget = int(task_config.get("axis_budget") or pack.get("default_axis_budget") or 3)
    task_max_budget = int(task_config.get("max_axis_budget") or pack.get("max_axis_budget") or default_budget)
    max_budget = min(task_max_budget, pack.get("max_axis_budget"), LOCAL_CORPUS_REASONING_HARD_MAX_AXES)
    axis_budget = max(1, min(default_budget, max_budget))

    axes = []
    for axis_id in required_axis_ids:
        axis = (pack.get("axes") or {}).get(axis_id, {})
        axes.append(
            {
                "axis_id": axis_id,
                "intent": axis.get("intent"),
                "query": _build_axis_query(problem_frame, axis_id, axis),
                "preferred_resource_types": axis.get("preferred_resource_types") or [],
                "preferred_evidence_tiers": axis.get("preferred_evidence_tiers") or [],
                "discipline_hints": axis.get("discipline_hints") or [],
                "boost_table_like": bool(axis.get("boost_table_like")),
                "max_books": min(3, int(axis.get("max_books") or 2)),
                "top_k": min(8, int(axis.get("top_k") or 4)),
            }
        )

    remaining_budget = max(0, axis_budget - len(axes))
    scored_optional = []
    for axis_id in optional_axis_ids:
        axis = (pack.get("axes") or {}).get(axis_id, {})
        scored_optional.append((axis_id, _axis_score(problem_frame, axis)))
    scored_optional.sort(key=lambda item: (-item[1], item[0]))

    for axis_id, score in scored_optional[:remaining_budget]:
        axis = (pack.get("axes") or {}).get(axis_id, {})
        axes.append(
            {
                "axis_id": axis_id,
                "intent": axis.get("intent"),
                "query": _build_axis_query(problem_frame, axis_id, axis),
                "preferred_resource_types": axis.get("preferred_resource_types") or [],
                "preferred_evidence_tiers": axis.get("preferred_evidence_tiers") or [],
                "discipline_hints": axis.get("discipline_hints") or [],
                "boost_table_like": bool(axis.get("boost_table_like")),
                "max_books": min(3, int(axis.get("max_books") or 2)),
                "top_k": min(8, int(axis.get("top_k") or 4)),
                "selection_score": round(score, 4),
            }
        )

    coverage_limited = len(required_axis_ids) + len(optional_axis_ids) > len(axes)
    open_questions = list(problem_frame.get("unknowns") or [])
    return {
        "status": "ok",
        "phase": "completed",
        "domain": domain,
        "task_type": task_type,
        "axes": axes,
        "axis_budget": len(axes),
        "coverage_limited": coverage_limited,
        "coverage_is_scaffold_not_exhaustive": True,
        "open_questions": open_questions,
        "unmodeled_space_note": (
            "This axis plan is a bounded scaffold over the retrieved corpus, not an exhaustive model of the real problem space."
        ),
        "pack_version": pack.get("version"),
    }


def collect_local_corpus_axis_evidence(
    *,
    problem_frame: dict[str, Any],
    axes: list[dict[str, Any]],
    max_books_per_axis: int = 2,
    include_related_tables: bool = True,
    include_related_figures: bool = False,
    config_or_path: Any = None,
) -> dict[str, Any]:
    if not isinstance(problem_frame, dict) or problem_frame.get("status") == "error":
        return {"status": "error", "error": "A valid problem_frame is required"}
    if not axes:
        return {"status": "error", "error": "At least one axis is required"}

    domain = str(problem_frame.get("domain") or "")
    task_type = str(problem_frame.get("primary_task_type") or "")
    bounded_books_per_axis = max(1, min(3, int(max_books_per_axis or 2)))
    axis_results = []

    for axis in axes[:LOCAL_CORPUS_REASONING_HARD_MAX_AXES]:
        axis_id = str(axis.get("axis_id") or "")
        axis_query = str(axis.get("query") or problem_frame.get("query") or "").strip()
        discipline_hints = [
            corpus._normalize_name(value)
            for value in (axis.get("discipline_hints") or [])
            if corpus._normalize_name(value)
        ]
        shortlist = corpus.shortlist_local_corpus_books(
            query=axis_query,
            domain=domain,
            disciplines=discipline_hints or None,
            max_books=min(bounded_books_per_axis, int(axis.get("max_books") or bounded_books_per_axis)),
            config_or_path=config_or_path,
        )
        shortlisted_books = shortlist.get("items") or []
        selected_book_ids = [item.get("book_id") for item in shortlisted_books if item.get("book_id")]

        evidence_payload = (
            corpus.retrieve_local_corpus_evidence(
                query=axis_query,
                book_ids=selected_book_ids,
                top_k=min(8, int(axis.get("top_k") or 4)),
                include_related_tables=include_related_tables,
                include_related_figures=include_related_figures,
                config_or_path=config_or_path,
            )
            if selected_book_ids
            else {"status": "ok", "items": [], "evidence_sufficiency": "weak", "freshness_note": None}
        )

        evidence_items = evidence_payload.get("items") or []
        directness = "none"
        if any(
            reason in (item.get("rationale") or [])
            for item in evidence_items
            for reason in ("exact_content", "direct_topic", "treatment_signal")
        ):
            directness = "direct"
        elif evidence_items:
            directness = "indirect"

        coverage = "none"
        if evidence_items:
            coverage = evidence_payload.get("evidence_sufficiency") or "partial"

        related_tables = []
        related_figures = []
        for item in evidence_items:
            for table in item.get("related_tables") or []:
                key = (table.get("table_id"), table.get("page_no"))
                if key not in {(t.get("table_id"), t.get("page_no")) for t in related_tables}:
                    related_tables.append(table)
            for figure in item.get("related_figures") or []:
                key = (figure.get("figure_id"), figure.get("page_no"))
                if key not in {(f.get("figure_id"), f.get("page_no")) for f in related_figures}:
                    related_figures.append(figure)

        axis_results.append(
            {
                "axis_id": axis_id,
                "intent": axis.get("intent"),
                "query": axis_query,
                "shortlisted_books": shortlisted_books,
                "evidence_items": evidence_items,
                "related_tables": related_tables[:5],
                "related_figures": related_figures[:5],
                "coverage": coverage,
                "directness": directness,
                "freshness_note": evidence_payload.get("freshness_note"),
            }
        )

    return {
        "status": "ok",
        "phase": "completed",
        "domain": domain,
        "task_type": task_type,
        "axis_results": axis_results,
        "axis_count": len(axis_results),
        "coverage_is_scaffold_not_exhaustive": True,
    }


def assess_local_corpus_evidence(
    *,
    problem_frame: dict[str, Any],
    evidence_bundle: dict[str, Any],
    config_or_path: Any = None,
) -> dict[str, Any]:
    if not isinstance(problem_frame, dict) or problem_frame.get("status") == "error":
        return {"status": "error", "error": "A valid problem_frame is required"}
    if not isinstance(evidence_bundle, dict) or evidence_bundle.get("status") == "error":
        return {"status": "error", "error": "A valid evidence_bundle is required"}

    domain = str(problem_frame.get("domain") or "")
    pack = load_local_corpus_pack_for_domain(domain, config_or_path)
    task_type = str(problem_frame.get("primary_task_type") or pack.get("default_task_type"))
    task_config = (pack.get("task_types") or {}).get(task_type) or {}
    insufficiency_profile = (pack.get("insufficiency_profiles") or {}).get(
        task_config.get("insufficiency_profile"), {}
    )
    risk_profile = (pack.get("risk_profiles") or {}).get(task_config.get("risk_profile"), {})

    axis_results = evidence_bundle.get("axis_results") or []
    covered_axes = [axis["axis_id"] for axis in axis_results if axis.get("evidence_items")]
    required_axes = list(task_config.get("required_axis_ids") or [])
    missing_axes = [axis_id for axis_id in required_axes if axis_id not in covered_axes]
    direct_axes = [axis["axis_id"] for axis in axis_results if axis.get("directness") == "direct"]
    total_items = sum(len(axis.get("evidence_items") or []) for axis in axis_results)
    source_conflicts = []
    freshness_note = next(
        (axis.get("freshness_note") for axis in axis_results if axis.get("freshness_note")),
        None,
    )

    if total_items == 0:
        evidence_sufficiency = "weak"
    elif missing_axes:
        evidence_sufficiency = "weak" if len(missing_axes) >= max(1, len(required_axes)) else "partial"
    elif len(direct_axes) >= max(1, len(required_axes)):
        evidence_sufficiency = "strong"
    else:
        evidence_sufficiency = "partial"

    critical_gaps = list(problem_frame.get("unknowns") or [])
    critical_gaps.extend(
        f"missing critical axis: {axis_id.replace('_', ' ')}" for axis_id in missing_axes
    )
    critical_gaps = critical_gaps[:6]

    answer_posture = insufficiency_profile.get("default_posture") or "scoped"
    if evidence_sufficiency == "weak":
        answer_posture = insufficiency_profile.get("weak_posture") or "cautious"
    elif evidence_sufficiency == "partial":
        answer_posture = insufficiency_profile.get("partial_posture") or "scoped"
    elif evidence_sufficiency == "strong":
        answer_posture = insufficiency_profile.get("strong_posture") or "grounded"

    guidance_parts = []
    if insufficiency_profile.get("must_admit_gaps", True) and critical_gaps:
        guidance_parts.append("State important gaps explicitly.")
    if risk_profile.get("must_surface_red_flags") and problem_frame.get("risk_flags"):
        guidance_parts.append("Surface the relevant risk flags explicitly.")
    if risk_profile.get("must_name_preconditions"):
        guidance_parts.append("Name the scope, assumptions, and preconditions explicitly.")
    if insufficiency_profile.get("avoid_exhaustive_language", True):
        guidance_parts.append(
            "Do not imply that the retrieved axes or buckets exhaust the real problem space."
        )
    guidance_parts.append(
        insufficiency_profile.get("guidance")
        or "Constrain the answer to what the retrieved evidence directly supports."
    )

    return {
        "status": "ok",
        "phase": "completed",
        "domain": domain,
        "task_type": task_type,
        "evidence_sufficiency": evidence_sufficiency,
        "critical_gaps": critical_gaps,
        "covered_axes": covered_axes,
        "missing_axes": missing_axes,
        "source_conflicts": source_conflicts,
        "answer_posture": answer_posture,
        "answer_guidance": " ".join(guidance_parts).strip(),
        "coverage_is_scaffold_not_exhaustive": True,
        "freshness_note": freshness_note,
    }
