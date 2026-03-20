from __future__ import annotations

import logging
import re
from typing import Any, Optional

from open_webui.extensions.simon_engine.memory_intents import (
    MEMORY_SAVE_PATTERNS_BG,
    MEMORY_SAVE_PATTERNS_EN,
    detect_memory_save,
)
from open_webui.extensions.simon_engine.token_budget import estimate_tokens_from_text
from open_webui.internal.fork_memory_db import is_fork_memory_available
from open_webui.models.chats import Chats
from open_webui.models.ledger import Ledgers
from open_webui.utils.context_maintenance import merge_system_message
from open_webui.utils.misc import get_content_from_message, get_message_list

log = logging.getLogger(__name__)

_LEDGER_STATUS_ACTION = "ledger_memory"
_LEDGER_MODE_AGENTIC = "agentic"
_LEDGER_MODE_VIBE = "vibe"
_SAVE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (MEMORY_SAVE_PATTERNS_EN + MEMORY_SAVE_PATTERNS_BG)
]

_TRANSIENT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(marker|canary|nonce|secret|token|otp|one[- ]off|transient)\b",
        r"\b[a-z]+-[a-z]+-\d{3,}\b",
        r"\b[0-9a-f]{24,}\b",
        r"\b[A-Za-z0-9+/]{24,}={0,2}\b",
    ]
]

_TOOL_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\buse\s+([A-Za-z0-9_.:/-]+)\s+instead\s+of\s+([A-Za-z0-9_.:/-]+)\b",
        r"\bswitch(?:ing)?\s+to\s+([A-Za-z0-9_.:/-]+)\b",
        r"\bkeep\s+using\s+([A-Za-z0-9_.:/-]+)\b",
        r"\bползвай\s+([A-Za-z0-9_.:/-]+)\b",
        r"\bвместо\s+([A-Za-z0-9_.:/-]+)\b",
    ]
]

_ACTION_MODE_PATTERNS = [
    ("observe", re.compile(r"\b(read[- ]only|observe|passive|recon only|just inspect)\b", re.IGNORECASE)),
    ("advise", re.compile(r"\b(report|suggest|recommend|summarize|brief me)\b", re.IGNORECASE)),
    ("act", re.compile(r"\b(do it|execute|apply|run it|turn on|turn off|change it)\b", re.IGNORECASE)),
]

_CONFIRMATION_PATTERNS = [
    ("required", re.compile(r"\b(confirm|ask first|before you do anything|don't act without)\b", re.IGNORECASE)),
    ("selective", re.compile(r"\b(confirm only|ask before actuator|ask before changes)\b", re.IGNORECASE)),
    ("not_required", re.compile(r"\b(no need to confirm|act directly|go ahead automatically)\b", re.IGNORECASE)),
]

_EVIDENCE_PATTERNS = [
    ("required", re.compile(r"\b(cite sources|with sources|must cite|show evidence)\b", re.IGNORECASE)),
    ("preferred", re.compile(r"\b(prefer sources|prefer evidence|ground it)\b", re.IGNORECASE)),
]

_SIDE_EFFECT_PATTERNS = [
    ("disallow", re.compile(r"\b(no destructive actions|don't change|read only|no side effects)\b", re.IGNORECASE)),
    ("allow_bounded", re.compile(r"\b(only bounded actions|only lighting|only safe changes)\b", re.IGNORECASE)),
    ("allow", re.compile(r"\b(full control|you may act|you can execute changes)\b", re.IGNORECASE)),
]

_OUTPUT_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(report|summary|briefing|write-up)\b.*\b(findings|next steps|sources|citations)\b",
        r"\b(give me|want)\b.*\b(report|summary|briefing)\b",
    ]
]

_DECISION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\b(we(?:'ll| will) go with|let's use|use .* instead of|switch to|keep using)\b",
        r"\b(решаваме|нека ползваме|ползвай .* вместо|оставаме на)\b",
    ]
]

_TONE_PATTERNS = {
    "concise": re.compile(r"\b(concise|brief|short|кратко|сбито)\b", re.IGNORECASE),
    "dry": re.compile(r"\b(dry|dry humor|сухо|сух хумор)\b", re.IGNORECASE),
    "playful": re.compile(r"\b(playful|witty|закачливо|игриво)\b", re.IGNORECASE),
    "direct": re.compile(r"\b(direct|straight|директно)\b", re.IGNORECASE),
    "warm": re.compile(r"\b(warm|gentle|меко|топло)\b", re.IGNORECASE),
}

_AVOID_TONE_RE = re.compile(
    r"\b(avoid|don't be|not overly|без|не бъди)\b.*\b(enthusiastic|cheerful|overly enthusiastic|прекалено ентусиазиран)\b",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"[\"“”'`]{1}([^\"“”'`]{3,80})[\"“”'`]{1}")


def _flatten_content(message: dict[str, Any]) -> str:
    content = get_content_from_message(message) or ""
    return str(content).strip()


def _latest_user_message(messages: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return next((msg for msg in reversed(messages or []) if msg.get("role") == "user"), None)


def _recent_user_messages(messages: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    items = [msg for msg in messages or [] if msg.get("role") == "user"]
    return items[-limit:]


def _parse_ledger_mode(value: Any) -> Optional[str]:
    if value is None:
        return None
    return (
        _LEDGER_MODE_AGENTIC
        if str(value).strip().lower() == _LEDGER_MODE_AGENTIC
        else _LEDGER_MODE_VIBE
    )


def _resolve_selected_ledger_mode(
    *,
    chat_id: str | None,
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    metadata_params = (metadata or {}).get("params")
    if isinstance(metadata_params, dict):
        metadata_mode = _parse_ledger_mode(metadata_params.get("ledger_mode"))
        if metadata_mode is not None:
            return metadata_mode

    if not chat_id:
        return _LEDGER_MODE_VIBE

    try:
        chat = Chats.get_chat_by_id(chat_id)
        chat_payload = getattr(chat, "chat", None)
        if isinstance(chat_payload, dict):
            chat_params = chat_payload.get("params")
            if isinstance(chat_params, dict):
                persisted_mode = _parse_ledger_mode(chat_params.get("ledger_mode"))
                if persisted_mode is not None:
                    return persisted_mode
    except Exception as exc:
        log.debug("Failed to resolve persisted ledger mode for %s: %s", chat_id, exc)

    return _LEDGER_MODE_VIBE


def _contains_transient_content(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    return any(pattern.search(value) for pattern in _TRANSIENT_PATTERNS)


def _strip_explicit_memory_save(text: str) -> str:
    value = str(text or "").strip()
    for pattern in _SAVE_PATTERNS:
        value = pattern.sub("", value, count=1).strip(" :,-")
    return value.strip()


def _build_source_ids(messages: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for message in messages or []:
        message_id = str(message.get("id") or "").strip()
        if message_id and message_id not in out:
            out.append(message_id)
    return out


def _extract_agentic_candidates(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_user = _latest_user_message(messages)
    if not latest_user:
        return []

    user_text = _flatten_content(latest_user)
    if not user_text:
        return []

    recent_messages = messages[-6:]
    source_ids = _build_source_ids(recent_messages)
    candidates: list[dict[str, Any]] = []

    if detect_memory_save(user_text):
        remembered = _strip_explicit_memory_save(user_text)
        if remembered and not _contains_transient_content(remembered):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "decision",
                    "content": remembered,
                    "rationale": "Explicit memory save override.",
                    "source_message_ids": source_ids,
                    "confidence": 1.0,
                }
            )

    for value, pattern in _ACTION_MODE_PATTERNS:
        if pattern.search(user_text):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "action_mode",
                    "content": value,
                    "rationale": "Detected durable action mode from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.85,
                }
            )
            break

    for value, pattern in _CONFIRMATION_PATTERNS:
        if pattern.search(user_text):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "confirmation_policy",
                    "content": value,
                    "rationale": "Detected confirmation policy from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.9,
                }
            )
            break

    for value, pattern in _EVIDENCE_PATTERNS:
        if pattern.search(user_text):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "evidence_policy",
                    "content": value,
                    "rationale": "Detected evidence policy from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.85,
                }
            )
            break

    for value, pattern in _SIDE_EFFECT_PATTERNS:
        if pattern.search(user_text):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "side_effect_policy",
                    "content": value,
                    "rationale": "Detected side effect policy from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.9,
                }
            )
            break

    for pattern in _TOOL_PATTERNS:
        match = pattern.search(user_text)
        if not match:
            continue
        tool_name = match.group(1).strip()
        if tool_name and not _contains_transient_content(tool_name):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "tooling",
                    "content": tool_name,
                    "rationale": "Detected stable tooling preference from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.88,
                }
            )
            break

    if any(pattern.search(user_text) for pattern in _OUTPUT_PATTERNS):
        candidates.append(
            {
                "ledger_kind": "agentic",
                "entry_type": "output_contract",
                "content": user_text,
                "rationale": "Detected durable output contract from the latest user turn.",
                "source_message_ids": source_ids,
                "confidence": 0.8,
            }
        )

    if any(pattern.search(user_text) for pattern in _DECISION_PATTERNS):
        normalized = user_text
        if not _contains_transient_content(normalized):
            candidates.append(
                {
                    "ledger_kind": "agentic",
                    "entry_type": "decision",
                    "content": normalized,
                    "rationale": "Detected durable operational decision from the latest user turn.",
                    "source_message_ids": source_ids,
                    "confidence": 0.82,
                }
            )

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        content = str(candidate.get("content") or "").strip()
        if not content or _contains_transient_content(content):
            continue
        key = (str(candidate["entry_type"]), content.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _extract_vibe_candidates(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    user_messages = _recent_user_messages(messages, limit=8)
    if len(user_messages) < 2:
        return []

    source_ids = _build_source_ids(user_messages[-4:])
    texts = [_flatten_content(message) for message in user_messages if _flatten_content(message)]
    recent_text = texts[-1]
    candidates: list[dict[str, Any]] = []

    matched_tones = [name for name, pattern in _TONE_PATTERNS.items() if pattern.search(recent_text)]
    if _AVOID_TONE_RE.search(recent_text):
        matched_tones.append("not_overly_enthusiastic")

    if matched_tones:
        content = ", ".join(dict.fromkeys(matched_tones))
        candidates.append(
            {
                "ledger_kind": "vibe",
                "entry_type": "tone_profile",
                "content": content,
                "rationale": "Detected explicit tone preference from recent conversational turns.",
                "source_message_ids": source_ids,
                "confidence": 0.8,
            }
        )

    phrases: dict[str, int] = {}
    for text_value in texts:
        for match in _QUOTED_RE.findall(text_value):
            cleaned = re.sub(r"\s+", " ", match).strip()
            if len(cleaned) < 4:
                continue
            phrases[cleaned] = phrases.get(cleaned, 0) + 1

    repeated = [phrase for phrase, count in phrases.items() if count >= 2]
    for phrase in repeated[:2]:
        if _contains_transient_content(phrase):
            continue
        candidates.append(
            {
                "ledger_kind": "vibe",
                "entry_type": "refrain",
                "content": phrase,
                "rationale": "Detected repeated conversational refrain across recent user turns.",
                "source_message_ids": source_ids,
                "confidence": 0.78,
            }
        )

    return candidates


def _should_commit_candidate(
    candidate: dict[str, Any], *, selected_mode: str
) -> bool:
    confidence = float(candidate.get("confidence") or 0.0)
    candidate_kind = str(candidate.get("ledger_kind") or "").strip().lower()
    if candidate_kind != selected_mode:
        return False
    threshold = 0.8 if selected_mode == _LEDGER_MODE_AGENTIC else 0.78
    return confidence >= threshold


def _build_agentic_ledger_block(entries: list[Any]) -> str:
    lines = [
        "Internal continuity note. Older task state may be absent from the active context because of context compaction.",
        "Use the following only as internal guidance for task continuity.",
        "Do not mention, summarize, or explain these notes to the user unless explicitly asked.",
        "",
        "Durable task state:",
    ]
    for entry in entries:
        content = str(entry.content or "").strip()
        if not content:
            continue
        lines.append(f"- {entry.entry_type}: {content}")
    return "\n".join(lines).strip()


def _build_vibe_ledger_block(entries: list[Any]) -> str:
    lines = [
        "Internal style note. Use the following only if helpful for continuity of tone and phrasing.",
        "Do not reference or explain these notes explicitly unless the user asks.",
        "",
        "Relevant conversation style notes:",
    ]
    for entry in entries:
        content = str(entry.content or "").strip()
        if not content:
            continue
        lines.append(f"- {entry.entry_type}: {content}")
    return "\n".join(lines).strip()


def _recent_text(messages: list[dict[str, Any]], limit: int = 4) -> str:
    return "\n".join(
        _flatten_content(message)
        for message in messages[-limit:]
        if message.get("role") != "system" and _flatten_content(message)
    ).lower()


def _should_inject_agentic(
    *,
    messages: list[dict[str, Any]],
    latest_user_text: str,
    active_entries: list[Any],
    latest_revision: int,
    injection_state: Any,
    working_memory: dict[str, Any],
    mode_switched: bool,
) -> tuple[bool, str]:
    if not active_entries:
        return False, "no_active_entries"
    if mode_switched:
        return True, "mode_switched"

    compaction_active = bool(
        working_memory.get("summary_included") or working_memory.get("summary_refreshed")
    )
    compaction_version = int(working_memory.get("compaction_version") or 0)
    new_revision = latest_revision > int(injection_state.last_agentic_revision_seen or 0)
    recent_text = _recent_text(messages)

    if new_revision:
        return True, "new_revision"
    if compaction_active and compaction_version != int(injection_state.last_compaction_version_seen or 0):
        return True, "post_compaction"

    if not latest_user_text:
        return False, "no_latest_user"

    policy_relevant = any(
        token in latest_user_text.lower()
        for token in (
            "use ",
            "switch",
            "keep",
            "report",
            "confirm",
            "source",
            "evidence",
            "policy",
            "constraint",
            "mode",
            "scope",
            "tool",
            "ползвай",
            "превключ",
            "доклад",
            "източник",
        )
    )
    if not policy_relevant:
        return False, "not_policy_relevant"

    for entry in active_entries:
        content = str(entry.content or "").strip().lower()
        if not content:
            continue
        if content in recent_text:
            return False, "recent_context_sufficient"

    return True, "policy_relevant"


def _should_inject_vibe(
    *,
    messages: list[dict[str, Any]],
    latest_user_text: str,
    active_entries: list[Any],
    latest_revision: int,
    injection_state: Any,
    working_memory: dict[str, Any],
    mode_switched: bool,
) -> tuple[bool, str]:
    if not active_entries:
        return False, "no_active_entries"
    if mode_switched:
        return True, "mode_switched"

    compaction_active = bool(
        working_memory.get("summary_included") or working_memory.get("summary_refreshed")
    )
    compaction_version = int(working_memory.get("compaction_version") or 0)
    new_revision = latest_revision > int(injection_state.last_vibe_revision_seen or 0)
    if new_revision:
        return True, "new_revision"
    if compaction_active and compaction_version != int(injection_state.last_compaction_version_seen or 0):
        return True, "post_compaction"

    if estimate_tokens_from_text(latest_user_text or "") > 64:
        return False, "turn_too_operational"
    if any(token in (latest_user_text or "").lower() for token in ("tool", "api", "file", "command", "endpoint", "report")):
        return False, "turn_too_operational"

    return False, "not_needed"


def _inject_ledger_block(
    messages: list[dict[str, Any]],
    *,
    block_text: str,
    original_system_message: Optional[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not block_text:
        return list(messages)

    current = list(messages)
    if current and current[0].get("role") == "system":
        first = dict(current[0])
        current_content = str(first.get("content") or "").strip()
        base_content = str((original_system_message or {}).get("content") or "").strip()
        if base_content and current_content.startswith(base_content):
            remainder = current_content[len(base_content) :].strip()
            parts = [base_content, block_text]
            if remainder:
                parts.append(remainder)
            first["content"] = "\n\n".join(part for part in parts if part).strip()
            current[0] = first
            return current

        if current_content:
            first["content"] = "\n\n".join([block_text, current_content]).strip()
            current[0] = first
            return current

        current[0] = merge_system_message(first, [block_text])
        return current

    merged = merge_system_message(original_system_message, [block_text])
    return [merged, *current] if merged else current


async def run_background_ledger_capture(
    *,
    chat_id: str,
    message_id: str,
    metadata: dict[str, Any] | None,
    event_emitter=None,
) -> dict[str, Any]:
    result = {
        "kind_considered": "none",
        "capture_candidates": 0,
        "commits": 0,
        "supersedes": 0,
    }
    if not is_fork_memory_available() or not chat_id or not message_id or str(chat_id).startswith("local:"):
        return result

    try:
        messages_map = Chats.get_messages_map_by_chat_id(chat_id)
        if not messages_map:
            return result
        history_messages = get_message_list(messages_map, message_id)
        if not history_messages:
            return result

        selected_mode = _resolve_selected_ledger_mode(chat_id=chat_id, metadata=metadata)
        result["kind_considered"] = selected_mode

        if selected_mode == _LEDGER_MODE_AGENTIC:
            candidates = _extract_agentic_candidates(history_messages)
        else:
            candidates = _extract_vibe_candidates(history_messages)
        result["capture_candidates"] = len(candidates)

        if candidates:
            Ledgers.record_event(
                chat_id,
                "candidate",
                ledger_kind=result["kind_considered"],
                payload={"count": len(candidates)},
            )

        for candidate in candidates:
            if not _should_commit_candidate(candidate, selected_mode=selected_mode):
                Ledgers.record_event(
                    chat_id,
                    "skip",
                    ledger_kind=candidate["ledger_kind"],
                    payload={
                        "entry_type": candidate["entry_type"],
                        "reason": "low_confidence_or_mode_mismatch",
                    },
                )
                continue

            upsert = Ledgers.upsert_entry(
                chat_id=chat_id,
                ledger_kind=candidate["ledger_kind"],
                entry_type=candidate["entry_type"],
                content=candidate["content"],
                rationale=candidate["rationale"],
                source_message_ids=candidate["source_message_ids"],
                confidence=float(candidate["confidence"]),
            )
            if upsert.get("committed"):
                result["commits"] += 1
                result["supersedes"] += int(upsert.get("superseded") or 0)
        if (metadata or {}).get("params", {}).get("debug_memory_telemetry") and event_emitter:
            await event_emitter(
                {
                    "type": "chat:memory:telemetry",
                    "data": {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "ledger": result,
                    },
                }
            )
    except Exception as exc:
        log.debug("Ledger capture failed for %s: %s", chat_id, exc)

    return result


def resolve_ledger_preview(
    *,
    chat_id: str | None,
    raw_history_messages: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    working_memory_telemetry: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    preview = {
        "kind_considered": "none",
        "should_inject": False,
        "injection_reason": "disabled",
        "active_entry_count": 0,
        "new_revision_seen": False,
        "block_text": "",
        "block_token_estimate": 0,
    }

    if not is_fork_memory_available() or not chat_id or str(chat_id).startswith("local:"):
        return preview

    history_messages = raw_history_messages or messages
    latest_user = _latest_user_message(history_messages)
    latest_user_text = _flatten_content(latest_user) if latest_user else ""
    selected_mode = _resolve_selected_ledger_mode(chat_id=chat_id, metadata=metadata)
    injection_state = Ledgers.get_injection_state(chat_id)
    mode_switched = bool(
        getattr(injection_state, "last_mode_seen", None)
        and getattr(injection_state, "last_mode_seen", None) != selected_mode
    )
    working_memory = working_memory_telemetry or {}

    if selected_mode == _LEDGER_MODE_AGENTIC:
        kind = _LEDGER_MODE_AGENTIC
        active_entries = Ledgers.get_active_entries(chat_id, kind)
        latest_revision = Ledgers.get_latest_revision(chat_id, kind)
        should_inject, reason = _should_inject_agentic(
            messages=messages,
            latest_user_text=latest_user_text,
            active_entries=active_entries,
            latest_revision=latest_revision,
            injection_state=injection_state,
            working_memory=working_memory,
            mode_switched=mode_switched,
        )
        block_text = _build_agentic_ledger_block(active_entries) if should_inject else ""
    else:
        kind = _LEDGER_MODE_VIBE
        active_entries = Ledgers.get_active_entries(chat_id, kind)
        latest_revision = Ledgers.get_latest_revision(chat_id, kind)
        should_inject, reason = _should_inject_vibe(
            messages=messages,
            latest_user_text=latest_user_text,
            active_entries=active_entries,
            latest_revision=latest_revision,
            injection_state=injection_state,
            working_memory=working_memory,
            mode_switched=mode_switched,
        )
        block_text = _build_vibe_ledger_block(active_entries) if should_inject else ""

    preview["kind_considered"] = kind
    preview["should_inject"] = bool(should_inject and block_text)
    preview["injection_reason"] = reason
    preview["active_entry_count"] = len(active_entries)
    preview["new_revision_seen"] = latest_revision > int(
        getattr(
            injection_state,
            "last_agentic_revision_seen" if kind == "agentic" else "last_vibe_revision_seen",
            0,
        )
        or 0
    )
    preview["block_text"] = block_text if should_inject else ""
    preview["block_token_estimate"] = estimate_tokens_from_text(block_text or "")
    return preview


async def maybe_apply_ledger(
    *,
    chat_id: str | None,
    raw_history_messages: list[dict[str, Any]] | None,
    messages: list[dict[str, Any]],
    original_system_message: Optional[dict[str, Any]],
    working_memory_telemetry: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    working_memory = working_memory_telemetry or {}
    telemetry = {
        "kind_considered": "none",
        "injected": False,
        "injected_kind": "none",
        "injection_reason": "disabled",
        "active_entry_count": 0,
        "new_revision_seen": False,
        "capture_candidates": 0,
        "commits": 0,
        "supersedes": 0,
    }

    preview = resolve_ledger_preview(
        chat_id=chat_id,
        raw_history_messages=raw_history_messages,
        messages=messages,
        working_memory_telemetry=working_memory_telemetry,
        metadata=metadata,
    )
    telemetry["kind_considered"] = str(preview.get("kind_considered") or "none")
    telemetry["active_entry_count"] = int(preview.get("active_entry_count") or 0)
    telemetry["new_revision_seen"] = bool(preview.get("new_revision_seen"))
    telemetry["injection_reason"] = str(preview.get("injection_reason") or "disabled")

    if not preview.get("should_inject") or not preview.get("block_text"):
        kind = telemetry["kind_considered"]
        Ledgers.record_event(
            chat_id,
            "skip",
            ledger_kind=kind,
            payload={
                "reason": telemetry["injection_reason"],
                "active_entry_count": telemetry["active_entry_count"],
            },
        )
        Ledgers.mark_mode_seen(chat_id=chat_id, ledger_mode=kind)
        return messages, telemetry

    updated_messages = _inject_ledger_block(
        messages,
        block_text=str(preview.get("block_text") or ""),
        original_system_message=original_system_message,
    )
    kind = telemetry["kind_considered"]
    latest_revision = Ledgers.get_latest_revision(chat_id, kind)
    Ledgers.mark_injected(
        chat_id=chat_id,
        ledger_kind=kind,
        revision_seen=latest_revision,
        compaction_version=int(working_memory.get("compaction_version") or 0),
    )
    Ledgers.record_event(
        chat_id,
        "inject",
        ledger_kind=kind,
        payload={
            "reason": telemetry["injection_reason"],
            "active_entry_count": telemetry["active_entry_count"],
            "estimated_tokens": int(preview.get("block_token_estimate") or 0),
        },
    )
    telemetry["injected"] = True
    telemetry["injected_kind"] = kind
    return updated_messages, telemetry
