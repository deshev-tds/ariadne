from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from open_webui.extensions.simon_engine.memory_intents import detect_archive_recall
from open_webui.extensions.simon_engine.token_budget import estimate_tokens_from_text
from open_webui.models.simon_lex_index import (
    _STOP_TOKENS,
    _WORD_RE,
    enqueue_missing_messages,
    flatten_content,
    is_supported_database,
    recursive_search,
)

log = logging.getLogger(__name__)

DEFAULT_RECALL_TIMEOUT_MS = 150
DEFAULT_RECALL_MAX_HITS = 3
DEFAULT_RECALL_SNIPPET_TOKEN_BUDGET = 300
_RECALL_STATUS_ACTION = "chat_recall"
_AMBIGUOUS_REFERENTIAL_MAX_TOKENS = 25
_BRANCH_RECENT_MIN_MESSAGES = 8
_BRANCH_RECENT_MAX_MESSAGES = 20

_EXPLICIT_REFERENCE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(earlier|previously|last time)\b",
        r"\b(we decided|we agreed|you said|i said|what was)\b",
        r"\b(по-рано|преди|решихме|уговорихме|ти каза|какво беше)\b",
    ]
]

_CONTINUATION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(continue|same|as before|stick with|keep using|use the same)\b",
        r"\b(продължи|същото|както преди|както решихме|остани на|ползвай пак)\b",
    ]
]

_AMBIGUOUS_REFERENTIAL_PATTERNS = [
    re.compile(
        pattern,
        re.IGNORECASE,
    )
    for pattern in [
        r"\b(?:the|that)\s+(?:other|previous|old|first)(?:\s+(?:one|tool|version|config|option|endpoint))?\b",
        r"\b(?:as|like)\s+we\s+(?:discussed|talked about|used|did)\b",
        r"\bwhat\s+happened\s+(?:to|with)\b",
        r"\b(?:оня|онова|онзи|този)\s+(?:другия|стария|предишния)(?:\s+(?:дето|който|вариант|тул))?\b",
        r"\b(?:стария|предишния)\s+(?:вариант|тул|конфиг|endpoint|ендпойнт)\b",
        r"\bкакто\s+(?:говорихме|обсъждахме|правихме)\b",
        r"\bкакво\s+стана\s+(?:с|със)\b",
    ]
]

_PATH_RE = re.compile(r"(?<!\w)(?:/[\w./:-]+|[\w.-]+\.(?:py|ts|tsx|js|json|yaml|yml|toml|md|txt|env|ini))(?!\w)")
_ENV_RE = re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")
_DOTTED_RE = re.compile(r"\b[a-zA-Z_][\w-]*(?:\.[a-zA-Z_][\w-]*)+\b")
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_ENDPOINT_RE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+/[\w./:-]*\b", re.IGNORECASE)
_CONTINUATION_FILLER_TOKENS = {
    "continue",
    "same",
    "before",
    "previous",
    "keep",
    "using",
    "use",
    "used",
    "next",
    "step",
    "again",
    "approach",
    "with",
    "as",
    "like",
    "продължи",
    "същото",
    "преди",
    "пак",
    "както",
    "ползвай",
    "ползвахме",
}


def resolve_chat_recall_enabled(request, user) -> bool:
    settings = getattr(user, "settings", None)
    if settings is None and isinstance(user, dict):
        settings = user.get("settings")

    ui_settings: dict[str, Any] = {}
    if hasattr(settings, "ui"):
        ui_settings = dict(getattr(settings, "ui") or {})
    elif isinstance(settings, dict):
        ui_settings = dict(settings.get("ui") or {})

    if "chatRecall" in ui_settings:
        return bool(ui_settings["chatRecall"])

    return bool(getattr(request.app.state.config, "ENABLE_CHAT_RECALL", False))


def resolve_recall_settings(request) -> dict[str, int]:
    config = request.app.state.config
    return {
        "timeout_ms": max(
            1,
            int(
                getattr(config, "CHAT_RECALL_TIMEOUT_MS", DEFAULT_RECALL_TIMEOUT_MS)
                or DEFAULT_RECALL_TIMEOUT_MS
            ),
        ),
        "max_hits": max(
            1,
            int(
                getattr(config, "CHAT_RECALL_MAX_HITS", DEFAULT_RECALL_MAX_HITS)
                or DEFAULT_RECALL_MAX_HITS
            ),
        ),
        "snippet_token_budget": max(
            64,
            int(
                getattr(
                    config,
                    "CHAT_RECALL_SNIPPET_TOKEN_BUDGET",
                    DEFAULT_RECALL_SNIPPET_TOKEN_BUDGET,
                )
                or DEFAULT_RECALL_SNIPPET_TOKEN_BUDGET
            ),
        ),
    }


def extract_branch_message_ids(history_messages: list[dict[str, Any]] | None) -> list[str]:
    out: list[str] = []
    for message in history_messages or []:
        message_id = str(message.get("id") or "").strip()
        if message_id:
            out.append(message_id)
    return out


def enqueue_branch_backfill(chat_id: str, history_messages: list[dict[str, Any]] | None) -> int:
    if not is_supported_database() or not chat_id or chat_id.startswith("local:"):
        return 0

    return enqueue_missing_messages(
        chat_id,
        extract_branch_message_ids(history_messages),
        priority=1,
    )


def _message_text(message: dict[str, Any]) -> str:
    return flatten_content(message.get("content"))


def _collect_context_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        text = _message_text(message)
        if text:
            parts.append(text)
    return "\n".join(parts).strip().lower()


def _query_tokens(text_value: str) -> list[str]:
    tokens = [
        token.lower()
        for token in _WORD_RE.findall(text_value or "")
        if len(token) >= 3 and token.lower() not in _STOP_TOKENS
    ]
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _continuation_query_tokens(text_value: str) -> list[str]:
    return [
        token
        for token in _query_tokens(text_value)
        if token.lower() not in _CONTINUATION_FILLER_TOKENS
    ]


def _normalized_word_count(text_value: str) -> int:
    return len(_WORD_RE.findall(text_value or ""))


def _extract_entities(text_value: str) -> list[str]:
    candidates: list[str] = []
    for match in _BACKTICK_RE.findall(text_value or ""):
        cleaned = match.strip()
        if cleaned:
            candidates.append(cleaned)
    for regex in (_PATH_RE, _ENV_RE, _DOTTED_RE, _ENDPOINT_RE):
        for match in regex.findall(text_value or ""):
            cleaned = match.strip()
            if cleaned:
                candidates.append(cleaned)

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)
    return out


def _has_local_resolution(
    *,
    context_text: str,
    entities: list[str],
    user_text: str,
) -> bool:
    if any(entity.lower() in context_text for entity in entities):
        return True

    query_tokens = _query_tokens(user_text)
    if not query_tokens:
        return False

    overlap = sum(1 for token in query_tokens if token in context_text)
    return overlap >= min(2, len(query_tokens))


def _is_ambiguous_referential(
    *,
    user_text: str,
    context_text: str,
    entities: list[str],
) -> bool:
    if _normalized_word_count(user_text) > _AMBIGUOUS_REFERENTIAL_MAX_TOKENS:
        return False

    if entities:
        return False

    if not any(pattern.search(user_text) for pattern in _AMBIGUOUS_REFERENTIAL_PATTERNS):
        return False

    if _has_local_resolution(
        context_text=context_text,
        entities=entities,
        user_text=user_text,
    ):
        return False

    return True


def detect_recall_need(messages: list[dict[str, Any]]) -> dict[str, Any]:
    if not messages:
        return {
            "trigger": False,
            "reason": "empty_messages",
            "mode": "none",
            "query_text": "",
            "depth": 0,
            "explicit": False,
        }

    latest_user_index = next(
        (idx for idx in range(len(messages) - 1, -1, -1) if messages[idx].get("role") == "user"),
        None,
    )
    if latest_user_index is None:
        return {
            "trigger": False,
            "reason": "no_latest_user",
            "mode": "none",
            "query_text": "",
            "depth": 0,
            "explicit": False,
        }

    user_text = _message_text(messages[latest_user_index]).strip()
    if not user_text:
        return {
            "trigger": False,
            "reason": "empty_latest_user",
            "mode": "none",
            "query_text": "",
            "depth": 0,
            "explicit": False,
        }

    recall_intent, explicit_recall, recall_query = detect_archive_recall(user_text)
    explicit_reference = explicit_recall or any(
        pattern.search(user_text) for pattern in _EXPLICIT_REFERENCE_PATTERNS
    )
    query_text = (recall_query or user_text).strip()

    if explicit_reference or recall_intent:
        return {
            "trigger": True,
            "reason": "explicit_reference",
            "mode": "fts",
            "query_text": query_text,
            "depth": 2,
            "explicit": True,
        }

    context_text = _collect_context_text(messages[:latest_user_index])
    entities = _extract_entities(user_text)
    missing_entities = [
        entity for entity in entities if entity.lower() not in context_text
    ]
    if missing_entities:
        return {
            "trigger": True,
            "reason": "entity_lookup_gap",
            "mode": "fts",
            "query_text": " ".join(missing_entities),
            "depth": 1,
            "explicit": False,
            "entities": missing_entities,
        }

    if any(pattern.search(user_text) for pattern in _CONTINUATION_PATTERNS):
        query_tokens = _continuation_query_tokens(user_text)
        overlap = sum(1 for token in query_tokens if token in context_text)
        if overlap < min(2, len(query_tokens) or 1):
            return {
                "trigger": True,
                "reason": "constraint_continuation_gap",
                "mode": "fts",
                "query_text": " ".join(query_tokens) if query_tokens else query_text,
                "depth": 1,
                "explicit": False,
            }

    if _is_ambiguous_referential(
        user_text=user_text,
        context_text=context_text,
        entities=entities,
    ):
        return {
            "trigger": True,
            "reason": "ambiguous_referential_gap",
            "mode": "branch_recent",
            "query_text": "",
            "depth": 0,
            "explicit": False,
        }

    return {
        "trigger": False,
        "reason": "live_context_sufficient",
        "mode": "none",
        "query_text": query_text,
        "depth": 0,
        "explicit": False,
    }


def _build_branch_recent_hits(
    *,
    branch_message_ids: list[str] | None,
    messages: list[dict[str, Any]],
    max_hits: int,
) -> list[dict[str, Any]]:
    if not branch_message_ids or not messages:
        return []

    message_lookup: dict[str, dict[str, Any]] = {}
    ordered_ids: list[str] = []
    for message in messages:
        message_id = str(message.get("id") or "").strip()
        if not message_id:
            continue
        message_lookup[message_id] = message
        ordered_ids.append(message_id)

    latest_user_index = next(
        (idx for idx in range(len(messages) - 1, -1, -1) if messages[idx].get("role") == "user"),
        None,
    )
    if latest_user_index is None:
        return []

    latest_user_id = str(messages[latest_user_index].get("id") or "").strip()
    cutoff_index = (
        branch_message_ids.index(latest_user_id)
        if latest_user_id and latest_user_id in branch_message_ids
        else len(branch_message_ids)
    )
    candidate_ids = branch_message_ids[:cutoff_index]
    if not candidate_ids:
        return []

    recent_window = candidate_ids[-max(_BRANCH_RECENT_MIN_MESSAGES, min(_BRANCH_RECENT_MAX_MESSAGES, len(candidate_ids))):]
    hits: list[dict[str, Any]] = []
    for message_id in reversed(recent_window):
        message = message_lookup.get(message_id)
        if not message:
            continue
        role = str(message.get("role") or "").strip()
        if role == "system":
            continue
        content = _message_text(message).strip()
        if not content:
            continue
        hits.append(
            {
                "message_id": message_id,
                "role": role or "assistant",
                "content": content,
            }
        )
        if len(hits) >= max_hits:
            break

    return hits


def _trim_snippet_to_budget(snippet: str, budget_tokens: int) -> str:
    value = re.sub(r"\s+", " ", snippet or "").strip()
    if not value:
        return ""

    while estimate_tokens_from_text(value) > budget_tokens and len(value) > 120:
        trim = max(32, len(value) // 10)
        value = value[:-trim].rstrip()
        if not value.endswith("..."):
            value = f"{value}..."

    return value


def _extract_snippet(content: str, query_text: str, budget_tokens: int) -> str:
    text_value = re.sub(r"\s+", " ", content or "").strip()
    if not text_value:
        return ""

    if estimate_tokens_from_text(text_value) <= budget_tokens:
        return text_value

    query_tokens = _query_tokens(query_text)
    lower_text = text_value.lower()
    start_index = -1
    for token in query_tokens:
        idx = lower_text.find(token)
        if idx >= 0 and (start_index < 0 or idx < start_index):
            start_index = idx

    target_chars = max(240, int(budget_tokens) * 4)
    if start_index < 0:
        snippet = text_value[:target_chars].rstrip()
        if len(text_value) > len(snippet):
            snippet = f"{snippet}..."
        return _trim_snippet_to_budget(snippet, budget_tokens)

    start = max(0, start_index - target_chars // 2)
    end = min(len(text_value), start + target_chars)
    snippet = text_value[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(text_value):
        snippet = f"{snippet}..."
    return _trim_snippet_to_budget(snippet, budget_tokens)


def build_evidence_message(
    hits: list[dict[str, Any]],
    *,
    query_text: str,
    max_hits: int,
    snippet_token_budget: int,
) -> Optional[dict[str, str]]:
    if not hits:
        return None

    lines: list[str] = ["Evidence from earlier conversation:"]
    used = 0
    for hit in hits:
        if used >= max_hits:
            break

        message_id = str(hit.get("message_id") or "").strip()
        role = str(hit.get("role") or "assistant").strip() or "assistant"
        content = str(hit.get("content") or "").strip()
        if not message_id or not content:
            continue

        snippet = _extract_snippet(content, query_text, snippet_token_budget)
        if not snippet:
            continue

        lines.append(f"[turn {message_id} | {role}]")
        lines.append(snippet)
        lines.append("")
        used += 1

    if used <= 0:
        return None

    lines.append(
        "Use these as raw evidence when answering about earlier conversation details. Prefer them over guesses."
    )
    return {
        "role": "system",
        "content": "\n".join(lines).strip(),
    }


async def emit_chat_recall_status(event_emitter, description: str, *, done: bool) -> None:
    if not event_emitter:
        return
    try:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": _RECALL_STATUS_ACTION,
                    "description": description,
                    "done": done,
                },
            }
        )
    except Exception:
        pass


async def maybe_apply_chat_recall(
    *,
    request,
    chat_id: str | None,
    branch_message_ids: list[str] | None,
    messages: list[dict[str, Any]],
    event_emitter,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result = {
        "triggered": False,
        "reason": "disabled",
        "evidence_injected": False,
        "timed_out": False,
        "hit_count": 0,
    }

    if (
        not is_supported_database()
        or not chat_id
        or str(chat_id).startswith("local:")
        or not messages
    ):
        return messages, result

    trigger = detect_recall_need(messages)
    result["reason"] = trigger["reason"]
    if not trigger.get("trigger"):
        return messages, result

    settings = resolve_recall_settings(request)
    result["triggered"] = True

    await emit_chat_recall_status(
        event_emitter,
        "Checking earlier conversation...",
        done=False,
    )

    try:
        if trigger.get("mode") == "branch_recent":
            hits = _build_branch_recent_hits(
                branch_message_ids=branch_message_ids,
                messages=messages,
                max_hits=max(settings["max_hits"], 2),
            )
        elif trigger.get("mode") == "fts":
            hits = await asyncio.wait_for(
                asyncio.to_thread(
                    recursive_search,
                    chat_id,
                    trigger["query_text"],
                    branch_message_ids=list(branch_message_ids or []),
                    limit=max(settings["max_hits"] * 2, 4),
                    depth=max(0, int(trigger.get("depth", 1))),
                    max_queries=10 if trigger.get("explicit") else 6,
                    max_branches=4 if trigger.get("explicit") else 3,
                ),
                timeout=settings["timeout_ms"] / 1000.0,
            )
        else:
            hits = []
    except asyncio.TimeoutError:
        result["timed_out"] = True
        await emit_chat_recall_status(
            event_emitter,
            "Continuing without earlier evidence",
            done=True,
        )
        return messages, result
    except Exception as exc:
        log.debug("Chat recall failed: %s", exc)
        await emit_chat_recall_status(
            event_emitter,
            "Continuing without earlier evidence",
            done=True,
        )
        return messages, result

    evidence_message = build_evidence_message(
        hits or [],
        query_text=trigger["query_text"],
        max_hits=settings["max_hits"],
        snippet_token_budget=settings["snippet_token_budget"],
    )
    result["hit_count"] = len(hits or [])
    if not evidence_message:
        await emit_chat_recall_status(
            event_emitter,
            "Continuing without earlier evidence",
            done=True,
        )
        return messages, result

    latest_user_index = next(
        (idx for idx in range(len(messages) - 1, -1, -1) if messages[idx].get("role") == "user"),
        None,
    )
    if latest_user_index is None:
        updated = [*messages, evidence_message]
    else:
        updated = [
            *messages[:latest_user_index],
            evidence_message,
            *messages[latest_user_index:],
        ]

    result["evidence_injected"] = True
    await emit_chat_recall_status(
        event_emitter,
        "Checking earlier conversation...",
        done=True,
    )
    return updated, result
