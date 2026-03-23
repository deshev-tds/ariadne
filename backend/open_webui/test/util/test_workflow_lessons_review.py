import json
from pathlib import Path

import pytest

import open_webui.utils.workflow_lessons as workflow_lessons
import open_webui.utils.workflow_lessons_review as workflow_lessons_review


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _runtime_observed_row(
    *,
    lesson_id: str,
    pattern_key: str,
    source_turn_id: str,
    updated_at: str,
) -> workflow_lessons.WorkflowLessonRow:
    pattern_payloads = {
        "research_web_evidence_grounded_turn": {
            "condition_codes": [
                "research_turn_used_bounded_web_evidence_tools",
                "answer_depended_on_fetched_or_queried_web_sources",
            ],
            "prefer_codes": [
                "fetch_or_store_concrete_sources_before_synthesis",
                "query_bounded_web_evidence_before_unsupported_synthesis",
            ],
            "avoid_codes": [
                "avoid_treating_broad_search_alone_as_sufficient_evidence",
            ],
            "signal_codes": [
                "web_evidence_tools_used_in_research_turn",
            ],
        },
        "offsec_guided_bounded_turn": {
            "condition_codes": [
                "guided_offsec_run_active",
                "workflow_uses_bounded_execution_or_evidence_steps",
            ],
            "prefer_codes": [
                "keep_execution_bounded_to_active_step",
                "register_plan_and_step_results_instead_of_free_form_loops",
            ],
            "avoid_codes": [
                "avoid_execution_drift_outside_guided_step",
            ],
            "signal_codes": [
                "guided_offsec_state_or_guided_tool_sequence_observed",
            ],
        },
    }
    return workflow_lessons.build_registry_backed_workflow_lesson_row(
        lesson_id=lesson_id,
        status="observed",
        pattern_key=pattern_key,
        source_turn_ids=[source_turn_id],
        updated_at=updated_at,
        confidence_note="test",
        origin=f"test:{pattern_key}",
        **pattern_payloads[pattern_key],
    )


def test_load_workflow_lesson_taxonomy_registry_rejects_duplicate_pattern_key(tmp_path):
    registry_path = tmp_path / "taxonomy-registry.json"
    _write_json(
        registry_path,
        {
            "registry_version": "workflow_lessons_taxonomy_v1",
            "patterns": [
                {
                    "pattern_key": "research_web_evidence_grounded_turn",
                    "working_mode": "science",
                    "workflow_family": "research",
                    "title": "One",
                    "conditions": {"a": "A"},
                    "prefer_actions": {"b": "B"},
                    "avoid_actions": {"c": "C"},
                    "signals": {"d": "D"},
                },
                {
                    "pattern_key": "research_web_evidence_grounded_turn",
                    "working_mode": "science",
                    "workflow_family": "research",
                    "title": "Two",
                    "conditions": {"a2": "A2"},
                    "prefer_actions": {"b2": "B2"},
                    "avoid_actions": {"c2": "C2"},
                    "signals": {"d2": "D2"},
                },
            ],
        },
    )

    with pytest.raises(workflow_lessons.WorkflowLessonsError, match="Duplicate `pattern_key`"):
        workflow_lessons.load_workflow_lesson_taxonomy_registry(registry_path)


def test_registry_backed_row_rejects_unknown_code():
    with pytest.raises(workflow_lessons.WorkflowLessonsError, match="does not allow"):
        workflow_lessons.build_registry_backed_workflow_lesson_row(
            lesson_id="invalid",
            status="observed",
            pattern_key="research_web_evidence_grounded_turn",
            condition_codes=["research_turn_used_bounded_web_evidence_tools", "not_allowed"],
            prefer_codes=[
                "fetch_or_store_concrete_sources_before_synthesis",
                "query_bounded_web_evidence_before_unsupported_synthesis",
            ],
            avoid_codes=["avoid_treating_broad_search_alone_as_sufficient_evidence"],
            signal_codes=["web_evidence_tools_used_in_research_turn"],
            source_turn_ids=["chat:msg"],
            updated_at="2026-03-23T19:00:00Z",
        )


def test_workflow_lesson_canonical_signature_ignores_surface_fields_when_codes_match():
    base = _runtime_observed_row(
        lesson_id="lesson-a",
        pattern_key="research_web_evidence_grounded_turn",
        source_turn_id="chat-a:msg-a",
        updated_at="2026-03-23T19:00:00Z",
    )
    jittered = workflow_lessons.WorkflowLessonRow(
        lesson_id="lesson-b",
        status="observed",
        working_mode=base.working_mode,
        workflow_family=base.workflow_family,
        title="Different Surface Title",
        applies_when=("different wording one", "different wording two"),
        prefer=("different prefer", "different prefer two"),
        avoid=("different avoid",),
        signal=("different signal",),
        source_turn_ids=("chat-b:msg-b",),
        updated_at="2026-03-23T19:05:00Z",
        registry_version=base.registry_version,
        pattern_key=base.pattern_key,
        condition_codes=base.condition_codes,
        prefer_codes=base.prefer_codes,
        avoid_codes=base.avoid_codes,
        signal_codes=base.signal_codes,
        confidence_note=base.confidence_note,
        origin=base.origin,
    )

    assert workflow_lessons.workflow_lesson_canonical_signature(base) == workflow_lessons.workflow_lesson_canonical_signature(jittered)


def test_review_runtime_workflow_lessons_emits_repeated_candidate_for_two_chats(tmp_path):
    runtime_root = tmp_path / "_workflow_lessons_runtime"
    catalog_path = runtime_root / "internal" / "lessons-catalog.jsonl"
    rows = [
        _runtime_observed_row(
            lesson_id="obs-a",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-a:msg-a",
            updated_at="2026-03-23T19:00:00Z",
        ),
        _runtime_observed_row(
            lesson_id="obs-b",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-b:msg-b",
            updated_at="2026-03-23T19:10:00Z",
        ),
    ]
    workflow_lessons.write_workflow_lessons_catalog(catalog_path, rows)

    summary = workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidates = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )

    assert summary.repeated_candidates == 1
    assert candidates[0]["pattern_key"] == "research_web_evidence_grounded_turn"
    assert candidates[0]["distinct_chat_count"] == 2
    assert candidates[0]["occurrence_count"] == 2
    digest = (runtime_root / "review" / "latest.md").read_text(encoding="utf-8")
    assert candidates[0]["candidate_id"] in digest


def test_review_runtime_workflow_lessons_same_chat_does_not_become_repeated(tmp_path):
    runtime_root = tmp_path / "_workflow_lessons_runtime"
    catalog_path = runtime_root / "internal" / "lessons-catalog.jsonl"
    rows = [
        _runtime_observed_row(
            lesson_id="obs-a",
            pattern_key="offsec_guided_bounded_turn",
            source_turn_id="chat-a:msg-a",
            updated_at="2026-03-23T19:00:00Z",
        ),
        _runtime_observed_row(
            lesson_id="obs-b",
            pattern_key="offsec_guided_bounded_turn",
            source_turn_id="chat-a:msg-b",
            updated_at="2026-03-23T19:05:00Z",
        ),
    ]
    workflow_lessons.write_workflow_lessons_catalog(catalog_path, rows)

    summary = workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidates = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )

    assert summary.repeated_candidates == 0
    assert candidates == []


def test_export_workflow_lesson_candidate_writes_curated_promoted_row_and_serving(tmp_path):
    runtime_root = tmp_path / "_workflow_lessons_runtime"
    runtime_catalog = runtime_root / "internal" / "lessons-catalog.jsonl"
    rows = [
        _runtime_observed_row(
            lesson_id="obs-a",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-a:msg-a",
            updated_at="2026-03-23T19:00:00Z",
        ),
        _runtime_observed_row(
            lesson_id="obs-b",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-b:msg-b",
            updated_at="2026-03-23T19:10:00Z",
        ),
    ]
    workflow_lessons.write_workflow_lessons_catalog(runtime_catalog, rows)
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidate = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )[0]

    curated_root = tmp_path / "workflow_lessons"
    summary = workflow_lessons_review.export_workflow_lesson_candidate(
        runtime_root=runtime_root,
        candidate_id=candidate["candidate_id"],
        target_lesson_id="research_web_evidence_promoted",
        curated_root=curated_root,
    )

    exported_rows = workflow_lessons.load_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl"
    )
    assert summary.replaced is False
    assert exported_rows[0].status == "promoted"
    assert exported_rows[0].pattern_key == "research_web_evidence_grounded_turn"
    lesson_card = curated_root / "_serving" / "lessons" / "research_web_evidence_promoted.md"
    assert lesson_card.exists()


def test_export_workflow_lesson_candidate_rejects_duplicate_signature_without_replace(tmp_path):
    runtime_root = tmp_path / "_workflow_lessons_runtime"
    runtime_catalog = runtime_root / "internal" / "lessons-catalog.jsonl"
    rows = [
        _runtime_observed_row(
            lesson_id="obs-a",
            pattern_key="offsec_guided_bounded_turn",
            source_turn_id="chat-a:msg-a",
            updated_at="2026-03-23T19:00:00Z",
        ),
        _runtime_observed_row(
            lesson_id="obs-b",
            pattern_key="offsec_guided_bounded_turn",
            source_turn_id="chat-b:msg-b",
            updated_at="2026-03-23T19:10:00Z",
        ),
    ]
    workflow_lessons.write_workflow_lessons_catalog(runtime_catalog, rows)
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidate = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )[0]

    curated_root = tmp_path / "workflow_lessons"
    existing = workflow_lessons.build_registry_backed_workflow_lesson_row(
        lesson_id="existing_offsec_promoted",
        status="promoted",
        pattern_key="offsec_guided_bounded_turn",
        condition_codes=candidate["condition_codes"],
        prefer_codes=candidate["prefer_codes"],
        avoid_codes=candidate["avoid_codes"],
        signal_codes=candidate["signal_codes"],
        source_turn_ids=["seed:offsec"],
        updated_at="2026-03-23T18:00:00Z",
        confidence_note="seed",
        origin="seed:test",
    )
    workflow_lessons.write_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl",
        [existing],
    )

    with pytest.raises(
        workflow_lessons.WorkflowLessonsError, match="same canonical lesson signature"
    ):
        workflow_lessons_review.export_workflow_lesson_candidate(
            runtime_root=runtime_root,
            candidate_id=candidate["candidate_id"],
            target_lesson_id="offsec_guided_promoted",
            curated_root=curated_root,
        )

    summary = workflow_lessons_review.export_workflow_lesson_candidate(
        runtime_root=runtime_root,
        candidate_id=candidate["candidate_id"],
        target_lesson_id="offsec_guided_promoted",
        curated_root=curated_root,
        replace=True,
    )
    exported_rows = workflow_lessons.load_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl"
    )

    assert summary.replaced is True
    assert [row.lesson_id for row in exported_rows] == ["offsec_guided_promoted"]
