from __future__ import annotations

from typing import Any

from open_webui.retrieval import local_corpus
from open_webui.retrieval.local_corpus_reasoning import frame_local_corpus_problem


MEDICAL_CORPUS_RELEVANCE_FLOOR = 0.42
MEDICAL_CORPUS_DIRECT_RELEVANCE_TARGET = 0.68
MEDICAL_CORPUS_TOPICAL_FIT_FLOOR = 0.45
MEDICAL_CORPUS_FRESHNESS_STRONG = 0.70


def assess_medical_corpus_sufficiency(
    *,
    query: str,
    config_or_path: Any = None,
) -> dict[str, Any]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        return {
            "status": "error",
            "error": "A non-empty query is required",
        }

    frame = frame_local_corpus_problem(
        query=normalized_query,
        domain_hint="medicine",
        config_or_path=config_or_path,
    )
    if frame.get("status") != "ok":
        return {
            "status": "error",
            "error": frame.get("error") or "Could not frame the query against the medical corpus",
        }

    domain_confidence = float(frame.get("domain_confidence") or 0.0)
    task_confidence = float(frame.get("task_type_confidence") or 0.0)
    corpus_compatible = bool(frame.get("domain") == "medicine" and domain_confidence >= 0.35)

    shortlist = local_corpus.shortlist_local_corpus_books(
        query=normalized_query,
        domain="medicine",
        max_books=3,
        config_or_path=config_or_path,
    )
    shortlisted_books = shortlist.get("items") or []
    shortlisted_book_ids = [
        item.get("book_id") for item in shortlisted_books if item.get("book_id")
    ]

    evidence = (
        local_corpus.retrieve_local_corpus_evidence(
            query=normalized_query,
            book_ids=shortlisted_book_ids,
            top_k=5,
            include_related_tables=False,
            include_related_figures=False,
            config_or_path=config_or_path,
        )
        if shortlisted_book_ids
        else {
            "status": "ok",
            "items": [],
            "evidence_sufficiency": "weak",
            "freshness_note": None,
            "answer_guidance": None,
        }
    )
    evidence_items = evidence.get("items") or []
    usable_anchor_count = len(evidence_items)
    top_score = float(evidence_items[0].get("score") or 0.0) if evidence_items else 0.0
    relevance_score = min(1.0, round(top_score / 4.0, 4))
    freshness_score = 0.55 if evidence.get("freshness_note") else 1.0
    topical_fit = min(1.0, round((domain_confidence * 0.55) + (task_confidence * 0.45), 4))
    contradiction_flag = False
    evidence_sufficiency = str(evidence.get("evidence_sufficiency") or "weak").strip().lower()

    fallback_reason = "none"
    decision = "skip_corpus"
    if not corpus_compatible:
        fallback_reason = "not_medical"
    elif relevance_score < MEDICAL_CORPUS_RELEVANCE_FLOOR:
        fallback_reason = "low_relevance"
    elif usable_anchor_count == 0:
        fallback_reason = "too_few_anchors"
    elif contradiction_flag:
        fallback_reason = "conflicting_anchors"
        decision = "use_corpus_plus_web"
    elif freshness_score < 0.65:
        fallback_reason = "stale_anchors"
        decision = "use_corpus_plus_web"
    elif (
        evidence_sufficiency == "strong"
        and usable_anchor_count >= 2
        and relevance_score >= MEDICAL_CORPUS_DIRECT_RELEVANCE_TARGET
        and topical_fit >= 0.65
        and freshness_score >= MEDICAL_CORPUS_FRESHNESS_STRONG
    ):
        decision = "use_corpus_only"
    elif topical_fit >= MEDICAL_CORPUS_TOPICAL_FIT_FLOOR and usable_anchor_count >= 1:
        fallback_reason = "insufficient_coverage"
        decision = "use_corpus_plus_web"
    else:
        fallback_reason = "insufficient_coverage"

    return {
        "status": "ok",
        "phase": "completed",
        "query": normalized_query,
        "domain": "medicine",
        "corpus_compatible": corpus_compatible,
        "relevance_score": relevance_score,
        "freshness_score": round(freshness_score, 4),
        "topical_fit": topical_fit,
        "usable_anchor_count": usable_anchor_count,
        "contradiction_flag": contradiction_flag,
        "decision": decision,
        "fallback_reason": fallback_reason,
        "evidence_sufficiency": evidence_sufficiency,
        "shortlisted_books": shortlisted_books,
        "evidence_items": evidence_items,
        "answer_guidance": evidence.get("answer_guidance"),
        "freshness_note": evidence.get("freshness_note"),
        "task_type": frame.get("primary_task_type"),
        "task_type_confidence": round(task_confidence, 4),
        "domain_confidence": round(domain_confidence, 4),
        "routing_notes": list(frame.get("routing_notes") or []),
    }
