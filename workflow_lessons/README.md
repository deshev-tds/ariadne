# Workflow Lessons

This workspace is the builder-only lessons substrate for Ariadne's workflow-learning epic.

Canonical internal source of truth:

- `internal/lessons-catalog.jsonl`

Generated model-facing serving layer:

- `_serving/`

Important rules:

- the internal catalog is for build, review, and later promotion logic
- the generated serving layer is the only future model-facing form
- only `promoted` lessons are materialized into `_serving/`
- raw diary packets or internal JSON rows should not be injected into live model context directly

Rebuild:

- `./.venv/bin/python scripts/build_workflow_lessons_serving.py`
