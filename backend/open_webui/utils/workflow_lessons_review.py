from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from open_webui.env import AGENTIC_ARTIFACTS_DIR
from open_webui.utils.workflow_diary_materializer import (
    RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH,
    RUNTIME_WORKFLOW_LESSONS_DIRNAME,
)
from open_webui.utils.workflow_lessons import (
    WorkflowLessonRow,
    WorkflowLessonsError,
    build_registry_backed_workflow_lesson_row,
    build_workflow_lessons_serving,
    default_workflow_lesson_registry_path,
    load_workflow_lessons_catalog,
    workflow_lesson_canonical_signature,
    workflow_lesson_canonical_signature_payload,
    workflow_lesson_has_registry_identity,
    write_workflow_lessons_catalog,
)

REPEATED_CANDIDATES_RELATIVE_PATH = Path("internal/repeated-candidates.jsonl")
REVIEW_DIGEST_RELATIVE_PATH = Path("review/latest.md")
REPEATED_CANDIDATE_KIND = "workflow_repeated_candidate"
REPEATED_CANDIDATE_VERSION = 1


@dataclass(frozen=True)
class WorkflowLessonsReviewSummary:
    runtime_root: Path
    observed_rows: int
    registry_backed_observed_rows: int
    unique_signatures: int
    repeated_candidates: int
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runtime_root"] = str(self.runtime_root)
        return payload


@dataclass(frozen=True)
class WorkflowLessonExportSummary:
    runtime_root: Path
    curated_root: Path
    candidate_id: str
    target_lesson_id: str
    replaced: bool
    dry_run: bool
    serving_root: Path

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runtime_root"] = str(self.runtime_root)
        payload["curated_root"] = str(self.curated_root)
        payload["serving_root"] = str(self.serving_root)
        return payload


def default_runtime_workflow_lessons_root(
    artifacts_root: str | Path = AGENTIC_ARTIFACTS_DIR,
) -> Path:
    return Path(artifacts_root).expanduser().resolve() / RUNTIME_WORKFLOW_LESSONS_DIRNAME


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temp_path.write_text(text, encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if payload:
        payload += "\n"
    _atomic_write_text(path, payload)


def _chat_id_from_source_turn_id(source_turn_id: str) -> str:
    return str(source_turn_id or "").split(":", 1)[0].strip()


def _parse_repeated_candidate(raw: dict[str, Any]) -> dict[str, Any]:
    required = {
        "version",
        "kind",
        "candidate_id",
        "signature",
        "registry_version",
        "working_mode",
        "workflow_family",
        "pattern_key",
        "condition_codes",
        "prefer_codes",
        "avoid_codes",
        "signal_codes",
        "title",
        "applies_when",
        "prefer",
        "avoid",
        "signal",
        "occurrence_count",
        "distinct_chat_count",
        "source_turn_ids",
        "source_chat_ids",
        "source_observed_lesson_ids",
        "first_seen_at",
        "last_seen_at",
        "origin",
    }
    if not isinstance(raw, dict):
        raise WorkflowLessonsError("Repeated candidate row must be a JSON object")
    missing = sorted(required - set(raw))
    if missing:
        raise WorkflowLessonsError(
            f"Repeated candidate row is missing required fields: {', '.join(missing)}"
        )
    if raw.get("kind") != REPEATED_CANDIDATE_KIND:
        raise WorkflowLessonsError("Repeated candidate row has unexpected kind")
    if int(raw.get("version") or 0) != REPEATED_CANDIDATE_VERSION:
        raise WorkflowLessonsError("Repeated candidate row has unexpected version")
    if int(raw.get("distinct_chat_count") or 0) < 2:
        raise WorkflowLessonsError("Repeated candidate row must have at least 2 chats")
    return raw


def load_repeated_candidates(path: str | Path) -> list[dict[str, Any]]:
    candidate_path = Path(path).expanduser().resolve()
    if not candidate_path.exists():
        raise WorkflowLessonsError(f"Missing repeated candidates file: {candidate_path}")
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_no, line in enumerate(
        candidate_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowLessonsError(
                f"Repeated candidate row {line_no} is not valid JSON: {exc}"
            ) from exc
        parsed = _parse_repeated_candidate(raw)
        if parsed["candidate_id"] in seen_ids:
            raise WorkflowLessonsError(
                f"Duplicate repeated candidate id: {parsed['candidate_id']}"
            )
        seen_ids.add(parsed["candidate_id"])
        rows.append(parsed)
    return rows


def build_repeated_candidates_from_rows(
    rows: list[WorkflowLessonRow],
) -> tuple[list[dict[str, Any]], dict[str, list[WorkflowLessonRow]]]:
    observed_rows = [
        row
        for row in rows
        if row.status == "observed" and workflow_lesson_has_registry_identity(row)
    ]
    clusters: dict[str, list[WorkflowLessonRow]] = {}
    for row in observed_rows:
        signature = workflow_lesson_canonical_signature(row)
        clusters.setdefault(signature, []).append(row)

    repeated_candidates: list[dict[str, Any]] = []
    for signature, clustered_rows in sorted(
        clusters.items(), key=lambda item: (item[0], len(item[1]))
    ):
        representative = sorted(clustered_rows, key=lambda row: row.lesson_id)[0]
        source_turn_ids = sorted(
            {
                source_turn_id
                for row in clustered_rows
                for source_turn_id in row.source_turn_ids
            }
        )
        source_chat_ids = sorted(
            {_chat_id_from_source_turn_id(source_turn_id) for source_turn_id in source_turn_ids}
        )
        if len(source_chat_ids) < 2:
            continue

        candidate_id = f"repeat_{representative.workflow_family}_{signature[:12]}"
        repeated_candidates.append(
            {
                "version": REPEATED_CANDIDATE_VERSION,
                "kind": REPEATED_CANDIDATE_KIND,
                "candidate_id": candidate_id,
                "signature": signature,
                **workflow_lesson_canonical_signature_payload(representative),
                "title": representative.title,
                "applies_when": list(representative.applies_when),
                "prefer": list(representative.prefer),
                "avoid": list(representative.avoid),
                "signal": list(representative.signal),
                "occurrence_count": len(clustered_rows),
                "distinct_chat_count": len(source_chat_ids),
                "source_turn_ids": source_turn_ids,
                "source_chat_ids": source_chat_ids,
                "source_observed_lesson_ids": sorted(row.lesson_id for row in clustered_rows),
                "first_seen_at": min(row.updated_at for row in clustered_rows),
                "last_seen_at": max(row.updated_at for row in clustered_rows),
                "origin": f"workflow_lessons_review_v1:{representative.pattern_key}",
            }
        )

    return repeated_candidates, clusters


def _render_review_digest(
    rows: list[WorkflowLessonRow],
    repeated_candidates: list[dict[str, Any]],
    clusters: dict[str, list[WorkflowLessonRow]],
) -> str:
    observed_rows = [row for row in rows if row.status == "observed"]
    repeated_signatures = {candidate["signature"] for candidate in repeated_candidates}
    repeated_by_family = {
        "research": [item for item in repeated_candidates if item["workflow_family"] == "research"],
        "offsec": [item for item in repeated_candidates if item["workflow_family"] == "offsec"],
    }

    lines = [
        "# Workflow Lessons Review",
        "",
        "Summary:",
        f"- observed rows: {len(observed_rows)}",
        f"- registry-backed observed rows: {sum(1 for row in observed_rows if workflow_lesson_has_registry_identity(row))}",
        f"- unique signatures: {len(clusters)}",
        f"- repeated candidates: {len(repeated_candidates)}",
        f"- research repeated candidates: {len(repeated_by_family['research'])}",
        f"- offsec repeated candidates: {len(repeated_by_family['offsec'])}",
        "",
        "## Repeated Candidates",
    ]

    if not repeated_candidates:
        lines.extend(["", "No repeated candidates yet."])
    else:
        for candidate in repeated_candidates:
            lines.extend(
                [
                    "",
                    f"### {candidate['candidate_id']}",
                    f"- title: {candidate['title']}",
                    f"- distinct chats: {candidate['distinct_chat_count']}",
                    f"- total occurrences: {candidate['occurrence_count']}",
                    f"- source turn ids: {', '.join(candidate['source_turn_ids'])}",
                    "- prefer:",
                ]
            )
            lines.extend(f"  - {item}" for item in candidate["prefer"])
            lines.append("- avoid:")
            lines.extend(f"  - {item}" for item in candidate["avoid"])
            lines.append("- signal:")
            lines.extend(f"  - {item}" for item in candidate["signal"])

    lines.extend(["", "## Observed Only"])
    observed_only = [
        (signature, clustered_rows)
        for signature, clustered_rows in sorted(clusters.items())
        if signature not in repeated_signatures
    ]
    if not observed_only:
        lines.extend(["", "No observed-only signatures remain."])
    else:
        for signature, clustered_rows in observed_only:
            representative = sorted(clustered_rows, key=lambda row: row.lesson_id)[0]
            source_turn_ids = sorted(
                {
                    source_turn_id
                    for row in clustered_rows
                    for source_turn_id in row.source_turn_ids
                }
            )
            lines.extend(
                [
                    "",
                    f"### {representative.pattern_key or 'non_registry'} ({signature[:12]})",
                    f"- title: {representative.title}",
                    f"- occurrences: {len(clustered_rows)}",
                    f"- distinct chats: {len({_chat_id_from_source_turn_id(turn_id) for turn_id in source_turn_ids})}",
                    f"- source turn ids: {', '.join(source_turn_ids)}",
                ]
            )

    return "\n".join(lines).strip() + "\n"


def review_runtime_workflow_lessons(
    *,
    runtime_root: str | Path,
    registry_path: str | Path | None = None,
    dry_run: bool = False,
) -> WorkflowLessonsReviewSummary:
    runtime_root_path = Path(runtime_root).expanduser().resolve()
    rows = load_workflow_lessons_catalog(
        runtime_root_path / RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH,
        registry_path=registry_path,
    )
    repeated_candidates, clusters = build_repeated_candidates_from_rows(rows)
    digest = _render_review_digest(rows, repeated_candidates, clusters)

    if not dry_run:
        _atomic_write_jsonl(
            runtime_root_path / REPEATED_CANDIDATES_RELATIVE_PATH,
            repeated_candidates,
        )
        _atomic_write_text(runtime_root_path / REVIEW_DIGEST_RELATIVE_PATH, digest)

    return WorkflowLessonsReviewSummary(
        runtime_root=runtime_root_path,
        observed_rows=sum(1 for row in rows if row.status == "observed"),
        registry_backed_observed_rows=sum(
            1
            for row in rows
            if row.status == "observed" and workflow_lesson_has_registry_identity(row)
        ),
        unique_signatures=len(clusters),
        repeated_candidates=len(repeated_candidates),
        dry_run=dry_run,
    )


def export_workflow_lesson_candidate(
    *,
    runtime_root: str | Path,
    candidate_id: str,
    target_lesson_id: str,
    target_title: str | None = None,
    replace: bool = False,
    dry_run: bool = False,
    curated_root: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> WorkflowLessonExportSummary:
    runtime_root_path = Path(runtime_root).expanduser().resolve()
    registry_path_value = (
        Path(registry_path).expanduser().resolve()
        if registry_path is not None
        else default_workflow_lesson_registry_path().resolve()
    )
    curated_root_path = (
        Path(curated_root).expanduser().resolve()
        if curated_root is not None
        else registry_path_value.parents[1]
    )
    candidates = load_repeated_candidates(runtime_root_path / REPEATED_CANDIDATES_RELATIVE_PATH)
    candidate = next((item for item in candidates if item["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise WorkflowLessonsError(f"Unknown repeated candidate id: {candidate_id}")

    exported_row = build_registry_backed_workflow_lesson_row(
        lesson_id=target_lesson_id,
        status="promoted",
        pattern_key=candidate["pattern_key"],
        condition_codes=candidate["condition_codes"],
        prefer_codes=candidate["prefer_codes"],
        avoid_codes=candidate["avoid_codes"],
        signal_codes=candidate["signal_codes"],
        source_turn_ids=candidate["source_turn_ids"],
        updated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        confidence_note="Promoted from repeated runtime lesson candidate after manual review.",
        origin=f"workflow_lesson_export_v1:{candidate_id}",
        registry_path=registry_path_value,
    )

    if target_title and target_title.strip() != exported_row.title:
        raise WorkflowLessonsError(
            "Registry-backed exports require the canonical registry title; "
            f"expected {exported_row.title!r}"
        )

    curated_catalog_path = curated_root_path / "internal" / "lessons-catalog.jsonl"
    existing_rows = (
        load_workflow_lessons_catalog(curated_catalog_path, registry_path=registry_path_value)
        if curated_catalog_path.exists()
        else []
    )

    exported_signature = workflow_lesson_canonical_signature(exported_row)
    existing_same_id = next(
        (row for row in existing_rows if row.lesson_id == target_lesson_id), None
    )
    existing_same_signature = next(
        (
            row
            for row in existing_rows
            if workflow_lesson_has_registry_identity(row)
            and workflow_lesson_canonical_signature(row) == exported_signature
        ),
        None,
    )

    if existing_same_id and not replace:
        raise WorkflowLessonsError(f"Curated lesson_id already exists: {target_lesson_id}")
    if existing_same_signature and not replace:
        raise WorkflowLessonsError(
            "Curated catalog already contains the same canonical lesson signature"
        )

    replaced = bool(existing_same_id or existing_same_signature)
    next_rows: list[WorkflowLessonRow] = []
    if replace:
        for row in existing_rows:
            same_id = row.lesson_id == target_lesson_id
            same_signature = workflow_lesson_has_registry_identity(row) and (
                workflow_lesson_canonical_signature(row) == exported_signature
            )
            if same_id or same_signature:
                continue
            next_rows.append(row)
    else:
        next_rows = list(existing_rows)

    next_rows.append(exported_row)

    if not dry_run:
        write_workflow_lessons_catalog(
            curated_catalog_path,
            next_rows,
            registry_path=registry_path_value,
        )
        build_workflow_lessons_serving(curated_root_path)

    return WorkflowLessonExportSummary(
        runtime_root=runtime_root_path,
        curated_root=curated_root_path,
        candidate_id=candidate_id,
        target_lesson_id=target_lesson_id,
        replaced=replaced,
        dry_run=dry_run,
        serving_root=curated_root_path / "_serving",
    )
