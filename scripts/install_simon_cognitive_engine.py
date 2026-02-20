#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


FUNCTION_ID = "simon_cognitive_engine"
FUNCTION_NAME = "Simon Cognitive Engine"
PIPE_FILE_PATH = (
    ROOT
    / "backend"
    / "open_webui"
    / "extensions"
    / "simon"
    / "simon_cognitive_engine_pipe.py"
)

MODEL_CAPABILITIES = {
    "file_upload": False,
    "file_context": False,
    "web_search": False,
    "image_generation": False,
    "code_interpreter": False,
    "builtin_tools": False,
    "vision": False,
}


SessionLocal = None
FunctionForm = None
FunctionMeta = None
Functions = None
ModelForm = None
ModelMeta = None
ModelParams = None
Models = None
Users = None


def _load_openwebui_modules():
    global SessionLocal
    global FunctionForm
    global FunctionMeta
    global Functions
    global ModelForm
    global ModelMeta
    global ModelParams
    global Models
    global Users

    from open_webui.internal.db import SessionLocal as _SessionLocal
    from open_webui.models.functions import (
        FunctionForm as _FunctionForm,
        FunctionMeta as _FunctionMeta,
        Functions as _Functions,
    )
    from open_webui.models.models import (
        ModelForm as _ModelForm,
        ModelMeta as _ModelMeta,
        ModelParams as _ModelParams,
        Models as _Models,
    )
    from open_webui.models.users import Users as _Users

    SessionLocal = _SessionLocal
    FunctionForm = _FunctionForm
    FunctionMeta = _FunctionMeta
    Functions = _Functions
    ModelForm = _ModelForm
    ModelMeta = _ModelMeta
    ModelParams = _ModelParams
    Models = _Models
    Users = _Users


def _resolve_owner_id(db):
    owner = Users.get_super_admin_user(db=db) or Users.get_first_user(db=db)
    if owner is None:
        raise RuntimeError("No user found in OpenWebUI DB; create an admin user first.")
    return owner.id


def _read_pipe_content() -> str:
    if not PIPE_FILE_PATH.exists():
        raise RuntimeError(f"Pipe source file not found: {PIPE_FILE_PATH}")
    return PIPE_FILE_PATH.read_text(encoding="utf-8")


def _upsert_function(db, owner_id: str, content: str):
    form = FunctionForm(
        id=FUNCTION_ID,
        name=FUNCTION_NAME,
        content=content,
        meta=FunctionMeta(description="Simon cognitive proxy pipe for OpenWebUI"),
    )

    existing = Functions.get_function_by_id(FUNCTION_ID, db=db)
    if existing:
        Functions.update_function_by_id(
            FUNCTION_ID,
            {
                "name": form.name,
                "content": form.content,
                "meta": form.meta.model_dump(),
                "type": "pipe",
                "is_active": True,
            },
            db=db,
        )
        return "updated"

    created = Functions.insert_new_function(owner_id, "pipe", form, db=db)
    if created is None:
        raise RuntimeError("Failed to create Simon pipe function record.")

    Functions.update_function_by_id(FUNCTION_ID, {"is_active": True}, db=db)
    return "created"


def _upsert_function_valves(db, args):
    current = Functions.get_function_valves_by_id(FUNCTION_ID, db=db) or {}
    merged = dict(current)

    merged["simon_default_model"] = args.simon_default_model or merged.get(
        "simon_default_model", ""
    )
    merged["enable_deep_mode"] = not args.disable_deep_mode
    merged["emit_trace_status"] = bool(args.emit_trace_status)
    merged["max_status_events_per_turn"] = int(args.max_status_events_per_turn)

    Functions.update_function_valves_by_id(FUNCTION_ID, merged, db=db)


def _upsert_model(db, owner_id: str):
    model_form = ModelForm(
        id=FUNCTION_ID,
        base_model_id=None,
        name=FUNCTION_NAME,
        meta=ModelMeta(
            description="Proxy-only Simon cognitive engine model",
            capabilities=MODEL_CAPABILITIES,
        ),
        params=ModelParams(function_calling="native"),
        is_active=True,
    )

    existing = Models.get_model_by_id(FUNCTION_ID, db=db)
    if existing:
        Models.update_model_by_id(FUNCTION_ID, model_form, db=db)
        return "updated"

    created = Models.insert_new_model(model_form, owner_id, db=db)
    if created is None:
        raise RuntimeError("Failed to create Simon model override record.")
    return "created"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or update the Simon Cognitive Engine pipe model artifacts in OpenWebUI."
    )
    parser.add_argument(
        "--simon-default-model",
        default="",
        help="Default backend model ID to store in function valves.",
    )
    parser.add_argument(
        "--disable-deep-mode",
        action="store_true",
        help="Set the default valve to disable Simon Deep Mode.",
    )
    parser.add_argument(
        "--emit-trace-status",
        action="store_true",
        help="Enable trace status events by default in valves.",
    )
    parser.add_argument(
        "--max-status-events-per-turn",
        type=int,
        default=8,
        help="Default valve value for max status events per turn.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    _load_openwebui_modules()
    content = _read_pipe_content()

    with SessionLocal() as db:
        owner_id = _resolve_owner_id(db)
        function_action = _upsert_function(db, owner_id, content)
        _upsert_function_valves(db, args)
        model_action = _upsert_model(db, owner_id)

    print(f"Function `{FUNCTION_ID}`: {function_action}")
    print(f"Model `{FUNCTION_ID}`: {model_action}")
    print("Simon Cognitive Engine registration complete.")


if __name__ == "__main__":
    main()
