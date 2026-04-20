from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

from fastapi import Request

from open_webui.models.users import UserModel
from open_webui.retrieval.medical_lane import assess_medical_corpus_sufficiency
from open_webui.routers.retrieval import search_web
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.lane_runtime import normalize_science_research_mode
from open_webui.utils.misc import get_last_user_message
from open_webui.utils.runtime_telemetry import runtime_telemetry
from open_webui.utils.scholarly_sources import (
    execute_scholarly_runtime_query,
    get_scholarly_source_settings,
    list_enabled_scholarly_runtime_sources,
)


log = logging.getLogger(__name__)

SCIENCE_ORCHESTRATION_PHASE_LABELS = {
    "classification": "Classifying research path",
    "planning": "Planning evidence path",
    "gathering": "Gathering evidence",
    "checking": "Checking evidence limits",
    "synthesis": "Synthesizing response",
}

SCIENCE_LIGHT_LOOKUP_HINTS = (
    "how to use",
    "how do i use",
    "dose",
    "apply",
    "what is",
    "side effects",
    "symptoms",
)
SCIENCE_DEEP_RESEARCH_HINTS = (
    "literature review",
    "review the literature",
    "systematic review",
    "meta-analysis",
    "meta analysis",
    "state of the art",
    "research gap",
    "evidence synthesis",
    "compare the evidence",
    "what does the evidence say",
)
SCIENCE_TERMINAL_TASK_HINTS = (
    "csv",
    "dataset",
    "spreadsheet",
    "notebook",
    "script",
    "screening table",
    "screening ledger",
    "analyze",
    "analysis",
    "plot",
    "markdown table",
)
SCIENCE_BIOMEDICAL_HINTS = (
    "patient",
    "dose",
    "trial",
    "guideline",
    "pmid",
    "pmcid",
    "pubmed",
    "disease",
    "treatment",
    "drug",
    "medication",
    "symptom",
    "clinical",
)


def build_synthetic_chat_response(content: str, model_id: str) -> dict[str, Any]:
    return {
        "id": f"science-orchestration-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }


def _extract_response_text(response: Any) -> str:
    if isinstance(response, list) and len(response) == 1:
        response = response[0]

    payload = response
    if hasattr(response, "body"):
        body = getattr(response, "body")
        if isinstance(body, bytes):
            payload = json.loads(body.decode("utf-8", "replace"))

    if not isinstance(payload, dict):
        raise ValueError("Internal model call did not return a JSON object payload")

    choices = payload.get("choices", [])
    if not choices:
        raise ValueError("Internal model call returned no choices")

    message = choices[0].get("message", {}) or {}
    content = message.get("content") or message.get("reasoning_content") or ""
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") in {"text", "output_text"}:
                    parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)

    if not isinstance(content, str) or not content.strip():
        raise ValueError("Internal model call returned empty content")
    return content.strip()


def should_activate_science_orchestration(
    model: dict[str, Any],
    metadata: dict[str, Any],
) -> bool:
    if metadata.get("science_orchestration_internal"):
        return False
    params = metadata.get("params", {}) or {}
    if params.get("working_mode") != "general_science":
        return False
    if params.get("function_calling") != "native":
        return False

    capability_enabled: Optional[bool] = None
    metadata_capability_sources = (
        metadata.get("persona_effective_capabilities"),
        (metadata.get("persona_requested_defaults") or {}).get("capabilities"),
        (metadata.get("persona_snapshot") or {}).get("capabilities"),
    )
    for capabilities in metadata_capability_sources:
        if isinstance(capabilities, dict) and "science_orchestration" in capabilities:
            capability_enabled = bool(capabilities.get("science_orchestration"))
            break

    if capability_enabled is None:
        capabilities = (
            (model.get("info", {}).get("meta", {}).get("capabilities") or {})
            if isinstance(model, dict)
            else {}
        )
        capability_enabled = bool(capabilities.get("science_orchestration"))

    return bool(capability_enabled)


def _classify_science_request(
    user_prompt: str,
    *,
    research_mode: str,
) -> dict[str, Any]:
    normalized = str(user_prompt or "").strip().lower()
    word_count = len(normalized.split())
    if any(hint in normalized for hint in SCIENCE_TERMINAL_TASK_HINTS):
        classification = "terminal_task"
    elif any(hint in normalized for hint in SCIENCE_DEEP_RESEARCH_HINTS):
        classification = "deep_research" if research_mode == "deep" else "literature_review"
    elif research_mode == "deep" and (word_count >= 20 or normalized.count("?") > 1):
        classification = "deep_research"
    elif any(hint in normalized for hint in SCIENCE_LIGHT_LOOKUP_HINTS) and research_mode == "light":
        classification = "light_lookup"
    elif research_mode == "light":
        classification = "light_lookup"
    else:
        classification = "literature_review"

    confidence = 0.86 if classification != "light_lookup" else 0.92
    return {
        "classification": classification,
        "confidence": confidence,
        "biomedical": any(hint in normalized for hint in SCIENCE_BIOMEDICAL_HINTS),
        "needs_terminal": classification == "terminal_task",
    }


async def _emit_phase_status(
    event_emitter,
    phase: str,
    *,
    done: bool,
    detail: str,
) -> None:
    if not event_emitter:
        return
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "science_orchestration",
                "phase": phase,
                "description": SCIENCE_ORCHESTRATION_PHASE_LABELS.get(phase, phase),
                "detail": detail,
                "done": done,
            },
        }
    )


def _build_grounding_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"discovery": 0, "abstract": 0, "full_text": 0}
    for item in items:
        scope = str(item.get("reasoning_scope") or "bibliographic_discovery")
        if scope == "full_text_grounded":
            summary["full_text"] += 1
        elif scope == "abstract_grounded":
            summary["abstract"] += 1
        else:
            summary["discovery"] += 1
    return summary


def _record_science_runtime_event(metadata: dict[str, Any], model_id: str, telemetry: dict[str, Any]) -> None:
    if not runtime_telemetry.is_enabled():
        return
    runtime_telemetry.record(
        kind="science_orchestration",
        payload=telemetry,
        chat_id=metadata.get("chat_id"),
        message_id=metadata.get("message_id"),
        user_id=metadata.get("user_id"),
        model_id=model_id,
    )


def _choose_source_order(*, biomedical: bool) -> list[str]:
    if biomedical:
        return ["pubmed", "europe-pmc", "crossref", "doi", "openalex"]
    return ["openalex", "crossref", "doi", "pubmed", "europe-pmc"]


async def _gather_science_evidence(
    *,
    request: Request,
    user_prompt: str,
    metadata: dict[str, Any],
    user: UserModel,
    biomedical: bool,
) -> dict[str, Any]:
    source_settings = get_scholarly_source_settings(request.app.state.config)
    enabled_sources = set(list_enabled_scholarly_runtime_sources(request.app.state.config))
    source_order = [source_id for source_id in _choose_source_order(biomedical=biomedical) if source_id in enabled_sources]

    local_evidence = None
    if "medicine" in set((metadata.get("params", {}) or {}).get("science_attached_corpora") or []):
        local_evidence = await asyncio.to_thread(
            assess_medical_corpus_sufficiency,
            query=user_prompt,
            config_or_path=request.app.state.config,
        )

    scholarly_results = []
    all_items: list[dict[str, Any]] = []
    for source_id in source_order:
        result = await execute_scholarly_runtime_query(
            source_id,
            query=user_prompt if source_id != "doi" else None,
            doi=user_prompt if source_id == "doi" and re.search(r"\b10\.\d{4,9}/\S+\b", user_prompt) else None,
            max_results=4 if source_id != "doi" else 1,
            settings=(source_settings.get("sources") or {}).get(source_id),
            fallback_contact_email=((source_settings.get("sources") or {}).get(source_id) or {}).get(
                "contact_email"
            ),
        )
        scholarly_results.append(result)
        all_items.extend(result.get("items") or [])

    web_results = []
    features = metadata.get("features", {}) or {}
    if not all_items and features.get("web_search") and getattr(request.app.state.config, "ENABLE_WEB_SEARCH", False):
        try:
            engine = request.app.state.config.WEB_SEARCH_ENGINE
            searched = await asyncio.to_thread(search_web, request, engine, user_prompt, user)
            for result in (searched or [])[:3]:
                web_results.append(
                    {
                        "title": result.title,
                        "url": result.link,
                        "snippet": result.snippet,
                    }
                )
        except Exception as exc:
            log.warning("Science web fallback failed: %s", exc)

    grounding_summary = _build_grounding_summary(all_items)
    return {
        "local_evidence": local_evidence,
        "scholarly_results": scholarly_results,
        "all_items": all_items,
        "web_results": web_results,
        "grounding_summary": grounding_summary,
    }


def _check_science_evidence(gathered: dict[str, Any]) -> dict[str, Any]:
    all_items = list(gathered.get("all_items") or [])
    grounding_summary = gathered.get("grounding_summary") or {}
    local_evidence = gathered.get("local_evidence") or {}

    evidence_basis = "bibliographic discovery only"
    if grounding_summary.get("full_text"):
        evidence_basis = "includes full-text-grounded records"
    elif grounding_summary.get("abstract"):
        evidence_basis = "abstract-grounded"
    elif all_items:
        evidence_basis = "metadata-grounded"
    elif local_evidence.get("decision") == "use_corpus_only":
        evidence_basis = "local corpus grounded"

    sufficient = bool(
        grounding_summary.get("full_text")
        or grounding_summary.get("abstract")
        or local_evidence.get("decision") in {"use_corpus_only", "use_corpus_plus_web"}
    )
    return {
        "sufficient": sufficient,
        "evidence_basis": evidence_basis,
        "grounding_summary": grounding_summary,
        "needs_warning": evidence_basis != "includes full-text-grounded records",
    }


async def _call_science_model(
    *,
    request: Request,
    user: UserModel,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    metadata_label: str,
) -> str:
    payload = {
        "model": model_id,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "metadata": {
            "science_orchestration_internal": metadata_label,
        },
    }
    response = await generate_chat_completion(
        request,
        payload,
        user,
        bypass_system_prompt=True,
    )
    return _extract_response_text(response)


async def _build_science_fallback_response(
    *,
    request: Request,
    user: UserModel,
    model: dict[str, Any],
    user_prompt: str,
    fallback_reason: str,
    evidence_basis: str,
) -> dict[str, Any]:
    fallback_prompt = (
        "You are in Ariadne's science_fallback_chat path. Full science orchestration did not run or could not complete. "
        "Start your answer with a short explicit warning that says science fallback mode is active and name the current evidence basis. "
        "Do not pretend that scholarly or full-text grounding happened if it did not. Be concise, cautious, and useful."
    )
    content = await _call_science_model(
        request=request,
        user=user,
        model_id=model["id"],
        system_prompt=fallback_prompt,
        user_prompt=(
            f"Fallback reason: {fallback_reason}\n"
            f"Current evidence basis: {evidence_basis}\n\n"
            f"User request:\n{user_prompt}"
        ),
        metadata_label=f"fallback:{fallback_reason}",
    )
    if "science fallback mode" not in content.lower():
        content = (
            f"Science fallback mode is active. Current evidence basis: {evidence_basis}.\n\n{content}"
        )
    return build_synthetic_chat_response(content, model["id"])


def _render_science_evidence_bundle(
    *,
    gathered: dict[str, Any],
    checked: dict[str, Any],
) -> str:
    lines = [
        f"Evidence basis: {checked.get('evidence_basis')}.",
        f"Grounding summary: {json.dumps(checked.get('grounding_summary') or {}, ensure_ascii=False)}",
    ]
    local_evidence = gathered.get("local_evidence")
    if isinstance(local_evidence, dict) and local_evidence:
        lines.append(
            "Medical corpus gate: "
            + json.dumps(
                {
                    "decision": local_evidence.get("decision"),
                    "fallback_reason": local_evidence.get("fallback_reason"),
                    "usable_anchor_count": local_evidence.get("usable_anchor_count"),
                    "relevance_score": local_evidence.get("relevance_score"),
                    "freshness_score": local_evidence.get("freshness_score"),
                    "topical_fit": local_evidence.get("topical_fit"),
                },
                ensure_ascii=False,
            )
        )
    trimmed_results = []
    for result in gathered.get("scholarly_results") or []:
        items = []
        for item in (result.get("items") or [])[:4]:
            items.append(
                {
                    "title": item.get("title"),
                    "year": item.get("year"),
                    "venue": item.get("venue"),
                    "grounding_level": item.get("grounding_level"),
                    "reasoning_scope": item.get("reasoning_scope"),
                    "study_signal": item.get("study_signal"),
                    "doi": item.get("doi"),
                    "pmid": item.get("pmid"),
                    "pmcid": item.get("pmcid"),
                    "url": item.get("url"),
                }
            )
        trimmed_results.append(
            {
                "source_id": result.get("source_id"),
                "warnings": result.get("warnings"),
                "items": items,
            }
        )
    lines.append("Scholarly results:\n" + json.dumps(trimmed_results, ensure_ascii=False, indent=2))
    if gathered.get("web_results"):
        lines.append(
            "Web fallback:\n"
            + json.dumps(gathered.get("web_results")[:3], ensure_ascii=False, indent=2)
        )
    return "\n".join(lines)


async def maybe_run_science_orchestration(
    *,
    request: Request,
    form_data: dict[str, Any],
    user: UserModel,
    metadata: dict[str, Any],
    model: dict[str, Any],
    task_model: Optional[dict[str, Any]],
    features: dict[str, Any],
    event_emitter,
) -> Optional[dict[str, Any]]:
    if not should_activate_science_orchestration(model, metadata):
        return None

    messages = form_data.get("messages", []) or []
    user_prompt = get_last_user_message(messages) or ""
    if not user_prompt:
        return None

    telemetry: dict[str, Any] = {
        "active": False,
        "classification": None,
        "science_research_mode": normalize_science_research_mode(
            (metadata.get("params", {}) or {}).get("science_research_mode")
        ),
        "attached_corpora": list(
            (metadata.get("params", {}) or {}).get("science_attached_corpora") or []
        ),
        "fallback_reason": None,
        "phase_timings_ms": {},
        "evidence_basis": None,
        "grounding_summary": {},
    }

    started_at = time.perf_counter()
    await _emit_phase_status(
        event_emitter,
        "classification",
        done=False,
        detail="Determining whether this needs a bounded lookup or the full science harness.",
    )
    classification = _classify_science_request(
        user_prompt,
        research_mode=telemetry["science_research_mode"],
    )
    telemetry["classification"] = classification
    telemetry["phase_timings_ms"]["classification"] = int((time.perf_counter() - started_at) * 1000)
    await _emit_phase_status(
        event_emitter,
        "classification",
        done=True,
        detail=f"Selected {classification['classification']} path.",
    )

    if classification["classification"] == "light_lookup":
        telemetry["fallback_reason"] = "light_lookup_passthrough"
        metadata["science_orchestration_telemetry"] = telemetry
        _record_science_runtime_event(metadata, model["id"], telemetry)
        return None

    telemetry["active"] = True
    try:
        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "planning",
            done=False,
            detail="Locking source order, corpus usage, and evidence requirements.",
        )
        _plan = {
            "classification": classification["classification"],
            "use_local_medicine": "medicine"
            in set((metadata.get("params", {}) or {}).get("science_attached_corpora") or []),
            "source_order": _choose_source_order(biomedical=classification["biomedical"]),
        }
        telemetry["phase_timings_ms"]["planning"] = int((time.perf_counter() - started_at) * 1000)
        await _emit_phase_status(
            event_emitter,
            "planning",
            done=True,
            detail="Evidence plan locked.",
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "gathering",
            done=False,
            detail="Collecting local corpus signal, scholarly records, and fallback discovery when needed.",
        )
        gathered = await _gather_science_evidence(
            request=request,
            user_prompt=user_prompt,
            metadata=metadata,
            user=user,
            biomedical=classification["biomedical"],
        )
        telemetry["phase_timings_ms"]["gathering"] = int((time.perf_counter() - started_at) * 1000)
        telemetry["grounding_summary"] = gathered.get("grounding_summary") or {}
        await _emit_phase_status(
            event_emitter,
            "gathering",
            done=True,
            detail="Evidence bundle collected.",
        )

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "checking",
            done=False,
            detail="Checking whether the evidence basis is strong enough for synthesis.",
        )
        checked = _check_science_evidence(gathered)
        telemetry["phase_timings_ms"]["checking"] = int((time.perf_counter() - started_at) * 1000)
        telemetry["evidence_basis"] = checked.get("evidence_basis")
        await _emit_phase_status(
            event_emitter,
            "checking",
            done=True,
            detail=f"Evidence basis assessed as {checked.get('evidence_basis')}.",
        )

        if not checked.get("sufficient"):
            telemetry["fallback_reason"] = "insufficient_evidence"
            metadata["science_orchestration_telemetry"] = telemetry
            _record_science_runtime_event(metadata, model["id"], telemetry)
            response = await _build_science_fallback_response(
                request=request,
                user=user,
                model=model,
                user_prompt=user_prompt,
                fallback_reason="insufficient_evidence",
                evidence_basis=checked.get("evidence_basis") or "limited",
            )
            metadata["science_orchestration_response"] = response
            return {"response": response, "events": []}

        started_at = time.perf_counter()
        await _emit_phase_status(
            event_emitter,
            "synthesis",
            done=False,
            detail="Synthesizing an evidence-shaped answer with explicit grounding limits.",
        )
        evidence_bundle = _render_science_evidence_bundle(gathered=gathered, checked=checked)
        system_prompt = (
            "You are Ariadne's General Science synthesis layer. Answer only from the supplied evidence bundle. "
            "Never treat bibliographic discovery as substantive proof. If the bundle is abstract-grounded, say that explicitly. "
            "If any claim needs full text but the bundle lacks it, say so rather than over-claiming. "
            "Be concise, structured, and explicit about limitations."
        )
        synthesized = await _call_science_model(
            request=request,
            user=user,
            model_id=model["id"],
            system_prompt=system_prompt,
            user_prompt=f"User request:\n{user_prompt}\n\nEvidence bundle:\n{evidence_bundle}",
            metadata_label=f"synthesis:{classification['classification']}",
        )
        if checked.get("needs_warning"):
            synthesized = (
                f"Evidence basis: {checked.get('evidence_basis')}.\n"
                "This answer is not fully full-text-grounded.\n\n"
                f"{synthesized}"
            )
        response = build_synthetic_chat_response(synthesized, model["id"])
        telemetry["phase_timings_ms"]["synthesis"] = int((time.perf_counter() - started_at) * 1000)
        metadata["science_orchestration_telemetry"] = telemetry
        metadata["science_orchestration_response"] = response
        _record_science_runtime_event(metadata, model["id"], telemetry)
        await _emit_phase_status(
            event_emitter,
            "synthesis",
            done=True,
            detail="Science synthesis ready.",
        )
        return {"response": response, "events": []}
    except Exception as exc:
        log.warning("Science orchestration fell back after error: %s", exc)
        telemetry["fallback_reason"] = "orchestrator_error"
        metadata["science_orchestration_telemetry"] = telemetry
        _record_science_runtime_event(metadata, model["id"], telemetry)
        response = await _build_science_fallback_response(
            request=request,
            user=user,
            model=model,
            user_prompt=user_prompt,
            fallback_reason="orchestrator_error",
            evidence_basis=telemetry.get("evidence_basis") or "limited",
        )
        metadata["science_orchestration_response"] = response
        return {"response": response, "events": []}
