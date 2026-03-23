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
from open_webui.utils.workflow_lessons import (  # noqa: E402
    WorkflowLessonsError,
)
from open_webui.utils.workflow_lessons_review import (  # noqa: E402
    default_runtime_workflow_lessons_root,
    review_runtime_workflow_lessons,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build repeated workflow lesson candidates from runtime observed rows."
    )
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_workflow_lessons_root(AGENTIC_ARTIFACTS_DIR)),
        help="Runtime workflow-lessons root (default: %(default)s)",
    )
    parser.add_argument(
        "--registry-path",
        default=None,
        help="Workflow lesson taxonomy registry path (default: repo workflow_lessons/internal/taxonomy-registry.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute repeated candidates without writing files",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = review_runtime_workflow_lessons(
            runtime_root=args.runtime_root,
            registry_path=args.registry_path,
            dry_run=args.dry_run,
        )
    except WorkflowLessonsError as exc:
        print(f"workflow-lessons review failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
