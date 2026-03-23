from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_webui.env import AGENTIC_ARTIFACTS_DIR
from open_webui.utils.workflow_lessons import (
    WorkflowLessonRow,
    WorkflowLessonsError,
    build_registry_backed_workflow_lesson_row,
    validate_workflow_lesson_row,
    workflow_lesson_row_to_dict,
    write_workflow_lessons_catalog,
)

log = logging.getLogger(__name__)

WORKFLOW_DIARY_ENTRY_VERSION = 1
WORKFLOW_DIARY_ENTRY_KIND = "workflow_diary_entry"

RUNTIME_WORKFLOW_LESSONS_DIRNAME = "_workflow_lessons_runtime"
RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH = Path("internal/lessons-catalog.jsonl")

RESEARCH_EXACT_TOOLS = {
    "search_web",
    "web_research_strong",
    "search_strong_sources",
    "fetch_url",
    "query_web_evidence",
}
OFFSEC_TOOLS = {
    "offsec_consult",
    "offsec_register_plan",
    "offsec_register_step_result",
    "offsec_retrieve_evidence",
    "run_command",
}

OFFSEC_GUIDED_SEQUENCE_TOOLSETS = (
    {"offsec_consult", "offsec_register_plan"},
    {"offsec_register_plan", "offsec_register_step_result"},
)

LESSON_PATTERN_RESEARCH_LOCAL = "research_local_corpus_grounded_turn"
LESSON_PATTERN_RESEARCH_WEB = "research_web_evidence_grounded_turn"
LESSON_PATTERN_OFFSEC_GUIDED = "offsec_guided_bounded_turn"

_SAFE_COMPONENT_RE = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True)
class WorkflowDiaryMaterializationSummary:
    artifacts_root: Path
    runtime_root: Path
    packets_scanned: int
    entries_written: int
    entries_skipped: int
    lesson_rows_emitted: int
    catalog_rows_written: int
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifacts_root"] = str(self.artifacts_root)
        payload["runtime_root"] = str(self.runtime_root)
        return payload


def default_runtime_workflow_lessons_root(
    artifacts_root: str | Path = AGENTIC_ARTIFACTS_DIR,
) -> Path:
    return Path(artifacts_root).expanduser().resolve() / RUNTIME_WORKFLOW_LESSONS_DIRNAME


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _parse_iso_timestamp(value: Any) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).astimezone(
            timezone.utc
        )
    except ValueError:
        return None


def _safe_lesson_component(value: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "unknown"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _load_json_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _discover_packet_paths(
    artifacts_root: Path,
    *,
    chat_id: str | None = None,
    message_id: str | None = None,
) -> list[Path]:
    if chat_id:
        pattern = f"{chat_id}__*/workflow_diary/packets/*.json"
        packet_paths = list(artifacts_root.glob(pattern))
    else:
        packet_paths = list(artifacts_root.glob("*/workflow_diary/packets/*.json"))

    packet_paths = [path for path in packet_paths if path.is_file()]
    if message_id:
        packet_paths = [path for path in packet_paths if path.stem == message_id]

    return sorted(packet_paths)


def _discover_entry_paths(artifacts_root: Path) -> list[Path]:
    return sorted(
        path
        for path in artifacts_root.glob("*/workflow_diary/entries/*.json")
        if path.is_file()
    )


def _packet_is_old_enough(path: Path, *, min_age_minutes: int) -> bool:
    if min_age_minutes <= 0:
        return True
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds >= (min_age_minutes * 60)


def _chat_dir_from_packet_path(packet_path: Path) -> Path:
    return packet_path.parents[2]


def _relative_chat_artifact_path(path: Path, *, chat_dir: Path) -> str:
    return path.relative_to(chat_dir).as_posix()


def _tool_names(packet: dict[str, Any]) -> list[str]:
    tooling = packet.get("tooling") or {}
    names = tooling.get("observed_tool_names") or []
    if not isinstance(names, list):
        return []
    return [str(name or "").strip() for name in names if str(name or "").strip()]


def _has_research_tool(tool_names: list[str]) -> bool:
    return any(
        name.startswith("local_corpus_") or name in RESEARCH_EXACT_TOOLS
        for name in tool_names
    )


def _has_local_corpus_tool(tool_names: list[str]) -> bool:
    return any(name.startswith("local_corpus_") for name in tool_names)


def _has_web_evidence_tool(tool_names: list[str]) -> bool:
    return any(name in RESEARCH_EXACT_TOOLS for name in tool_names)


def _has_offsec_tool(tool_names: list[str]) -> bool:
    return any(name.startswith("offsec_") or name in OFFSEC_TOOLS for name in tool_names)


def _has_offsec_guided_semantics(
    packet: dict[str, Any], tool_names: list[str]
) -> tuple[bool, list[str]]:
    offsec_snapshot = packet.get("offsec_snapshot") or {}
    if offsec_snapshot.get("present"):
        return True, ["offsec_guided_state_present"]

    tool_set = set(tool_names)
    for required in OFFSEC_GUIDED_SEQUENCE_TOOLSETS:
        if required.issubset(tool_set):
            return True, [f"offsec_guided_sequence:{'+'.join(sorted(required))}"]
    return False, []


def _classify_workflow_family(
    packet: dict[str, Any],
    tool_names: list[str],
    *,
    offsec_guided: bool,
    offsec_guided_reasons: list[str],
) -> tuple[str, list[str]]:
    request_context = packet.get("request_context") or {}
    offsec_snapshot = packet.get("offsec_snapshot") or {}
    working_mode = str(request_context.get("working_mode") or "").strip().lower()

    reasons: list[str] = []
    if (
        working_mode == "offsec"
        or offsec_guided
        or _has_offsec_tool(tool_names)
    ):
        if working_mode == "offsec":
            reasons.append("working_mode_offsec")
        if offsec_snapshot.get("present"):
            reasons.append("offsec_guided_state_present")
        elif offsec_guided_reasons:
            reasons.extend(offsec_guided_reasons)
        if _has_offsec_tool(tool_names):
            reasons.append("offsec_tool_observed")
        return "offsec", reasons

    if working_mode == "science" or _has_research_tool(tool_names):
        if working_mode == "science":
            reasons.append("working_mode_science")
        if _has_research_tool(tool_names):
            reasons.append("research_tool_observed")
        return "research", reasons

    reasons.append("fallback_general")
    return "general", reasons


def _workflow_tags(
    packet: dict[str, Any], tool_names: list[str], *, offsec_guided: bool
) -> list[str]:
    tags: list[str] = []
    tooling = packet.get("tooling") or {}

    if offsec_guided:
        tags.append("guided")
    if "run_command" in tool_names:
        tags.append("terminal")
    if _has_local_corpus_tool(tool_names):
        tags.append("local_corpus")
    if _has_web_evidence_tool(tool_names):
        tags.append("web_evidence")
    if int(tooling.get("tool_call_count") or 0) > 1 or int(
        tooling.get("tool_kinds_count") or 0
    ) > 1:
        tags.append("multi_tool")
    if tooling.get("tool_names_partial"):
        tags.append("partial_tooling")
    return tags


def _success_signals(
    packet: dict[str, Any],
    *,
    source_diary_path: str | None,
    offsec_guided: bool,
) -> list[str]:
    tooling = packet.get("tooling") or {}
    assistant_snapshot = packet.get("assistant_snapshot") or {}
    offsec_snapshot = packet.get("offsec_snapshot") or {}

    signals: list[str] = []
    if int(tooling.get("tool_call_count") or 0) > 0:
        signals.append("tool_calls_observed")
    if assistant_snapshot.get("turn_recap_present"):
        signals.append("turn_recap_present")
    if offsec_snapshot.get("present"):
        signals.append("guided_state_present")
    elif offsec_guided:
        signals.append("guided_sequence_observed")
    if source_diary_path:
        signals.append("source_diary_available")
    return signals


def _failure_signals(packet: dict[str, Any]) -> list[str]:
    tooling = packet.get("tooling") or {}
    assistant_snapshot = packet.get("assistant_snapshot") or {}

    signals: list[str] = []
    if assistant_snapshot.get("termination_cause") is not None:
        signals.append("termination_cause_present")
    if tooling.get("tool_names_partial"):
        signals.append("partial_tool_names")
    if (
        not str(assistant_snapshot.get("content_preview") or "").strip()
        and int(tooling.get("tool_call_count") or 0) > 0
        and tooling.get("source") == "output"
    ):
        signals.append("empty_assistant_preview_after_tool_marker")
    return signals


def _candidate_lesson_spec(
    *,
    workflow_family: str,
    tool_names: list[str],
    tooling: dict[str, Any],
    offsec_guided: bool,
) -> dict[str, Any] | None:
    if tooling.get("tool_names_partial"):
        return None
    if workflow_family == "offsec" and (offsec_guided or _has_offsec_tool(tool_names)):
        return {
            "pattern_key": LESSON_PATTERN_OFFSEC_GUIDED,
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
        }
    if workflow_family == "research" and _has_local_corpus_tool(tool_names):
        return {
            "pattern_key": LESSON_PATTERN_RESEARCH_LOCAL,
            "condition_codes": [
                "grounded_science_workflow_used_local_corpus_tools",
                "question_compatible_with_local_corpus_evidence",
            ],
            "prefer_codes": [
                "narrow_local_corpus_before_synthesis",
                "use_retrieved_local_evidence_before_answering_from_weights",
            ],
            "avoid_codes": [
                "avoid_unsupported_synthesis_when_local_evidence_available",
            ],
            "signal_codes": [
                "local_corpus_tools_used_in_grounded_research_turn",
            ],
        }
    if workflow_family == "research" and _has_web_evidence_tool(tool_names):
        return {
            "pattern_key": LESSON_PATTERN_RESEARCH_WEB,
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
        }
    return None


def _candidate_lesson_row(
    *,
    chat_id: str,
    message_id: str,
    materialized_at: str,
    lesson_spec: dict[str, Any],
    registry_path: str | Path | None = None,
) -> WorkflowLessonRow:
    pattern_key = str(lesson_spec["pattern_key"])
    lesson_id = "__".join(
        [
            pattern_key,
            _safe_lesson_component(chat_id),
            _safe_lesson_component(message_id),
        ]
    )
    return build_registry_backed_workflow_lesson_row(
        lesson_id=lesson_id,
        status="observed",
        pattern_key=pattern_key,
        condition_codes=lesson_spec["condition_codes"],
        prefer_codes=lesson_spec["prefer_codes"],
        avoid_codes=lesson_spec["avoid_codes"],
        signal_codes=lesson_spec["signal_codes"],
        source_turn_ids=[f"{chat_id}:{message_id}"],
        updated_at=materialized_at,
        confidence_note="Deterministic observation from workflow capture packet.",
        origin=f"workflow_diary_materializer_v1:{pattern_key}",
        registry_path=registry_path,
    )


def _materialize_entry_from_packet(
    *,
    packet: dict[str, Any],
    packet_path: Path,
    existing_entry: dict[str, Any] | None = None,
    registry_path: str | Path | None = None,
) -> tuple[dict[str, Any], list[WorkflowLessonRow]]:
    chat_id = str(packet.get("chat_id") or "").strip()
    message_id = str(packet.get("message_id") or "").strip()
    chat_dir = _chat_dir_from_packet_path(packet_path)

    request_context = copy.deepcopy(packet.get("request_context") or {})
    assistant_snapshot = copy.deepcopy(packet.get("assistant_snapshot") or {})
    tooling = copy.deepcopy(packet.get("tooling") or {})
    offsec_snapshot = copy.deepcopy(packet.get("offsec_snapshot") or {})
    tool_names = _tool_names(packet)
    offsec_guided, offsec_guided_reasons = _has_offsec_guided_semantics(packet, tool_names)
    workflow_family, classifier_reasons = _classify_workflow_family(
        packet,
        tool_names,
        offsec_guided=offsec_guided,
        offsec_guided_reasons=offsec_guided_reasons,
    )
    source_diary_path = chat_dir / "source_diary" / f"{message_id}.md"
    source_diary_ref = (
        _relative_chat_artifact_path(source_diary_path, chat_dir=chat_dir)
        if source_diary_path.exists()
        else None
    )
    existing_materialized_at = (
        str((existing_entry or {}).get("materialized_at") or "").strip()
    )
    materialized_at = existing_materialized_at or _utc_now_iso()

    candidate_rows: list[WorkflowLessonRow] = []
    lesson_spec = _candidate_lesson_spec(
        workflow_family=workflow_family,
        tool_names=tool_names,
        tooling=tooling,
        offsec_guided=offsec_guided,
    )
    if lesson_spec:
        candidate_rows.append(
            _candidate_lesson_row(
                chat_id=chat_id,
                message_id=message_id,
                materialized_at=materialized_at,
                lesson_spec=lesson_spec,
                registry_path=registry_path,
            )
        )

    entry = {
        "version": WORKFLOW_DIARY_ENTRY_VERSION,
        "kind": WORKFLOW_DIARY_ENTRY_KIND,
        "chat_id": chat_id,
        "message_id": message_id,
        "status": "complete",
        "captured_at": packet.get("captured_at"),
        "materialized_at": materialized_at,
        "workflow_family": workflow_family,
        "workflow_tags": _workflow_tags(packet, tool_names, offsec_guided=offsec_guided),
        "classifier": {
            "kind": "heuristic_v1",
            "confidence": 1.0,
            "reasons": classifier_reasons,
        },
        "canonical_lesson": (
            {
                "registry_version": candidate_rows[0].registry_version,
                "pattern_key": candidate_rows[0].pattern_key,
                "condition_codes": list(candidate_rows[0].condition_codes),
                "prefer_codes": list(candidate_rows[0].prefer_codes),
                "avoid_codes": list(candidate_rows[0].avoid_codes),
                "signal_codes": list(candidate_rows[0].signal_codes),
            }
            if candidate_rows
            else None
        ),
        "request_context": request_context,
        "assistant_snapshot": assistant_snapshot,
        "tooling": tooling,
        "offsec_snapshot": offsec_snapshot,
        "outcome": {
            "success_signals": _success_signals(
                packet,
                source_diary_path=source_diary_ref,
                offsec_guided=offsec_guided,
            ),
            "failure_signals": _failure_signals(packet),
            "wasteful_actions": [],
            "invariant_violations": [],
            "evidence_inference_issues": [],
            "operator_correction_signals": [],
        },
        "references": {
            "capture_packet": _relative_chat_artifact_path(packet_path, chat_dir=chat_dir),
            "source_diary": source_diary_ref,
        },
        "candidate_lessons": [
            workflow_lesson_row_to_dict(row) for row in candidate_rows
        ],
    }
    return entry, candidate_rows


def _load_entry_candidate_rows(entry_path: Path) -> list[WorkflowLessonRow]:
    try:
        entry = _load_json_file(entry_path)
    except Exception as exc:
        raise ValueError(f"Invalid workflow diary entry {entry_path}: {exc}") from exc

    candidate_rows: list[WorkflowLessonRow] = []
    for idx, raw in enumerate(entry.get("candidate_lessons") or [], start=1):
        if not isinstance(raw, dict):
            raise ValueError(
                f"Entry {entry_path} candidate lesson {idx} must be a JSON object"
            )
        candidate_rows.append(validate_workflow_lesson_row(raw, line_no=idx))
    return candidate_rows


def materialize_workflow_diary(
    *,
    artifacts_root: str | Path = AGENTIC_ARTIFACTS_DIR,
    runtime_root: str | Path | None = None,
    chat_id: str | None = None,
    message_id: str | None = None,
    min_age_minutes: int = 15,
    dry_run: bool = False,
    registry_path: str | Path | None = None,
) -> WorkflowDiaryMaterializationSummary:
    artifacts_root_path = Path(artifacts_root).expanduser().resolve()
    runtime_root_path = (
        Path(runtime_root).expanduser().resolve()
        if runtime_root is not None
        else default_runtime_workflow_lessons_root(artifacts_root_path)
    )

    packet_paths = _discover_packet_paths(
        artifacts_root_path, chat_id=chat_id, message_id=message_id
    )

    entries_written = 0
    entries_skipped = 0
    pending_entries: dict[tuple[str, str], dict[str, Any]] = {}

    for packet_path in packet_paths:
        if not _packet_is_old_enough(packet_path, min_age_minutes=min_age_minutes):
            entries_skipped += 1
            continue

        try:
            packet = _load_json_file(packet_path)
            if packet.get("kind") != "workflow_capture":
                raise ValueError("unexpected packet kind")
            chat_id_value = str(packet.get("chat_id") or "").strip()
            message_id_value = str(packet.get("message_id") or "").strip()
            if not chat_id_value or not message_id_value:
                raise ValueError("missing chat_id/message_id")
            existing_entry_path = (
                _chat_dir_from_packet_path(packet_path)
                / "workflow_diary"
                / "entries"
                / f"{message_id_value}.json"
            )
            existing_entry = None
            if existing_entry_path.exists():
                try:
                    existing_entry = _load_json_file(existing_entry_path)
                except Exception:
                    existing_entry = None
            entry, _ = _materialize_entry_from_packet(
                packet=packet,
                packet_path=packet_path,
                existing_entry=existing_entry,
                registry_path=registry_path,
            )
            pending_entries[(chat_id_value, message_id_value)] = entry
            entries_written += 1
            if not dry_run:
                chat_dir = _chat_dir_from_packet_path(packet_path)
                _atomic_write_json(
                    chat_dir / "workflow_diary" / "entries" / f"{message_id_value}.json",
                    entry,
                )
        except Exception as exc:
            entries_skipped += 1
            log.warning("Skipping workflow diary packet %s: %s", packet_path, exc)

    entry_map: dict[tuple[str, str], dict[str, Any]] = {}
    for entry_path in _discover_entry_paths(artifacts_root_path):
        try:
            entry = _load_json_file(entry_path)
            if entry.get("kind") != WORKFLOW_DIARY_ENTRY_KIND:
                raise ValueError("unexpected entry kind")
            key = (
                str(entry.get("chat_id") or "").strip(),
                str(entry.get("message_id") or "").strip(),
            )
            if not key[0] or not key[1]:
                raise ValueError("missing chat_id/message_id")
            entry_map[key] = entry
        except Exception as exc:
            log.warning("Skipping workflow diary entry %s: %s", entry_path, exc)

    entry_map.update(pending_entries)

    all_rows: list[WorkflowLessonRow] = []
    for key in sorted(entry_map):
        entry = entry_map[key]
        for idx, raw in enumerate(entry.get("candidate_lessons") or [], start=1):
            try:
                all_rows.append(
                    validate_workflow_lesson_row(
                        raw, line_no=idx, registry_path=registry_path
                    )
                )
            except WorkflowLessonsError as exc:
                log.warning(
                    "Skipping candidate lesson for %s/%s: %s",
                    key[0],
                    key[1],
                    exc,
                )

    catalog_rows = sorted(all_rows, key=lambda row: row.lesson_id)
    if not dry_run:
        write_workflow_lessons_catalog(
            runtime_root_path / RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH,
            catalog_rows,
            registry_path=registry_path,
        )

    return WorkflowDiaryMaterializationSummary(
        artifacts_root=artifacts_root_path,
        runtime_root=runtime_root_path,
        packets_scanned=len(packet_paths),
        entries_written=entries_written,
        entries_skipped=entries_skipped,
        lesson_rows_emitted=len(catalog_rows),
        catalog_rows_written=len(catalog_rows),
        dry_run=dry_run,
    )
