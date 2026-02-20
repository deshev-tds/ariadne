"""
title: Simon Cognitive Engine
author: Simon
version: 0.1.0
description: Proxy-only pipe model that routes turns through Simon's cognitive engine.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field


class Pipe:
    class Valves(BaseModel):
        simon_default_model: str = Field(
            default="",
            description="Default Simon backend model ID (LM Studio/OpenAI-compatible).",
        )
        enable_deep_mode: bool = Field(
            default=True,
            description="Allow Simon gatekeeper to route complex turns into Deep Mode.",
        )
        emit_trace_status: bool = Field(
            default=False,
            description="Emit extra hidden status events with Simon routing/RAG trace data.",
        )
        max_status_events_per_turn: int = Field(
            default=8,
            ge=0,
            le=50,
            description="Maximum number of status events emitted to OpenWebUI per turn.",
        )

    def __init__(self):
        self.type = "pipe"
        self.valves = self.Valves()
        self._bridge = None
        self._bridge_config = None

    def _ensure_bridge(self):
        try:
            from simon_openwebui.bridge import SimonBridge
        except Exception as exc:
            raise RuntimeError(
                "Failed to import Simon bridge. Install Simon package first "
                "(e.g. `pip install -e /Users/damyandeshev/projects/simon`)."
            ) from exc

        bridge_config = (
            self.valves.simon_default_model,
            self.valves.enable_deep_mode,
            self.valves.emit_trace_status,
            int(self.valves.max_status_events_per_turn),
        )

        if self._bridge is None or self._bridge_config != bridge_config:
            self._bridge = SimonBridge(
                default_model=self.valves.simon_default_model,
                enable_deep_mode=self.valves.enable_deep_mode,
                emit_trace_status=self.valves.emit_trace_status,
                max_status_events_per_turn=self.valves.max_status_events_per_turn,
            )
            self._bridge_config = bridge_config

        try:
            from simon_openwebui.types import SimonTurnRequest
        except Exception as exc:
            raise RuntimeError("Failed to import Simon bridge types.") from exc

        return self._bridge, SimonTurnRequest

    def _extract_latest_user_text(self, messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages or []):
            if not isinstance(message, dict):
                continue
            if (message.get("role") or "").strip() != "user":
                continue

            content = message.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text:
                    return text

            if isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            parts.append(text.strip())
                merged = "\n".join(parts).strip()
                if merged:
                    return merged

            if isinstance(content, dict):
                text = content.get("text") or content.get("content")
                if isinstance(text, str) and text.strip():
                    return text.strip()

        return ""

    async def _emit_status(self, __event_emitter__, payload: dict[str, Any]):
        if __event_emitter__ is None:
            return

        status_payload = dict(payload or {})
        status_payload.setdefault("action", "simon")
        status_payload.setdefault("done", False)
        await __event_emitter__({"type": "status", "data": status_payload})

    async def pipe(
        self,
        body: dict,
        __metadata__: dict | None = None,
        __chat_id__: str | None = None,
        __event_emitter__=None,
        __user__: dict | None = None,
        __request__=None,
    ):
        bridge, SimonTurnRequest = self._ensure_bridge()

        metadata = __metadata__ or {}
        messages = body.get("messages") or []
        if not isinstance(messages, list):
            raise RuntimeError("Invalid messages payload: expected list")

        user_text = self._extract_latest_user_text(messages)
        if not user_text:
            raise RuntimeError("No user message found in request payload")

        chat_id = __chat_id__ or metadata.get("chat_id") or metadata.get("session_id")
        if not chat_id:
            chat_id = "owui-local"

        session_id = bridge.resolve_session(
            chat_id=str(chat_id),
            model=self.valves.simon_default_model or None,
        )

        turn_request = SimonTurnRequest(
            chat_id=str(chat_id),
            session_id=int(session_id),
            messages=messages,
            user_text=user_text,
            user=__user__ or {},
            metadata=metadata,
        )

        streamed_delta = False
        final_fallback = ""

        try:
            async for event in bridge.run_turn(
                turn_request,
                model=self.valves.simon_default_model or None,
                enable_deep_mode=self.valves.enable_deep_mode,
                emit_trace_status=self.valves.emit_trace_status,
                max_status_events=self.valves.max_status_events_per_turn,
            ):
                if event.type == "status":
                    payload = event.data if isinstance(event.data, dict) else {
                        "description": str(event.data)
                    }
                    await self._emit_status(__event_emitter__, payload)
                elif event.type == "delta":
                    streamed_delta = True
                    yield str(event.data)
                elif event.type == "final":
                    final_fallback = str(event.data or "")
                elif event.type == "error":
                    message = (
                        event.data.get("message")
                        if isinstance(event.data, dict)
                        else str(event.data)
                    )
                    raise RuntimeError(message or "Simon bridge error")
                elif event.type == "done":
                    break
        except asyncio.CancelledError:
            raise

        if not streamed_delta and final_fallback:
            yield final_fallback
