from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from typing import Any, AsyncGenerator

from starlette.responses import StreamingResponse

from open_webui.extensions.simon_engine.context_builder import (
    build_context_messages,
    build_runtime_context,
)
from open_webui.extensions.simon_engine.gatekeeper import RLMGatekeeper
from open_webui.extensions.simon_engine.memory_intents import (
    detect_archive_recall,
    detect_memory_save,
)
from open_webui.extensions.simon_engine.persistence import persist_post_flight
from open_webui.extensions.simon_engine.retrieval import probe_retrieval, query_vector_memories
from open_webui.extensions.simon_engine.token_budget import estimate_tokens_from_messages
from open_webui.extensions.simon_engine.types import (
    GateDecision,
    GateContext,
    RetrievalProbe,
    SimonTurnEvent,
    SimonTurnRequest,
)
from open_webui.models.users import UserModel, Users
from open_webui.utils.chat import generate_chat_completion

log = logging.getLogger(__name__)

_DEFAULT_KV_BUDGET_TOKENS = 4096
_STANDARD_ANCHOR_BUDGET_TOKENS = 900
_DEEP_ANCHOR_BUDGET_TOKENS = 1300
_DEFAULT_FROZEN_MEMORY_K = 3
_DEFAULT_FROZEN_MEMORY_TTL_SEC = 6 * 60 * 60
_INJECTION_PREVIEW_LINES = 3

_FROZEN_SESSION_MEMORY_LOCK = threading.Lock()
_FROZEN_SESSION_MEMORY: dict[str, dict[str, Any]] = {}


def _dedupe_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        value = str(line or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _hash_lines(lines: list[str]) -> str:
    if not lines:
        return ""
    digest = hashlib.sha256("\n".join(lines).encode("utf-8", errors="ignore")).hexdigest()
    return digest[:16]


def _preview_lines(lines: list[str], max_items: int = _INJECTION_PREVIEW_LINES) -> list[str]:
    previews: list[str] = []
    for line in lines[: max(0, int(max_items))]:
        text = str(line or "").strip()
        previews.append(text[:180])
    return previews


def _normalize_session_scope(chat_id: str, session_id: str | None) -> str:
    cid = str(chat_id or "").strip() or "unknown_chat"
    sid = str(session_id or "").strip() or "default"
    return f"{cid}:{sid}"


def _prune_frozen_session_memory(ttl_sec: int) -> None:
    now = int(time.time())
    ttl = max(60, int(ttl_sec))
    stale_keys: list[str] = []
    for key, value in _FROZEN_SESSION_MEMORY.items():
        touched_at = int(value.get("touched_at", value.get("created_at", 0)) or 0)
        if touched_at <= 0 or (now - touched_at) > ttl:
            stale_keys.append(key)

    for key in stale_keys:
        _FROZEN_SESSION_MEMORY.pop(key, None)


def _get_frozen_session_entry(scope_key: str, ttl_sec: int) -> dict[str, Any] | None:
    with _FROZEN_SESSION_MEMORY_LOCK:
        _prune_frozen_session_memory(ttl_sec)
        entry = _FROZEN_SESSION_MEMORY.get(scope_key)
        if not entry:
            return None

        entry["touched_at"] = int(time.time())
        return {
            "lines": list(entry.get("lines", [])),
            "scores": list(entry.get("scores", [])),
            "seed_query": str(entry.get("seed_query") or ""),
            "created_at": int(entry.get("created_at", 0) or 0),
            "touched_at": int(entry.get("touched_at", 0) or 0),
        }


def _set_frozen_session_entry(
    scope_key: str,
    *,
    lines: list[str],
    scores: list[float],
    seed_query: str,
) -> None:
    now = int(time.time())
    with _FROZEN_SESSION_MEMORY_LOCK:
        _FROZEN_SESSION_MEMORY[scope_key] = {
            "lines": list(lines),
            "scores": list(scores),
            "seed_query": str(seed_query or ""),
            "created_at": now,
            "touched_at": now,
        }


class SimonEngine:
    def __init__(
        self,
        *,
        request,
        user_payload: dict[str, Any] | None,
        metadata: dict[str, Any] | None,
        valves,
    ):
        self.request = request
        self.user_payload = user_payload or {}
        self.metadata = metadata or {}
        self.valves = valves
        self.gatekeeper = RLMGatekeeper()

    def _coerce_user_model(self) -> UserModel | None:
        payload = dict(self.user_payload or {})
        payload.pop("valves", None)

        try:
            return UserModel(**payload)
        except Exception:
            user_id = payload.get("id")
            if not user_id:
                return None
            return Users.get_user_by_id(user_id)

    def _resolve_target_model(self, current_model_id: str) -> str:
        target_model = str(getattr(self.valves, "simon_default_model", "") or "").strip()
        if not target_model:
            raise ValueError("Simon valve 'simon_default_model' is required")

        if target_model == current_model_id:
            raise ValueError("simon_default_model cannot point to the Simon pipe model")

        models = getattr(self.request.app.state, "MODELS", {}) or {}
        model_info = models.get(target_model)
        if not model_info:
            raise ValueError(f"Configured simon_default_model '{target_model}' was not found")

        if model_info.get("pipe"):
            raise ValueError("simon_default_model cannot be a pipe model")

        return target_model

    @staticmethod
    def _extract_delta_from_chunk(chunk: dict[str, Any]) -> str:
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if not isinstance(choices, list) or not choices:
            return ""

        delta = choices[0].get("delta") or {}
        content = delta.get("content")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text_value = item.get("text")
                if isinstance(text_value, str):
                    parts.append(text_value)
            return "".join(parts)

        return ""

    @staticmethod
    def _extract_final_text(payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)

        return ""

    async def _iter_sse_data(self, body_iterator) -> AsyncGenerator[str, None]:
        buffer = ""

        async for chunk in body_iterator:
            if isinstance(chunk, bytes):
                text = chunk.decode("utf-8", errors="ignore")
            else:
                text = str(chunk)

            buffer += text

            while "\n\n" in buffer:
                raw_event, buffer = buffer.split("\n\n", 1)
                for line in raw_event.splitlines():
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    yield stripped[len("data:") :].strip()

        if buffer.strip().startswith("data:"):
            yield buffer.strip()[len("data:") :].strip()

    async def _stream_inner_response(
        self,
        response: StreamingResponse,
    ) -> AsyncGenerator[tuple[str, str], None]:
        async for data in self._iter_sse_data(response.body_iterator):
            if not data:
                continue

            if data == "[DONE]":
                yield "data: [DONE]", ""
                continue

            try:
                payload = json.loads(data)
            except Exception:
                continue

            if isinstance(payload, dict) and payload.get("error"):
                detail = payload.get("error", {}).get("detail") or payload.get("error")
                raise RuntimeError(str(detail))

            delta = self._extract_delta_from_chunk(payload)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}", delta

    def _status_event(self, action: str, description: str, done: bool = True) -> SimonTurnEvent:
        return SimonTurnEvent(
            type="status",
            data={
                "action": action,
                "description": description,
                "done": done,
            },
        )

    def _build_inner_payload(
        self,
        *,
        body: dict[str, Any],
        target_model: str,
        messages: list[dict[str, Any]],
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "stream": stream,
            "metadata": self.metadata,
        }

        passthrough_keys = [
            "temperature",
            "top_p",
            "top_k",
            "seed",
            "stop",
            "max_tokens",
            "max_completion_tokens",
            "presence_penalty",
            "frequency_penalty",
            "logit_bias",
            "reasoning_effort",
            "response_format",
            "stream_options",
        ]

        for key in passthrough_keys:
            if key in body:
                payload[key] = body[key]

        return payload

    def _session_scope_key(self, chat_id: str) -> str:
        return _normalize_session_scope(chat_id, self.metadata.get("session_id"))

    async def _resolve_frozen_memory_anchors(
        self,
        *,
        user_model: UserModel,
        scope_key: str,
        query_text: str,
    ) -> tuple[list[str], list[float], str]:
        frozen_k = max(1, int(getattr(self.valves, "frozen_memory_k", _DEFAULT_FROZEN_MEMORY_K) or _DEFAULT_FROZEN_MEMORY_K))
        ttl_sec = max(60, int(getattr(self.valves, "frozen_memory_ttl_sec", _DEFAULT_FROZEN_MEMORY_TTL_SEC) or _DEFAULT_FROZEN_MEMORY_TTL_SEC))
        freeze_enabled = bool(getattr(self.valves, "freeze_memory_per_session", True))

        if not freeze_enabled:
            memory_lines, vector_scores, _ = await query_vector_memories(
                request=self.request,
                user_model=user_model,
                query_text=query_text,
                k=frozen_k,
            )
            lines = _dedupe_lines(memory_lines)
            scores = [float(score) for score in (vector_scores or [])[: len(lines)]]
            return lines, scores, "stateless"

        entry = _get_frozen_session_entry(scope_key, ttl_sec=ttl_sec)
        if entry is not None:
            lines = _dedupe_lines(entry.get("lines", []))
            scores = [float(score) for score in entry.get("scores", [])[: len(lines)]]
            return lines, scores, "cache_hit"

        memory_lines, vector_scores, _ = await query_vector_memories(
            request=self.request,
            user_model=user_model,
            query_text=query_text,
            k=frozen_k,
        )

        frozen_lines = _dedupe_lines(memory_lines)
        frozen_scores = [float(score) for score in (vector_scores or [])[: len(frozen_lines)]]
        _set_frozen_session_entry(
            scope_key,
            lines=frozen_lines,
            scores=frozen_scores,
            seed_query=query_text,
        )
        return frozen_lines, frozen_scores, "bootstrapped"

    @staticmethod
    def _build_frozen_probe(lines: list[str], scores: list[float]) -> RetrievalProbe:
        frozen_lines = _dedupe_lines(lines)
        frozen_scores = [float(score) for score in scores[: len(frozen_lines)]]
        return RetrievalProbe(
            vector_memories=frozen_lines,
            vector_scores=frozen_scores,
            lexical_hits=[],
            lexical_lines=frozen_lines,
            metrics={
                "memory_hits": len(frozen_lines),
                "lexical_hits": 0,
                "source_mix": {
                    "memory": len(frozen_lines),
                    "lexical": 0,
                },
                "frozen_memory_hits": len(frozen_lines),
                "on_demand": False,
            },
        )

    @staticmethod
    def _merge_probe_with_frozen(
        *,
        probe: RetrievalProbe,
        frozen_lines: list[str],
        on_demand_reason: str,
    ) -> RetrievalProbe:
        merged_lines = _dedupe_lines([*frozen_lines, *(probe.lexical_lines or [])])
        metrics = dict(probe.metrics or {})
        metrics["frozen_memory_hits"] = len(frozen_lines)
        metrics["on_demand"] = True
        metrics["on_demand_reason"] = on_demand_reason

        return probe.model_copy(
            update={
                "lexical_lines": merged_lines,
                "metrics": metrics,
            }
        )

    @staticmethod
    def _build_injection_trace(
        *,
        scope_key: str,
        frozen_source: str,
        frozen_lines: list[str],
        combined_lines: list[str],
        on_demand: bool,
        on_demand_reason: str,
        query_text: str,
    ) -> dict[str, Any]:
        return {
            "scope_key": scope_key,
            "frozen_source": frozen_source,
            "frozen_anchor_count": len(frozen_lines),
            "frozen_anchor_hash": _hash_lines(frozen_lines),
            "combined_anchor_count": len(combined_lines),
            "combined_anchor_hash": _hash_lines(combined_lines),
            "combined_anchor_preview": _preview_lines(combined_lines),
            "on_demand": bool(on_demand),
            "on_demand_reason": on_demand_reason,
            "query_hash": _hash_lines([query_text]),
        }

    async def run_turn(
        self,
        *,
        turn_request: SimonTurnRequest,
        body: dict[str, Any],
    ) -> AsyncGenerator[SimonTurnEvent, None]:
        try:
            user_model = self._coerce_user_model()
            if user_model is None:
                raise ValueError("Simon engine requires a valid user context")
            target_model = self._resolve_target_model(str(body.get("model") or ""))

            runtime = build_runtime_context(
                chat_id=turn_request.chat_id,
                lineage_anchor_message_id=turn_request.lineage_anchor_message_id,
                body_messages=turn_request.messages,
                hot_cache_mode=getattr(self.valves, "hot_cache_mode", "auto"),
            )

            if getattr(self.valves, "emit_trace_status", False):
                yield self._status_event(
                    "simon_runtime",
                    f"hot_cache={runtime.hot_enabled} ({runtime.hot_mode_reason})",
                )

            recall_intent, explicit_recall, recall_query = detect_archive_recall(
                turn_request.user_text
            )
            save_intent = detect_memory_save(turn_request.user_text)
            query_text = recall_query or turn_request.user_text
            scope_key = self._session_scope_key(turn_request.chat_id)
            frozen_lines, frozen_scores, frozen_source = await self._resolve_frozen_memory_anchors(
                user_model=user_model,
                scope_key=scope_key,
                query_text=query_text,
            )

            deep_mode_enabled = bool(getattr(self.valves, "enable_deep_mode", False))
            if deep_mode_enabled:
                gate_context = GateContext(
                    session_tokens=estimate_tokens_from_messages(
                        [*runtime.warm_history, *runtime.hot_history]
                    ),
                    window_tokens=_DEFAULT_KV_BUDGET_TOKENS,
                    vector_scores=frozen_scores,
                    fts_hit_count=0,
                    query_len=len((query_text or "").strip()),
                )
                gate_decision = self.gatekeeper.evaluate(
                    gate_context,
                    user_query=query_text,
                    explicit_recall=explicit_recall,
                    soft_recall=recall_intent,
                    recent_history=runtime.warm_history,
                )
                deep_triggered = bool(gate_decision.trigger)
            else:
                gate_decision = GateDecision(
                    trigger=False,
                    reason="deep_mode_disabled",
                    metrics={
                        "debt": round(
                            estimate_tokens_from_messages([*runtime.warm_history, *runtime.hot_history])
                            / max(1, _DEFAULT_KV_BUDGET_TOKENS),
                            3,
                        ),
                        "vector_best": round(max(frozen_scores) if frozen_scores else 0.0, 3),
                        "fts_hits": 0,
                        "query_len": len((query_text or "").strip()),
                        "explicit_recall": bool(explicit_recall),
                        "soft_recall": bool(recall_intent),
                    },
                )
                deep_triggered = False

            on_demand_enabled = bool(getattr(self.valves, "enable_on_demand_retrieval", True))
            on_demand_triggered = bool(on_demand_enabled and (recall_intent or deep_triggered))
            if deep_triggered:
                on_demand_reason = "deep_triggered"
            elif recall_intent:
                on_demand_reason = "recall_intent"
            else:
                on_demand_reason = "none"

            if getattr(self.valves, "emit_trace_status", False):
                route = "deep" if deep_triggered else "standard"
                yield self._status_event(
                    "simon_route",
                    (
                        f"route={route}; reason={gate_decision.reason}; "
                        f"frozen_source={frozen_source}; frozen_count={len(frozen_lines)}; "
                        f"on_demand={on_demand_triggered}"
                    ),
                )

            if on_demand_triggered:
                if getattr(self.valves, "emit_trace_status", False):
                    yield self._status_event("simon_probe", "Running on-demand retrieval probe")

                dynamic_probe = await probe_retrieval(
                    request=self.request,
                    user_model=user_model,
                    runtime=runtime,
                    query_text=query_text,
                    deep=deep_triggered,
                    memory_k=max(_DEFAULT_FROZEN_MEMORY_K, 5 if deep_triggered else _DEFAULT_FROZEN_MEMORY_K),
                    lexical_limit=10 if deep_triggered else 6,
                )
                probe = self._merge_probe_with_frozen(
                    probe=dynamic_probe,
                    frozen_lines=frozen_lines,
                    on_demand_reason=on_demand_reason,
                )
            else:
                probe = self._build_frozen_probe(frozen_lines, frozen_scores)

            anchor_budget = (
                _DEEP_ANCHOR_BUDGET_TOKENS
                if deep_triggered
                else _STANDARD_ANCHOR_BUDGET_TOKENS
            )

            context_messages = build_context_messages(
                original_messages=turn_request.messages,
                runtime=runtime,
                user_text=turn_request.user_text,
                anchor_lines=probe.lexical_lines,
                kv_budget_tokens=_DEFAULT_KV_BUDGET_TOKENS,
                anchor_budget_tokens=anchor_budget,
            )

            stream = bool(body.get("stream", False))
            inner_payload = self._build_inner_payload(
                body=body,
                target_model=target_model,
                messages=context_messages,
                stream=stream,
            )

            response = await generate_chat_completion(
                self.request,
                inner_payload,
                user=user_model,
                bypass_filter=False,
                bypass_system_prompt=False,
            )

            final_parts: list[str] = []

            if isinstance(response, StreamingResponse):
                saw_done = False
                async for raw_line, delta in self._stream_inner_response(response):
                    final_parts.append(delta)
                    yield SimonTurnEvent(type="sse", data=raw_line)
                    if raw_line == "data: [DONE]":
                        saw_done = True

                if not saw_done:
                    yield SimonTurnEvent(type="sse", data="data: [DONE]")
            elif isinstance(response, dict):
                final_text = self._extract_final_text(response)
                if final_text:
                    final_parts.append(final_text)
                    if stream:
                        yield SimonTurnEvent(type="delta", data=final_text)
            else:
                final_text = str(response or "")
                if final_text:
                    final_parts.append(final_text)
                    if stream:
                        yield SimonTurnEvent(type="delta", data=final_text)

            final_text = "".join(final_parts).strip()

            asyncio.create_task(
                persist_post_flight(
                    request=self.request,
                    user_payload=self.user_payload,
                    chat_id=turn_request.chat_id,
                    next_lineage_anchor_message_id=self.metadata.get("message_id"),
                    user_text=turn_request.user_text,
                    assistant_text=final_text,
                    save_intent=save_intent,
                    gate_decision=gate_decision,
                    retrieval_probe=probe,
                    deep_triggered=deep_triggered,
                    hot_enabled=runtime.hot_enabled,
                    injection_trace=self._build_injection_trace(
                        scope_key=scope_key,
                        frozen_source=frozen_source,
                        frozen_lines=frozen_lines,
                        combined_lines=probe.lexical_lines,
                        on_demand=on_demand_triggered,
                        on_demand_reason=on_demand_reason,
                        query_text=query_text,
                    ),
                )
            )

            if final_text:
                yield SimonTurnEvent(type="final", data=final_text)
            yield SimonTurnEvent(type="done", data={"done": True})
        except Exception as exc:
            log.exception("Simon engine turn failed: %s", exc)
            yield SimonTurnEvent(type="error", data={"message": str(exc)})
            yield SimonTurnEvent(type="done", data={"done": True})
