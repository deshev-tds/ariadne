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
from open_webui.utils.workflow_diary_materializer import (  # noqa: E402
    default_runtime_workflow_lessons_root,
    materialize_workflow_diary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize workflow diary packets into entries and a runtime lessons catalog."
    )
    parser.add_argument(
        "--artifacts-root",
        default=str(AGENTIC_ARTIFACTS_DIR),
        help="Agentic artifacts root (default: %(default)s)",
    )
    parser.add_argument(
        "--runtime-root",
        default=None,
        help="Runtime workflow-lessons root (default: <artifacts-root>/_workflow_lessons_runtime)",
    )
    parser.add_argument("--chat-id", default=None, help="Optional chat id filter")
    parser.add_argument(
        "--message-id",
        default=None,
        help="Optional message id filter (requires --chat-id)",
    )
    parser.add_argument(
        "--min-age-minutes",
        type=int,
        default=15,
        help="Minimum packet age before materialization (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute results without writing entries or runtime catalog",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.message_id and not args.chat_id:
        print("--message-id requires --chat-id", file=sys.stderr)
        return 2

    runtime_root = (
        Path(args.runtime_root).expanduser().resolve()
        if args.runtime_root
        else default_runtime_workflow_lessons_root(args.artifacts_root)
    )

    summary = materialize_workflow_diary(
        artifacts_root=args.artifacts_root,
        runtime_root=runtime_root,
        chat_id=args.chat_id,
        message_id=args.message_id,
        min_age_minutes=args.min_age_minutes,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
