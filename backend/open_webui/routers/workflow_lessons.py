from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from open_webui.utils.auth import get_admin_user
from open_webui.utils.workflow_diary_materializer import (
    RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH,
    default_runtime_workflow_lessons_root,
)
from open_webui.utils.workflow_lessons import (
    WorkflowLessonRow,
    WorkflowLessonsError,
    default_workflow_lesson_registry_path,
    load_workflow_lessons_catalog,
    workflow_lesson_canonical_signature,
    workflow_lesson_has_registry_identity,
    workflow_lesson_row_to_dict,
)
from open_webui.utils.workflow_lessons_review import (
    REPEATED_CANDIDATES_RELATIVE_PATH,
    REVIEW_DIGEST_RELATIVE_PATH,
    build_repeated_candidates_from_rows,
    export_workflow_lesson_candidate,
    load_repeated_candidates,
    review_runtime_workflow_lessons,
)

router = APIRouter()


class WorkflowLessonsStateResponse(BaseModel):
    runtime_root: str
    curated_root: str
    runtime: dict[str, Any]
    curated: dict[str, Any]


class WorkflowLessonsReviewResponse(BaseModel):
    review_summary: dict[str, Any]
    state: WorkflowLessonsStateResponse


class WorkflowLessonsPromoteForm(BaseModel):
    candidate_id: str = Field(min_length=1)
    target_lesson_id: str = Field(min_length=1)


class WorkflowLessonsPromoteResponse(BaseModel):
    export_summary: dict[str, Any]
    state: WorkflowLessonsStateResponse


def _default_registry_path() -> Path:
    return default_workflow_lesson_registry_path().resolve()


def _default_curated_root() -> Path:
    return _default_registry_path().parents[1]


def _default_runtime_root() -> Path:
    return default_runtime_workflow_lessons_root().resolve()


def _load_catalog_if_exists(
    catalog_path: Path,
    *,
    registry_path: Path,
) -> list[WorkflowLessonRow]:
    if not catalog_path.exists():
        return []
    return load_workflow_lessons_catalog(catalog_path, registry_path=registry_path)


def _load_review_digest_if_exists(runtime_root: Path) -> str | None:
    digest_path = runtime_root / REVIEW_DIGEST_RELATIVE_PATH
    if not digest_path.exists():
        return None
    return digest_path.read_text(encoding="utf-8", errors="replace")


def _load_repeated_candidates_if_exists(runtime_root: Path) -> list[dict[str, Any]]:
    repeated_path = runtime_root / REPEATED_CANDIDATES_RELATIVE_PATH
    if not repeated_path.exists():
        return []
    return load_repeated_candidates(repeated_path)


def _curated_signature_map(promoted_rows: list[WorkflowLessonRow]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in promoted_rows:
        if not workflow_lesson_has_registry_identity(row):
            continue
        mapping[workflow_lesson_canonical_signature(row)] = row.lesson_id
    return mapping


def _serialize_rows(rows: list[WorkflowLessonRow]) -> list[dict[str, Any]]:
    return [workflow_lesson_row_to_dict(row) for row in rows]


def _build_review_summary(
    *,
    runtime_root: Path,
    observed_rows: list[WorkflowLessonRow],
    repeated_candidates: list[dict[str, Any]],
    review_digest_markdown: str | None,
) -> dict[str, Any] | None:
    repeated_path = runtime_root / REPEATED_CANDIDATES_RELATIVE_PATH
    digest_path = runtime_root / REVIEW_DIGEST_RELATIVE_PATH
    if not repeated_path.exists() and not digest_path.exists():
        return None

    _, clusters = build_repeated_candidates_from_rows(observed_rows)
    return {
        "runtime_root": str(runtime_root),
        "observed_rows": len(observed_rows),
        "registry_backed_observed_rows": sum(
            1 for row in observed_rows if workflow_lesson_has_registry_identity(row)
        ),
        "unique_signatures": len(clusters),
        "repeated_candidates": len(repeated_candidates),
        "digest_present": review_digest_markdown is not None,
    }


def _enrich_repeated_candidates(
    candidates: list[dict[str, Any]],
    *,
    promoted_signature_map: dict[str, str],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for candidate in candidates:
        existing_curated_lesson_id = promoted_signature_map.get(candidate["signature"])
        enriched.append(
            {
                **candidate,
                "suggested_lesson_id": candidate["pattern_key"],
                "existing_curated_lesson_id": existing_curated_lesson_id,
                "can_promote": existing_curated_lesson_id is None,
            }
        )
    return enriched


def build_workflow_lessons_state(
    *,
    runtime_root: str | Path | None = None,
    curated_root: str | Path | None = None,
    registry_path: str | Path | None = None,
) -> WorkflowLessonsStateResponse:
    registry_path_value = (
        Path(registry_path).expanduser().resolve()
        if registry_path is not None
        else _default_registry_path()
    )
    runtime_root_path = (
        Path(runtime_root).expanduser().resolve()
        if runtime_root is not None
        else _default_runtime_root()
    )
    curated_root_path = (
        Path(curated_root).expanduser().resolve()
        if curated_root is not None
        else _default_curated_root()
    )

    runtime_rows = _load_catalog_if_exists(
        runtime_root_path / RUNTIME_WORKFLOW_LESSONS_CATALOG_RELATIVE_PATH,
        registry_path=registry_path_value,
    )
    observed_rows = [row for row in runtime_rows if row.status == "observed"]

    promoted_rows = [
        row
        for row in _load_catalog_if_exists(
            curated_root_path / "internal" / "lessons-catalog.jsonl",
            registry_path=registry_path_value,
        )
        if row.status == "promoted"
    ]

    review_digest_markdown = _load_review_digest_if_exists(runtime_root_path)
    repeated_candidates = _load_repeated_candidates_if_exists(runtime_root_path)
    enriched_candidates = _enrich_repeated_candidates(
        repeated_candidates,
        promoted_signature_map=_curated_signature_map(promoted_rows),
    )

    return WorkflowLessonsStateResponse(
        runtime_root=str(runtime_root_path),
        curated_root=str(curated_root_path),
        runtime={
            "observed_rows": _serialize_rows(observed_rows),
            "repeated_candidates": enriched_candidates,
            "review_summary": _build_review_summary(
                runtime_root=runtime_root_path,
                observed_rows=observed_rows,
                repeated_candidates=enriched_candidates,
                review_digest_markdown=review_digest_markdown,
            ),
            "review_digest_markdown": review_digest_markdown,
        },
        curated={
            "promoted_rows": _serialize_rows(promoted_rows),
        },
    )


def _workflow_lessons_http_error(exc: WorkflowLessonsError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/state", response_model=WorkflowLessonsStateResponse)
async def get_workflow_lessons_state(user=Depends(get_admin_user)):
    try:
        return build_workflow_lessons_state()
    except WorkflowLessonsError as exc:
        raise _workflow_lessons_http_error(exc) from exc


@router.post("/review", response_model=WorkflowLessonsReviewResponse)
async def review_workflow_lessons(user=Depends(get_admin_user)):
    try:
        summary = review_runtime_workflow_lessons(
            runtime_root=_default_runtime_root(),
            registry_path=_default_registry_path(),
        )
        state = build_workflow_lessons_state(
            runtime_root=_default_runtime_root(),
            curated_root=_default_curated_root(),
            registry_path=_default_registry_path(),
        )
    except WorkflowLessonsError as exc:
        raise _workflow_lessons_http_error(exc) from exc

    return WorkflowLessonsReviewResponse(
        review_summary=summary.to_dict(),
        state=state,
    )


@router.post("/promote", response_model=WorkflowLessonsPromoteResponse)
async def promote_workflow_lesson(
    form_data: WorkflowLessonsPromoteForm,
    user=Depends(get_admin_user),
):
    candidate_id = form_data.candidate_id.strip()
    target_lesson_id = form_data.target_lesson_id.strip()
    if not candidate_id:
        raise HTTPException(status_code=400, detail="`candidate_id` must not be empty")
    if not target_lesson_id:
        raise HTTPException(status_code=400, detail="`target_lesson_id` must not be empty")

    try:
        summary = export_workflow_lesson_candidate(
            runtime_root=_default_runtime_root(),
            candidate_id=candidate_id,
            target_lesson_id=target_lesson_id,
            curated_root=_default_curated_root(),
            registry_path=_default_registry_path(),
        )
        state = build_workflow_lessons_state(
            runtime_root=_default_runtime_root(),
            curated_root=_default_curated_root(),
            registry_path=_default_registry_path(),
        )
    except WorkflowLessonsError as exc:
        raise _workflow_lessons_http_error(exc) from exc

    return WorkflowLessonsPromoteResponse(
        export_summary=summary.to_dict(),
        state=state,
    )
