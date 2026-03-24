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
RESEARCH_INCOMPLETE_MARKER = "### Research Incomplete"
RESEARCH_GUIDED_QUERY_OVERLAP_THRESHOLD = 0.6
RESEARCH_GUIDED_MAX_QUERY_REWRITES_PER_SCOPE = 1
RESEARCH_GUIDED_MAX_QUERY_REWRITES_PER_GOAL = 2
RESEARCH_GUIDED_MAX_VERIFIER_REASONS = 3
RESEARCH_GUIDED_MAX_VERIFIER_INSTRUCTIONS = 2

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
PMID_RE = re.compile(
    r"/pubmed/(\d+)|[?&]term=(\d+)\[pmid\]|\bPMID:\s*(\d+)\b",
    re.IGNORECASE,
)
PMCID_RE = re.compile(r"/articles/(PMC\d+)|\b(PMC\d+)\b", re.IGNORECASE)
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([A-Za-z0-9.\-]+)", re.IGNORECASE)
NCT_RE = re.compile(r"\b(NCT\d{8})\b", re.IGNORECASE)
DOI_SUFFIX_TRIMS = (
    "/full",
    "/abstract",
    "/pdf",
    "/epdf",
    "/xml",
)
FAMILY_PRECEDENCE = {
    "doi:": 0,
    "pmcid:": 1,
    "pmid:": 2,
    "arxiv:": 3,
    "nct:": 4,
    "url:": 5,
    "unresolved:": 6,
}

PAGE_QUALITY_USABLE = "usable_article"
PAGE_QUALITY_PARTIAL = "partial_article"
PAGE_QUALITY_THIN = "thin_shell"
PAGE_QUALITY_CHALLENGE = "challenge_or_antibot"
PAGE_QUALITY_GENERIC = "generic_index"

PAGE_QUALITY_REJECTED = {
    PAGE_QUALITY_THIN,
    PAGE_QUALITY_CHALLENGE,
    PAGE_QUALITY_GENERIC,
}

PRIMARY_SOURCE_ROLES = {"primary_study"}
SECONDARY_SOURCE_ROLES = {
    "systematic_synthesis",
    "secondary_summary",
    "guideline_or_standard",
    "mirror_or_index",
}

HOSTNAME_TITLE_PREFIXES = {
    "www.",
}

PAGE_CHALLENGE_CUES = {
    "just a moment",
    "verify you are human",
    "enable javascript",
    "checking your browser",
    "security check",
    "cloudflare",
    "vercel security checkpoint",
    "access denied",
    "captcha",
}

PAGE_GENERIC_CUES = {
    "home",
    "homepage",
    "index",
    "article",
    "paper",
}

NAMED_PRIMARY_ENTITY_PATTERNS = (
    re.compile(
        r"\b([A-Z][A-Za-z0-9-]+(?: [A-Z][A-Za-z0-9-]+){0,5} "
        r"(?:trial|study|theorem|dataset|benchmark|experiment))\b"
    ),
    re.compile(r"\b([A-Z][A-Za-z]+ et al\. \d{4})\b"),
    re.compile(r"\b(NCT\d{8}|PMC\d+)\b", re.IGNORECASE),
    re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)\b", re.IGNORECASE),
)


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


def title_looks_like_hostname(title: Any, url: Any = "") -> bool:
    normalized_title = _normalize_text(title).strip().lower()
    if not normalized_title:
        return True
    normalized_title = normalized_title.rstrip("/")
    try:
        netloc = (urlsplit(_normalize_text(url)).netloc or "").strip().lower()
    except Exception:
        netloc = ""
    for prefix in HOSTNAME_TITLE_PREFIXES:
        if netloc.startswith(prefix):
            netloc = netloc[len(prefix) :]
    title_cmp = normalized_title
    if title_cmp.startswith("www."):
        title_cmp = title_cmp[4:]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if title_cmp == netloc:
        return True
    return bool(re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", title_cmp))


def _extract_title_from_content(content: Any) -> str:
    text = str(content or "").strip()
    if not text:
        return ""
    for raw_line in text.splitlines()[:12]:
        line = raw_line.strip().lstrip("#").strip()
        if len(line) < 12 or len(line) > 180:
            continue
        lowered = line.lower()
        if any(cue in lowered for cue in PAGE_CHALLENGE_CUES):
            continue
        if line.count(" ") < 2:
            continue
        return _normalize_text(line)
    return ""


def resolve_stored_title(
    *,
    explicit_title: Any = "",
    url: Any = "",
    content: Any = "",
    metadata_title_candidates: Optional[list[Any]] = None,
    search_result_title: Any = "",
) -> tuple[str, str]:
    explicit = _normalize_text(explicit_title)
    if explicit and not title_looks_like_hostname(explicit, url):
        return explicit, "explicit_title"

    for candidate in metadata_title_candidates or []:
        normalized = _normalize_text(candidate)
        if normalized and not title_looks_like_hostname(normalized, url):
            return normalized, "extracted_metadata"

    extracted = _extract_title_from_content(content)
    if extracted and not title_looks_like_hostname(extracted, url):
        return extracted, "content_heading"

    search_title = _normalize_text(search_result_title)
    if search_title and not title_looks_like_hostname(search_title, url):
        return search_title, "search_result_registry"

    fallback = explicit or _normalize_text(urlsplit(_normalize_text(url)).netloc or url)
    return fallback, "url_fallback"


def classify_page_quality(
    *,
    url: Any = "",
    resolved_title: Any = "",
    content: Any = "",
    content_source: Any = "",
    resource_kind: Any = "",
    content_type: Any = "",
    error: Any = "",
    status: Any = "",
    content_chars: Any = 0,
) -> str:
    normalized_text = _normalize_text(content).lower()
    normalized_title = _normalize_text(resolved_title)
    normalized_error = _normalize_text(error).lower()
    normalized_status = _normalize_text(status).lower()
    chars = 0
    try:
        chars = int(content_chars or 0)
    except Exception:
        chars = 0

    challenge_text = " ".join(
        filter(
            None,
            [
                normalized_text[:400],
                normalized_error,
                normalized_status,
                _normalize_text(content_source).lower(),
                _normalize_text(resource_kind).lower(),
                _normalize_text(content_type).lower(),
            ],
        )
    )
    if any(cue in challenge_text for cue in PAGE_CHALLENGE_CUES):
        return PAGE_QUALITY_CHALLENGE
    if chars <= 0:
        return PAGE_QUALITY_THIN
    if title_looks_like_hostname(normalized_title, url):
        if chars >= 800:
            return PAGE_QUALITY_PARTIAL
        return PAGE_QUALITY_GENERIC
    if chars < 180:
        return PAGE_QUALITY_THIN
    if chars < 900:
        return PAGE_QUALITY_PARTIAL
    if any(cue == normalized_title.lower() for cue in PAGE_GENERIC_CUES):
        return PAGE_QUALITY_GENERIC
    return PAGE_QUALITY_USABLE


def counts_as_strong_source(page_quality: Any) -> bool:
    return _normalize_text(page_quality) == PAGE_QUALITY_USABLE


def query_fingerprint(value: Any) -> str:
    normalized = " ".join(sorted(_tokenize_terms(value)))
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _family_precedence_key(family_id: str) -> tuple[int, int, str]:
    normalized = str(family_id or "").strip()
    for prefix, rank in FAMILY_PRECEDENCE.items():
        if normalized.startswith(prefix):
            return (rank, len(normalized), normalized)
    return (len(FAMILY_PRECEDENCE), len(normalized), normalized)


def _normalize_doi(value: Any) -> str:
    doi = _normalize_text(value)
    if not doi:
        return ""
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    doi = doi.split("?", 1)[0].split("#", 1)[0].strip()
    doi = doi.rstrip(").,;:]}>\"'")
    lowered = doi.lower()
    trimmed = True
    while trimmed:
        trimmed = False
        for suffix in DOI_SUFFIX_TRIMS:
            if lowered.endswith(suffix):
                doi = doi[: -len(suffix)].rstrip("/")
                lowered = doi.lower()
                trimmed = True
    return doi.lower()


def extract_identifier_hints(
    *,
    title: str = "",
    url: str = "",
    text: str = "",
) -> dict[str, str]:
    combined = " ".join(
        filter(None, [_normalize_text(title), _normalize_text(url), _normalize_text(text)])
    )
    doi_match = DOI_RE.search(combined)
    pmid_match = PMID_RE.search(combined)
    pmcid_match = PMCID_RE.search(combined)
    arxiv_match = ARXIV_RE.search(combined)
    nct_match = NCT_RE.search(combined)
    return {
        "doi": _normalize_doi(doi_match.group(1)) if doi_match else "",
        "pmid": next(
            (
                group
                for group in (pmid_match.groups() if pmid_match else ())
                if group and str(group).strip()
            ),
            "",
        ),
        "pmcid": next(
            (
                str(group).upper()
                for group in (pmcid_match.groups() if pmcid_match else ())
                if group and str(group).strip()
            ),
            "",
        ),
        "arxiv": arxiv_match.group(1).lower() if arxiv_match else "",
        "nct": nct_match.group(1).upper() if nct_match else "",
    }


def _normalize_identifier_hints(identifier_hints: Optional[dict[str, str]]) -> dict[str, str]:
    hints = dict(identifier_hints or {})
    return {
        "doi": _normalize_doi(hints.get("doi")),
        "pmid": _normalize_text(hints.get("pmid")),
        "pmcid": _normalize_text(hints.get("pmcid")).upper(),
        "arxiv": _normalize_text(hints.get("arxiv")).lower(),
        "nct": _normalize_text(hints.get("nct")).upper(),
    }


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


def _contract_implies_strict(contract: dict[str, Any] | None) -> bool:
    contract = contract or {}
    return _normalize_bool(contract.get("direct_support_required_for_verified")) and _normalize_bool(
        contract.get("contradiction_blocks_verified")
    )


def _is_strict_goal(
    objective: str,
    *,
    contract: Optional[dict[str, Any]] = None,
    priority: str = "primary",
) -> bool:
    normalized = _normalize_text(objective).lower()
    if priority == "primary" and any(
        term in normalized for term in STRICT_EVIDENCE_REQUIREMENT_CUES | {"verified facts"}
    ):
        return True
    return _contract_implies_strict(contract)


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
    contract = _contract_for_resolution_type(resolution_type)
    is_strict = _is_strict_goal(
        objective,
        contract=contract,
        priority="primary" if goal_index == 1 else "secondary",
    )
    return {
        "goal_id": goal_id,
        "question": question,
        "priority": "primary",
        "resolution_type": resolution_type,
        "coverage_requirement": "strict" if is_strict else _coverage_requirement_for_objective(objective),
        "is_strict": is_strict,
        "acceptance_contract": contract,
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
        "search_result_titles": {},
        "unique_query_fingerprints": [],
        "unique_fetch_urls": [],
        "duplicate_query_count": 0,
        "duplicate_fetch_count": 0,
        "negative_signal_count": 0,
        "blocked_access_count": 0,
        "page_quality_counts": {},
        "family_aliases": {},
        "family_alias_count": 0,
        "same_family_conflict_count": 0,
        "query_rewrite_count": 0,
        "low_novelty_query_count": 0,
        "evidence_query_history": [],
        "emitted_rewrite_scopes": [],
        "goal_query_rewrite_counts": {},
        "ready_to_answer": False,
        "stop_reason": "",
        "post_draft_gate_triggered": False,
        "repair_pass_count": 0,
        "incomplete_reason": "",
        "verifier_enabled": False,
        "verifier_verdict": "",
        "verifier_reasons": [],
        "verifier_latency_ms": 0,
        "verifier_unsupported_claims": [],
        "verifier_missing_limitations": [],
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


def _refresh_page_quality_counts(state: dict[str, Any]) -> None:
    counts: dict[str, int] = {}
    for artifact in state.get("stored_artifacts") or []:
        quality = _normalize_text(artifact.get("page_quality") or "")
        if not quality:
            continue
        counts[quality] = counts.get(quality, 0) + 1
    state["page_quality_counts"] = counts


def _search_result_title_for_url(state: dict[str, Any], url: str) -> str:
    registry = state.get("search_result_titles") or {}
    if not isinstance(registry, dict):
        return ""
    return _normalize_text(registry.get(canonicalize_url(url)) or "")


def _family_ids_from_hints(
    identifier_hints: dict[str, str],
    *,
    url: str,
    title: str = "",
) -> list[str]:
    family_ids: list[str] = []
    for key in ("doi", "pmcid", "pmid", "arxiv", "nct"):
        value = str(identifier_hints.get(key) or "").strip()
        if value:
            family_ids.append(f"{key}:{value}")

    canonical = canonicalize_url(url)
    if canonical:
        family_ids.append(f"url:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:16]}")
    elif title:
        family_ids.append(f"unresolved:{hashlib.sha256(title.encode('utf-8')).hexdigest()[:16]}")

    deduped: list[str] = []
    seen: set[str] = set()
    for family_id in family_ids:
        if family_id and family_id not in seen:
            seen.add(family_id)
            deduped.append(family_id)
    return deduped


def _follow_family_alias(alias_map: dict[str, str], family_id: str) -> str:
    current = str(family_id or "").strip()
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        next_id = str(alias_map.get(current) or "").strip()
        if not next_id or next_id == current:
            break
        current = next_id
    return current


def _preferred_family_id(family_ids: list[str]) -> str:
    if not family_ids:
        return ""
    return sorted((_normalize_text(item) for item in family_ids if _normalize_text(item)), key=_family_precedence_key)[0]


def _resolve_family_id(
    state: dict[str, Any],
    *,
    url: str,
    title: str = "",
    text: str = "",
    identifier_hints: Optional[dict[str, str]] = None,
) -> tuple[str, dict[str, str]]:
    hints = _normalize_identifier_hints(identifier_hints)
    if not hints:
        hints = extract_identifier_hints(title=title, url=url, text=text)
    candidate_ids = _family_ids_from_hints(hints, url=url, title=title)
    alias_map = state.setdefault("family_aliases", {})
    resolved_ids = [_follow_family_alias(alias_map, family_id) for family_id in candidate_ids]
    preferred = _preferred_family_id([*candidate_ids, *resolved_ids])
    if not preferred:
        preferred = f"unresolved:{hashlib.sha256(_normalize_text(title or url).encode('utf-8')).hexdigest()[:16]}"

    added_aliases = 0
    for family_id in {item for item in [*candidate_ids, *resolved_ids] if item and item != preferred}:
        current = _follow_family_alias(alias_map, family_id)
        if current != preferred:
            alias_map[family_id] = preferred
            added_aliases += 1
    if added_aliases:
        state["family_alias_count"] = int(state.get("family_alias_count") or 0) + added_aliases
    return preferred, hints


def derive_evidence_family_id(
    *,
    url: str,
    title: str = "",
    text: str = "",
    identifier_hints: Optional[dict[str, str]] = None,
) -> str:
    hints = dict(identifier_hints or {}) or extract_identifier_hints(
        title=title,
        url=url,
        text=text,
    )
    family_ids = _family_ids_from_hints(hints, url=url, title=title)
    return _preferred_family_id(family_ids)


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
    identifier_hints: Optional[dict[str, str]] = None,
    canonical_family_id: str = "",
) -> str:
    evidence_role = derive_evidence_role(domain=domain, title=title, url=url)
    evidence_class = derive_evidence_class(role=evidence_role, title=title, url=url)
    family_id = _normalize_text(canonical_family_id)
    normalized_hints = _normalize_identifier_hints(identifier_hints)
    if not family_id:
        family_id, normalized_hints = _resolve_family_id(
            state,
            url=url,
            title=title,
            text=text,
            identifier_hints=normalized_hints,
        )
    evidence_id = f"ev_{uuid4().hex[:12]}"
    record = {
        "evidence_id": evidence_id,
        "goal_ids": list(goal_ids),
        "source_role": source_role or evidence_role,
        "source_ref": {"title": title, "url": url, "domain": domain},
        "canonical_url": canonicalize_url(url),
        "evidence_family_id": family_id,
        "identifier_hints": normalized_hints,
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


def _basis_summary(
    goal: dict[str, Any],
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    internally_mixed: Optional[list[dict[str, Any]]] = None,
) -> str:
    internally_mixed = internally_mixed or []
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
    support_family_count = len(
        {
            str(record.get("evidence_family_id") or "").strip()
            for record in supporting
            if str(record.get("evidence_family_id") or "").strip()
        }
    )
    oppose_family_count = len(
        {
            str(record.get("evidence_family_id") or "").strip()
            for record in opposing
            if str(record.get("evidence_family_id") or "").strip()
        }
    )
    snippet_only = supporting and all(
        str(record.get("content_depth") or "") in {"snippet", "summary"}
        for record in supporting
    )
    if supporting and opposing:
        return (
            f"mixed {kind_text} across {support_family_count + oppose_family_count} "
            f"independent source families for {resolution_type} question '{question}'"
        )
    if internally_mixed:
        return (
            f"single-source-family conflict in {kind_text} for {resolution_type} "
            f"question '{question}', without enough independent corroboration"
        )
    if supporting:
        if coverage_requirement == "strict" and snippet_only:
            return (
                f"supported only by {kind_text} across {support_family_count} source "
                f"families from snippet/summary-level evidence for '{question}', without enough independent corroboration"
            )
        return (
            f"supported by {kind_text} across {support_family_count} independent "
            f"source families for '{question}', limited to evidence retrieved in this turn"
        )
    if opposing:
        return (
            f"opposed by {kind_text} across {oppose_family_count} independent "
            f"source families for '{question}', limited to evidence retrieved in this turn"
        )
    return f"no qualifying evidence for '{question}' under the bounded retrieval budget"


def _limitations_for_goal(
    goal: dict[str, Any],
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    internally_mixed: Optional[list[dict[str, Any]]] = None,
) -> list[str]:
    internally_mixed = internally_mixed or []
    limitations: list[str] = []
    if goal.get("status") == GOAL_STATUS_MIXED:
        limitations.append("conflicting medium/high-value evidence remains")
    elif internally_mixed:
        limitations.append("a single source family contains internally conflicting evidence")
    if goal.get("disconfirmation_outcome") == "not_meaningfully_tested":
        limitations.append("disconfirmation probe was not meaningfully completed")
    if goal.get("resolution_basis") == "blocked_access":
        limitations.append("relevant source access was blocked or empty")
    if goal.get("resolution_basis") == "budget_exhausted_before_resolution":
        limitations.append("bounded search budget ended before clean resolution")
    coverage_pending = _normalize_text(goal.get("coverage_pending_reason") or "")
    if coverage_pending:
        limitations.append(coverage_pending)
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


def _derive_candidate_claim(
    goal: dict[str, Any],
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    internally_mixed: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    internally_mixed = internally_mixed or []
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
            "basis_summary": _basis_summary(goal, supporting, opposing, internally_mixed),
            "must_include_limitations": _limitations_for_goal(
                goal,
                supporting,
                opposing,
                internally_mixed,
            ),
            "support_ids": [item.get("evidence_id") for item in supporting[:4]],
            "oppose_ids": [item.get("evidence_id") for item in opposing[:4]],
        }
    ]
    return claims[:RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]


def _record_rank(record: dict[str, Any]) -> tuple[int, int, int, int]:
    value_rank = {"low": 0, "medium": 1, "high": 2}
    depth_rank = {"summary": 0, "snippet": 1, "unknown": 1, "full_text": 2}
    method_rank = {"low": 0, "medium": 1, "high": 2}
    directness_rank = {"indirect": 0, "direct": 1}
    return (
        value_rank.get(str(record.get("value_bucket") or ""), -1),
        depth_rank.get(str(record.get("content_depth") or ""), -1),
        method_rank.get(str(record.get("method_strength") or ""), -1),
        directness_rank.get(str(record.get("directness") or ""), -1),
    )


def _adjudicated_family_evidence(
    state: dict[str, Any],
    goal_id: str,
) -> dict[str, list[dict[str, Any]]]:
    records_by_family: dict[str, dict[str, dict[str, Any]]] = {}

    for record in state.get("evidence_ledger") or []:
        if goal_id not in (record.get("goal_ids") or []):
            continue
        if record.get("context_fit") == "weak":
            continue
        if record.get("blocked"):
            continue
        stance = str(record.get("stance") or "")
        family_id = str(record.get("evidence_family_id") or "").strip()
        family_key = family_id or f"unresolved:{record.get('evidence_id')}"
        family_bucket = records_by_family.setdefault(family_key, {})
        if stance not in {"supports", "opposes"}:
            continue
        existing = family_bucket.get(stance)
        if existing is None or _record_rank(record) > _record_rank(existing):
            family_bucket[stance] = record

    supporting: list[dict[str, Any]] = []
    opposing: list[dict[str, Any]] = []
    internally_mixed: list[dict[str, Any]] = []
    for family_id, family_bucket in records_by_family.items():
        support = family_bucket.get("supports")
        oppose = family_bucket.get("opposes")
        if support and oppose:
            internally_mixed.append(
                {
                    "family_id": family_id,
                    "support_record": support,
                    "oppose_record": oppose,
                }
            )
            continue
        if support:
            supporting.append(support)
        elif oppose:
            opposing.append(oppose)

    return {
        "supports": supporting,
        "opposes": opposing,
        "internally_mixed": internally_mixed,
    }


def _support_and_opposition_for_goal(state: dict[str, Any], goal_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    adjudicated = _adjudicated_family_evidence(state, goal_id)
    return adjudicated["supports"], adjudicated["opposes"]


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


def _missing_required_probes(goal: dict[str, Any]) -> list[str]:
    budget = goal.get("probe_budget") or {}
    required = budget.get("required") or {}
    observed = budget.get("observed") or {}
    return [
        key
        for key, is_required in required.items()
        if is_required and int(observed.get(key) or 0) <= 0
    ]


def _humanize_probe_kind(value: str) -> str:
    return str(value or "").replace("_", " ").strip()


def _coverage_pending_reason(goal: dict[str, Any]) -> str:
    missing = _missing_required_probes(goal)
    if not missing:
        return ""
    return "required coverage probes still missing: " + ", ".join(
        _humanize_probe_kind(item) for item in missing
    )


def _goal_budget_exhausted(state: dict[str, Any]) -> bool:
    return len(state.get("unique_query_fingerprints") or []) >= int(
        state.get("max_unique_queries") or 0
    )


def _resolve_goal(goal: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    updated = copy.deepcopy(goal)
    adjudicated = _adjudicated_family_evidence(state, str(goal.get("goal_id") or ""))
    supporting = adjudicated["supports"]
    opposing = adjudicated["opposes"]
    internally_mixed = adjudicated["internally_mixed"]
    previous_status = str(goal.get("status") or GOAL_STATUS_OPEN)
    previous_basis = str(goal.get("resolution_basis") or "")
    previous_pending_reason = str(goal.get("coverage_pending_reason") or "")
    updated["coverage_pending_reason"] = ""

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

    strict_coverage_pending = (
        str(updated.get("coverage_requirement") or "normal") == "strict"
        and not _probe_budget_complete(updated)
    )
    if strict_coverage_pending:
        updated["coverage_pending_reason"] = _coverage_pending_reason(updated)

    has_cross_family_conflict = bool(supporting and opposing) or bool(
        internally_mixed and (supporting or opposing)
    )
    has_single_family_conflict_only = bool(internally_mixed) and not (
        supporting or opposing
    )

    if strict_coverage_pending and state.get("blocked_access_count"):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "blocked_access"
    elif strict_coverage_pending and _goal_budget_exhausted(state):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budget_exhausted_before_resolution"
    elif strict_coverage_pending:
        updated["status"] = GOAL_STATUS_OPEN
        updated["resolution_basis"] = ""
    elif has_cross_family_conflict:
        updated["status"] = GOAL_STATUS_MIXED
        updated["resolution_basis"] = "contradictory_high_value_evidence"
    elif opposing and not supporting:
        updated["status"] = GOAL_STATUS_NOT_SUPPORTED
        updated["resolution_basis"] = "contradictory_high_value_evidence"
    elif _goal_contract_satisfied(updated, supporting):
        updated["status"] = GOAL_STATUS_SUPPORTED
        updated["resolution_basis"] = "contract_satisfied"
    elif has_single_family_conflict_only and state.get("blocked_access_count"):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "blocked_access"
    elif has_single_family_conflict_only and (
        _probe_budget_complete(updated) or _goal_budget_exhausted(state)
    ):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budgeted_high_value_search_exhausted"
    elif state.get("blocked_access_count"):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "blocked_access"
    elif _probe_budget_complete(updated):
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budgeted_high_value_search_exhausted"
    elif _goal_budget_exhausted(state) and not supporting:
        updated["status"] = GOAL_STATUS_INSUFFICIENT
        updated["resolution_basis"] = "budget_exhausted_before_resolution"
    else:
        updated["status"] = GOAL_STATUS_OPEN
        updated["resolution_basis"] = ""

    changed = (
        updated["status"] != previous_status
        or updated["resolution_basis"] != previous_basis
        or updated.get("coverage_pending_reason") != previous_pending_reason
    )
    return updated, changed


def _update_working_proposition(
    proposition: dict[str, Any],
    goal: dict[str, Any],
    supporting: list[dict[str, Any]],
    opposing: list[dict[str, Any]],
    internally_mixed: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], bool]:
    internally_mixed = internally_mixed or []
    updated = copy.deepcopy(proposition)
    previous_state = str(proposition.get("state") or "open")
    if goal.get("status") == GOAL_STATUS_SUPPORTED:
        updated["state"] = "leaning_support"
    elif goal.get("status") == GOAL_STATUS_MIXED:
        updated["state"] = "leaning_mixed"
    elif goal.get("status") == GOAL_STATUS_NOT_SUPPORTED:
        updated["state"] = "leaning_not_supported"
    elif internally_mixed or (supporting and opposing):
        updated["state"] = "leaning_mixed"
    elif supporting:
        updated["state"] = "leaning_support"
    elif opposing:
        updated["state"] = "leaning_not_supported"
    else:
        updated["state"] = "open"
    updated["support_ids"] = [item.get("evidence_id") for item in supporting[:4]]
    updated["oppose_ids"] = [item.get("evidence_id") for item in opposing[:4]]
    updated["contradiction_pressure"] = bool(supporting and opposing) or bool(internally_mixed)
    return updated, updated["state"] != previous_state


def _set_pending_note(state: dict[str, Any], note: str) -> None:
    state["pending_system_note"] = _normalize_text(note)


def _append_pending_note(state: dict[str, Any], note: str) -> None:
    current = _normalize_text(state.get("pending_system_note") or "")
    addition = _normalize_text(note)
    if not addition:
        return
    if not current:
        state["pending_system_note"] = addition
        return
    if addition in current:
        return
    state["pending_system_note"] = _normalize_text(f"{current} {addition}")


def _query_token_overlap(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _artifact_family_scope(
    state: dict[str, Any],
    artifact_ids: list[str],
) -> tuple[str, ...]:
    if not artifact_ids:
        return ()
    scope: list[str] = []
    for artifact_id in artifact_ids:
        artifact = next(
            (
                item
                for item in state.get("stored_artifacts") or []
                if _normalize_text(item.get("artifact_id") or "") == _normalize_text(artifact_id)
            ),
            {},
        )
        family_id = _normalize_text(artifact.get("canonical_family_id") or "")
        if not family_id:
            family_id, _ = _resolve_family_id(
                state,
                url=_normalize_text(artifact.get("url") or ""),
                title=_normalize_text(artifact.get("title") or ""),
                identifier_hints=artifact.get("identifier_hints") or {},
            )
            if artifact:
                artifact["canonical_family_id"] = family_id
        if family_id and family_id not in scope:
            scope.append(family_id)
    return tuple(sorted(scope))


def _goal_family_count(state: dict[str, Any], goal_id: str) -> int:
    adjudicated = _adjudicated_family_evidence(state, goal_id)
    family_ids = {
        str(record.get("evidence_family_id") or "").strip()
        for record in adjudicated["supports"] + adjudicated["opposes"]
        if str(record.get("evidence_family_id") or "").strip()
    }
    family_ids |= {
        str(item.get("family_id") or "").strip()
        for item in adjudicated["internally_mixed"]
        if str(item.get("family_id") or "").strip()
    }
    return len(family_ids)


def _goal_probe_snapshot(goal: dict[str, Any]) -> dict[str, int]:
    observed = ((goal.get("probe_budget") or {}).get("observed") or {})
    return {str(key): int(value or 0) for key, value in observed.items()}


def _build_query_rewrite_instruction(goal_questions: list[str]) -> str:
    joined_goals = "; ".join(_normalize_text(item) for item in goal_questions if _normalize_text(item))
    if joined_goals:
        joined_goals = f" for goal(s): {joined_goals}"
    return (
        "Recent evidence queries are circling the same source family without improving coverage"
        f"{joined_goals}. Rewrite the next query_web_evidence call as a short retrieval phrase. "
        "Preserve the proposition, population, outcome, and one discriminator. "
        "Remove rhetorical glue and copied sentences. Prefer an outcome phrase, significance phrase, conclusion phrase, or subgroup phrase. "
        "If possible, widen artifact scope before drilling further."
    )


def _maybe_flag_low_novelty_query(
    state: dict[str, Any],
    *,
    query_value: str,
    family_scope: tuple[str, ...],
    goal_ids: list[str],
    searched_artifact_count: int,
    searched_domains: list[str],
) -> None:
    if not query_value or not family_scope or len(family_scope) != 1 or not goal_ids:
        return

    history = state.setdefault("evidence_query_history", [])
    tokens = set(_tokenize_terms(query_value))
    current_entry = {
        "query": query_value,
        "query_fp": query_fingerprint(query_value),
        "query_tokens": sorted(tokens),
        "family_scope": list(family_scope),
        "goal_ids": list(goal_ids),
        "family_counts": {
            goal_id: _goal_family_count(state, goal_id)
            for goal_id in goal_ids
        },
        "probe_counts": {
            goal_id: _goal_probe_snapshot(
                next(
                    (
                        goal
                        for goal in state.get("goals") or []
                        if goal.get("goal_id") == goal_id
                    ),
                    {},
                )
            )
            for goal_id in goal_ids
        },
        "searched_artifact_count": int(searched_artifact_count or 0),
        "searched_domain_count": len(searched_domains or []),
    }

    prior = next(
        (
            entry
            for entry in reversed(history)
            if tuple(entry.get("family_scope") or ()) == family_scope
            and set(entry.get("goal_ids") or []).intersection(goal_ids)
        ),
        None,
    )
    if prior is not None:
        overlap = _query_token_overlap(tokens, set(prior.get("query_tokens") or []))
        family_growth = any(
            current_entry["family_counts"].get(goal_id, 0)
            > int((prior.get("family_counts") or {}).get(goal_id) or 0)
            for goal_id in goal_ids
        )
        probe_growth = any(
            current_entry["probe_counts"].get(goal_id, {}).get("broader_fallback", 0)
            > int(((prior.get("probe_counts") or {}).get(goal_id) or {}).get("broader_fallback") or 0)
            for goal_id in goal_ids
        )
        scope_growth = (
            current_entry["searched_artifact_count"] > int(prior.get("searched_artifact_count") or 0)
            or current_entry["searched_domain_count"] > int(prior.get("searched_domain_count") or 0)
        )
        if overlap >= RESEARCH_GUIDED_QUERY_OVERLAP_THRESHOLD and not family_growth and not probe_growth and not scope_growth:
            state["low_novelty_query_count"] = int(state.get("low_novelty_query_count") or 0) + 1
            emitted_scopes = set(state.get("emitted_rewrite_scopes") or [])
            scope_key = "|".join(family_scope)
            per_goal_counts = state.setdefault("goal_query_rewrite_counts", {})
            rewrite_allowed = scope_key not in emitted_scopes and any(
                int(per_goal_counts.get(goal_id) or 0) < RESEARCH_GUIDED_MAX_QUERY_REWRITES_PER_GOAL
                for goal_id in goal_ids
            )
            if rewrite_allowed and not state.get("ready_to_answer"):
                state["query_rewrite_count"] = int(state.get("query_rewrite_count") or 0) + 1
                state["emitted_rewrite_scopes"] = [
                    *list(emitted_scopes),
                    scope_key,
                ][: RESEARCH_GUIDED_MAX_QUERY_REWRITES_PER_SCOPE * RESEARCH_GUIDED_MAX_GOALS]
                for goal_id in goal_ids:
                    if int(per_goal_counts.get(goal_id) or 0) < RESEARCH_GUIDED_MAX_QUERY_REWRITES_PER_GOAL:
                        per_goal_counts[goal_id] = int(per_goal_counts.get(goal_id) or 0) + 1
                questions = [
                    next(
                        (
                            goal.get("question")
                            for goal in state.get("goals") or []
                            if goal.get("goal_id") == goal_id
                        ),
                        "",
                    )
                    for goal_id in goal_ids
                ]
                _append_pending_note(state, _build_query_rewrite_instruction(questions))

    history.append(current_entry)
    state["evidence_query_history"] = history[-12:]


def _refresh_resolutions(state: dict[str, Any]) -> dict[str, Any]:
    updated_goals: list[dict[str, Any]] = []
    updated_props: list[dict[str, Any]] = []
    note_lines: list[str] = []
    same_family_conflict_count = 0

    for goal in state.get("goals") or []:
        next_goal, changed = _resolve_goal(goal, state)
        adjudicated = _adjudicated_family_evidence(state, str(goal.get("goal_id") or ""))
        supporting = adjudicated["supports"]
        opposing = adjudicated["opposes"]
        internally_mixed = adjudicated["internally_mixed"]
        same_family_conflict_count += len(internally_mixed)
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
        next_prop, prop_changed = _update_working_proposition(
            proposition,
            next_goal,
            supporting,
            opposing,
            internally_mixed,
        )
        if changed or prop_changed:
            if next_prop["state"] == "leaning_not_supported":
                note_lines.append(
                    f"Current evidence leans against goal '{next_goal.get('question')}'. Focus on disconfirmation or pivot."
                )
            elif next_prop["state"] == "leaning_mixed":
                if next_goal.get("coverage_pending_reason"):
                    note_lines.append(
                        f"Current evidence is mixed for goal '{next_goal.get('question')}', but required coverage is still incomplete. Broaden coverage before concluding."
                    )
                else:
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
            if next_goal.get("coverage_pending_reason"):
                note_lines.append(
                    f"Goal '{next_goal.get('question')}' still has pending coverage: {next_goal.get('coverage_pending_reason')}."
                )
        updated_goals.append(next_goal)
        updated_props.append(next_prop)

    state["goals"] = updated_goals
    state["working_propositions"] = updated_props
    state["same_family_conflict_count"] = same_family_conflict_count
    all_terminal = all(
        goal.get("status") in TERMINAL_GOAL_STATUSES
        and goal.get("resolution_basis")
        and goal.get("disconfirmation_outcome")
        and not goal.get("coverage_pending_reason")
        for goal in updated_goals
    )
    state["ready_to_answer"] = bool(all_terminal)
    if note_lines:
        _append_pending_note(state, " ".join(note_lines[:2]))
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
    query_family_scope: tuple[str, ...] = ()
    query_goal_ids: list[str] = []
    query_scope_artifact_count = 0
    query_scope_domains: list[str] = []
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
        if isinstance(parsed, list):
            title_registry = updated.setdefault("search_result_titles", {})
            for item in parsed[:8]:
                if not isinstance(item, dict):
                    continue
                link = canonicalize_url(item.get("link") or item.get("url") or "")
                title = _normalize_text(item.get("title") or "")
                if link and title:
                    title_registry[link] = title
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
            page_quality = _normalize_text(parsed.get("page_quality") or "")
            counts_as_strong = (
                _normalize_bool(parsed.get("counts_as_strong_source"))
                if "counts_as_strong_source" in parsed
                else (
                    counts_as_strong_source(page_quality)
                    if page_quality
                    else not blocked
                )
            )
            if page_quality == PAGE_QUALITY_CHALLENGE:
                blocked = True
            if blocked:
                updated["blocked_access_count"] = int(updated.get("blocked_access_count") or 0) + 1
                updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1
            elif page_quality in PAGE_QUALITY_REJECTED or page_quality == PAGE_QUALITY_PARTIAL:
                updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1
            if parsed.get("artifact_id"):
                title = _normalize_text(
                    parsed.get("resolved_title")
                    or parsed.get("title")
                    or _search_result_title_for_url(updated, url)
                )
                title_source = _normalize_text(parsed.get("title_source") or "")
                if title_source == "url_fallback":
                    search_title = _search_result_title_for_url(updated, url)
                    if search_title:
                        title = search_title
                        title_source = "search_result_registry"
                identifier_hints = dict(parsed.get("identifier_hints") or {})
                canonical_family_id, identifier_hints = _resolve_family_id(
                    updated,
                    url=url,
                    title=title,
                    identifier_hints=identifier_hints,
                )
                updated.setdefault("stored_artifacts", []).append(
                    {
                        "artifact_id": parsed.get("artifact_id"),
                        "url": url,
                        "domain": parsed.get("domain") or _domain_from_url(url),
                        "title": title,
                        "title_source": title_source,
                        "blocked": blocked,
                        "mode": parsed.get("mode") or tool_params.get("mode") or "",
                        "content_chars": content_chars,
                        "page_quality": page_quality,
                        "counts_as_strong_source": counts_as_strong,
                        "identifier_hints": identifier_hints,
                        "canonical_family_id": canonical_family_id,
                        "evidence_role": derive_evidence_role(
                            domain=_normalize_text(parsed.get("domain") or _domain_from_url(url)),
                            title=title,
                            url=url,
                        ),
                    }
                )
                _refresh_page_quality_counts(updated)
            if not blocked:
                title = _normalize_text(parsed.get("resolved_title") or parsed.get("title") or "")
                combined_text = " ".join(filter(None, [title, url]))
                for goal in updated.get("goals") or []:
                    if counts_as_strong and _context_fit(goal, combined_text) != "weak":
                        _mark_probe_observed(goal, "strong_source")

    elif normalized_tool_name == "query_web_evidence" and isinstance(parsed, dict):
        snippets = parsed.get("snippets") or []
        if not snippets and str(parsed.get("status") or "") == "not_found":
            updated["negative_signal_count"] = int(updated.get("negative_signal_count") or 0) + 1

        artifact_scope_ids = [
            _normalize_text(value)
            for value in (
                tool_params.get("artifact_ids")
                or parsed.get("searched_artifact_ids")
                or parsed.get("artifact_ids")
                or []
            )
            if _normalize_text(value)
        ]
        if (
            not artifact_scope_ids
            and int(parsed.get("searched_artifact_count") or 0) == 1
            and len(updated.get("stored_artifacts") or []) == 1
        ):
            only_artifact = (updated.get("stored_artifacts") or [{}])[0]
            only_artifact_id = _normalize_text(only_artifact.get("artifact_id") or "")
            if only_artifact_id:
                artifact_scope_ids = [only_artifact_id]
        query_family_scope = _artifact_family_scope(updated, artifact_scope_ids)
        query_scope_artifact_count = int(parsed.get("searched_artifact_count") or 0)
        query_scope_domains = list(parsed.get("searched_domains") or [])

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
                if goal.get("goal_id") not in query_goal_ids:
                    query_goal_ids.append(goal.get("goal_id"))
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
            identifier_hints = dict(artifact.get("identifier_hints") or {})
            canonical_family_id = _normalize_text(artifact.get("canonical_family_id") or "")
            artifact_source_role = _normalize_text(artifact.get("evidence_role") or "")
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
                        source_role=artifact_source_role,
                        title=title,
                        url=url,
                        domain=domain,
                        text=text,
                        content_depth=content_depth,
                        identifier_hints=identifier_hints,
                        canonical_family_id=canonical_family_id,
                    )
                    goal.setdefault("support_ids", []).append(evidence_id)
                elif alignment == "disconfirming":
                    evidence_id = _append_evidence_record(
                        updated,
                        goal_ids=[goal.get("goal_id")],
                        stance="opposes",
                        source_role=artifact_source_role,
                        title=title,
                        url=url,
                        domain=domain,
                        text=text,
                        content_depth=content_depth,
                        identifier_hints=identifier_hints,
                        canonical_family_id=canonical_family_id,
                    )
                    goal.setdefault("oppose_ids", []).append(evidence_id)
                elif _context_fit(goal, text) == "weak":
                    goal.setdefault("orthogonal_ids", [])

    updated = _refresh_resolutions(updated)
    if normalized_tool_name == "query_web_evidence":
        _maybe_flag_low_novelty_query(
            updated,
            query_value=query_value,
            family_scope=query_family_scope,
            goal_ids=[goal_id for goal_id in query_goal_ids if goal_id],
            searched_artifact_count=query_scope_artifact_count,
            searched_domains=query_scope_domains,
        )
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
        adjudicated = _adjudicated_family_evidence(updated, str(goal.get("goal_id") or ""))
        candidate_claims.extend(
            _derive_candidate_claim(
                goal,
                adjudicated["supports"],
                adjudicated["opposes"],
                adjudicated["internally_mixed"],
            )
        )
    updated["candidate_claims"] = candidate_claims
    updated["ready_to_answer"] = bool(candidate_claims) or bool(updated.get("goals"))
    updated["phase"] = "final_response" if updated.get("ready_to_answer") else updated.get("phase")
    updated["stop_reason"] = updated.get("stop_reason") or (
        "all_primary_goals_resolved" if updated.get("ready_to_answer") else ""
    )
    return updated


def state_has_strict_goals(state: Optional[dict[str, Any]]) -> bool:
    if not isinstance(state, dict):
        return False
    return any(_normalize_bool(goal.get("is_strict")) for goal in (state.get("goals") or []))


def build_research_capped_block(state: Optional[dict[str, Any]]) -> str:
    if not isinstance(state, dict):
        return ""
    claims = state.get("candidate_claims") or []
    if not claims:
        return ""
    lines = ["**Current evidence:**"]
    for claim in claims[: RESEARCH_GUIDED_MAX_GOALS * RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]:
        lines.append(f"- {claim.get('text')} [{claim.get('label')}]")
        lines.append(f"  Basis: {claim.get('basis_summary')}")
        limitations = claim.get("must_include_limitations") or []
        if limitations:
            lines.append(f"  Limitation: {limitations[0]}")
    return "\n".join(lines).strip()


def build_research_incomplete_block(state: Optional[dict[str, Any]]) -> str:
    if not isinstance(state, dict):
        return ""
    lines = [RESEARCH_INCOMPLETE_MARKER]
    goals = state.get("goals") or []
    claims = state.get("candidate_claims") or []
    if goals:
        for goal in goals[:RESEARCH_GUIDED_MAX_GOALS]:
            lines.append("")
            lines.append(f"Goal: {goal.get('question')}")
            status = _normalize_text(goal.get("status") or "open") or "open"
            resolution = _normalize_text(goal.get("resolution_basis") or "") or "unresolved"
            lines.append(f"Status: {status} ({resolution})")
            pending = _normalize_text(goal.get("coverage_pending_reason") or "")
            if pending:
                lines.append(f"Missing: {pending}")
            elif _normalize_text(goal.get("disconfirmation_outcome") or "") == "not_meaningfully_tested":
                lines.append("Missing: disconfirmation probe was not meaningfully completed")
    elif claims:
        for claim in claims[: RESEARCH_GUIDED_MAX_GOALS * RESEARCH_GUIDED_MAX_CLAIMS_PER_GOAL]:
            lines.append("")
            lines.append(f"Claim: {claim.get('text')}")
            lines.append(f"Label: {claim.get('label')}")
            lines.append(f"Basis: {claim.get('basis_summary')}")
            limitations = claim.get("must_include_limitations") or []
            if limitations:
                lines.append(f"Limitation: {limitations[0]}")
    reason = _normalize_text(state.get("incomplete_reason") or "")
    if reason:
        lines.append("")
        lines.append(f"Reason: {reason}")
    return "\n".join(lines).strip()


def build_research_repair_instruction(
    state: Optional[dict[str, Any]],
    *,
    mode: str,
    verifier_result: Optional[dict[str, Any]] = None,
) -> str:
    if not isinstance(state, dict):
        return ""
    lines: list[str] = []
    if mode == "unresolved":
        lines.append(
            "Do not finalize the answer yet. The research-guided gate blocked the draft because the evidence state is not ready."
        )
        missing_items: list[str] = []
        for goal in state.get("goals") or []:
            pending = _normalize_text(goal.get("coverage_pending_reason") or "")
            if pending:
                missing_items.append(pending)
        if missing_items:
            lines.append("Missing coverage items: " + "; ".join(missing_items[:2]) + ".")
        lines.append(
            "Continue only if you can close the missing coverage cleanly in this turn. Otherwise answer cautiously and avoid verified-fact framing."
        )
        return " ".join(lines).strip()

    verifier_result = verifier_result or {}
    lines.append(
        "Revise the draft so it matches the research-guided state exactly. Do not add new claims, new examples, or stronger phrasing."
    )
    reasons = [
        _normalize_text(item)
        for item in (verifier_result.get("reasons") or [])[:RESEARCH_GUIDED_MAX_VERIFIER_REASONS]
        if _normalize_text(item)
    ]
    if reasons:
        lines.append("Verifier concerns: " + "; ".join(reasons) + ".")
    instructions = [
        _normalize_text(item)
        for item in (verifier_result.get("instructions") or [])[:RESEARCH_GUIDED_MAX_VERIFIER_INSTRUCTIONS]
        if _normalize_text(item)
    ]
    if instructions:
        lines.append("Apply these corrections: " + "; ".join(instructions) + ".")
    lines.append(
        "Preserve claim labels and explicitly keep required limitations for any non-verified claim."
    )
    return " ".join(lines).strip()


def _family_views(state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    families: dict[str, dict[str, Any]] = {}
    for record in state.get("evidence_ledger") or []:
        if record.get("blocked"):
            continue
        family_id = _normalize_text(record.get("evidence_family_id") or "")
        if not family_id:
            continue
        bucket = families.setdefault(
            family_id,
            {
                "family_id": family_id,
                "titles": [],
                "roles": set(),
                "identifiers": record.get("identifier_hints") or {},
            },
        )
        title = _normalize_text((record.get("source_ref") or {}).get("title") or "")
        if title and title not in bucket["titles"]:
            bucket["titles"].append(title)
        role = _normalize_text(record.get("source_role") or "")
        if role:
            bucket["roles"].add(role)

    primary: list[dict[str, Any]] = []
    secondary: list[dict[str, Any]] = []
    for bucket in families.values():
        item = {
            "family_id": bucket["family_id"],
            "titles": bucket["titles"][:3],
            "roles": sorted(bucket["roles"]),
            "identifiers": bucket["identifiers"],
        }
        if any(role in PRIMARY_SOURCE_ROLES for role in bucket["roles"]):
            primary.append(item)
        else:
            secondary.append(item)
    return primary, secondary


def extract_named_entity_mentions(text: Any) -> list[str]:
    normalized = str(text or "")
    mentions: list[str] = []
    seen: set[str] = set()
    for pattern in NAMED_PRIMARY_ENTITY_PATTERNS:
        for match in pattern.finditer(normalized):
            value = _normalize_text(match.group(1) if match.groups() else match.group(0))
            if not value or value.lower() in seen:
                continue
            seen.add(value.lower())
            mentions.append(value)
            if len(mentions) >= 8:
                return mentions
    return mentions


def build_micro_verifier_context(
    state: Optional[dict[str, Any]],
    draft_answer: str,
) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    primary_families, secondary_families = _family_views(state)
    return {
        "objective": _normalize_text(state.get("objective") or ""),
        "goal_summary": [
            {
                "question": goal.get("question"),
                "is_strict": bool(goal.get("is_strict")),
                "status": goal.get("status"),
                "resolution_basis": goal.get("resolution_basis"),
                "coverage_pending_reason": goal.get("coverage_pending_reason"),
            }
            for goal in (state.get("goals") or [])[:RESEARCH_GUIDED_MAX_GOALS]
        ],
        "candidate_claims": copy.deepcopy(state.get("candidate_claims") or []),
        "research_snapshot": build_research_snapshot(state),
        "primary_source_families": primary_families,
        "secondary_source_families": secondary_families,
        "named_entity_mentions_in_draft": extract_named_entity_mentions(draft_answer),
    }


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
                "is_strict": bool(goal.get("is_strict")),
                "status": goal.get("status"),
                "resolution_basis": goal.get("resolution_basis"),
                "disconfirmation_outcome": goal.get("disconfirmation_outcome"),
                "coverage_pending_reason": goal.get("coverage_pending_reason"),
                "required_probe_summary": copy.deepcopy(
                    ((goal.get("probe_budget") or {}).get("required") or {})
                ),
                "observed_probe_summary": copy.deepcopy(
                    ((goal.get("probe_budget") or {}).get("observed") or {})
                ),
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
        "family_alias_count": int(state.get("family_alias_count") or 0),
        "same_family_conflict_count": int(state.get("same_family_conflict_count") or 0),
        "query_rewrite_count": int(state.get("query_rewrite_count") or 0),
        "low_novelty_query_count": int(state.get("low_novelty_query_count") or 0),
        "page_quality_counts": copy.deepcopy(state.get("page_quality_counts") or {}),
        "stop_reason": state.get("stop_reason"),
        "ready_to_answer": bool(state.get("ready_to_answer")),
        "post_draft_gate_triggered": bool(state.get("post_draft_gate_triggered")),
        "repair_pass_count": int(state.get("repair_pass_count") or 0),
        "incomplete_reason": _normalize_text(state.get("incomplete_reason") or ""),
        "verifier_enabled": bool(state.get("verifier_enabled")),
        "verifier_verdict": _normalize_text(state.get("verifier_verdict") or ""),
        "verifier_reasons": copy.deepcopy(state.get("verifier_reasons") or []),
        "verifier_latency_ms": int(state.get("verifier_latency_ms") or 0),
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
        "family_alias_count": snapshot.get("family_alias_count"),
        "same_family_conflict_count": snapshot.get("same_family_conflict_count"),
        "query_rewrite_count": snapshot.get("query_rewrite_count"),
        "low_novelty_query_count": snapshot.get("low_novelty_query_count"),
        "page_quality_counts": snapshot.get("page_quality_counts"),
        "stop_reason": snapshot.get("stop_reason"),
        "ready_to_answer": snapshot.get("ready_to_answer"),
        "post_draft_gate_triggered": snapshot.get("post_draft_gate_triggered"),
        "repair_pass_count": snapshot.get("repair_pass_count"),
        "incomplete_reason": snapshot.get("incomplete_reason"),
        "verifier_enabled": snapshot.get("verifier_enabled"),
        "verifier_verdict": snapshot.get("verifier_verdict"),
        "verifier_reasons": snapshot.get("verifier_reasons"),
        "verifier_latency_ms": snapshot.get("verifier_latency_ms"),
    }
