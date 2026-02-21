from __future__ import annotations

import os
import threading
from collections import deque
from typing import Any

from open_webui.extensions.simon_engine.token_budget import (
    clamp_retrieval_lines,
    estimate_tokens_from_message,
    estimate_tokens_from_messages,
    estimate_tokens_from_text,
)
from open_webui.extensions.simon_engine.types import SimonRuntimeContext
from open_webui.models.chats import Chats
from open_webui.models.simon_lex_index import flatten_content

HOT_RING_LIMIT = 16
_CONTEXT_RESERVE_TOKENS = 128

_HOT_CACHE_LOCK = threading.Lock()
_HOT_CACHE: dict[str, deque[dict[str, Any]]] = {}


def extract_latest_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if message.get("role") != "user":
            continue
        text = flatten_content(message.get("content"))
        if text:
            return text
    return ""


def resolve_hot_cache_mode(mode: str | None) -> tuple[bool, str]:
    value = str(mode or "auto").strip().lower()

    if value == "off":
        return False, "forced_off"
    if value == "on":
        return True, "forced_on"

    workers_raw = os.getenv("WEB_CONCURRENCY") or os.getenv("UVICORN_WORKERS") or "1"
    try:
        workers = int(workers_raw)
    except Exception:
        workers = 1

    if workers > 1:
        return False, f"auto_disabled_workers={workers}"

    return True, "auto_enabled"


def build_hot_key(chat_id: str, lineage_anchor_message_id: str | None) -> str:
    anchor = lineage_anchor_message_id or "root"
    return f"{chat_id}:{anchor}"


def _normalize_message(message: dict[str, Any]) -> dict[str, Any] | None:
    role = str(message.get("role") or "").strip().lower()
    if role not in {"system", "user", "assistant"}:
        return None

    content = flatten_content(message.get("content"))
    if not content:
        return None

    return {
        "role": role,
        "content": content,
    }


def _build_branch_chain(
    messages_map: dict[str, dict[str, Any]],
    message_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    if not messages_map or not message_id:
        return []

    chain: list[tuple[str, dict[str, Any]]] = []
    current_id: str | None = message_id

    while current_id:
        message = messages_map.get(current_id)
        if not message:
            break
        chain.insert(0, (current_id, message))
        parent_id = message.get("parentId")
        current_id = str(parent_id) if parent_id else None

    return chain


def _body_history_without_latest_user(
    body_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in body_messages or []:
        item = _normalize_message(message)
        if item:
            normalized.append(item)

    if normalized and normalized[-1].get("role") == "user":
        return normalized[:-1]
    return normalized


def _load_hot_history(hot_key: str) -> list[dict[str, Any]]:
    with _HOT_CACHE_LOCK:
        entries = _HOT_CACHE.get(hot_key)
        if not entries:
            return []
        return [dict(item) for item in entries]


def append_hot_turn(
    *,
    chat_id: str,
    lineage_anchor_message_id: str | None,
    user_text: str,
    assistant_text: str,
) -> None:
    if not chat_id:
        return

    hot_key = build_hot_key(chat_id, lineage_anchor_message_id)
    entries: list[dict[str, Any]] = []

    user_text = (user_text or "").strip()
    assistant_text = (assistant_text or "").strip()

    if user_text:
        entries.append({"role": "user", "content": user_text})
    if assistant_text:
        entries.append({"role": "assistant", "content": assistant_text})

    if not entries:
        return

    with _HOT_CACHE_LOCK:
        bucket = _HOT_CACHE.get(hot_key)
        if bucket is None:
            bucket = deque(maxlen=HOT_RING_LIMIT)
            _HOT_CACHE[hot_key] = bucket

        for item in entries:
            bucket.append(item)


def build_runtime_context(
    *,
    chat_id: str,
    lineage_anchor_message_id: str | None,
    body_messages: list[dict[str, Any]] | None,
    hot_cache_mode: str = "auto",
) -> SimonRuntimeContext:
    hot_enabled, hot_mode_reason = resolve_hot_cache_mode(hot_cache_mode)
    hot_key = build_hot_key(chat_id, lineage_anchor_message_id)

    warm_history: list[dict[str, Any]] = []
    branch_message_ids: list[str] = []
    messages_map: dict[str, dict[str, Any]] = {}

    if chat_id and not chat_id.startswith("local:"):
        messages_map = Chats.get_messages_map_by_chat_id(chat_id) or {}

        anchor_id = lineage_anchor_message_id
        if not anchor_id and messages_map:
            chat = Chats.get_chat_by_id(chat_id)
            anchor_id = (
                chat.chat.get("history", {}).get("currentId") if chat and chat.chat else None
            )

        if anchor_id and messages_map:
            chain = _build_branch_chain(messages_map, anchor_id)
            branch_message_ids = [item[0] for item in chain]
            for _, message in chain:
                normalized = _normalize_message(message)
                if normalized:
                    warm_history.append(normalized)

    if not warm_history:
        warm_history = _body_history_without_latest_user(body_messages or [])

    hot_history = _load_hot_history(hot_key) if hot_enabled else []

    return SimonRuntimeContext(
        chat_id=chat_id,
        lineage_anchor_message_id=lineage_anchor_message_id,
        hot_key=hot_key,
        hot_enabled=hot_enabled,
        hot_mode_reason=hot_mode_reason,
        warm_history=warm_history,
        hot_history=hot_history,
        branch_message_ids=branch_message_ids,
        messages_map=messages_map,
    )


def _dedupe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()

    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if not role or not content:
            continue

        key = f"{role}:{content}"
        if key in seen:
            continue

        seen.add(key)
        deduped.append({"role": role, "content": content})

    return deduped


def _collect_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    systems: list[dict[str, Any]] = []
    for message in messages or []:
        if message.get("role") != "system":
            continue
        normalized = _normalize_message(message)
        if normalized:
            systems.append(normalized)
    return systems


def build_context_messages(
    *,
    original_messages: list[dict[str, Any]],
    runtime: SimonRuntimeContext,
    user_text: str,
    anchor_lines: list[str],
    kv_budget_tokens: int,
    anchor_budget_tokens: int,
) -> list[dict[str, Any]]:
    safe_budget = max(512, int(kv_budget_tokens))
    safe_anchor_budget = max(0, int(anchor_budget_tokens))

    system_messages = _collect_system_messages(original_messages)

    trimmed_anchors = clamp_retrieval_lines(anchor_lines, safe_anchor_budget)
    if trimmed_anchors:
        anchors_block = "\n".join(f"- {line}" for line in trimmed_anchors)
        system_messages.append(
            {
                "role": "system",
                "content": (
                    "Deterministic memory anchors below are retrieved context. "
                    "Prefer exact anchors for historical recall and resolve conflicts by recency.\n"
                    f"{anchors_block}"
                ),
            }
        )

    history_candidates = _dedupe_messages([*runtime.warm_history, *runtime.hot_history])

    user_text = (user_text or "").strip()
    if not user_text:
        user_text = extract_latest_user_text(original_messages)

    used_tokens = estimate_tokens_from_messages(system_messages)
    used_tokens += estimate_tokens_from_text(user_text)

    remaining_for_history = max(0, safe_budget - used_tokens - _CONTEXT_RESERVE_TOKENS)

    selected_history: list[dict[str, Any]] = []
    consumed_history = 0

    for message in reversed(history_candidates):
        token_cost = estimate_tokens_from_message(message)
        if consumed_history + token_cost > remaining_for_history:
            break
        selected_history.insert(0, message)
        consumed_history += token_cost

    result = [*system_messages, *selected_history]
    if user_text:
        result.append({"role": "user", "content": user_text})

    return result
