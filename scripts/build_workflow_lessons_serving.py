#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from open_webui.utils.workflow_lessons import (  # noqa: E402
    WorkflowLessonsError,
    build_workflow_lessons_serving,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the generated workflow-lessons serving layer."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=str(REPO_ROOT / "workflow_lessons"),
        help="Workflow lessons root (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = build_workflow_lessons_serving(args.root)
    except WorkflowLessonsError as exc:
        print(f"workflow-lessons build failed: {exc}", file=sys.stderr)
        return 1

    print(
        "Built workflow lessons serving layer:",
        f"{summary.promoted_count} promoted / {summary.lesson_count} total",
        f"-> {summary.serving_root}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
