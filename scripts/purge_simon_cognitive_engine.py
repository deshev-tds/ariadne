#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from open_webui.models.functions import Functions
from open_webui.models.models import Models

LEGACY_ID = "simon-cognitive-engine"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Purge legacy simon-cognitive-engine function/model records."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply deletion. Without this flag, the script only reports current status.",
    )
    return parser.parse_args()


def _present_or_missing(value: object | None) -> str:
    return "present" if value is not None else "missing"


def _print_status(function_record: object | None, model_record: object | None) -> None:
    print(f"id={LEGACY_ID}")
    print(f"function={_present_or_missing(function_record)}")
    print(f"model={_present_or_missing(model_record)}")


def main() -> int:
    args = parse_args()

    function_record = Functions.get_function_by_id(LEGACY_ID)
    model_record = Models.get_model_by_id(LEGACY_ID)

    if not args.apply:
        _print_status(function_record, model_record)
        print("dry_run=true")
        print("No changes applied. Re-run with --apply to purge records.")
        return 0

    deleted_function = False
    deleted_model = False

    if function_record is not None:
        deleted_function = Functions.delete_function_by_id(LEGACY_ID)
        if not deleted_function:
            print("error=failed_to_delete_function")
            return 1

    if model_record is not None:
        deleted_model = Models.delete_model_by_id(LEGACY_ID)
        if not deleted_model:
            print("error=failed_to_delete_model")
            return 1

    remaining_function = Functions.get_function_by_id(LEGACY_ID)
    remaining_model = Models.get_model_by_id(LEGACY_ID)

    _print_status(remaining_function, remaining_model)
    print("dry_run=false")
    print(f"deleted_function={deleted_function}")
    print(f"deleted_model={deleted_model}")
    if remaining_function is None and remaining_model is None:
        print("result=purge_complete")
        return 0

    print("error=records_still_present_after_delete")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
