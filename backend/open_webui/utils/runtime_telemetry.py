from __future__ import annotations

import copy
import threading
import time
from collections import Counter, deque
from typing import Any, Optional


RUNTIME_TELEMETRY_MAX_EVENTS = 400
RUNTIME_TELEMETRY_MAX_MESSAGES = 120
RUNTIME_TELEMETRY_PREVIEW_CHARS = 220


def _now_ts() -> int:
    return int(time.time())


def _truncate_text(value: Any, *, max_chars: int = RUNTIME_TELEMETRY_PREVIEW_CHARS) -> str:
    text = value if isinstance(value, str) else str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _normalize_prompt_entries(payload: Any) -> list[dict[str, Any]]:
    entries = []
    if not isinstance(payload, dict):
        return entries

    for entry in payload.get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        item = {
            "captured_at": entry.get("captured_at"),
            "provider": entry.get("provider"),
            "request_url": entry.get("request_url"),
            "model": entry.get("model"),
            "task": entry.get("task"),
            "chat_id": entry.get("chat_id"),
            "message_id": entry.get("message_id"),
        }
        payload_body = entry.get("payload")
        if isinstance(payload_body, dict):
            item["message_count"] = len(payload_body.get("messages") or [])
            if payload_body.get("messages"):
                last_message = (payload_body.get("messages") or [])[-1]
                if isinstance(last_message, dict):
                    item["last_role"] = last_message.get("role")
                    item["last_content_preview"] = _truncate_text(
                        last_message.get("content", "")
                    )
        entries.append(item)
    return entries


def _normalize_runtime_event(kind: str, payload: Any) -> dict[str, Any]:
    if kind == "tool_journey":
        data = payload if isinstance(payload, dict) else {}
        event = {
            "phase": data.get("phase"),
            "kind": data.get("kind"),
            "tool": data.get("tool"),
            "call_id": data.get("call_id"),
            "duration_ms": data.get("duration_ms"),
            "status": data.get("status"),
            "error_class": data.get("error_class"),
            "task_kind": data.get("task_kind"),
            "operation": data.get("operation"),
            "actor": data.get("actor"),
            "model_id": data.get("model_id"),
            "active_model_id": data.get("active_model_id"),
            "selected_via": data.get("selected_via"),
            "route_source": data.get("route_source"),
            "fallback_used": bool(data.get("fallback_used", False)),
            "reason": data.get("reason"),
        }
        result_summary = data.get("result_summary")
        if isinstance(result_summary, dict):
            event["result_summary"] = copy.deepcopy(result_summary)
        return event

    if kind == "memory":
        data = payload if isinstance(payload, dict) else {}
        working = data.get("working_memory") or {}
        recall = data.get("recall") or {}
        ledger = data.get("ledger") or {}
        return {
            "chat_id": data.get("chat_id"),
            "message_id": data.get("message_id"),
            "working_memory": {
                "summary_included": bool(working.get("summary_included", False)),
                "request_tokens": working.get("request_tokens"),
                "anchor_message_count": working.get("anchor_message_count"),
                "tail_message_count": working.get("tail_message_count"),
                "fallback_used": bool(working.get("fallback_used", False)),
            },
            "recall": {
                "triggered": bool(recall.get("triggered", False)),
                "reason": recall.get("reason"),
                "mode": recall.get("mode"),
                "evidence_injected": bool(recall.get("evidence_injected", False)),
                "hit_count": recall.get("hit_count"),
                "fallback_used": bool(recall.get("fallback_used", False)),
                "fallback_mode": recall.get("fallback_mode"),
            },
            "ledger": {
                "injected": bool(ledger.get("injected", False)),
                "injected_kind": ledger.get("injected_kind"),
                "injection_reason": ledger.get("injection_reason"),
            }
            if isinstance(ledger, dict) and ledger
            else None,
        }

    if kind == "prompt":
        data = payload if isinstance(payload, dict) else {}
        entries = _normalize_prompt_entries(data)
        return {
            "entry_count": len(entries),
            "capped": bool(data.get("capped", False)),
            "entries": entries[-4:],
        }

    if kind == "science_orchestration":
        data = payload if isinstance(payload, dict) else {}
        return {
            "active": bool(data.get("active", False)),
            "classification": ((data.get("classification") or {}).get("classification")),
            "science_research_mode": data.get("science_research_mode"),
            "attached_corpora": list(data.get("attached_corpora") or []),
            "fallback_reason": data.get("fallback_reason"),
            "evidence_basis": data.get("evidence_basis"),
            "grounding_summary": copy.deepcopy(data.get("grounding_summary") or {}),
            "phase_timings_ms": copy.deepcopy(data.get("phase_timings_ms") or {}),
        }

    if kind == "medical_lane":
        data = payload if isinstance(payload, dict) else {}
        return {
            "decision": data.get("decision"),
            "fallback_reason": data.get("fallback_reason"),
            "corpus_compatible": data.get("corpus_compatible"),
            "relevance_score": data.get("relevance_score"),
            "freshness_score": data.get("freshness_score"),
            "topical_fit": data.get("topical_fit"),
            "usable_anchor_count": data.get("usable_anchor_count"),
            "contradiction_flag": data.get("contradiction_flag"),
        }

    if kind == "skill_usage":
        data = payload if isinstance(payload, dict) else {}
        return {
            "operation": data.get("operation"),
            "working_mode": data.get("working_mode"),
            "science_research_mode": data.get("science_research_mode"),
            "lane_default_skill_ids": list(data.get("lane_default_skill_ids") or []),
            "explicit_skill_ids": list(data.get("explicit_skill_ids") or []),
            "model_skill_ids": list(data.get("model_skill_ids") or []),
            "available_skill_ids": list(data.get("available_skill_ids") or []),
            "skill_id": data.get("skill_id"),
            "skill_name": data.get("skill_name"),
        }

    return {"preview": _truncate_text(payload)}


class RuntimeTelemetryTap:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._enabled = False
        self._started_at: Optional[int] = None
        self._event_seq = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=RUNTIME_TELEMETRY_MAX_EVENTS)
        self._message_order: deque[str] = deque(maxlen=RUNTIME_TELEMETRY_MAX_MESSAGES)
        self._message_summaries: dict[str, dict[str, Any]] = {}
        self._kind_counts: Counter[str] = Counter()

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._enabled = True
            self._started_at = _now_ts()
            self._event_seq = 0
            self._events.clear()
            self._message_order.clear()
            self._message_summaries.clear()
            self._kind_counts.clear()
            return self._snapshot_locked(limit=120)

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self._enabled = False
            return self._snapshot_locked(limit=120)

    def clear(self) -> dict[str, Any]:
        with self._lock:
            self._event_seq = 0
            self._events.clear()
            self._message_order.clear()
            self._message_summaries.clear()
            self._kind_counts.clear()
            if self._enabled and self._started_at is None:
                self._started_at = _now_ts()
            return self._snapshot_locked(limit=120)

    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def record(
        self,
        *,
        kind: str,
        payload: Any,
        chat_id: Optional[str] = None,
        message_id: Optional[str] = None,
        user_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            if not self._enabled:
                return

            ts = _now_ts()
            event = {
                "seq": self._event_seq,
                "ts": ts,
                "kind": kind,
                "chat_id": chat_id,
                "message_id": message_id,
                "user_id": user_id,
                "model_id": model_id,
                "payload": _normalize_runtime_event(kind, payload),
            }
            self._event_seq += 1
            self._events.append(event)
            self._kind_counts[kind] += 1
            self._update_message_summary_locked(event)

    def snapshot(self, *, limit: int = 120) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked(limit=limit)

    def _snapshot_locked(self, *, limit: int) -> dict[str, Any]:
        events = list(self._events)[-max(1, int(limit)) :]
        recent_messages = []
        for key in reversed(self._message_order):
            summary = self._message_summaries.get(key)
            if summary:
                recent_messages.append(copy.deepcopy(summary))

        total_events = sum(self._kind_counts.values())
        tool_journey_count = int(self._kind_counts.get("tool_journey", 0))
        model_activity_count = sum(
            1
            for event in self._events
            if event.get("kind") == "tool_journey"
            and (event.get("payload") or {}).get("kind") == "model_activity"
        )
        fallback_count = sum(
            1
            for event in self._events
            if bool((event.get("payload") or {}).get("fallback_used", False))
        )

        return {
            "enabled": self._enabled,
            "started_at": self._started_at,
            "buffer_size": self._events.maxlen,
            "message_buffer_size": self._message_order.maxlen,
            "total_events": total_events,
            "kind_counts": dict(self._kind_counts),
            "tool_journey_count": tool_journey_count,
            "model_activity_count": model_activity_count,
            "fallback_count": fallback_count,
            "recent_events": copy.deepcopy(events),
            "recent_messages": recent_messages,
        }

    def _update_message_summary_locked(self, event: dict[str, Any]) -> None:
        chat_id = event.get("chat_id")
        message_id = event.get("message_id")
        key = f"{chat_id}:{message_id}"
        if not chat_id or not message_id:
            return

        summary = self._message_summaries.get(key)
        if summary is None:
            if (
                self._message_order.maxlen
                and len(self._message_order) >= self._message_order.maxlen
            ):
                stale_key = self._message_order.popleft()
                self._message_summaries.pop(stale_key, None)
            summary = {
                "chat_id": chat_id,
                "message_id": message_id,
                "user_id": event.get("user_id"),
                "first_seen_at": event.get("ts"),
                "last_seen_at": event.get("ts"),
                "event_count": 0,
                "tool_event_count": 0,
                "model_activity_count": 0,
                "fallback_count": 0,
                "models": [],
                "active_models": [],
                "task_kinds": [],
                "operations": [],
                "memory": None,
                "prompt_entry_count": 0,
                "skill_event_count": 0,
                "skill_ids": [],
            }
            self._message_summaries[key] = summary
            self._message_order.append(key)
        else:
            try:
                self._message_order.remove(key)
            except ValueError:
                pass
            self._message_order.append(key)

        summary["last_seen_at"] = event.get("ts")
        summary["event_count"] += 1
        payload = event.get("payload") or {}

        if event.get("kind") == "tool_journey":
            summary["tool_event_count"] += 1
            if payload.get("kind") == "model_activity":
                summary["model_activity_count"] += 1

            model_id = payload.get("model_id")
            if model_id and model_id not in summary["models"]:
                summary["models"].append(model_id)

            active_model_id = payload.get("active_model_id")
            if active_model_id and active_model_id not in summary["active_models"]:
                summary["active_models"].append(active_model_id)

            task_kind = payload.get("task_kind")
            if task_kind and task_kind not in summary["task_kinds"]:
                summary["task_kinds"].append(task_kind)

            operation = payload.get("operation")
            if operation and operation not in summary["operations"]:
                summary["operations"].append(operation)

            if bool(payload.get("fallback_used", False)):
                summary["fallback_count"] += 1

        elif event.get("kind") == "memory":
            summary["memory"] = copy.deepcopy(payload)

        elif event.get("kind") == "prompt":
            summary["prompt_entry_count"] = int(payload.get("entry_count", 0) or 0)

        elif event.get("kind") == "skill_usage":
            summary["skill_event_count"] += 1
            for skill_id in (
                list(payload.get("lane_default_skill_ids") or [])
                + list(payload.get("explicit_skill_ids") or [])
                + list(payload.get("available_skill_ids") or [])
                + ([payload.get("skill_id")] if payload.get("skill_id") else [])
            ):
                if skill_id and skill_id not in summary["skill_ids"]:
                    summary["skill_ids"].append(skill_id)


runtime_telemetry = RuntimeTelemetryTap()
