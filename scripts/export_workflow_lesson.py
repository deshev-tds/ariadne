#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from open_webui.env import AGENTIC_ARTIFACTS_DIR  # noqa: E402
from open_webui.utils.workflow_lessons import WorkflowLessonsError  # noqa: E402
from open_webui.utils.workflow_lessons_review import (  # noqa: E402
    default_runtime_workflow_lessons_root,
    export_workflow_lesson_candidate,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export one repeated workflow lesson candidate into the curated catalog."
    )
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_workflow_lessons_root(AGENTIC_ARTIFACTS_DIR)),
        help="Runtime workflow-lessons root (default: %(default)s)",
    )
    parser.add_argument("--candidate-id", required=True, help="Repeated candidate id to export")
    parser.add_argument("--target-lesson-id", required=True, help="Curated lesson_id to write")
    parser.add_argument(
        "--target-title",
        default=None,
        help="Optional canonical title override; must match the registry-rendered title",
    )
    parser.add_argument(
        "--curated-root",
        default=None,
        help="Optional curated workflow_lessons root (default: repo workflow_lessons)",
    )
    parser.add_argument(
        "--registry-path",
        default=None,
        help="Workflow lesson taxonomy registry path (default: repo workflow_lessons/internal/taxonomy-registry.json)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Allow replacing an existing curated row by lesson_id or canonical signature",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate export without writing catalog or rebuilding serving",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = export_workflow_lesson_candidate(
            runtime_root=args.runtime_root,
            candidate_id=args.candidate_id,
            target_lesson_id=args.target_lesson_id,
            target_title=args.target_title,
            replace=args.replace,
            dry_run=args.dry_run,
            curated_root=args.curated_root,
            registry_path=args.registry_path,
        )
    except WorkflowLessonsError as exc:
        print(f"workflow-lessons export failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
