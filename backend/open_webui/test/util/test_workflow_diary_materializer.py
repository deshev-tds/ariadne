import json
from pathlib import Path

import open_webui.utils.workflow_diary_materializer as materializer
import open_webui.utils.workflow_lessons as workflow_lessons


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_packet(
    artifacts_root: Path,
    *,
    chat_id: str,
    chat_label: str,
    message_id: str,
    packet: dict,
) -> Path:
    path = (
        artifacts_root
        / f"{chat_id}__{chat_label}"
        / "workflow_diary"
        / "packets"
        / f"{message_id}.json"
    )
    _write_json(path, packet)
    return path


def _packet(
    *,
    chat_id: str,
    message_id: str,
    working_mode: str = "science",
    tool_names: list[str] | None = None,
    tool_call_count: int | None = None,
    tool_names_partial: bool = False,
    tooling_source: str = "output",
    turn_recap_present: bool = False,
    termination_cause: dict | None = None,
    offsec_snapshot: dict | None = None,
    content_preview: str = "assistant content",
    capture_reasons: list[str] | None = None,
) -> dict:
    observed_tool_names = tool_names or []
    return {
        "version": 1,
        "kind": "workflow_capture",
        "chat_id": chat_id,
        "message_id": message_id,
        "captured_at": "2026-03-23T18:00:00Z",
        "request_context": {
            "working_mode": working_mode,
            "local_corpus_mode": "prefer" if working_mode == "science" else "off",
            "function_calling": "native",
        },
        "assistant_snapshot": {
            "content_preview": content_preview,
            "turn_recap_present": turn_recap_present,
            "termination_cause": termination_cause,
            "artifact_refs": [],
        },
        "tooling": {
            "observed_tool_names": observed_tool_names,
            "tool_call_count": (
                tool_call_count
                if tool_call_count is not None
                else len(observed_tool_names)
            ),
            "tool_kinds_count": len(set(observed_tool_names)),
            "tool_names_partial": tool_names_partial,
            "source": tooling_source,
        },
        "telemetry_presence": {
            "memory": {"present": False},
            "tool_journey": {"present": False},
            "prompt": {"present": False},
        },
        "offsec_snapshot": offsec_snapshot or {"present": False},
        "capture_reasons": capture_reasons or ["tool_calls_in_output"],
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_materialize_workflow_diary_research_local_corpus_packet(tmp_path):
    chat_id = "chat-local"
    message_id = "msg-local"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="research-local",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            tool_names=["local_corpus_frame_problem", "local_corpus_retrieve_evidence"],
            tool_call_count=2,
            turn_recap_present=True,
        ),
    )

    summary = materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    entry_path = (
        tmp_path
        / f"{chat_id}__research-local"
        / "workflow_diary"
        / "entries"
        / f"{message_id}.json"
    )
    entry = _read_json(entry_path)
    candidate = entry["candidate_lessons"][0]
    catalog = workflow_lessons.load_workflow_lessons_catalog(
        tmp_path / "_workflow_lessons_runtime" / "internal" / "lessons-catalog.jsonl"
    )

    assert summary.entries_written == 1
    assert summary.lesson_rows_emitted == 1
    assert entry["workflow_family"] == "research"
    assert "local_corpus" in entry["workflow_tags"]
    assert "multi_tool" in entry["workflow_tags"]
    assert candidate["status"] == "observed"
    assert candidate["lesson_id"] == (
        "research_local_corpus_grounded_turn__chat-local__msg-local"
    )
    assert catalog[0].lesson_id == candidate["lesson_id"]


def test_materialize_workflow_diary_research_web_packet_with_source_diary(tmp_path):
    chat_id = "chat-web"
    message_id = "msg-web"
    packet_path = _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="research-web",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            tool_names=["search_web", "query_web_evidence"],
            tool_call_count=2,
            turn_recap_present=True,
        ),
    )
    source_diary_path = packet_path.parents[2] / "source_diary" / f"{message_id}.md"
    source_diary_path.parent.mkdir(parents=True, exist_ok=True)
    source_diary_path.write_text("# Source Diary\n", encoding="utf-8")

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    entry = _read_json(
        packet_path.parents[2] / "workflow_diary" / "entries" / f"{message_id}.json"
    )

    assert entry["workflow_family"] == "research"
    assert "web_evidence" in entry["workflow_tags"]
    assert "source_diary_available" in entry["outcome"]["success_signals"]
    assert entry["references"]["source_diary"] == f"source_diary/{message_id}.md"
    assert entry["candidate_lessons"][0]["lesson_id"] == (
        "research_web_evidence_grounded_turn__chat-web__msg-web"
    )


def test_materialize_workflow_diary_offsec_guided_packet(tmp_path):
    chat_id = "chat-offsec"
    message_id = "msg-offsec"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="offsec-guided",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            working_mode="offsec",
            tool_names=["offsec_register_plan"],
            offsec_snapshot={
                "present": True,
                "guided_run_id": "run-1",
                "active_step_id": "step-1",
                "waiting_for_confirmation": False,
                "current_step_run_command_count": 0,
                "remaining_step_run_command_budget": 3,
            },
        ),
    )

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    entry = _read_json(
        tmp_path
        / f"{chat_id}__offsec-guided"
        / "workflow_diary"
        / "entries"
        / f"{message_id}.json"
    )

    assert entry["workflow_family"] == "offsec"
    assert "guided" in entry["workflow_tags"]
    assert "guided_state_present" in entry["outcome"]["success_signals"]
    assert entry["candidate_lessons"][0]["lesson_id"] == (
        "offsec_guided_bounded_turn__chat-offsec__msg-offsec"
    )


def test_materialize_workflow_diary_general_packet_writes_entry_without_candidate(tmp_path):
    chat_id = "chat-general"
    message_id = "msg-general"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="general",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            working_mode="chat",
            tool_names=[],
            tool_call_count=0,
            tooling_source="none",
            capture_reasons=["termination_cause", "memory_telemetry"],
            termination_cause={"code": "stopped"},
        ),
    )

    summary = materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    entry = _read_json(
        tmp_path / f"{chat_id}__general" / "workflow_diary" / "entries" / f"{message_id}.json"
    )
    catalog_path = tmp_path / "_workflow_lessons_runtime" / "internal" / "lessons-catalog.jsonl"

    assert summary.entries_written == 1
    assert summary.lesson_rows_emitted == 0
    assert entry["workflow_family"] == "general"
    assert entry["candidate_lessons"] == []
    assert "termination_cause_present" in entry["outcome"]["failure_signals"]
    assert catalog_path.exists()
    assert catalog_path.read_text(encoding="utf-8") == ""


def test_materialize_workflow_diary_partial_tooling_adds_failure_signal_without_lesson(tmp_path):
    chat_id = "chat-partial"
    message_id = "msg-partial"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="partial",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            tool_names=["search_web"],
            tool_call_count=1,
            tool_names_partial=True,
            tooling_source="turn_recap",
            turn_recap_present=True,
        ),
    )

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    entry = _read_json(
        tmp_path / f"{chat_id}__partial" / "workflow_diary" / "entries" / f"{message_id}.json"
    )

    assert "partial_tooling" in entry["workflow_tags"]
    assert "partial_tool_names" in entry["outcome"]["failure_signals"]
    assert entry["candidate_lessons"] == []


def test_materialize_workflow_diary_is_idempotent(tmp_path):
    chat_id = "chat-idempotent"
    message_id = "msg-idempotent"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="idem",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            tool_names=["search_web", "query_web_evidence"],
            tool_call_count=2,
        ),
    )

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )
    entry_path = (
        tmp_path / f"{chat_id}__idem" / "workflow_diary" / "entries" / f"{message_id}.json"
    )
    catalog_path = tmp_path / "_workflow_lessons_runtime" / "internal" / "lessons-catalog.jsonl"
    first_entry = entry_path.read_text(encoding="utf-8")
    first_catalog = catalog_path.read_text(encoding="utf-8")

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )
    second_entry = entry_path.read_text(encoding="utf-8")
    second_catalog = catalog_path.read_text(encoding="utf-8")

    assert first_entry == second_entry
    assert first_catalog == second_catalog


def test_materialize_workflow_diary_same_message_id_in_two_chats_do_not_collide(tmp_path):
    message_id = "shared-message"
    for chat_id, label in (("chat-a", "alpha"), ("chat-b", "beta")):
        _write_packet(
            tmp_path,
            chat_id=chat_id,
            chat_label=label,
            message_id=message_id,
            packet=_packet(
                chat_id=chat_id,
                message_id=message_id,
                tool_names=["search_web"],
            ),
        )

    summary = materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )
    catalog = workflow_lessons.load_workflow_lessons_catalog(
        tmp_path / "_workflow_lessons_runtime" / "internal" / "lessons-catalog.jsonl"
    )

    assert summary.entries_written == 2
    assert len(catalog) == 2
    assert {row.lesson_id for row in catalog} == {
        "research_web_evidence_grounded_turn__chat-a__shared-message",
        "research_web_evidence_grounded_turn__chat-b__shared-message",
    }


def test_materialize_workflow_diary_skips_malformed_packet_and_continues(tmp_path):
    _write_packet(
        tmp_path,
        chat_id="chat-good",
        chat_label="good",
        message_id="msg-good",
        packet=_packet(
            chat_id="chat-good",
            message_id="msg-good",
            tool_names=["local_corpus_retrieve_evidence"],
        ),
    )
    bad_path = (
        tmp_path / "chat-bad__bad" / "workflow_diary" / "packets" / "msg-bad.json"
    )
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text("{not json", encoding="utf-8")

    summary = materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=tmp_path / "_workflow_lessons_runtime",
        min_age_minutes=0,
    )

    assert summary.entries_written == 1
    assert summary.entries_skipped == 1
    catalog = workflow_lessons.load_workflow_lessons_catalog(
        tmp_path / "_workflow_lessons_runtime" / "internal" / "lessons-catalog.jsonl"
    )
    assert len(catalog) == 1


def test_runtime_catalog_builds_cleanly_but_serves_no_observed_lessons(tmp_path):
    chat_id = "chat-runtime"
    message_id = "msg-runtime"
    _write_packet(
        tmp_path,
        chat_id=chat_id,
        chat_label="runtime",
        message_id=message_id,
        packet=_packet(
            chat_id=chat_id,
            message_id=message_id,
            tool_names=["search_web"],
        ),
    )
    runtime_root = tmp_path / "_workflow_lessons_runtime"

    materializer.materialize_workflow_diary(
        artifacts_root=tmp_path,
        runtime_root=runtime_root,
        min_age_minutes=0,
    )
    summary = workflow_lessons.build_workflow_lessons_serving(runtime_root)

    assert summary.lesson_count == 1
    assert summary.promoted_count == 0
    lessons_dir = runtime_root / "_serving" / "lessons"
    assert not lessons_dir.exists() or list(lessons_dir.glob("*.md")) == []
