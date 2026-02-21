from __future__ import annotations

import asyncio
import json
import logging
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
from open_webui.extensions.simon_engine.retrieval import probe_retrieval
from open_webui.extensions.simon_engine.token_budget import estimate_tokens_from_messages
from open_webui.extensions.simon_engine.types import (
    GateContext,
    SimonTurnEvent,
    SimonTurnRequest,
)
from open_webui.models.users import UserModel, Users
from open_webui.utils.chat import generate_chat_completion

log = logging.getLogger(__name__)

_DEFAULT_KV_BUDGET_TOKENS = 4096
_STANDARD_ANCHOR_BUDGET_TOKENS = 900
_DEEP_ANCHOR_BUDGET_TOKENS = 1300


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
    ) -> AsyncGenerator[str, None]:
        async for data in self._iter_sse_data(response.body_iterator):
            if not data or data == "[DONE]":
                continue

            try:
                payload = json.loads(data)
            except Exception:
                continue

            if isinstance(payload, dict) and payload.get("error"):
                detail = payload.get("error", {}).get("detail") or payload.get("error")
                raise RuntimeError(str(detail))

            delta = self._extract_delta_from_chunk(payload)
            if delta:
                yield delta

    def _status_event(self, action: str, description: str, done: bool = False) -> SimonTurnEvent:
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
        ]

        for key in passthrough_keys:
            if key in body:
                payload[key] = body[key]

        return payload

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

            if getattr(self.valves, "emit_trace_status", False):
                yield self._status_event("simon_probe", "Running retrieval probe")

            probe = await probe_retrieval(
                request=self.request,
                user_model=user_model,
                runtime=runtime,
                query_text=query_text,
                deep=False,
            )

            gate_context = GateContext(
                session_tokens=estimate_tokens_from_messages(
                    [*runtime.warm_history, *runtime.hot_history]
                ),
                window_tokens=_DEFAULT_KV_BUDGET_TOKENS,
                vector_scores=probe.vector_scores,
                fts_hit_count=len(probe.lexical_hits),
                query_len=len((query_text or "").strip()),
            )

            gate_decision = self.gatekeeper.evaluate(
                gate_context,
                user_query=query_text,
                explicit_recall=explicit_recall,
                soft_recall=recall_intent,
                recent_history=runtime.warm_history,
            )

            deep_triggered = bool(gate_decision.trigger and getattr(self.valves, "enable_deep_mode", False))

            if getattr(self.valves, "emit_trace_status", False):
                route = "deep" if deep_triggered else "standard"
                yield self._status_event(
                    "simon_route",
                    f"route={route}; reason={gate_decision.reason}",
                )

            if deep_triggered:
                probe = await probe_retrieval(
                    request=self.request,
                    user_model=user_model,
                    runtime=runtime,
                    query_text=query_text,
                    deep=True,
                    memory_k=5,
                    lexical_limit=10,
                )

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
                async for delta in self._stream_inner_response(response):
                    final_parts.append(delta)
                    yield SimonTurnEvent(type="delta", data=delta)
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
                )
            )

            if final_text:
                yield SimonTurnEvent(type="final", data=final_text)
            yield SimonTurnEvent(type="done", data={"done": True})
        except Exception as exc:
            log.exception("Simon engine turn failed: %s", exc)
            yield SimonTurnEvent(type="error", data={"message": str(exc)})
            yield SimonTurnEvent(type="done", data={"done": True})
