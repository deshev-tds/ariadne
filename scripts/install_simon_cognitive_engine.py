#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models
from open_webui.models.users import Users

FUNCTION_ID = "simon-cognitive-engine"
FUNCTION_NAME = "Simon Cognitive Engine"
PIPE_SOURCE = (
    ROOT
    / "backend"
    / "open_webui"
    / "extensions"
    / "simon"
    / "simon_cognitive_engine_pipe.py"
)

DEFAULT_FUNCTION_VALVES = {
    "simon_default_model": "",
    "enable_deep_mode": False,
    "emit_trace_status": False,
    "max_status_events_per_turn": 8,
    "hot_cache_mode": "auto",
    "lex_queue_batch_size": 20,
    "lex_queue_poll_ms": 1200,
}

MODEL_META = {
    "description": "Opt-in embedded Simon cognitive engine (text-only V1)",
    "capabilities": {
        "file_upload": False,
        "file_context": False,
        "web_search": False,
        "image_generation": False,
        "code_interpreter": False,
        "builtin_tools": False,
        "vision": False,
    },
}

MODEL_PARAMS = {
    "function_calling": "native",
    "stream_response": True,
}

MODEL_ACCESS_GRANTS = [
    {
        "principal_type": "user",
        "principal_id": "*",
        "permission": "read",
    }
]


def _read_pipe_source() -> str:
    if not PIPE_SOURCE.exists():
        raise FileNotFoundError(f"Pipe source not found: {PIPE_SOURCE}")
    return PIPE_SOURCE.read_text(encoding="utf-8")


def _resolve_owner_id() -> str:
    first_user = Users.get_first_user()
    if first_user:
        return first_user.id
    return "system"


def _upsert_function(owner_id: str, content: str) -> None:
    form = FunctionForm(
        id=FUNCTION_ID,
        name=FUNCTION_NAME,
        content=content,
        meta=FunctionMeta(
            description="Embedded Simon cognitive pipe for deterministic memory routing",
            manifest={
                "name": FUNCTION_NAME,
                "id": FUNCTION_ID,
                "version": "1.0.0",
            },
        ),
    )

    existing = Functions.get_function_by_id(FUNCTION_ID)
    if existing:
        Functions.update_function_by_id(
            FUNCTION_ID,
            {
                "name": FUNCTION_NAME,
                "type": "pipe",
                "content": content,
                "meta": form.meta.model_dump(),
                "is_active": True,
                "is_global": False,
            },
        )
    else:
        Functions.insert_new_function(owner_id, "pipe", form)
        Functions.update_function_by_id(
            FUNCTION_ID,
            {
                "is_active": True,
                "is_global": False,
            },
        )

    current_valves = Functions.get_function_valves_by_id(FUNCTION_ID) or {}
    next_valves = dict(DEFAULT_FUNCTION_VALVES)

    current_model = current_valves.get("simon_default_model")
    if isinstance(current_model, str) and current_model.strip():
        next_valves["simon_default_model"] = current_model.strip()

    Functions.update_function_valves_by_id(FUNCTION_ID, next_valves)


def _upsert_model(owner_id: str) -> None:
    model_form = ModelForm(
        id=FUNCTION_ID,
        base_model_id=None,
        name=FUNCTION_NAME,
        meta=ModelMeta(**MODEL_META),
        params=ModelParams(**MODEL_PARAMS),
        access_grants=MODEL_ACCESS_GRANTS,
        is_active=True,
    )

    existing = Models.get_model_by_id(FUNCTION_ID)
    if existing:
        Models.update_model_by_id(FUNCTION_ID, model_form)
    else:
        Models.insert_new_model(model_form, user_id=owner_id)


def main() -> None:
    owner_id = _resolve_owner_id()
    content = _read_pipe_source()

    _upsert_function(owner_id, content)
    _upsert_model(owner_id)

    print("Installed/updated Simon Cognitive Engine function and model.")
    print(f"function_id={FUNCTION_ID}")
    print(f"owner_id={owner_id}")
    print("Set valve 'simon_default_model' in the UI before first use.")


if __name__ == "__main__":
    main()
