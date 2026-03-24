from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


RESEARCH_GUIDED_STATE_KEY = "research_guided_state"
RESEARCH_GUIDED_STATE_VERSION = 1
RESEARCH_GUIDED_RUNTIME_EVENT_CAP = 24
RESEARCH_GUIDED_MAX_GOALS = 4
RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL = 2
RESEARCH_GUIDED_DEFAULT_MAX_UNIQUE_QUERIES = 8
RESEARCH_GUIDED_DEFAULT_MAX_UNIQUE_FETCHES = 6
RESEARCH_STATUS_MARKER = "### Research Status"

GOAL_STATUS_OPEN = "open"
GOAL_STATUS_SUPPORTED = "supported"
GOAL_STATUS_MIXED = "mixed"
GOAL_STATUS_NOT_SUPPORTED = "not_supported"
GOAL_STATUS_INSUFFICIENT = "insufficient"

CLAIM_LABEL_VERIFIED = "verified_fact"
CLAIM_LABEL_INFERENCE = "reasonable_inference"
CLAIM_LABEL_UNCERTAIN = "uncertain"

WORKING_PROPOSITION_STATES = {
    "open",
    "leaning_support",
    "leaning_mixed",
    "leaning_not_supported",
}
TERMINAL_GOAL_STATUSES = {
    GOAL_STATUS_SUPPORTED,
    GOAL_STATUS_MIXED,
    GOAL_STATUS_NOT_SUPPORTED,
    GOAL_STATUS_INSUFFICIENT,
}
TERMINAL_DISCONFIRMATION_OUTCOMES = {
    "found",
    "not_found_under_budgeted_probe",
    "not_meaningfully_tested",
}

SCIENTIFIC_EVIDENCE_CUES = {
    "association",
    "causal",
    "clinical",
    "compare",
    "comparison",
    "data",
    "dose",
    "effect",
    "evidence",
    "experiment",
    "hypothesis",
    "literature",
    "mechanism",
    "meta-analysis",
    "meta analysis",
    "outcome",
    "paper",
    "papers",
    "proof",
    "randomized",
    "rct",
    "research",
    "review",
    "risk",
    "safety",
    "scientific",
    "significant",
    "study",
    "studies",
    "systematic review",
    "theorem",
    "trial",
}

SCIENTIFIC_DOMAIN_TERMS = {
    "algorithm",
    "biology",
    "chemical",
    "chemistry",
    "circadian",
    "clinical",
    "dosage",
    "equation",
    "gene",
    "mathematics",
    "medical",
    "melatonin",
    "molecule",
    "neural",
    "outcome",
    "physics",
    "quantum",
    "reaction",
    "spectrum",
    "theorem",
    "wavelength",
}

STRICT_EVIDENCE_REQUIREMENT_CUES = {
    "strong evidence",
    "robust evidence",
    "well established",
    "consensus",
    "clinically meaningful",
    "placebo-controlled",
    "placebo controlled",
    "meta-analysis",
    "meta analysis",
    "systematic review",
    "human studies and reviews",
    "recent human studies",
    "larger studies",
}

DISCONFIRMATION_QUERY_CUES = {
    "counterevidence",
    "counter-evidence",
    "null",
    "no effect",
    "no meaningful",
    "not statistically significant",
    "insufficient evidence",
    "fails to support",
    "does not support",
}

NON_SCIENCE_BYPASS_TERMS = {
    "airbnb",
    "bali",
    "bar",
    "bars",
    "beach club",
    "brunch",
    "cafe",
    "cafes",
    "cheap eats",
    "cocktail",
    "cocktails",
    "date night",
    "dinner",
    "drink",
    "drinks",
    "eat",
    "flight",
    "food",
    "fun things",
    "hotel",
    "hostel",
    "itinerary",
    "nightlife",
    "party",
    "restaurant",
    "restaurants",
    "shopping",
    "surf",
    "taxi",
    "things to do",
    "travel",
    "trip",
    "visa",
    "where to go",
}

GENERAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "would",
}

DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.IGNORECASE)
PMID_RE = re.compile(r"/pubmed/(\d+)|[?&]term=(\d+)\[pmid\]", re.IGNORECASE)
PMCID_RE = re.compile(r"/articles/(PMC\d+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-]+)", re.IGNORECASE)
NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)


def normalize_research_guided_mode(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_bool(value: Any) -> bool:
    return normalize_research_guided_mode(value)


def _goal_slug(value: Any) -> str:
    text = _normalize_text(value).lower()
    if not text:
        return "goal"
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:48] or "goal"


def _tokenize_terms(value: Any) -> list[str]:
    text = _normalize_text(value).lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9.+/\-]{1,}", text)
    return [token for token in tokens if token not in GENERAL_STOPWORDS]


def _domain_from_url(url: str) -> str:
    try:
        return (urlsplit(url).netloc or "").strip().lower()
    except Exception:
        return ""


def canonicalize_url(url: Any) -> str:
    raw = _normalize_text(url)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except Exception:
        return raw.lower()

    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    query = parsed.query
    if query:
        pairs = sorted(parse_qs(query, keep_blank_values=False).items())
        query = "&".join(
            f"{key}={value}"
            for key, values in pairs
            for value in sorted(values)
        )
    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized = f"{normalized}?{query}"
    return normalized


def query_fingerprint(value: Any) -> str:
    normalized = " ".join(sorted(_tokenize_terms(value)))
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def is_research_guided_turn_eligible(
    *,
    latest_user_text: str,
    working_mode: str,
    research_guided_mode: bool,
    web_evidence_enabled: bool,
    deep_research_enabled: bool = False,
) -> tuple[bool, str | None]:
    if not research_guided_mode:
        return False, "toggle_disabled"
    if str(working_mode or "").strip().lower() != "science":
        return False, "working_mode_not_science"
    if not web_evidence_enabled:
        return False, "web_evidence_inactive"
    if deep_research_enabled:
        return False, "deep_research_active"

    prompt = _normalize_text(latest_user_text).lower()
    if not prompt:
        return False, "empty_prompt"

    if any(term in prompt for term in NON_SCIENCE_BYPASS_TERMS):
        return False, "non_science_query"

    has_science_cue = any(term in prompt for term in SCIENTIFIC_EVIDENCE_CUES)
    has_domain_cue = any(term in prompt for term in SCIENTIFIC_DOMAIN_TERMS)

    if has_science_cue or has_domain_cue:
        return True, None

    return False, "insufficient_science_signal"


def infer_domain_profile(objective: Any) -> str:
    text = _normalize_text(objective).lower()
    if not text:
        return "general_science"

    if any(term in text for term in {"clinical", "medical", "patient", "dosage", "disease", "drug", "medicine"}):
        return "medicine_bio"
    if any(term in text for term in {"cell", "mouse", "organism", "gene", "protein", "biology"}):
        return "medicine_bio"
    if any(term in text for term in {"reaction", "molecule", "quantum", "physics", "chemistry", "wavelength"}):
        return "physics_chemistry"
    if any(term in text for term in {"theorem", "proof", "lemma", "corollary"}):
        return "math_theory"
    if any(term in text for term in {"benchmark", "algorithm", "runtime", "complexity", "model", "systems"}):
        return "cs_systems_ml"
    return "general_science"


def _goal_resolution_type(objective: str) -> str:
    normalized = objective.lower()
    if any(term in normalized for term in {"mechanism", "how", "why", "pathway"}):
        return "mechanism"
    if any(term in normalized for term in {"compare", "vs", "versus", "difference", "better than"}):
        return "comparison"
    if any(term in normalized for term in {"how much", "effect size", "clinically meaningful", "magnitude"}):
        return "magnitude"
    if any(term in normalized for term in {"should", "recommend", "practical", "what follows", "ordinary use"}):
        return "recommendation"
    if any(term in normalized for term in {"cause", "causal", "effect", "lead to", "impact"}):
        return "causal"
    return "fact"


def _default_disconfirmation_target(resolution_type: str, objective: str) -> str:
    if resolution_type == "comparison":
        return "no meaningful difference between the compared conditions"
    if resolution_type == "mechanism":
        return "the proposed mechanism is unsupported or only indirectly supported"
    if resolution_type == "magnitude":
        return "the observed effect is too small, inconsistent, or proxy-only"
    if resolution_type == "recommendation":
        return "there is insufficient direct outcome evidence for a practical recommendation"
    if resolution_type == "causal":
        return "the evidence is non-causal, confounded, or materially weaker than implied"
    return "the available evidence does not materially support the proposition"


def _contract_for_resolution_type(resolution_type: str) -> dict[str, Any]:
    contract = {
        "allowed_evidence_classes": [
            "direct_empirical",
            "observational",
            "systematic_synthesis",
            "guideline",
            "canonical_reference",
            "mechanistic",
        ],
        "forbidden_as_sole_support_classes": ["secondary_summary", "mirror_or_index"],
        "direct_support_required_for_verified": True,
        "contradiction_blocks_verified": True,
    }
    if resolution_type == "mechanism":
        contract["direct_support_required_for_verified"] = False
        contract["forbidden_as_sole_support_classes"] = [
            "secondary_summary",
            "mirror_or_index",
        ]
    elif resolution_type == "recommendation":
        contract["forbidden_as_sole_support_classes"] = [
            "secondary_summary",
            "mirror_or_index",
            "mechanistic",
            "observational",
        ]
    return contract


def _coverage_requirement_for_objective(objective: str) -> str:
    normalized = _normalize_text(objective).lower()
    if any(term in normalized for term in STRICT_EVIDENCE_REQUIREMENT_CUES):
        return "strict"
    return "normal"


def _probe_budget_for_resolution_type(resolution_type: str) -> dict[str, Any]:
    require_strong_source = resolution_type in {"comparison", "magnitude", "recommendation", "causal", "fact"}
    return {
        "required": {
            "target_aligned": True,
            "disconfirming": True,
            "strong_source": require_strong_source,
            "broader_fallback": False,
        },
        "observed": {
            "target_aligned": 0,
            "disconfirming": 0,
            "strong_source": 0,
            "broader_fallback": 0,
        },
    }


def _build_primary_goal(objective: str, *, goal_index: int = 1) -> dict[str, Any]:
    question = _normalize_text(objective)
    if not question.endswith("?"):
        question = f"{question}?"
    resolution_type = _goal_resolution_type(objective)
    disconfirmation_target = _default_disconfirmation_target(resolution_type, objective)
    goal_terms = _tokenize_terms(question)
    disconfirmation_terms = _tokenize_terms(disconfirmation_target)
    goal_id = f"goal_{goal_index}_{_goal_slug(question)}"
    return {
        "goal_id": goal_id,
        "question": question,
        "priority": "primary",
        "resolution_type": resolution_type,
        "coverage_requirement": _coverage_requirement_for_objective(objective),
        "acceptance_contract": _contract_for_resolution_type(resolution_type),
        "status": GOAL_STATUS_OPEN,
        "resolution_basis": "",
        "disconfirmation_targets": [disconfirmation_target],
        "disconfirmation_attempts": [],
        "disconfirmation_outcome": "",
        "support_ids": [],
        "oppose_ids": [],
        "orthogonal_ids": [],
        "goal_terms": goal_terms,
        "disconfirmation_terms": disconfirmation_terms,
        "probe_budget": _probe_budget_for_resolution_type(resolution_type),
    }


def _build_secondary_goals(objective: str, primary_goal: dict[str, Any]) -> list[dict[str, Any]]:
    objective_text = _normalize_text(objective)
    normalized = objective_text.lower()
    goals: list[dict[str, Any]] = []
    next_index = 2

    if "mechanism" in normalized and primary_goal["resolution_type"] != "mechanism":
        goals.append(
            _build_primary_goal(
                f"What mechanism-level evidence exists for {objective_text}",
                goal_index=next_index,
            )
        )
        next_index += 1

    if (
        any(term in normalized for term in {"how much", "effect size", "clinically meaningful", "magnitude"})
        and primary_goal["resolution_type"] != "magnitude"
    ):
        goals.append(
            _build_primary_goal(
                f"What evidence exists for the effect size or practical magnitude of {objective_text}",
                goal_index=next_index,
            )
        )
        next_index += 1

    if (
        any(term in normalized for term in {"should", "recommend", "practical", "ordinary use", "what follows"})
        and primary_goal["resolution_type"] != "recommendation"
    ):
        goals.append(
            _build_primary_goal(
                f"What practical conclusion follows from the evidence about {objective_text}",
                goal_index=next_index,
            )
        )

    return goals[: max(0, RESEARCH_GUIDED_MAX_GOALS - 1)]


def build_goal_plan(objective: Any, *, domain_profile: Optional[str] = None) -> list[dict[str, Any]]:
    normalized_objective = _normalize_text(objective)
    primary_goal = _build_primary_goal(normalized_objective, goal_index=1)
    goals = [primary_goal, *_build_secondary_goals(normalized_objective, primary_goal)]
    profile = domain_profile or infer_domain_profile(normalized_objective)
    for goal in goals:
        goal["domain_profile"] = profile
    return goals[:RESEARCH_GUIDED_MAX_GOALS]


def build_initial_state(objective: Any) -> dict[str, Any]:
    normalized_objective = _normalize_text(objective)
    domain_profile = infer_domain_profile(normalized_objective)
    goals = build_goal_plan(normalized_objective, domain_profile=domain_profile)
    return {
        "version": RESEARCH_GUIDED_STATE_VERSION,
        "research_run_id": f"rg_{uuid4().hex[:12]}",
        "phase": "plan",
        "objective": normalized_objective,
        "domain_profile": domain_profile,
        "goals": goals,
        "working_propositions": [
            {
                "proposition_id": f"prop_{goal['goal_id']}",
                "goal_id": goal["goal_id"],
                "text": goal["question"],
                "state": "open",
                "support_ids": [],
                "oppose_ids": [],
                "contradiction_pressure": False,
            }
            for goal in goals
        ],
        "candidate_claims": [],
        "evidence_ledger": [],
        "stored_artifacts": [],
        "unique_query_fingerprints": [],
        "unique_fetch_urls": [],
        "duplicate_query_count": 0,
        "duplicate_fetch_count": 0,
        "negative_signal_count": 0,
        "blocked_access_count": 0,
        "ready_to_answer": False,
        "stop_reason": "",
        "max_unique_queries": RESEARCH_GUIDED_DEFAULT_MAX_UNIQUE_QUERIES,
        "max_unique_fetches": RESEARCH_GUIDED_DEFAULT_MAX_UNIQUE_FETCHES,
        "pending_system_note": "",
    }


def build_entry_prompt(state: dict[str, Any]) -> str:
    goals = state.get("goals") or []
    lines = [
        "This science turn is using the research-guided loop.",
        "Stay goals-first. Do not lock in a thesis early.",
        "Use evidence tools to resolve the goals below, and preserve uncertainty when evidence is weak or conflicting.",
    ]
    if goals:
        lines.append("Primary goals:")
        for goal in goals[:RESEARCH_GUIDED_MAX_GOALS]:
            lines.append(f"- {goal.get('question')}")
    lines.append("Do not present a final conclusion until the evidence-check phase is complete.")
    return "\n".join(lines).strip()


def _query_alignment(goal: dict[str, Any], query: str) -> str:
    normalized_query = _normalize_text(query).lower()
    tokens = set(_tokenize_terms(normalized_query))
    goal_terms = set(goal.get("goal_terms") or [])
    disconfirmation_terms = set(goal.get("disconfirmation_terms") or [])
    if tokens & disconfirmation_terms or any(
        cue in normalized_query for cue in DISCONFIRMATION_QUERY_CUES
    ):
        return "disconfirming"
    if tokens & goal_terms:
        return "target_aligned"
    return "other"


def _mark_probe_observed(goal: dict[str, Any], probe_kind: str) -> None:
    observed = ((goal.get("probe_budget") or {}).get("observed") or {})
    if probe_kind in observed:
        observed[probe_kind] = int(observed.get(probe_kind) or 0) + 1


def _probe_budget_complete(goal: dict[str, Any]) -> bool:
    budget = goal.get("probe_budget") or {}
    required = budget.get("required") or {}
    observed = budget.get("observed") or {}
    for key, is_required in required.items():
        if is_required and int(observed.get(key) or 0) <= 0:
            return False
    return True


def _parse_title_and_url_identifiers(title: str, url: str) -> dict[str, str]:
    combined = " ".join(filter(None, [_normalize_text(title), _normalize_text(url)]))
    doi_match = DOI_RE.search(combined)
    pmid_match = PMID_RE.search(combined)
    pmcid_match = PMCID_RE.search(combined)
    arxiv_match = ARXIV_RE.search(combined)
    nct_match = NCT_RE.search(combined)
    return {
        "doi": doi_match.group(1).lower() if doi_match else "",
        "pmid": next((group for group in (pmid_match.groups() if pmid_match else ()) if group), ""),
        "pmcid": pmcid_match.group(1).upper() if pmcid_match else "",
        "arxiv": arxiv_match.group(1).lower() if arxiv_match else "",
        "nct": nct_match.group(1).upper() if nct_match else "",
    }


def derive_evidence_family_id(*, url: str, title: str = "") -> str:
    identifiers = _parse_title_and_url_identifiers(title, url)
    if identifiers["doi"]:
        return f"doi:{identifiers['doi']}"
    if identifiers["pmid"]:
        return f"pmid:{identifiers['pmid']}"
    if identifiers["pmcid"]:
        return f"pmcid:{identifiers['pmcid']}"
    if identifiers["arxiv"]:
        return f"arxiv:{identifiers['arxiv']}"
    if identifiers["nct"]:
        return f"nct:{identifiers['nct']}"

    canonical = canonicalize_url(url)
    if "/articles/pmc" in canonical.lower():
        return canonical.lower()
    if canonical:
        return f"url:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}"
    return f"unresolved:{hashlib.sha256(title.encode('utf-8')).hexdigest()[:16]}"


def derive_evidence_role(*, domain: str, title: str, url: str) -> str:
    normalized_title = _normalize_text(title).lower()
    normalized_domain = _normalize_text(domain).lower()
    normalized_url = canonicalize_url(url).lower()
    if any(term in normalized_title for term in {"meta-analysis", "meta analysis", "systematic review"}):
        return "systematic_synthesis"
    if any(term in normalized_title for term in {"guideline", "consensus", "position statement", "recommendation", "standard"}):
        return "guideline_or_standard"
    if any(term in normalized_domain for term in {"pubmed", "pmc"}):
        return "mirror_or_index"
    if any(term in normalized_domain for term in {"news", "medium.com", "substack", "reddit", "stackexchange"}):
        return "secondary_summary"
    if any(term in normalized_title for term in {"review", "overview"}) and "systematic" not in normalized_title:
        return "secondary_summary"
    if normalized_url:
        return "primary_study"
    return "secondary_summary"


def derive_evidence_class(*, role: str, title: str, url: str) -> str:
    normalized_title = _normalize_text(title).lower()
    if role == "guideline_or_standard":
        return "guideline"
    if role == "systematic_synthesis":
        return "systematic_synthesis"
    if role == "secondary_summary":
        return "secondary_summary"
    if any(term in normalized_title for term in {"observational", "cohort", "registry", "case-control", "cross-sectional"}):
        return "observational"
    if any(term in normalized_title for term in {"in vitro", "cell", "mouse", "rat", "animal model", "mechanism"}):
        return "mechanistic"
    if any(term in normalized_title for term in {"simulation", "modeling"}):
        return "simulation"
    return "direct_empirical"


def _directness_for_class(evidence_class: str) -> str:
    if evidence_class in {"mechanistic", "simulation", "secondary_summary"}:
        return "indirect"
    return "direct"


def _value_bucket_for_class(
    evidence_class: str,
    role: str,
    blocked: bool,
    *,
    content_depth: str = "unknown",
) -> str:
    if blocked:
        return "low"
    if role in {"mirror_or_index", "secondary_summary"}:
        return "low"
    if evidence_class in {"systematic_synthesis", "guideline", "canonical_reference"}:
        if content_depth == "full_text":
            return "high"
        if content_depth == "snippet":
            return "medium"
        return "low"
    if evidence_class in {"direct_empirical", "observational"}:
        if content_depth in {"full_text", "snippet", "unknown"}:
            return "medium"
        return "low"
    return "low"


def _context_fit(goal: dict[str, Any], text: str) -> str:
    goal_terms = set(goal.get("goal_terms") or [])
    tokens = set(_tokenize_terms(text))
    overlap = len(goal_terms & tokens)
    if overlap >= 3:
        return "strong"
    if overlap >= 1:
        return "partial"
    return "weak"


def _append_evidence_record(
    state: dict[str, Any],
    *,
    goal_ids: list[str],
    stance: str,
    source_role: str,
    title: str,
    url: str,
    domain: str,
    text: str,
    content_depth: str = "unknown",
    blocked: bool = False,
) -> str:
    evidence_role = derive_evidence_role(domain=domain, title=title, url=url)
    evidence_class = derive_evidence_class(role=evidence_role, title=title, url=url)
    family_id = derive_evidence_family_id(url=url, title=title)
    evidence_id = f"ev_{uuid4().hex[:12]}"
    record = {
        "evidence_id": evidence_id,
        "goal_ids": list(goal_ids),
        "source_role": source_role or evidence_role,
        "source_ref": {"title": title, "url": url, "domain": domain},
        "canonical_url": canonicalize_url(url),
        "evidence_family_id": family_id,
        "evidence_class": evidence_class,
        "stance": stance,
        "directness": _directness_for_class(evidence_class),
        "method_strength": "high" if evidence_class in {"systematic_synthesis", "guideline"} else "medium" if evidence_class in {"direct_empirical", "observational"} else "low",
        "context_fit": "weak",
        "content_depth": content_depth if content_depth in {"full_text", "snippet", "summary", "unknown"} else "unknown",
        "value_bucket": "low",
        "limitations": [],
        "blocked": blocked,
        "text_preview": _normalize_text(text)[:220],
    }
    record["value_bucket"] = _value_bucket_for_class(
        record.get("evidence_class") or "",
        record.get("source_role") or evidence_role,
        blocked,
        content_depth=str(record.get("content_depth") or "unknown"),
    )
    if blocked:
        record["limitations"].append("access_blocked_or_empty")
    for goal in state.get("goals") or []:
        if goal.get("goal_id") in goal_ids:
            record["context_fit"] = _context_fit(goal, " ".join(filter(None, [title, text])))
            break
    state.setdefault("evidence_ledger", []).append(record)
    return evidence_id


def _record_disconfirmation_attempt(goal: dict[str, Any], query: str, *, found: bool, meaningful: bool) -> None:
    attempts = goal.setdefault("disconfirmation_attempts", [])
    attempts.append(
        {
            "query": _normalize_text(query),
            "found": bool(found),
            "meaningful": bool(meaningful),
        }
    )
    if found:
        goal["disconfirmation_outcome"] = "found"


def _basis_summary(goal: dict[str, Any], supporting: list[dict[str, Any]], opposing: list[dict[str, Any]]) -> str:
    evidence_kinds = []
    for record in supporting[:2]:
        kind = str(record.get("evidence_class") or "").replace("_", " ")
        if kind and kind not in evidence_kinds:
            evidence_kinds.append(kind)
    if not evidence_kinds and opposing:
        for record in opposing[:2]:
            kind = str(record.get("evidence_class") or "").replace("_", " ")
            if kind and kind not in evidence_kinds:
                evidence_kinds.append(kind)

    kind_text = ", ".join(evidence_kinds[:2]) or "retrieved evidence"
    question = _normalize_text(goal.get("question") or "")
    resolution_type = str(goal.get("resolution_type") or "").replace("_", " ")
    coverage_requirement = str(goal.get("coverage_requirement") or "normal")
    snippet_only = supporting and all(
        str(record.get("content_depth") or "") in {"snippet", "summary"}
        for record in supporting
    )
    if supporting and opposing:
        return f"mixed {kind_text} for {resolution_type} question '{question}', limited to conflicting retrieved source families"
    if supporting:
        if coverage_requirement == "strict" and snippet_only:
            return f"supported only by {kind_text} from snippet/summary-level evidence for '{question}', without enough independent corroboration"
        return f"supported by {kind_text} for '{question}', limited to evidence retrieved in this turn"
    if opposing:
        return f"opposed by {kind_text} for '{question}', limited to evidence retrieved in this turn"
    return f"no qualifying evidence for '{question}' under the bounded retrieval budget"


def _limitations_for_goal(goal: dict[str, Any], supporting: list[dict[str, Any]], opposing: list[dict[str, Any]]) -> list[str]:
    limitations: list[str] = []
    if goal.get("status") == GOAL_STATUS_MIXED:
        limitations.append("conflicting medium/high-value evidence remains")
    if goal.get("disconfirmation_outcome") == "not_meaningfully_tested":
        limitations.append("disconfirmation probe was not meaningfully completed")
    if goal.get("resolution_basis") == "blocked_access":
        limitations.append("relevant source access was blocked or empty")
    if goal.get("resolution_basis") == "budget_exhausted_before_resolution":
        limitations.append("bounded search budget ended before clean resolution")
    if goal.get("coverage_requirement") == "strict":
        full_text_support = any(
            str(record.get("content_depth") or "") == "full_text"
            for record in supporting
        )
        if supporting and not full_text_support:
            limitations.append("support relied on snippet/summary evidence rather than full-text corroboration")
    if not supporting and not opposing:
        limitations.append("no qualifying evidence was found under the bounded probe budget")
    return limitations[:2]


def _derive_candidate_claim(goal: dict[str, Any], supporting: list[dict[str, Any]], opposing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    primary_label = CLAIM_LABEL_UNCERTAIN
    disconfirmation_outcome = str(goal.get("disconfirmation_outcome") or "")
    resolution_basis = str(goal.get("resolution_basis") or "")
    if goal.get("status") == GOAL_STATUS_SUPPORTED:
        if disconfirmation_outcome == "not_meaningfully_tested":
            primary_label = CLAIM_LABEL_INFERENCE
        elif resolution_basis in {
            "blocked_access",
            "budgeted_high_value_search_exhausted",
            "budget_exhausted_before_resolution",
        }:
            primary_label = CLAIM_LABEL_INFERENCE
        else:
            primary_label = CLAIM_LABEL_VERIFIED
    elif goal.get("status") in {GOAL_STATUS_MIXED, GOAL_STATUS_INSUFFICIENT}:
        primary_label = CLAIM_LABEL_INFERENCE if supporting else CLAIM_LABEL_UNCERTAIN
    elif goal.get("status") == GOAL_STATUS_NOT_SUPPORTED:
        primary_label = CLAIM_LABEL_UNCERTAIN

    claims = [
        {
            "claim_id": f"claim_{goal.get('goal_id')}",
            "goal_id": goal.get("goal_id"),
            "text": _normalize_text(goal.get("question") or ""),
            "label": primary_label,
            "basis_summary": _basis_summary(goal, supporting, opposing),
            "must_include_limitations": _limitations_for_goal(goal, supporting, opposing),
            "support_ids": [item.get("evidence_id") for item in supporting[:4]],
            "oppose_ids": [item.get("evidence_id") for item in opposing[:4]],
        }
    ]
    return claims[:RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]


def _support_and_opposition_for_goal(state: dict[str, Any], goal_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    supporting_by_family: dict[str, dict[str, Any]] = {}
    opposing_by_family: dict[str, dict[str, Any]] = {}

    value_rank = {"low": 0, "medium": 1, "high": 2}
    depth_rank = {"summary": 0, "snippet": 1, "unknown": 1, "full_text": 2}
    method_rank = {"low": 0, "medium": 1, "high": 2}
    directness_rank = {"indirect": 0, "direct": 1}

    def _record_rank(record: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            value_rank.get(str(record.get("value_bucket") or ""), -1),
            depth_rank.get(str(record.get("content_depth") or ""), -1),
            method_rank.get(str(record.get("method_strength") or ""), -1),
            directness_rank.get(str(record.get("directness") or ""), -1),
        )

    for record in state.get("evidence_ledger") or []:
        if goal_id not in (record.get("goal_ids") or []):
            continue
        if record.get("context_fit") == "weak":
            continue
        if record.get("blocked"):
            continue
        stance = record.get("stance")
        family_id = str(record.get("evidence_family_id") or "")
        if stance == "supports":
            key = family_id or f"unresolved_support:{record.get('evidence_id')}"
            existing = supporting_by_family.get(key)
            if existing is None or _record_rank(record) > _record_rank(existing):
                supporting_by_family[key] = record
        elif stance == "opposes":
            key = family_id or f"unresolved_oppose:{record.get('evidence_id')}"
            existing = opposing_by_family.get(key)
            if existing is None or _record_rank(record) > _record_rank(existing):
                opposing_by_family[key] = record
    supporting = list(supporting_by_family.values())
    opposing = list(opposing_by_family.values())
    return supporting, opposing


def _goal_support_metrics(goal: dict[str, Any], supporting: list[dict[str, Any]]) -> dict[str, Any]:
    qualifying = list(supporting)
    qualifying_families = {
        str(record.get("evidence_family_id") or "").strip()
        for record in qualifying
        if str(record.get("evidence_family_id") or "").strip()
        and not str(record.get("evidence_family_id") or "").startswith("unresolved:")
    }
    high_or_medium = [
        record
        for record in qualifying
        if str(record.get("value_bucket") or "") in {"high", "medium"}
    ]
    high_or_medium_families = {
        str(record.get("evidence_family_id") or "").strip()
        for record in high_or_medium
        if str(record.get("evidence_family_id") or "").strip()
        and not str(record.get("evidence_family_id") or "").startswith("unresolved:")
    }
    full_text_families = {
        str(record.get("evidence_family_id") or "").strip()
        for record in high_or_medium
        if str(record.get("content_depth") or "") == "full_text"
        and str(record.get("evidence_family_id") or "").strip()
        and not str(record.get("evidence_family_id") or "").startswith("unresolved:")
    }
    snippet_only_families = set()
    for family_id in high_or_medium_families:
        family_records = [
            record
            for record in high_or_medium
            if str(record.get("evidence_family_id") or "").strip() == family_id
        ]
        if family_records and all(
            str(record.get("content_depth") or "") in {"snippet", "summary"}
            for record in family_records
        ):
            snippet_only_families.add(family_id)
    systematic_or_guideline_families = {
        str(record.get("evidence_family_id") or "").strip()
        for record in high_or_medium
        if str(record.get("evidence_class") or "")
        in {"systematic_synthesis", "guideline", "canonical_reference"}
        and str(record.get("evidence_family_id") or "").strip()
        and not str(record.get("evidence_family_id") or "").startswith("unresolved:")
    }
    return {
        "qualifying_count": len(qualifying),
        "independent_family_count": len(qualifying_families),
        "high_or_medium_family_count": len(high_or_medium_families),
        "full_text_family_count": len(full_text_families),
        "snippet_only_family_count": len(snippet_only_families),
        "systematic_or_guideline_family_count": len(systematic_or_guideline_families),
        "has_only_snippet_or_summary_support": bool(high_or_medium_families) and high_or_medium_families == snippet_only_families,
    }


def _goal_contract_satisfied(goal: dict[str, Any], supporting: list[dict[str, Any]]) -> bool:
    contract = goal.get("acceptance_contract") or {}
    allowed_classes = set(contract.get("allowed_evidence_classes") or [])
    forbidden_as_sole = set(contract.get("forbidden_as_sole_support_classes") or [])
    direct_required = _normalize_bool(contract.get("direct_support_required_for_verified"))
    qualifying = [
        record
        for record in supporting
        if str(record.get("evidence_class") or "") in allowed_classes
    ]
    if not qualifying:
        return False
    if len(qualifying) == 1 and str(qualifying[0].get("evidence_class") or "") in forbidden_as_sole:
        return False
    if direct_required and not any(record.get("directness") == "direct" for record in qualifying):
        return False
    high_or_medium = [
        record
        for record in qualifying
        if str(record.get("value_bucket") or "") in {"high", "medium"}
    ]
    if not high_or_medium:
        return False
    metrics = _goal_support_metrics(goal, high_or_medium)

    resolution_type = str(goal.get("resolution_type") or "")
    if resolution_type == "mechanism":
        return True

    if str(goal.get("coverage_requirement") or "normal") == "strict":
        return (
            metrics["high_or_medium_family_count"] >= 2
            and metrics["full_text_family_count"] >= 1
            and not metrics["has_only_snippet_or_summary_support"]
        )

    if any(
        str(record.get("evidence_class") or "")
        in {"systematic_synthesis", "guideline", "canonical_reference"}
        and str(record.get("value_bucket") or "") == "high"
        for record in high_or_medium
    ):
        return True

    return metrics["high_or_medium_family_count"] >= 2


def _resolve_goal(goal: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    updated = copy.deepcopy(goal)
    supporting, opposing = _support_and_opposition_for_goal(state, str(goal.get("goal_id") or ""))
    previous_status = str(goal.get("status") or GOAL_STATUS_OPEN)
    previous_basis = str(goal.get("resolution_basis") or "")

    if not updated.get("disconfirmation_outcome"):
        if any(attempt.get("found") for attempt in updated.get("disconfirmation_attempts") or []):
            updated["disconfirmation_outcome"] = "found"
        elif _probe_budget_complete(updated):
            has_meaningful = any(
                attempt.get("meaningful") for attempt in updated.get("disconfirmation_attempts") or []
            )
            updated["disconfirmation_outcome"] = (
                "not_found_under_budgeted_probe" if has_meaningful else "not_meaningfully_tested"
            )

    if supporting and opposing:
        updated["status"] = GOAL_STATUS_MIXED
        updated["resolution_basis"] = "contradictory_high_value_evidence"
    elif opposing and not supporting:
        updated["status"] = GOAL_STATUS_NOT_SUPPORTED
        updated["resolution_basis"] = "contradictory_high_value_evidence"
    elif _goal_contract_satisfied(updated, supporting):
        updated["status"] = GOAL_STATUS_SUPPORTED
        updated["resolution_basis"] = "contract_satisfied"
    elif state.get("blocked_access_count"):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "blocked_access"
    elif _probe_budget_complete(updated):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budgeted_high_value_search_exhausted"
    elif (
        len(state.get("unique_query_fingerprints") or []) >= int(state.get("max_unique_queries") or 0)
        and not supporting
    ):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budget_exhausted_before_resolution"
    else:
        updated["status"] = GOAL_STATUS_OPEN
        updated["resolution_basis"] = ""

    changed = updated["status"] != previous_status or updated["resolution_basis"] != previous_basis
    return updated, changed


def _update_working_proposition(proposition: dict[str, Any], goal: dict[str, Any], supporting: list[dict[str, Any]], opposing: list[dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    updated = copy.deepcopy(proposition)
    previous_state = str(proposition.get("state") or "open")
    if goal.get("status") == GOAL_STATUS_SUPPORTED:
        updated["state"] = "leaning_support"
    elif goal.get("status") == GOAL_STATUS_MIXED:
        updated["state"] = "leaning_mixed"
    elif goal.get("status") == GOAL_STATUS_NOT_SUPPORTED:
        updated["state"] = "leaning_not_supported"
    else:
        updated["state"] = "open"
    updated["support_ids"] = [item.get("evidence_id") for item in supporting[:4]]
    updated["oppose_ids"] = [item.get("evidence_id") for item in opposing[:4]]
    updated["contradiction_pressure"] = bool(supporting and opposing)
    return updated, updated["state"] != previous_state


def _set_pending_note(state: dict[str, Any], note: str) -> None:
    state["pending_system_note"] = _normalize_text(note)


def _refresh_resolutions(state: dict[str, Any]) -> dict[str, Any]:
    updated_goals: list[dict[str, Any]] = []
    updated_props: list[dict[str, Any]] = []
    note_lines: list[str] = []

    for goal in state.get("goals") or []:
        next_goal, changed = _resolve_goal(goal, state)
        supporting, opposing = _support_and_opposition_for_goal(state, str(goal.get("goal_id") or ""))
        proposition = next(
            (item for item in state.get("working_propositions") or [] if item.get("goal_id") == goal.get("goal_id")),
            None,
        ) or {
            "proposition_id": f"prop_{goal.get('goal_id')}",
            "goal_id": goal.get("goal_id"),
            "text": goal.get("question"),
            "state": "open",
            "support_ids": [],
            "oppose_ids": [],
            "contradiction_pressure": False,
        }
        next_prop, prop_changed = _update_working_proposition(proposition, next_goal, supporting, opposing)
        if changed or prop_changed:
            if next_prop["state"] == "leaning_not_supported":
                note_lines.append(
                    f"Current evidence leans against goal '{next_goal.get('question')}'. Focus on disconfirmation or pivot."
                )
            elif next_prop["state"] == "leaning_mixed":
                note_lines.append(
                    f"Current evidence is mixed for goal '{next_goal.get('question')}'. Look for stronger differentiating evidence."
                )
            elif supporting and str(next_goal.get("coverage_requirement") or "normal") == "strict":
                metrics = _goal_support_metrics(next_goal, supporting)
                if (
                    metrics["high_or_medium_family_count"] < 2
                    or metrics["full_text_family_count"] < 1
                ):
                    note_lines.append(
                        f"Current evidence for goal '{next_goal.get('question')}' is too narrow. Find additional independent high-value sources before concluding."
                    )
        updated_goals.append(next_goal)
        updated_props.append(next_prop)

    state["goals"] = updated_goals
    state["working_propositions"] = updated_props
    all_terminal = all(
        goal.get("status") in TERMINAL_GOAL_STATUSES
        and goal.get("resolution_basis")
        and goal.get("disconfirmation_outcome")
        for goal in updated_goals
    )
    state["ready_to_answer"] = bool(all_terminal)
    if note_lines:
        _set_pending_note(state, " ".join(note_lines[:2]))
    if all_terminal:
        state["phase"] = "evidence_check"
        state["stop_reason"] = state.get("stop_reason") or "all_primary_goals_resolved"
    else:
        state["phase"] = "research"
    return state


def register_tool_event(
    state: dict[str, Any],
    *,
    tool_name: str,
    tool_params: dict[str, Any],
    tool_result: Any,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    normalized_tool_name = str(tool_name or "").strip()
    parsed = tool_result
    if isinstance(tool_result, str):
        try:
            parsed = json.loads(tool_result)
        except Exception:
            parsed = tool_result

    query_value = _normalize_text(
        tool_params.get("query")
        or tool_params.get("q")
        or (parsed.get("query") if isinstance(parsed, dict) else "")
    )
    query_fp = query_fingerprint(query_value)
    if query_fp and normalized_tool_name in {"search_web", "web_research_strong", "query_web_evidence"}:
        if query_fp in (updated.get("unique_query_fingerprints") or []):
            updated["duplicate_query_count"] = int(updated.get("duplicate_query_count") or 0) + 1
        else:
            updated.setdefault("unique_query_fingerprints", []).append(query_fp)

    if normalized_tool_name == "search_web":
        for goal in updated.get("goals") or []:
            alignment = _query_alignment(goal, query_value)
            if alignment == "target_aligned":
                _mark_probe_observed(goal, "target_aligned")
            elif alignment == "disconfirming":
                _mark_probe_observed(goal, "disconfirming")
                _record_disconfirmation_attempt(goal, query_value, found=False, meaningful=True)
        if updated.get("duplicate_query_count"):
            updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1

    elif normalized_tool_name == "web_research_strong":
        for goal in updated.get("goals") or []:
            _mark_probe_observed(goal, "strong_source")
            alignment = _query_alignment(goal, query_value)
            if alignment == "target_aligned":
                _mark_probe_observed(goal, "target_aligned")
            elif alignment == "disconfirming":
                _mark_probe_observed(goal, "disconfirming")
                _record_disconfirmation_attempt(goal, query_value, found=False, meaningful=True)

        if isinstance(parsed, dict):
            items = (parsed.get("citation_items") or []) + (parsed.get("evidence_items") or [])
            for item in items[:6]:
                if not isinstance(item, dict):
                    continue
                title = _normalize_text(item.get("title") or "")
                url = _normalize_text(item.get("link") or "")
                domain = _normalize_text(item.get("domain") or _domain_from_url(url))
                for goal in updated.get("goals") or []:
                    alignment = _query_alignment(goal, query_value or title)
                    if alignment == "target_aligned":
                        evidence_id = _append_evidence_record(
                            updated,
                            goal_ids=[goal.get("goal_id")],
                            stance="supports",
                            source_role="secondary_summary",
                            title=title,
                            url=url,
                            domain=domain,
                            text=title,
                            content_depth="summary",
                        )
                        goal.setdefault("support_ids", []).append(evidence_id)
                    elif alignment == "disconfirming":
                        evidence_id = _append_evidence_record(
                            updated,
                            goal_ids=[goal.get("goal_id")],
                            stance="opposes",
                            source_role="secondary_summary",
                            title=title,
                            url=url,
                            domain=domain,
                            text=title,
                            content_depth="summary",
                        )
                        goal.setdefault("oppose_ids", []).append(evidence_id)

    elif normalized_tool_name == "fetch_url":
        if isinstance(parsed, dict):
            url = _normalize_text(parsed.get("url") or tool_params.get("url") or "")
            canonical_url = canonicalize_url(url)
            if canonical_url:
                if canonical_url in (updated.get("unique_fetch_urls") or []):
                    updated["duplicate_fetch_count"] = int(updated.get("duplicate_fetch_count") or 0) + 1
                else:
                    updated.setdefault("unique_fetch_urls", []).append(canonical_url)
            content_chars = int(parsed.get("content_chars") or 0)
            blocked = bool(parsed.get("error")) or parsed.get("status") in {
                "access_denied",
                "unsupported_binary",
                "document_extract_failed",
            } or (parsed.get("mode") == "store" and content_chars <= 0)
            if blocked:
                updated["blocked_access_count"] = int(updated.get("blocked_access_count") or 0) + 1
                updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1
            if parsed.get("artifact_id"):
                updated.setdefault("stored_artifacts", []).append(
                    {
                        "artifact_id": parsed.get("artifact_id"),
                        "url": url,
                        "domain": parsed.get("domain") or _domain_from_url(url),
                        "title": parsed.get("title") or "",
                        "blocked": blocked,
                        "mode": parsed.get("mode") or tool_params.get("mode") or "",
                        "content_chars": content_chars,
                    }
                )
            if not blocked:
                title = _normalize_text(parsed.get("title") or "")
                combined_text = " ".join(filter(None, [title, url]))
                for goal in updated.get("goals") or []:
                    if _context_fit(goal, combined_text) != "weak":
                        _mark_probe_observed(goal, "strong_source")

    elif normalized_tool_name == "query_web_evidence" and isinstance(parsed, dict):
        snippets = parsed.get("snippets") or []
        if not snippets and str(parsed.get("status") or "") == "not_found":
            updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1

        searched_domains = parsed.get("searched_domains") or []
        if int(parsed.get("searched_artifact_count") or 0) <= 1 or len(searched_domains) <= 1:
            for goal in updated.get("goals") or []:
                if _query_alignment(goal, query_value) == "target_aligned":
                    goal["probe_budget"]["required"]["broader_fallback"] = True
        else:
            for goal in updated.get("goals") or []:
                if _query_alignment(goal, query_value) == "target_aligned":
                    _mark_probe_observed(goal, "broader_fallback")

        for goal in updated.get("goals") or []:
            alignment = _query_alignment(goal, query_value)
            if alignment == "target_aligned":
                _mark_probe_observed(goal, "target_aligned")
            elif alignment == "disconfirming":
                _mark_probe_observed(goal, "disconfirming")
                _record_disconfirmation_attempt(goal, query_value, found=bool(snippets), meaningful=True)

        for snippet in snippets[:8]:
            if not isinstance(snippet, dict):
                continue
            artifact_id = _normalize_text(snippet.get("artifact_id") or "")
            artifact = next(
                (
                    item
                    for item in updated.get("stored_artifacts") or []
                    if _normalize_text(item.get("artifact_id") or "") == artifact_id
                ),
                {},
            )
            title = _normalize_text(artifact.get("title") or "")
            url = _normalize_text(artifact.get("url") or "")
            domain = _normalize_text(snippet.get("domain") or artifact.get("domain") or _domain_from_url(url))
            text = _normalize_text(snippet.get("text") or "")
            content_depth = (
                "full_text"
                if str(artifact.get("mode") or "").strip().lower() == "content"
                and int(artifact.get("content_chars") or 0) > 0
                else "snippet"
            )
            for goal in updated.get("goals") or []:
                alignment = _query_alignment(goal, query_value or text)
                if alignment == "target_aligned":
                    evidence_id = _append_evidence_record(
                        updated,
                        goal_ids=[goal.get("goal_id")],
                        stance="supports",
                        source_role="primary_study",
                        title=title,
                        url=url,
                        domain=domain,
                        text=text,
                        content_depth=content_depth,
                    )
                    goal.setdefault("support_ids", []).append(evidence_id)
                elif alignment == "disconfirming":
                    evidence_id = _append_evidence_record(
                        updated,
                        goal_ids=[goal.get("goal_id")],
                        stance="opposes",
                        source_role="primary_study",
                        title=title,
                        url=url,
                        domain=domain,
                        text=text,
                        content_depth=content_depth,
                    )
                    goal.setdefault("oppose_ids", []).append(evidence_id)
                elif _context_fit(goal, text) == "weak":
                    goal.setdefault("orthogonal_ids", [])

    updated = _refresh_resolutions(updated)
    if updated.get("ready_to_answer"):
        updated = finalize_state_for_answer(updated)
        _set_pending_note(
            updated,
            build_ready_to_answer_instruction(updated),
        )
    return updated


def _coerce_disconfirmation_outcomes_for_completed_turn(state: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    for goal in updated.get("goals") or []:
        if goal.get("status") not in TERMINAL_GOAL_STATUSES:
            continue
        if not goal.get("resolution_basis"):
            continue
        if goal.get("disconfirmation_outcome") in TERMINAL_DISCONFIRMATION_OUTCOMES:
            continue

        attempts = goal.get("disconfirmation_attempts") or []
        if any(attempt.get("found") for attempt in attempts):
            goal["disconfirmation_outcome"] = "found"
        elif any(attempt.get("meaningful") for attempt in attempts):
            goal["disconfirmation_outcome"] = "not_found_under_budgeted_probe"
        else:
            goal["disconfirmation_outcome"] = "not_meaningfully_tested"
    return updated


def finalize_state_for_completed_turn(
    state: dict[str, Any],
    *,
    visible_answer_present: bool = False,
) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    if updated.get("ready_to_answer"):
        if updated.get("candidate_claims"):
            updated["phase"] = "final_response"
            updated["stop_reason"] = updated.get("stop_reason") or "all_primary_goals_resolved"
            return updated
        return finalize_state_for_answer(updated)

    if not visible_answer_present:
        return updated

    updated = _coerce_disconfirmation_outcomes_for_completed_turn(updated)
    updated = _refresh_resolutions(updated)
    if updated.get("ready_to_answer"):
        return finalize_state_for_answer(updated)
    return updated


def build_ready_to_answer_instruction(state: dict[str, Any]) -> str:
    goals = state.get("goals") or []
    claims = state.get("candidate_claims") or []
    lines = [
        "Research phase is complete for this turn.",
        "Use the resolved goals and candidate claims below to write the final answer.",
        "Do not change the labels. Preserve the substance of the basis summaries and listed limitations.",
    ]
    if goals:
        lines.append("Resolved goals:")
        for goal in goals[:RESEARCH_GUIDED_MAX_GOALS]:
            lines.append(
                f"- {goal.get('question')} -> {goal.get('status')} ({goal.get('resolution_basis')})"
            )
    if claims:
        lines.append("Candidate claims:")
        for claim in claims[: RESEARCH_GUIDED_MAX_GOALS * RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]:
            lines.append(
                f"- {claim.get('text')} :: {claim.get('label')} :: {claim.get('basis_summary')}"
            )
    lines.append(
        f"The final user-visible answer will include a machine-owned '{RESEARCH_STATUS_MARKER.replace('### ', '')}' appendix."
    )
    return "\n".join(lines).strip()


def consume_pending_system_note(state: Optional[dict[str, Any]]) -> str | None:
    if not isinstance(state, dict):
        return None
    note = _normalize_text(state.get("pending_system_note") or "")
    if note:
        state["pending_system_note"] = ""
        return note
    return None


def finalize_state_for_answer(state: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(state)
    candidate_claims: list[dict[str, Any]] = []
    for goal in updated.get("goals") or []:
        supporting, opposing = _support_and_opposition_for_goal(updated, str(goal.get("goal_id") or ""))
        candidate_claims.extend(_derive_candidate_claim(goal, supporting, opposing))
    updated["candidate_claims"] = candidate_claims
    updated["ready_to_answer"] = bool(candidate_claims) or bool(updated.get("goals"))
    updated["phase"] = "final_response" if updated.get("ready_to_answer") else updated.get("phase")
    updated["stop_reason"] = updated.get("stop_reason") or (
        "all_primary_goals_resolved" if updated.get("ready_to_answer") else ""
    )
    return updated


def build_research_status_block(state: Optional[dict[str, Any]]) -> str:
    if not isinstance(state, dict):
        return ""
    claims = state.get("candidate_claims") or []
    if not claims:
        return ""
    lines = [RESEARCH_STATUS_MARKER]
    for claim in claims[: RESEARCH_GUIDED_MAX_GOALS * RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]:
        lines.append("")
        lines.append(f"Claim: {claim.get('text')}")
        lines.append(f"Label: {claim.get('label')}")
        lines.append(f"Basis: {claim.get('basis_summary')}")
        limitations = claim.get("must_include_limitations") or []
        if limitations:
            lines.append(f"Limitation: {limitations[0]}")
    return "\n".join(lines).strip()


def build_research_snapshot(state: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {"present": False}
    goals = state.get("goals") or []
    propositions = state.get("working_propositions") or []
    claims = state.get("candidate_claims") or []
    return {
        "present": True,
        "phase": state.get("phase"),
        "domain_profile": state.get("domain_profile"),
        "goal_count": len(goals),
        "goal_statuses": [
            {
                "goal_id": goal.get("goal_id"),
                "status": goal.get("status"),
                "resolution_basis": goal.get("resolution_basis"),
                "disconfirmation_outcome": goal.get("disconfirmation_outcome"),
            }
            for goal in goals[:RESEARCH_GUIDED_MAX_GOALS]
        ],
        "proposition_state_counts": {
            key: sum(1 for proposition in propositions if proposition.get("state") == key)
            for key in WORKING_PROPOSITION_STATES
        },
        "candidate_claim_labels": [claim.get("label") for claim in claims[:8]],
        "duplicate_query_count": int(state.get("duplicate_query_count") or 0),
        "duplicate_fetch_count": int(state.get("duplicate_fetch_count") or 0),
        "negative_signal_count": int(state.get("negative_signal_count") or 0),
        "blocked_access_count": int(state.get("blocked_access_count") or 0),
        "stop_reason": state.get("stop_reason"),
        "ready_to_answer": bool(state.get("ready_to_answer")),
    }


def build_runtime_summary(state: Optional[dict[str, Any]]) -> dict[str, Any]:
    snapshot = build_research_snapshot(state)
    if not snapshot.get("present"):
        return snapshot
    return {
        "phase": snapshot.get("phase"),
        "goal_count": snapshot.get("goal_count"),
        "goal_statuses": snapshot.get("goal_statuses"),
        "candidate_claim_labels": snapshot.get("candidate_claim_labels"),
        "duplicate_query_count": snapshot.get("duplicate_query_count"),
        "duplicate_fetch_count": snapshot.get("duplicate_fetch_count"),
        "negative_signal_count": snapshot.get("negative_signal_count"),
        "blocked_access_count": snapshot.get("blocked_access_count"),
        "stop_reason": snapshot.get("stop_reason"),
        "ready_to_answer": snapshot.get("ready_to_answer"),
    }
