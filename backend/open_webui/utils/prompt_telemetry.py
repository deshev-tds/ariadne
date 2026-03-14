import copy
import time
from typing import Any, Optional


PROMPT_TELEMETRY_VERSION = 1
PROMPT_TELEMETRY_MAX_ENTRIES = 24


def _is_debug_flag_enabled(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def is_prompt_telemetry_enabled(metadata: Optional[dict]) -> bool:
    params = ((metadata or {}).get("params", {}) or {})
    return _is_debug_flag_enabled(params.get("debug_prompt_telemetry"))


def _sanitize_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _sanitize_prompt_payload(item)
            for key, item in value.items()
            if str(key).lower() not in {"authorization", "api-key", "x-api-key"}
        }
    if isinstance(value, list):
        return [_sanitize_prompt_payload(item) for item in value]
    if isinstance(value, str) and value.startswith("data:"):
        header, _, _ = value.partition(",")
        return f"{header},[omitted]"
    return copy.deepcopy(value)


def _ensure_prompt_telemetry_state(target: dict) -> dict:
    telemetry = target.setdefault(
        "prompt_telemetry",
        {
            "enabled": True,
            "version": PROMPT_TELEMETRY_VERSION,
            "entries": [],
            "capped": False,
        },
    )
    entries = telemetry.get("entries")
    if not isinstance(entries, list):
        telemetry["entries"] = []
    return telemetry


def append_prompt_telemetry(
    request: Any,
    metadata: Optional[dict],
    *,
    provider: str,
    request_url: Optional[str],
    payload: dict,
) -> Optional[dict]:
    if not is_prompt_telemetry_enabled(metadata):
        return None

    entry = {
        "captured_at": int(time.time()),
        "provider": provider,
        "request_url": request_url,
        "model": payload.get("model"),
        "task": (metadata or {}).get("task"),
        "chat_id": (metadata or {}).get("chat_id"),
        "message_id": (metadata or {}).get("message_id"),
        "payload": _sanitize_prompt_payload(payload),
    }

    result = None
    targets: list[dict] = []
    if isinstance(metadata, dict):
        targets.append(metadata)

    request_state = getattr(request, "state", None)
    request_metadata = getattr(request_state, "metadata", None)
    if isinstance(request_metadata, dict) and request_metadata is not metadata:
        targets.append(request_metadata)

    for target in targets:
        telemetry = _ensure_prompt_telemetry_state(target)
        entries = telemetry.get("entries", [])
        if len(entries) >= PROMPT_TELEMETRY_MAX_ENTRIES:
            telemetry["capped"] = True
            continue
        entries.append(copy.deepcopy(entry))
        telemetry["entries"] = entries
        result = telemetry

    return result


def get_prompt_telemetry(request: Any, metadata: Optional[dict]) -> Optional[dict]:
    if isinstance(metadata, dict):
        telemetry = metadata.get("prompt_telemetry")
        if isinstance(telemetry, dict):
            return telemetry

    request_state = getattr(request, "state", None)
    request_metadata = getattr(request_state, "metadata", None)
    if isinstance(request_metadata, dict):
        telemetry = request_metadata.get("prompt_telemetry")
        if isinstance(telemetry, dict):
            return telemetry

    return None
