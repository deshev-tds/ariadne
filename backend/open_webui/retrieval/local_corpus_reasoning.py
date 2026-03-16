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
    observations = measurements + [
        segment
        for segment in _extract_segments(query)
        if any(char.isdigit() for char in segment) or len(segment.split()) >= 4
    ][:4]
    observations = list(dict.fromkeys(observations))[:6]

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
        "entities": _extract_entities(query, domain),
        "observations": observations,
        "constraints": constraints,
        "risk_flags": _extract_risk_flags(query, risk_profile),
        "unknowns": _infer_unknowns(domain, query, measurements, constraints),
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
            " ".join(problem_frame.get("entities") or []),
            " ".join(problem_frame.get("observations") or []),
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
    parts: list[str] = []
    parts.extend(problem_frame.get("entities") or [])
    parts.extend(problem_frame.get("observations") or [])
    parts.extend((axis_config.get("literal_terms") or [])[:4])
    if problem_frame.get("risk_flags") and axis_id in {"dangerous_causes", "red_flags", "safety_risks"}:
        parts.extend(problem_frame.get("risk_flags") or [])

    seen: set[str] = set()
    ordered_parts: list[str] = []
    for part in parts:
        normalized = corpus._normalize_text(part)
        lowered = normalized.lower()
        if normalized and lowered not in seen:
            seen.add(lowered)
            ordered_parts.append(normalized)
    return " ".join(ordered_parts[:18]).strip() or problem_frame.get("query", "")


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
