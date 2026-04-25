from pathlib import Path
from types import SimpleNamespace

import pytest

from open_webui.retrieval.corpus_runtime import resolve_corpus_runtime
from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode
from open_webui.retrieval.medical_lane import assess_medical_corpus_sufficiency
from open_webui.retrieval.working_mode import normalize_working_mode
from open_webui.utils.science_orchestration import should_activate_science_orchestration


def test_normalize_working_mode_keeps_legacy_science_alias_but_defaults_missing_modes_to_general():
    assert normalize_working_mode("science") == "medical"
    assert normalize_working_mode(None, local_corpus_mode="auto") == "general"
    assert normalize_working_mode(None, local_corpus_mode=None) == "general"
    assert normalize_working_mode("general_science") == "general_science"
    assert normalize_working_mode("unknown", local_corpus_mode="off") == "general"
    assert normalize_working_mode("unknown", local_corpus_mode="prefer") == "general"


def test_normalize_local_corpus_mode_removes_auto_and_defaults_to_off():
    assert normalize_local_corpus_mode("prefer") == "prefer"
    assert normalize_local_corpus_mode("off") == "off"
    assert normalize_local_corpus_mode("auto") == "off"
    assert normalize_local_corpus_mode(None) == "off"
    assert normalize_local_corpus_mode("unknown") == "off"


def test_resolve_corpus_runtime_separates_general_prefer_from_medical_and_general_science(
    monkeypatch,
):
    monkeypatch.setattr(
        "open_webui.retrieval.corpus_runtime.resolve_local_corpus_root",
        lambda _config: Path("/tmp/medical-corpus"),
    )

    config = SimpleNamespace(ENABLE_LOCAL_CORPUS_TOOLS=True, NEWS_ENABLED=False)

    medical_runtime = resolve_corpus_runtime(
        config,
        {"working_mode": "medical", "local_corpus_mode": "prefer"},
    )
    assert medical_runtime.medical_enabled is True
    assert medical_runtime.medical_root == Path("/tmp/medical-corpus")
    assert medical_runtime.attached_roots == {}

    general_runtime = resolve_corpus_runtime(
        config,
        {"working_mode": "general", "local_corpus_mode": "prefer"},
    )
    assert general_runtime.medical_enabled is True
    assert general_runtime.medical_root == Path("/tmp/medical-corpus")
    assert general_runtime.attached_roots == {}

    science_runtime = resolve_corpus_runtime(
        config,
        {
            "working_mode": "general_science",
            "local_corpus_mode": "prefer",
            "science_attached_corpora": ["medicine"],
        },
    )
    assert science_runtime.medical_enabled is True
    assert science_runtime.has_attached_corpus("medicine") is True
    assert science_runtime.get_attached_root("medicine") == Path("/tmp/medical-corpus")

    detached_runtime = resolve_corpus_runtime(
        config,
        {
            "working_mode": "general_science",
            "local_corpus_mode": "prefer",
            "science_attached_corpora": [],
        },
    )
    assert detached_runtime.medical_enabled is False
    assert detached_runtime.attached_roots == {}


def test_assess_medical_corpus_sufficiency_returns_use_corpus_only(monkeypatch):
    monkeypatch.setattr(
        "open_webui.retrieval.medical_lane.frame_local_corpus_problem",
        lambda **_kwargs: {
            "status": "ok",
            "domain": "medicine",
            "domain_confidence": 0.9,
            "task_type_confidence": 0.8,
            "primary_task_type": "therapy",
            "routing_notes": ["strong topical fit"],
        },
    )
    monkeypatch.setattr(
        "open_webui.retrieval.medical_lane.local_corpus.shortlist_local_corpus_books",
        lambda **_kwargs: {
            "items": [{"book_id": "book-1"}, {"book_id": "book-2"}],
        },
    )
    monkeypatch.setattr(
        "open_webui.retrieval.medical_lane.local_corpus.retrieve_local_corpus_evidence",
        lambda **_kwargs: {
            "status": "ok",
            "items": [
                {"score": 3.4, "title": "Guide A"},
                {"score": 3.0, "title": "Guide B"},
            ],
            "evidence_sufficiency": "strong",
            "freshness_note": None,
            "answer_guidance": "Stay local",
        },
    )

    result = assess_medical_corpus_sufficiency(query="How should xylometazoline be applied?")

    assert result["status"] == "ok"
    assert result["decision"] == "use_corpus_only"
    assert result["fallback_reason"] == "none"
    assert result["usable_anchor_count"] == 2


def test_assess_medical_corpus_sufficiency_returns_skip_corpus_for_non_medical(monkeypatch):
    monkeypatch.setattr(
        "open_webui.retrieval.medical_lane.frame_local_corpus_problem",
        lambda **_kwargs: {
            "status": "ok",
            "domain": "physics",
            "domain_confidence": 0.2,
            "task_type_confidence": 0.4,
            "primary_task_type": "conceptual",
            "routing_notes": ["off-domain"],
        },
    )
    monkeypatch.setattr(
        "open_webui.retrieval.medical_lane.local_corpus.shortlist_local_corpus_books",
        lambda **_kwargs: {"items": []},
    )

    result = assess_medical_corpus_sufficiency(query="Explain Bell inequalities.")

    assert result["status"] == "ok"
    assert result["decision"] == "skip_corpus"
    assert result["fallback_reason"] == "not_medical"


def test_should_activate_science_orchestration_only_for_general_science_native():
    model = {"id": "demo", "info": {"meta": {"capabilities": {"science_orchestration": True}}}}

    assert (
        should_activate_science_orchestration(
            model,
            {
                "params": {
                    "working_mode": "general_science",
                    "function_calling": "native",
                }
            },
        )
        is True
    )
    assert (
        should_activate_science_orchestration(
            model,
            {
                "params": {
                    "working_mode": "medical",
                    "function_calling": "native",
                }
            },
        )
        is False
    )
