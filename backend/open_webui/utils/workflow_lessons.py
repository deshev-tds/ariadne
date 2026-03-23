from __future__ import annotations

import json
import math
import re
import shutil
from uuid import uuid4
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import tiktoken
except Exception:  # pragma: no cover - optional runtime fallback
    tiktoken = None


WORKFLOW_LESSONS_SCHEMA_VERSION = 1
WORKFLOW_LESSON_STATUSES = {"observed", "repeated", "promoted"}
WORKFLOW_LESSON_WORKING_MODES = {"science", "offsec"}
WORKFLOW_LESSON_FAMILIES = {"research", "offsec"}
WORKFLOW_LESSON_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
WORKFLOW_LESSON_ALLOWED_FIELDS = {
    "lesson_id",
    "status",
    "working_mode",
    "workflow_family",
    "title",
    "applies_when",
    "prefer",
    "avoid",
    "signal",
    "source_turn_ids",
    "updated_at",
    "do_not_apply_when",
    "confidence_note",
    "evidence_refs",
    "origin",
}
WORKFLOW_LESSON_REQUIRED_FIELDS = {
    "lesson_id",
    "status",
    "working_mode",
    "workflow_family",
    "title",
    "applies_when",
    "prefer",
    "avoid",
    "signal",
    "source_turn_ids",
    "updated_at",
}
WORKFLOW_LESSON_CARD_CHAR_BUDGET = 1200
WORKFLOW_LESSON_CARD_TOKEN_BUDGET = 250
WORKFLOW_LESSON_TOKEN_ENCODING_NAME = "cl100k_base"

_TIKTOKEN_ENCODING = None


class WorkflowLessonsError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowLessonRow:
    lesson_id: str
    status: str
    working_mode: str
    workflow_family: str
    title: str
    applies_when: tuple[str, ...]
    prefer: tuple[str, ...]
    avoid: tuple[str, ...]
    signal: tuple[str, ...]
    source_turn_ids: tuple[str, ...]
    updated_at: str
    do_not_apply_when: tuple[str, ...] = ()
    confidence_note: str | None = None
    evidence_refs: tuple[str, ...] = ()
    origin: str | None = None


@dataclass(frozen=True)
class WorkflowLessonsBuildSummary:
    workflow_root: Path
    serving_root: Path
    catalog_path: Path
    lesson_count: int
    promoted_count: int
    research_count: int
    offsec_count: int


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_string_list(
    value: Any, *, field_name: str, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkflowLessonsError(f"`{field_name}` must be a list of strings")

    normalized: list[str] = []
    for item in value:
        text = _normalize_text(item)
        if not text:
            raise WorkflowLessonsError(
                f"`{field_name}` must contain only non-empty strings"
            )
        normalized.append(text)

    if not allow_empty and not normalized:
        raise WorkflowLessonsError(f"`{field_name}` must not be empty")
    return tuple(normalized)


def _normalize_optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowLessonsError(f"`{field_name}` must be a string when present")
    normalized = _normalize_text(value)
    return normalized or None


def _validate_updated_at(value: Any) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        raise WorkflowLessonsError("`updated_at` must be a non-empty ISO date string")

    candidate = normalized.replace("Z", "+00:00")
    try:
        if "T" in candidate or "+" in candidate:
            datetime.fromisoformat(candidate)
        else:
            datetime.fromisoformat(f"{candidate}T00:00:00")
    except ValueError as exc:
        raise WorkflowLessonsError(
            f"`updated_at` must be an ISO date or datetime string: {normalized}"
        ) from exc
    return normalized


def _get_tiktoken_encoding():
    global _TIKTOKEN_ENCODING
    if tiktoken is None:
        return None
    if _TIKTOKEN_ENCODING is None:
        _TIKTOKEN_ENCODING = tiktoken.get_encoding(WORKFLOW_LESSON_TOKEN_ENCODING_NAME)
    return _TIKTOKEN_ENCODING


def _estimate_token_count(text: str) -> int:
    encoding = _get_tiktoken_encoding()
    if encoding is not None:
        return len(encoding.encode(text))
    return max(1, math.ceil(len(text) / 4))


def _render_section(title: str, items: tuple[str, ...]) -> list[str]:
    lines = [f"{title}:"]
    lines.extend(f"- {item}" for item in items)
    return lines


def _validate_row(raw: dict[str, Any], *, line_no: int) -> WorkflowLessonRow:
    if not isinstance(raw, dict):
        raise WorkflowLessonsError(f"Catalog row {line_no} must be a JSON object")

    unknown_fields = sorted(set(raw) - WORKFLOW_LESSON_ALLOWED_FIELDS)
    if unknown_fields:
        raise WorkflowLessonsError(
            f"Catalog row {line_no} contains unknown fields: {', '.join(unknown_fields)}"
        )

    missing_fields = sorted(WORKFLOW_LESSON_REQUIRED_FIELDS - set(raw))
    if missing_fields:
        raise WorkflowLessonsError(
            f"Catalog row {line_no} is missing required fields: {', '.join(missing_fields)}"
        )

    lesson_id = _normalize_text(raw.get("lesson_id"))
    if not WORKFLOW_LESSON_ID_RE.fullmatch(lesson_id):
        raise WorkflowLessonsError(
            f"`lesson_id` must match {WORKFLOW_LESSON_ID_RE.pattern}: {lesson_id!r}"
        )

    status = _normalize_text(raw.get("status")).lower()
    if status not in WORKFLOW_LESSON_STATUSES:
        raise WorkflowLessonsError(
            f"`status` must be one of {sorted(WORKFLOW_LESSON_STATUSES)}"
        )

    working_mode = _normalize_text(raw.get("working_mode")).lower()
    if working_mode not in WORKFLOW_LESSON_WORKING_MODES:
        raise WorkflowLessonsError(
            f"`working_mode` must be one of {sorted(WORKFLOW_LESSON_WORKING_MODES)}"
        )

    workflow_family = _normalize_text(raw.get("workflow_family")).lower()
    if workflow_family not in WORKFLOW_LESSON_FAMILIES:
        raise WorkflowLessonsError(
            f"`workflow_family` must be one of {sorted(WORKFLOW_LESSON_FAMILIES)}"
        )

    title = _normalize_text(raw.get("title"))
    if not title:
        raise WorkflowLessonsError("`title` must be a non-empty string")

    return WorkflowLessonRow(
        lesson_id=lesson_id,
        status=status,
        working_mode=working_mode,
        workflow_family=workflow_family,
        title=title,
        applies_when=_normalize_string_list(
            raw.get("applies_when"), field_name="applies_when"
        ),
        prefer=_normalize_string_list(raw.get("prefer"), field_name="prefer"),
        avoid=_normalize_string_list(raw.get("avoid"), field_name="avoid"),
        signal=_normalize_string_list(raw.get("signal"), field_name="signal"),
        source_turn_ids=_normalize_string_list(
            raw.get("source_turn_ids"), field_name="source_turn_ids"
        ),
        updated_at=_validate_updated_at(raw.get("updated_at")),
        do_not_apply_when=_normalize_string_list(
            raw.get("do_not_apply_when", []),
            field_name="do_not_apply_when",
            allow_empty=True,
        ),
        confidence_note=_normalize_optional_string(
            raw.get("confidence_note"), field_name="confidence_note"
        ),
        evidence_refs=_normalize_string_list(
            raw.get("evidence_refs", []), field_name="evidence_refs", allow_empty=True
        ),
        origin=_normalize_optional_string(raw.get("origin"), field_name="origin"),
    )


def load_workflow_lessons_catalog(catalog_path: Path) -> list[WorkflowLessonRow]:
    if not catalog_path.exists():
        raise WorkflowLessonsError(f"Missing lessons catalog: {catalog_path}")

    rows: list[WorkflowLessonRow] = []
    seen_ids: set[str] = set()
    for line_no, line in enumerate(
        catalog_path.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise WorkflowLessonsError(
                f"Catalog row {line_no} is not valid JSON: {exc}"
            ) from exc

        row = _validate_row(raw, line_no=line_no)
        if row.lesson_id in seen_ids:
            raise WorkflowLessonsError(f"Duplicate lesson_id: {row.lesson_id}")
        seen_ids.add(row.lesson_id)
        rows.append(row)

    return rows


def validate_workflow_lesson_row(
    raw: dict[str, Any], *, line_no: int = 1
) -> WorkflowLessonRow:
    return _validate_row(raw, line_no=line_no)


def workflow_lesson_row_to_dict(row: WorkflowLessonRow) -> dict[str, Any]:
    payload = {
        "lesson_id": row.lesson_id,
        "status": row.status,
        "working_mode": row.working_mode,
        "workflow_family": row.workflow_family,
        "title": row.title,
        "applies_when": list(row.applies_when),
        "prefer": list(row.prefer),
        "avoid": list(row.avoid),
        "signal": list(row.signal),
        "source_turn_ids": list(row.source_turn_ids),
        "updated_at": row.updated_at,
    }
    if row.do_not_apply_when:
        payload["do_not_apply_when"] = list(row.do_not_apply_when)
    if row.confidence_note:
        payload["confidence_note"] = row.confidence_note
    if row.evidence_refs:
        payload["evidence_refs"] = list(row.evidence_refs)
    if row.origin:
        payload["origin"] = row.origin
    return payload


def _coerce_workflow_lesson_row(
    row: WorkflowLessonRow | dict[str, Any], *, line_no: int
) -> WorkflowLessonRow:
    if isinstance(row, WorkflowLessonRow):
        return row
    if isinstance(row, dict):
        return validate_workflow_lesson_row(row, line_no=line_no)
    raise WorkflowLessonsError(f"Catalog row {line_no} must be a workflow lesson row")


def write_workflow_lessons_catalog(
    catalog_path: str | Path,
    rows: list[WorkflowLessonRow | dict[str, Any]],
) -> list[WorkflowLessonRow]:
    path = Path(catalog_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized_rows: list[WorkflowLessonRow] = []
    seen_ids: set[str] = set()
    for line_no, row in enumerate(rows, start=1):
        normalized = _coerce_workflow_lesson_row(row, line_no=line_no)
        if normalized.lesson_id in seen_ids:
            raise WorkflowLessonsError(f"Duplicate lesson_id: {normalized.lesson_id}")
        seen_ids.add(normalized.lesson_id)
        normalized_rows.append(normalized)

    normalized_rows.sort(key=lambda row: row.lesson_id)
    payload = "\n".join(
        json.dumps(workflow_lesson_row_to_dict(row), ensure_ascii=False)
        for row in normalized_rows
    )
    if payload:
        payload += "\n"

    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    temp_path.write_text(payload, encoding="utf-8")
    temp_path.replace(path)
    return normalized_rows


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def _human_family_name(family: str) -> str:
    return "Research" if family == "research" else "Offsec"


def _render_serving_root_readme(promoted_rows: list[WorkflowLessonRow]) -> str:
    research_count = sum(1 for row in promoted_rows if row.workflow_family == "research")
    offsec_count = sum(1 for row in promoted_rows if row.workflow_family == "offsec")
    return f"""
# Workflow Lessons Serving Layer

This is the generated, model-facing serving layer for `workflow_lessons/`.

It is builder-generated from:

- `../internal/lessons-catalog.jsonl`

Only `promoted` lessons are materialized here.

Counts:

- promoted lessons: {len(promoted_rows)}
- research lessons: {research_count}
- offsec lessons: {offsec_count}

Use:

- `index.md` for the top-level shortlist
- `families/research.md` for research lessons
- `families/offsec.md` for Offsec lessons
- `lessons/*.md` for compact serving cards
"""


def _render_serving_index(promoted_rows: list[WorkflowLessonRow]) -> str:
    research_count = sum(1 for row in promoted_rows if row.workflow_family == "research")
    offsec_count = sum(1 for row in promoted_rows if row.workflow_family == "offsec")
    return f"""
# Workflow Lessons

This serving layer is optimized for future model-time consultation.

Only `promoted` lessons are exposed here.

Families:

- [Research](./families/research.md): {research_count} promoted lessons
- [Offsec](./families/offsec.md): {offsec_count} promoted lessons

Open individual lesson cards only after family-level narrowing.
"""


def _lesson_summary(row: WorkflowLessonRow) -> str:
    if row.signal:
        return row.signal[0]
    return row.title


def _render_family_index(
    family: str, promoted_rows: list[WorkflowLessonRow]
) -> str:
    title = _human_family_name(family)
    family_rows = [row for row in promoted_rows if row.workflow_family == family]
    lines = [
        f"# {title} Lessons",
        "",
        "Only promoted lessons appear here.",
    ]
    if not family_rows:
        lines.extend(["", "No promoted lessons are available yet."])
        return "\n".join(lines)

    lines.append("")
    for row in family_rows:
        lines.append(
            f"- [{row.title}](../lessons/{row.lesson_id}.md): {_lesson_summary(row)}"
        )
    return "\n".join(lines)


def render_workflow_lesson_card(row: WorkflowLessonRow) -> str:
    lines = [f"# {row.title}", ""]
    lines.extend(_render_section("Applies When", row.applies_when))
    if row.do_not_apply_when:
        lines.extend(["", *_render_section("Do Not Apply When", row.do_not_apply_when)])
    lines.extend(["", *_render_section("Prefer", row.prefer)])
    lines.extend(["", *_render_section("Avoid", row.avoid)])
    lines.extend(["", *_render_section("Signal", row.signal)])
    lines.extend(["", "Status:", f"- {row.status}"])
    lines.extend(["", "Last Updated:", f"- {row.updated_at}"])
    card = "\n".join(lines)

    char_count = len(card)
    token_count = _estimate_token_count(card)
    if (
        char_count > WORKFLOW_LESSON_CARD_CHAR_BUDGET
        or token_count > WORKFLOW_LESSON_CARD_TOKEN_BUDGET
    ):
        raise WorkflowLessonsError(
            "Serving card budget exceeded for "
            f"{row.lesson_id}: {char_count} chars, {token_count} tokens"
        )
    return card


def build_workflow_lessons_serving(
    workflow_root: str | Path,
) -> WorkflowLessonsBuildSummary:
    root = Path(workflow_root).expanduser().resolve()
    catalog_path = root / "internal" / "lessons-catalog.jsonl"
    rows = load_workflow_lessons_catalog(catalog_path)
    promoted_rows = sorted(
        (row for row in rows if row.status == "promoted"),
        key=lambda row: (row.workflow_family, row.title.lower(), row.lesson_id),
    )

    serving_root = root / "_serving"
    if serving_root.exists():
        shutil.rmtree(serving_root)
    serving_root.mkdir(parents=True, exist_ok=True)

    _write_text(serving_root / "README.md", _render_serving_root_readme(promoted_rows))
    _write_text(serving_root / "index.md", _render_serving_index(promoted_rows))
    _write_text(
        serving_root / "families" / "research.md",
        _render_family_index("research", promoted_rows),
    )
    _write_text(
        serving_root / "families" / "offsec.md",
        _render_family_index("offsec", promoted_rows),
    )

    for row in promoted_rows:
        _write_text(
            serving_root / "lessons" / f"{row.lesson_id}.md",
            render_workflow_lesson_card(row),
        )

    return WorkflowLessonsBuildSummary(
        workflow_root=root,
        serving_root=serving_root,
        catalog_path=catalog_path,
        lesson_count=len(rows),
        promoted_count=len(promoted_rows),
        research_count=sum(
            1 for row in promoted_rows if row.workflow_family == "research"
        ),
        offsec_count=sum(1 for row in promoted_rows if row.workflow_family == "offsec"),
    )
