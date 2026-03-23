from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import open_webui.routers.workflow_lessons as workflow_lessons_router
import open_webui.utils.workflow_lessons as workflow_lessons
import open_webui.utils.workflow_lessons_review as workflow_lessons_review
from open_webui.utils.auth import get_admin_user


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


def _build_client(monkeypatch, tmp_path):
    runtime_root = tmp_path / "_workflow_lessons_runtime"
    curated_root = tmp_path / "workflow_lessons"
    registry_path = workflow_lessons.default_workflow_lesson_registry_path().resolve()

    monkeypatch.setattr(workflow_lessons_router, "_default_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(workflow_lessons_router, "_default_curated_root", lambda: curated_root)
    monkeypatch.setattr(
        workflow_lessons_router, "_default_registry_path", lambda: registry_path
    )

    app = FastAPI()
    app.include_router(
        workflow_lessons_router.router, prefix="/api/v1/workflow-lessons"
    )
    app.dependency_overrides[get_admin_user] = lambda: SimpleNamespace(
        id="admin",
        role="admin",
    )
    return TestClient(app), runtime_root, curated_root


def test_workflow_lessons_state_empty_runtime_returns_empty_lists(monkeypatch, tmp_path):
    client, runtime_root, curated_root = _build_client(monkeypatch, tmp_path)

    response = client.get("/api/v1/workflow-lessons/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_root"] == str(runtime_root)
    assert payload["curated_root"] == str(curated_root)
    assert payload["runtime"]["observed_rows"] == []
    assert payload["runtime"]["repeated_candidates"] == []
    assert payload["runtime"]["review_summary"] is None
    assert payload["runtime"]["review_digest_markdown"] is None
    assert payload["curated"]["promoted_rows"] == []


def test_workflow_lessons_state_exposes_existing_curated_signature(monkeypatch, tmp_path):
    client, runtime_root, curated_root = _build_client(monkeypatch, tmp_path)

    runtime_catalog = runtime_root / "internal" / "lessons-catalog.jsonl"
    runtime_rows = [
        _runtime_observed_row(
            lesson_id="obs-a",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-a:msg-a",
            updated_at="2026-03-23T20:00:00Z",
        ),
        _runtime_observed_row(
            lesson_id="obs-b",
            pattern_key="research_web_evidence_grounded_turn",
            source_turn_id="chat-b:msg-b",
            updated_at="2026-03-23T20:05:00Z",
        ),
    ]
    workflow_lessons.write_workflow_lessons_catalog(runtime_catalog, runtime_rows)
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)

    promoted = workflow_lessons.build_registry_backed_workflow_lesson_row(
        lesson_id="research_web_evidence_before_synthesis",
        status="promoted",
        pattern_key="research_web_evidence_grounded_turn",
        condition_codes=[
            "research_turn_used_bounded_web_evidence_tools",
            "answer_depended_on_fetched_or_queried_web_sources",
        ],
        prefer_codes=[
            "fetch_or_store_concrete_sources_before_synthesis",
            "query_bounded_web_evidence_before_unsupported_synthesis",
        ],
        avoid_codes=["avoid_treating_broad_search_alone_as_sufficient_evidence"],
        signal_codes=["web_evidence_tools_used_in_research_turn"],
        source_turn_ids=["seed:msg"],
        updated_at="2026-03-23T19:50:00Z",
        confidence_note="seed",
        origin="seed:test",
    )
    workflow_lessons.write_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl",
        [promoted],
    )

    response = client.get("/api/v1/workflow-lessons/state")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["runtime"]["observed_rows"]) == 2
    assert len(payload["runtime"]["repeated_candidates"]) == 1
    assert payload["runtime"]["repeated_candidates"][0]["existing_curated_lesson_id"] == (
        "research_web_evidence_before_synthesis"
    )
    assert payload["runtime"]["repeated_candidates"][0]["can_promote"] is False
    assert len(payload["curated"]["promoted_rows"]) == 1
    assert payload["curated"]["promoted_rows"][0]["can_unpromote"] is False
    assert "CLI-only in V1" in payload["curated"]["promoted_rows"][0]["unpromote_reason"]


def test_workflow_lessons_review_endpoint_writes_repeated_candidates(monkeypatch, tmp_path):
    client, runtime_root, _ = _build_client(monkeypatch, tmp_path)

    workflow_lessons.write_workflow_lessons_catalog(
        runtime_root / "internal" / "lessons-catalog.jsonl",
        [
            _runtime_observed_row(
                lesson_id="obs-a",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-a:msg-a",
                updated_at="2026-03-23T20:00:00Z",
            ),
            _runtime_observed_row(
                lesson_id="obs-b",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-b:msg-b",
                updated_at="2026-03-23T20:05:00Z",
            ),
        ],
    )

    response = client.post("/api/v1/workflow-lessons/review")

    assert response.status_code == 200
    payload = response.json()
    assert payload["review_summary"]["repeated_candidates"] == 1
    assert payload["state"]["runtime"]["review_digest_markdown"]
    assert (runtime_root / "internal" / "repeated-candidates.jsonl").exists()
    assert (runtime_root / "review" / "latest.md").exists()


def test_workflow_lessons_promote_endpoint_exports_promoted_row(monkeypatch, tmp_path):
    client, runtime_root, curated_root = _build_client(monkeypatch, tmp_path)

    workflow_lessons.write_workflow_lessons_catalog(
        runtime_root / "internal" / "lessons-catalog.jsonl",
        [
            _runtime_observed_row(
                lesson_id="obs-a",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-a:msg-a",
                updated_at="2026-03-23T20:00:00Z",
            ),
            _runtime_observed_row(
                lesson_id="obs-b",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-b:msg-b",
                updated_at="2026-03-23T20:05:00Z",
            ),
        ],
    )
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidate_id = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )[0]["candidate_id"]

    response = client.post(
        "/api/v1/workflow-lessons/promote",
        json={
            "candidate_id": candidate_id,
            "target_lesson_id": "research_web_evidence_before_synthesis",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["export_summary"]["target_lesson_id"] == (
        "research_web_evidence_before_synthesis"
    )
    promoted_rows = workflow_lessons.load_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl"
    )
    assert [row.lesson_id for row in promoted_rows] == [
        "research_web_evidence_before_synthesis"
    ]
    assert (
        curated_root / "_serving" / "lessons" / "research_web_evidence_before_synthesis.md"
    ).exists()


def test_workflow_lessons_promote_endpoint_rejects_invalid_or_duplicate(monkeypatch, tmp_path):
    client, runtime_root, curated_root = _build_client(monkeypatch, tmp_path)

    workflow_lessons.write_workflow_lessons_catalog(
        runtime_root / "internal" / "lessons-catalog.jsonl",
        [
            _runtime_observed_row(
                lesson_id="obs-a",
                pattern_key="offsec_guided_bounded_turn",
                source_turn_id="chat-a:msg-a",
                updated_at="2026-03-23T20:00:00Z",
            ),
            _runtime_observed_row(
                lesson_id="obs-b",
                pattern_key="offsec_guided_bounded_turn",
                source_turn_id="chat-b:msg-b",
                updated_at="2026-03-23T20:05:00Z",
            ),
        ],
    )
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidate_id = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )[0]["candidate_id"]

    empty_target = client.post(
        "/api/v1/workflow-lessons/promote",
        json={"candidate_id": candidate_id, "target_lesson_id": "   "},
    )
    assert empty_target.status_code == 400

    unknown_candidate = client.post(
        "/api/v1/workflow-lessons/promote",
        json={"candidate_id": "repeat_offsec_missing", "target_lesson_id": "offsec_guided_v1"},
    )
    assert unknown_candidate.status_code == 400
    assert "Unknown repeated candidate id" in unknown_candidate.json()["detail"]

    existing = workflow_lessons.build_registry_backed_workflow_lesson_row(
        lesson_id="existing_offsec_promoted",
        status="promoted",
        pattern_key="offsec_guided_bounded_turn",
        condition_codes=[
            "guided_offsec_run_active",
            "workflow_uses_bounded_execution_or_evidence_steps",
        ],
        prefer_codes=[
            "keep_execution_bounded_to_active_step",
            "register_plan_and_step_results_instead_of_free_form_loops",
        ],
        avoid_codes=["avoid_execution_drift_outside_guided_step"],
        signal_codes=["guided_offsec_state_or_guided_tool_sequence_observed"],
        source_turn_ids=["seed:offsec"],
        updated_at="2026-03-23T19:00:00Z",
        confidence_note="seed",
        origin="seed:test",
    )
    workflow_lessons.write_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl",
        [existing],
    )

    duplicate = client.post(
        "/api/v1/workflow-lessons/promote",
        json={
            "candidate_id": candidate_id,
            "target_lesson_id": "offsec_guided_bounded_turn",
        },
    )

    assert duplicate.status_code == 400
    assert "same canonical lesson signature" in duplicate.json()["detail"]


def test_workflow_lessons_unpromote_endpoint_removes_exported_row(monkeypatch, tmp_path):
    client, runtime_root, curated_root = _build_client(monkeypatch, tmp_path)

    workflow_lessons.write_workflow_lessons_catalog(
        runtime_root / "internal" / "lessons-catalog.jsonl",
        [
            _runtime_observed_row(
                lesson_id="obs-a",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-a:msg-a",
                updated_at="2026-03-23T20:00:00Z",
            ),
            _runtime_observed_row(
                lesson_id="obs-b",
                pattern_key="research_web_evidence_grounded_turn",
                source_turn_id="chat-b:msg-b",
                updated_at="2026-03-23T20:05:00Z",
            ),
        ],
    )
    workflow_lessons_review.review_runtime_workflow_lessons(runtime_root=runtime_root)
    candidate_id = workflow_lessons_review.load_repeated_candidates(
        runtime_root / "internal" / "repeated-candidates.jsonl"
    )[0]["candidate_id"]
    client.post(
        "/api/v1/workflow-lessons/promote",
        json={
            "candidate_id": candidate_id,
            "target_lesson_id": "research_web_evidence_grounded_turn",
        },
    )

    response = client.post(
        "/api/v1/workflow-lessons/unpromote",
        json={"lesson_id": "research_web_evidence_grounded_turn"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["unpromote_summary"]["lesson_id"] == "research_web_evidence_grounded_turn"
    assert payload["state"]["curated"]["promoted_rows"] == []
    assert payload["state"]["runtime"]["repeated_candidates"][0]["existing_curated_lesson_id"] is None
    assert payload["state"]["runtime"]["repeated_candidates"][0]["can_promote"] is True
    assert not (
        curated_root / "_serving" / "lessons" / "research_web_evidence_grounded_turn.md"
    ).exists()


def test_workflow_lessons_unpromote_endpoint_rejects_unknown_or_seed(monkeypatch, tmp_path):
    client, _, curated_root = _build_client(monkeypatch, tmp_path)
    workflow_lessons.write_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl",
        [],
    )

    unknown = client.post(
        "/api/v1/workflow-lessons/unpromote",
        json={"lesson_id": "missing_lesson"},
    )
    assert unknown.status_code == 400
    assert "Unknown curated lesson id" in unknown.json()["detail"]

    seed = workflow_lessons.build_registry_backed_workflow_lesson_row(
        lesson_id="offsec_consult_before_guided_plan",
        status="promoted",
        pattern_key="offsec_guided_bounded_turn",
        condition_codes=[
            "guided_offsec_run_active",
            "workflow_uses_bounded_execution_or_evidence_steps",
        ],
        prefer_codes=[
            "keep_execution_bounded_to_active_step",
            "register_plan_and_step_results_instead_of_free_form_loops",
        ],
        avoid_codes=["avoid_execution_drift_outside_guided_step"],
        signal_codes=["guided_offsec_state_or_guided_tool_sequence_observed"],
        source_turn_ids=["seed:offsec"],
        updated_at="2026-03-23T19:00:00Z",
        confidence_note="seed",
        origin="seed:project_contract",
    )
    workflow_lessons.write_workflow_lessons_catalog(
        curated_root / "internal" / "lessons-catalog.jsonl",
        [seed],
    )

    seed_response = client.post(
        "/api/v1/workflow-lessons/unpromote",
        json={"lesson_id": "offsec_consult_before_guided_plan"},
    )
    assert seed_response.status_code == 400
    assert "CLI-only in V1" in seed_response.json()["detail"]
