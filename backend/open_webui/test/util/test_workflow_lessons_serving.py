import json
import shutil
from pathlib import Path

import pytest

import open_webui.utils.workflow_lessons as workflow_lessons


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    _write(
        path,
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
    )


def _base_row(**overrides):
    row = {
        "lesson_id": "research_seed",
        "status": "promoted",
        "working_mode": "science",
        "workflow_family": "research",
        "title": "Research Seed",
        "applies_when": ["grounded research flow"],
        "prefer": ["narrow sources before synthesis"],
        "avoid": ["answering from weights alone"],
        "signal": ["compatible grounded question"],
        "source_turn_ids": ["seed:research"],
        "updated_at": "2026-03-23",
    }
    row.update(overrides)
    return row


def _serving_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted((root / "_serving").rglob("*.md")):
        snapshot[path.relative_to(root).as_posix()] = path.read_text(
            encoding="utf-8"
        )
    return snapshot


def test_build_workflow_lessons_serving_fails_on_missing_required_field(tmp_path):
    root = tmp_path / "workflow_lessons"
    row = _base_row()
    row.pop("title")
    _write_jsonl(root / "internal" / "lessons-catalog.jsonl", [row])

    with pytest.raises(workflow_lessons.WorkflowLessonsError, match="title"):
        workflow_lessons.build_workflow_lessons_serving(root)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("status", "draft"),
        ("working_mode", "general"),
        ("workflow_family", "travel"),
    ],
)
def test_build_workflow_lessons_serving_fails_on_invalid_enum(
    tmp_path, field_name, value
):
    root = tmp_path / "workflow_lessons"
    _write_jsonl(
        root / "internal" / "lessons-catalog.jsonl",
        [_base_row(**{field_name: value})],
    )

    with pytest.raises(workflow_lessons.WorkflowLessonsError, match=field_name):
        workflow_lessons.build_workflow_lessons_serving(root)


def test_build_workflow_lessons_serving_only_materializes_promoted_lessons(tmp_path):
    root = tmp_path / "workflow_lessons"
    rows = [
        _base_row(lesson_id="research_promoted"),
        _base_row(
            lesson_id="research_observed",
            status="observed",
            title="Observed Only",
            source_turn_ids=["turn:observed"],
        ),
        _base_row(
            lesson_id="offsec_repeated",
            status="repeated",
            working_mode="offsec",
            workflow_family="offsec",
            title="Repeated Only",
            source_turn_ids=["turn:repeated"],
        ),
        _base_row(
            lesson_id="offsec_promoted",
            working_mode="offsec",
            workflow_family="offsec",
            title="Offsec Promoted",
            source_turn_ids=["turn:offsec"],
        ),
    ]
    _write_jsonl(root / "internal" / "lessons-catalog.jsonl", rows)

    summary = workflow_lessons.build_workflow_lessons_serving(root)

    assert summary.promoted_count == 2
    lesson_paths = sorted(
        path.name for path in (root / "_serving" / "lessons").glob("*.md")
    )
    assert lesson_paths == ["offsec_promoted.md", "research_promoted.md"]

    research_index = (root / "_serving" / "families" / "research.md").read_text(
        encoding="utf-8"
    )
    offsec_index = (root / "_serving" / "families" / "offsec.md").read_text(
        encoding="utf-8"
    )
    assert "research_promoted.md" in research_index
    assert "research_observed.md" not in research_index
    assert "offsec_promoted.md" in offsec_index
    assert "offsec_repeated.md" not in offsec_index


def test_build_workflow_lessons_serving_is_idempotent(tmp_path):
    root = tmp_path / "workflow_lessons"
    _write_jsonl(
        root / "internal" / "lessons-catalog.jsonl",
        [
            _base_row(lesson_id="research_promoted"),
            _base_row(
                lesson_id="offsec_promoted",
                working_mode="offsec",
                workflow_family="offsec",
                title="Offsec Promoted",
                source_turn_ids=["turn:offsec"],
            ),
        ],
    )

    workflow_lessons.build_workflow_lessons_serving(root)
    first_snapshot = _serving_snapshot(root)

    workflow_lessons.build_workflow_lessons_serving(root)
    second_snapshot = _serving_snapshot(root)

    assert first_snapshot == second_snapshot


def test_build_workflow_lessons_serving_enforces_card_budget(tmp_path):
    root = tmp_path / "workflow_lessons"
    oversized_prefer = [
        " ".join(["long-budget-fragment"] * 200),
    ]
    _write_jsonl(
        root / "internal" / "lessons-catalog.jsonl",
        [_base_row(lesson_id="oversized_card", prefer=oversized_prefer)],
    )

    with pytest.raises(
        workflow_lessons.WorkflowLessonsError, match="oversized_card"
    ):
        workflow_lessons.build_workflow_lessons_serving(root)


def test_seed_catalog_matches_committed_serving_layer(tmp_path):
    repo_root = Path(__file__).resolve().parents[4]
    source_root = repo_root / "workflow_lessons"
    expected_snapshot = _serving_snapshot(source_root)

    build_root = tmp_path / "workflow_lessons"
    shutil.copytree(source_root / "internal", build_root / "internal")

    workflow_lessons.build_workflow_lessons_serving(build_root)
    actual_snapshot = _serving_snapshot(build_root)

    assert actual_snapshot == expected_snapshot
