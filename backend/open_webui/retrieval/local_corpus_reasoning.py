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
LOCAL_CORPUS_VISIBLE_ITEMS_PER_AXIS = 2
LOCAL_CORPUS_VISIBLE_SNIPPET_CHARS = 600
LOCAL_CORPUS_VISIBLE_SNIPPET_HARD_CAP = 800
LOCAL_CORPUS_MAX_TOTAL_VISIBLE_ITEMS = 6
LOCAL_CORPUS_MAX_TOTAL_VISIBLE_CHARS = 4000
LOCAL_CORPUS_EXPAND_MAX_HANDLES = 4
LOCAL_CORPUS_EXPANDED_EXCERPT_CHARS = 2200
LOCAL_CORPUS_COMPACT_TABLE_LIMIT = 2
LOCAL_CORPUS_COMPACT_FIGURE_LIMIT = 2

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


def _coverage_priority(coverage: str) -> int:
    return {"strong": 3, "partial": 2, "weak": 1}.get(str(coverage or "").lower(), 0)


def _artifact_priority(
    artifact: dict[str, Any],
    *,
    visible_keys: set[tuple[str, Any]],
    query_text: str,
) -> tuple[int, int, int, str]:
    page_no = int(artifact.get("page_no") or 0)
    key = (artifact.get("table_id") or artifact.get("figure_id"), artifact.get("page_no"))
    linked_to_visible = 1 if key in visible_keys else 0
    normalized_query = corpus._phrase_ready_text(query_text)
    table_like = any(
        term in normalized_query for term in ("criteria", "threshold", "score", "staging", "dose", "regimen")
    )
    section = corpus._phrase_ready_text(str(artifact.get("section_path") or ""))
    relevance = 1 if table_like and any(
        token in section for token in ("criteria", "threshold", "regimen", "dose", "staging", "score")
    ) else 0
    return (-linked_to_visible, -relevance, page_no, str(key))


def _rank_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    visible_keys: set[tuple[str, Any]],
    query_text: str,
    limit: int,
) -> list[dict[str, Any]]:
    unique = []
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        key = (artifact.get("table_id") or artifact.get("figure_id"), artifact.get("page_no"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(artifact)
    unique.sort(key=lambda artifact: _artifact_priority(artifact, visible_keys=visible_keys, query_text=query_text))
    return unique[:limit]


def _compact_content(text: str, *, max_chars: int) -> tuple[str, bool]:
    normalized = corpus._normalize_text(text)
    if len(normalized) <= max_chars:
        return normalized, False
    snippet = normalized[:max_chars].rstrip()
    return f"{snippet}...", True


def _build_evidence_handle(axis_id: str, book_id: str, chunk_id: str) -> str:
    return f"{axis_id}|{book_id}|{chunk_id}"


def _parse_evidence_handle(handle: str) -> tuple[str, str, str]:
    parts = str(handle or "").split("|", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Invalid evidence handle: {handle}")
    return parts[0], parts[1], parts[2]


def _axis_priority(axis_result: dict[str, Any]) -> tuple[int, int, str]:
    return (
        -1 if axis_result.get("directness") == "direct" else 0,
        -_coverage_priority(str(axis_result.get("coverage") or "")),
        str(axis_result.get("axis_id") or ""),
    )


def _candidate_priority(candidate: dict[str, Any]) -> tuple[int, int, int, float, int, str]:
    rationale = set(candidate.get("rationale") or [])
    direct = 1 if candidate.get("directness") == "direct" else 0
    coverage = _coverage_priority(str(candidate.get("coverage") or ""))
    policy_signal = 1 if rationale & {"table_locality", "treatment_signal", "direct_topic"} else 0
    score = float(candidate.get("score") or 0.0)
    rank_index = int(candidate.get("rank_index") or 0)
    return (-direct, -coverage, -policy_signal, -score, rank_index, str(candidate.get("handle") or ""))


def _apply_global_visible_budget(axis_results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    total_items_budget = LOCAL_CORPUS_MAX_TOTAL_VISIBLE_ITEMS
    total_chars_budget = LOCAL_CORPUS_MAX_TOTAL_VISIBLE_CHARS

    primary_candidates = []
    overflow_candidates = []
    for axis in axis_results:
        visible_items = list(axis.get("evidence_items") or [])
        if visible_items:
            primary_candidates.append((axis.get("axis_id"), visible_items[0]))
            for item in visible_items[1:]:
                overflow_candidates.append((axis.get("axis_id"), item))

    primary_candidates.sort(key=lambda pair: _candidate_priority(pair[1]))
    overflow_candidates.sort(key=lambda pair: _candidate_priority(pair[1]))

    kept_handles: set[str] = set()
    used_chars = 0
    kept_items = 0
    budget_limited = False

    for bucket in (primary_candidates, overflow_candidates):
        for axis_id, item in bucket:
            if kept_items >= total_items_budget:
                budget_limited = True
                continue
            item_chars = len(str(item.get("content") or ""))
            if used_chars + item_chars > total_chars_budget:
                budget_limited = True
                continue
            kept_handles.add(str(item.get("handle") or ""))
            kept_items += 1
            used_chars += item_chars

    compact_axis_results = []
    for axis in axis_results:
        total_items = int(axis.get("total_evidence_count") or 0)
        visible_items = [
            item for item in (axis.get("evidence_items") or []) if str(item.get("handle") or "") in kept_handles
        ]
        omitted = max(0, total_items - len(visible_items))
        axis = dict(axis)
        axis["evidence_items"] = visible_items
        axis["visible_evidence_count"] = len(visible_items)
        axis["omitted_evidence_count"] = omitted
        axis["has_more_evidence"] = omitted > 0
        if total_items > 0 and not visible_items:
            budget_limited = True
        compact_axis_results.append(axis)

    return compact_axis_results, budget_limited


def _expansion_reason_codes(
    *,
    axis_query: str,
    coverage: str,
    directness: str,
    related_tables: list[dict[str, Any]],
    budget_limited: bool,
) -> list[str]:
    codes = []
    normalized_query = corpus._phrase_ready_text(axis_query)
    if coverage in {"weak", "partial"}:
        codes.append("partial_evidence")
    if directness == "indirect":
        codes.append("indirect_evidence")
    if related_tables:
        codes.append("table_dependent")
    if any(
        term in normalized_query for term in ("criteria", "criterion", "threshold", "score", "staging")
    ):
        codes.append("criteria_dependent")
    if any(
        term in normalized_query for term in ("regimen", "antibiotic", "dose", "dosing", "empiric")
    ):
        codes.append("regimen_dependent")
    if budget_limited:
        codes.append("global_budget_limited")
    deduped = []
    seen = set()
    for code in codes:
        if code not in seen:
            seen.add(code)
            deduped.append(code)
    return deduped


def _expansion_reason_text(codes: list[str]) -> str:
    labels = {
        "partial_evidence": "retrieved evidence is only partial",
        "indirect_evidence": "the visible evidence is relevant but indirect",
        "table_dependent": "nearby tables may materially refine the answer",
        "criteria_dependent": "criteria or threshold details may matter",
        "regimen_dependent": "regimen or dosing details may need direct inspection",
        "conflict_present": "the retrieved sources conflict and need inspection",
        "global_budget_limited": "the global visible budget hid some potentially useful evidence",
    }
    rendered = [labels[code] for code in codes if code in labels]
    return "; ".join(rendered)


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
    compact_axis_results = []

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
        visible_candidates = []
        for item in evidence_items:
            for table in item.get("related_tables") or []:
                key = (table.get("table_id"), table.get("page_no"))
                if key not in {(t.get("table_id"), t.get("page_no")) for t in related_tables}:
                    related_tables.append(table)
            for figure in item.get("related_figures") or []:
                key = (figure.get("figure_id"), figure.get("page_no"))
                if key not in {(f.get("figure_id"), f.get("page_no")) for f in related_figures}:
                    related_figures.append(figure)

        for rank_index, item in enumerate(
            evidence_items[:LOCAL_CORPUS_VISIBLE_ITEMS_PER_AXIS], start=1
        ):
            max_chars = min(
                LOCAL_CORPUS_VISIBLE_SNIPPET_HARD_CAP, LOCAL_CORPUS_VISIBLE_SNIPPET_CHARS
            )
            compact_content, content_truncated = _compact_content(
                str(item.get("content") or ""), max_chars=max_chars
            )
            visible_candidates.append(
                {
                    "handle": _build_evidence_handle(
                        axis_id,
                        str(item.get("book_id") or ""),
                        str(item.get("chunk_id") or ""),
                    ),
                    "chunk_id": item.get("chunk_id"),
                    "domain": item.get("domain"),
                    "book_id": item.get("book_id"),
                    "title": item.get("title"),
                    "discipline": item.get("discipline"),
                    "resource_type": item.get("resource_type"),
                    "evidence_tier": item.get("evidence_tier"),
                    "page_no": item.get("page_no"),
                    "section_path": item.get("section_path"),
                    "content": compact_content,
                    "content_kind": "snippet",
                    "content_truncated": content_truncated,
                    "score": item.get("score"),
                    "rationale": item.get("rationale") or [],
                    "citation_label": item.get("citation_label"),
                    "rank_index": rank_index,
                    "coverage": coverage,
                    "directness": directness,
                }
            )

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
        compact_axis_results.append(
            {
                "axis_id": axis_id,
                "intent": axis.get("intent"),
                "query": axis_query,
                "shortlisted_books": shortlisted_books,
                "evidence_items": visible_candidates,
                "total_evidence_count": len(evidence_items),
                "visible_evidence_count": len(visible_candidates),
                "omitted_evidence_count": max(0, len(evidence_items) - len(visible_candidates)),
                "has_more_evidence": len(evidence_items) > len(visible_candidates),
                "coverage": coverage,
                "directness": directness,
                "freshness_note": evidence_payload.get("freshness_note"),
                "related_tables": related_tables,
                "related_figures": related_figures,
            }
        )

    compact_axis_results.sort(key=_axis_priority)
    compact_axis_results, global_budget_limited = _apply_global_visible_budget(compact_axis_results)

    artifacts_by_handle: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for source_axis in axis_results:
        source_axis_id = str(source_axis.get("axis_id") or "")
        for original_item in source_axis.get("evidence_items") or []:
            handle = _build_evidence_handle(
                source_axis_id,
                str(original_item.get("book_id") or ""),
                str(original_item.get("chunk_id") or ""),
            )
            artifacts_by_handle[handle] = {
                "tables": list(original_item.get("related_tables") or []),
                "figures": list(original_item.get("related_figures") or []),
            }

    final_axis_results = []
    for axis in compact_axis_results:
        visible_table_keys = {
            (table.get("table_id"), table.get("page_no"))
            for item in axis.get("evidence_items") or []
            for table in (artifacts_by_handle.get(str(item.get("handle") or ""), {}) or {}).get("tables", [])
        }
        visible_figure_keys = {
            (figure.get("figure_id"), figure.get("page_no"))
            for item in axis.get("evidence_items") or []
            for figure in (artifacts_by_handle.get(str(item.get("handle") or ""), {}) or {}).get("figures", [])
        }
        ranked_tables = _rank_artifacts(
            axis.get("related_tables") or [],
            visible_keys=visible_table_keys,
            query_text=str(axis.get("query") or ""),
            limit=LOCAL_CORPUS_COMPACT_TABLE_LIMIT,
        )
        ranked_figures = _rank_artifacts(
            axis.get("related_figures") or [],
            visible_keys=visible_figure_keys,
            query_text=str(axis.get("query") or ""),
            limit=LOCAL_CORPUS_COMPACT_FIGURE_LIMIT,
        )
        reason_codes = _expansion_reason_codes(
            axis_query=str(axis.get("query") or ""),
            coverage=str(axis.get("coverage") or ""),
            directness=str(axis.get("directness") or ""),
            related_tables=ranked_tables,
            budget_limited=bool(global_budget_limited and axis.get("has_more_evidence")),
        )
        final_axis_results.append(
            {
                "axis_id": axis.get("axis_id"),
                "intent": axis.get("intent"),
                "query": axis.get("query"),
                "shortlisted_books": axis.get("shortlisted_books") or [],
                "evidence_items": axis.get("evidence_items") or [],
                "visible_evidence_count": axis.get("visible_evidence_count") or 0,
                "total_evidence_count": axis.get("total_evidence_count") or 0,
                "omitted_evidence_count": axis.get("omitted_evidence_count") or 0,
                "has_more_evidence": bool(axis.get("has_more_evidence")),
                "coverage": axis.get("coverage"),
                "directness": axis.get("directness"),
                "freshness_note": axis.get("freshness_note"),
                "related_tables": ranked_tables,
                "related_figures": ranked_figures,
                "expansion_recommended": bool(reason_codes),
                "expansion_reason_code": reason_codes[0] if reason_codes else None,
                "expansion_reason_codes": reason_codes,
                "expansion_reason": _expansion_reason_text(reason_codes) if reason_codes else "",
            }
        )

    return {
        "status": "ok",
        "phase": "completed",
        "domain": domain,
        "task_type": task_type,
        "axis_results": final_axis_results,
        "axis_count": len(final_axis_results),
        "visible_evidence_count": sum(
            int(axis.get("visible_evidence_count") or 0) for axis in final_axis_results
        ),
        "total_evidence_count": sum(
            int(axis.get("total_evidence_count") or 0) for axis in final_axis_results
        ),
        "omitted_evidence_count": sum(
            int(axis.get("omitted_evidence_count") or 0) for axis in final_axis_results
        ),
        "global_budget_limited": bool(global_budget_limited),
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
    # Important invariant: assess over retrieval-derived totals/directness, not only the compact visible projection.
    covered_axes = [
        axis["axis_id"] for axis in axis_results if int(axis.get("total_evidence_count") or 0) > 0
    ]
    required_axes = list(task_config.get("required_axis_ids") or [])
    missing_axes = [axis_id for axis_id in required_axes if axis_id not in covered_axes]
    direct_axes = [axis["axis_id"] for axis in axis_results if axis.get("directness") == "direct"]
    total_items = sum(int(axis.get("total_evidence_count") or 0) for axis in axis_results)
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


def expand_local_corpus_axis_evidence(
    *,
    handles: list[str],
    include_related_tables: bool = True,
    include_related_figures: bool = False,
    config_or_path: Any = None,
) -> dict[str, Any]:
    normalized_handles = [str(handle).strip() for handle in (handles or []) if str(handle).strip()]
    if not normalized_handles:
        return {"status": "error", "error": "At least one evidence handle is required"}

    bounded_handles = normalized_handles[:LOCAL_CORPUS_EXPAND_MAX_HANDLES]
    parsed_handles = []
    for handle in bounded_handles:
        axis_id, book_id, chunk_id = _parse_evidence_handle(handle)
        parsed_handles.append(
            {
                "handle": handle,
                "axis_id": axis_id,
                "book_id": book_id,
                "chunk_id": chunk_id,
            }
        )

    book_ids = list(dict.fromkeys(entry["book_id"] for entry in parsed_handles))
    chunk_ids = list(dict.fromkeys(entry["chunk_id"] for entry in parsed_handles))
    payload = corpus.expand_local_corpus_evidence_chunks(
        book_ids=book_ids,
        chunk_ids=chunk_ids,
        excerpt_chars=LOCAL_CORPUS_EXPANDED_EXCERPT_CHARS,
        include_related_tables=include_related_tables,
        include_related_figures=include_related_figures,
        config_or_path=config_or_path,
    )
    if payload.get("status") == "error":
        return payload

    items_by_chunk = {str(item.get("chunk_id") or ""): item for item in payload.get("items") or []}
    expanded_items = []
    for entry in parsed_handles:
        item = items_by_chunk.get(entry["chunk_id"])
        if not item:
            continue
        expanded_items.append(
            {
                **item,
                "handle": entry["handle"],
                "axis_id": entry["axis_id"],
            }
        )

    return {
        "status": "ok",
        "phase": "completed",
        "items": expanded_items,
        "expanded_count": len(expanded_items),
    }
