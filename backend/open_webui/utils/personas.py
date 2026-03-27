from __future__ import annotations

from copy import deepcopy
from typing import Any, Optional

from open_webui.models.functions import Functions
from open_webui.models.personas import PersonaModel
from open_webui.models.skills import Skills
from open_webui.models.tools import Tools
from open_webui.utils.access_control import has_permission

PERSONA_DEFAULT_FEATURE_IDS = (
    "web_search",
    "image_generation",
    "code_interpreter",
    "memory",
    "voice",
)


def build_persona_defaults_snapshot(persona: PersonaModel) -> dict[str, Any]:
    return {
        "bound_model_id": persona.bound_model_id,
        "system_prompt": persona.system_prompt,
        "greeting": persona.greeting,
        "voice_id": persona.voice_id,
        "voice_speed": persona.voice_speed,
        "tool_ids": list(persona.tool_ids or []),
        "skill_ids": list(persona.skill_ids or []),
        "filter_ids": list(persona.filter_ids or []),
        "action_ids": list(persona.action_ids or []),
        "default_feature_ids": list(persona.default_feature_ids or []),
        "capabilities": deepcopy(persona.capabilities or {}),
    }


def _merge_requested_state(
    snapshot: dict[str, Any],
    persisted_overrides: Optional[dict[str, Any]] = None,
    request_overrides: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    requested = deepcopy(snapshot or {})

    for overrides in (persisted_overrides or {}, request_overrides or {}):
        for key, value in overrides.items():
            requested[key] = deepcopy(value)

    requested.setdefault("tool_ids", [])
    requested.setdefault("skill_ids", [])
    requested.setdefault("filter_ids", [])
    requested.setdefault("action_ids", [])
    requested.setdefault("default_feature_ids", [])
    requested.setdefault("capabilities", {})

    return requested


def _available_tool_ids(user_id: str) -> set[str]:
    return {tool.id for tool in Tools.get_tools_by_user_id(user_id, permission="read")}


def _available_skill_ids(user_id: str) -> set[str]:
    return {
        skill.id
        for skill in Skills.get_skills_by_user_id(user_id, permission="read")
        if skill.is_active
    }


def _active_function_ids(function_type: str) -> set[str]:
    return {
        function.id
        for function in Functions.get_functions_by_type(function_type, active_only=True)
    }


def _sanitize_feature_ids(
    request,
    user,
    requested_feature_ids: list[str],
    model_capabilities: dict[str, Any],
) -> list[str]:
    feature_permissions = {
        "web_search": ("features.web_search", request.app.state.config.ENABLE_WEB_SEARCH),
        "image_generation": (
            "features.image_generation",
            request.app.state.config.ENABLE_IMAGE_GENERATION,
        ),
        "code_interpreter": (
            "features.code_interpreter",
            request.app.state.config.ENABLE_CODE_INTERPRETER,
        ),
        "memory": ("features.memories", request.app.state.config.ENABLE_MEMORIES),
    }

    sanitized: list[str] = []
    for feature_id in requested_feature_ids:
        if feature_id == "voice":
            sanitized.append(feature_id)
            continue

        if feature_id not in PERSONA_DEFAULT_FEATURE_IDS:
            continue

        permission_key, enabled = feature_permissions.get(feature_id, (None, True))
        if not enabled:
            continue

        if permission_key and user.role != "admin" and not has_permission(
            user.id, permission_key, request.app.state.config.USER_PERMISSIONS
        ):
            continue

        if feature_id in model_capabilities and not bool(model_capabilities.get(feature_id)):
            continue

        sanitized.append(feature_id)

    return sanitized


def resolve_effective_persona_state(
    chat,
    persona: PersonaModel,
    user,
    runtime_context: dict[str, Any],
) -> dict[str, Any]:
    request = runtime_context["request"]
    snapshot = deepcopy(
        runtime_context.get("snapshot")
        or (chat.meta if chat and isinstance(chat.meta, dict) else {}).get(
            "persona_defaults_snapshot"
        )
        or build_persona_defaults_snapshot(persona)
    )
    persisted_overrides = deepcopy(
        runtime_context.get("persisted_overrides")
        or (chat.meta if chat and isinstance(chat.meta, dict) else {}).get(
            "persona_chat_overrides"
        )
        or {}
    )
    request_overrides = deepcopy(runtime_context.get("request_overrides") or {})

    requested = _merge_requested_state(snapshot, persisted_overrides, request_overrides)

    model = runtime_context.get("model")
    model_capabilities = (
        (model or {}).get("info", {}).get("meta", {}).get("capabilities", {}) or {}
    )

    requested_bound_model_id = requested.get("bound_model_id") or persona.bound_model_id

    available_tool_ids = _available_tool_ids(user.id)
    available_skill_ids = _available_skill_ids(user.id)
    active_filter_ids = _active_function_ids("filter")
    active_action_ids = _active_function_ids("action")

    requested_tool_ids = list(requested.get("tool_ids") or [])
    requested_skill_ids = list(requested.get("skill_ids") or [])
    requested_filter_ids = list(requested.get("filter_ids") or [])
    requested_action_ids = list(requested.get("action_ids") or [])
    requested_feature_ids = list(requested.get("default_feature_ids") or [])

    effective_tool_ids = [tool_id for tool_id in requested_tool_ids if tool_id in available_tool_ids]
    effective_skill_ids = [
        skill_id for skill_id in requested_skill_ids if skill_id in available_skill_ids
    ]
    effective_filter_ids = [
        filter_id for filter_id in requested_filter_ids if filter_id in active_filter_ids
    ]
    effective_action_ids = [
        action_id for action_id in requested_action_ids if action_id in active_action_ids
    ]
    effective_feature_ids = _sanitize_feature_ids(
        request, user, requested_feature_ids, model_capabilities
    )

    requested_system_prompt = requested.get("system_prompt")
    effective_system_prompt = requested_system_prompt
    system_prompt_override_present = requested_system_prompt is not None

    voice_speed = requested.get("voice_speed")
    if request.app.state.config.TTS_ENGINE == "kokoro_onnx":
        try:
            voice_speed = float(voice_speed) if voice_speed is not None else None
        except Exception:
            voice_speed = None
        if voice_speed is not None:
            voice_speed = max(0.5, min(2.0, voice_speed))
    else:
        voice_speed = voice_speed if isinstance(voice_speed, (int, float)) else None

    effective_capabilities = deepcopy(requested.get("capabilities") or {})
    if model_capabilities:
        for key, value in list(effective_capabilities.items()):
            if key in model_capabilities:
                effective_capabilities[key] = bool(value) and bool(model_capabilities.get(key))

    return {
        "snapshot": snapshot,
        "requested": requested,
        "effective": {
            "bound_model_id": requested_bound_model_id,
            "system_prompt": effective_system_prompt,
            "system_prompt_override_present": system_prompt_override_present,
            "voice_id": requested.get("voice_id"),
            "voice_speed": voice_speed,
            "tool_ids": effective_tool_ids,
            "skill_ids": effective_skill_ids,
            "filter_ids": effective_filter_ids,
            "action_ids": effective_action_ids,
            "default_feature_ids": effective_feature_ids,
            "capabilities": effective_capabilities,
        },
    }
