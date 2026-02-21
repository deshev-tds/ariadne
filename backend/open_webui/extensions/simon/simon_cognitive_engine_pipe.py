from __future__ import annotations

from typing import Any, AsyncGenerator

from pydantic import BaseModel, Field

from open_webui.extensions.simon_engine.context_builder import extract_latest_user_text
from open_webui.extensions.simon_engine.engine import SimonEngine
from open_webui.extensions.simon_engine.types import SimonTurnRequest
from open_webui.models.simon_lex_index import update_worker_settings
from open_webui.models.users import UserModel, Users
from open_webui.utils.chat import generate_chat_completion


class Pipe:
    class Valves(BaseModel):
        simon_default_model: str = Field(
            default="",
            description="Pinned inner model used by the Simon engine",
        )
        enable_deep_mode: bool = Field(
            default=False,
            description="Enable deep-route retrieval path (deterministic V1 scaffold)",
        )
        emit_trace_status: bool = Field(
            default=False,
            description="Emit Simon routing and retrieval status events",
        )
        max_status_events_per_turn: int = Field(
            default=8,
            ge=1,
            le=64,
            description="Maximum status events emitted per user turn",
        )
        hot_cache_mode: str = Field(
            default="auto",
            description="Hot cache mode: auto, on, off",
            json_schema_extra={
                "input": {
                    "type": "select",
                    "options": ["auto", "on", "off"],
                }
            },
        )
        lex_queue_batch_size: int = Field(
            default=20,
            ge=1,
            le=256,
            description="Background lexical queue worker batch size",
        )
        lex_queue_poll_ms: int = Field(
            default=1200,
            ge=100,
            le=60_000,
            description="Background lexical queue worker polling interval",
        )

    def __init__(self):
        self.type = "pipe"
        self.id = "simon-cognitive-engine"
        self.name = "Simon Cognitive Engine"
        self.valves = self.Valves()

    def _resolve_target_model(self, current_model_id: str, request) -> str:
        target_model = str(getattr(self.valves, "simon_default_model", "") or "").strip()
        if not target_model:
            raise RuntimeError("Simon valve 'simon_default_model' is required")

        if target_model == current_model_id:
            raise RuntimeError("simon_default_model cannot point to the Simon pipe model")

        models = getattr(request.app.state, "MODELS", {}) or {}
        model_info = models.get(target_model)
        if not model_info:
            raise RuntimeError(f"Configured simon_default_model '{target_model}' was not found")

        if model_info.get("pipe"):
            raise RuntimeError("simon_default_model cannot be a pipe model")

        return target_model

    def _resolve_user_model(self, user_payload: dict[str, Any]) -> UserModel:
        try:
            return UserModel(**user_payload)
        except Exception:
            user_id = user_payload.get("id")
            if not user_id:
                raise RuntimeError("Simon pipe requires a valid user context")
            user_model = Users.get_user_by_id(user_id)
            if user_model is None:
                raise RuntimeError("Simon pipe could not resolve user context")
            return user_model

    async def _run_task_passthrough(
        self,
        *,
        body: dict,
        metadata: dict[str, Any],
        user_payload: dict[str, Any],
        request,
    ):
        target_model = self._resolve_target_model(str(body.get("model") or ""), request)
        user_model = self._resolve_user_model(user_payload)
        passthrough_body = {**body, "model": target_model, "metadata": metadata}
        response = await generate_chat_completion(
            request,
            passthrough_body,
            user=user_model,
            bypass_filter=False,
            bypass_system_prompt=False,
        )

        if isinstance(response, dict):
            return response
        return str(response or "")

    async def _emit_status(
        self,
        *,
        emitter,
        payload: dict[str, Any],
        emitted_count: int,
        max_events: int,
    ) -> int:
        if emitter is None:
            return emitted_count

        if emitted_count >= max_events:
            return emitted_count

        await emitter(
            {
                "type": "status",
                "data": payload,
            }
        )
        return emitted_count + 1

    async def _run_stream(
        self,
        *,
        engine: SimonEngine,
        turn_request: SimonTurnRequest,
        body: dict[str, Any],
        event_emitter,
    ) -> AsyncGenerator[str, None]:
        max_events = int(getattr(self.valves, "max_status_events_per_turn", 8) or 8)
        status_count = 0

        async for event in engine.run_turn(turn_request=turn_request, body=body):
            event_type = event.type

            if event_type == "status":
                if isinstance(event.data, dict):
                    status_count = await self._emit_status(
                        emitter=event_emitter,
                        payload=event.data,
                        emitted_count=status_count,
                        max_events=max_events,
                    )
                continue

            if event_type == "delta":
                yield str(event.data)
                continue

            if event_type == "sse":
                yield str(event.data)
                continue

            if event_type == "error":
                message = (
                    event.data.get("message")
                    if isinstance(event.data, dict)
                    else str(event.data)
                )
                raise RuntimeError(message)

            if event_type == "done":
                break

    async def _run_non_stream(
        self,
        *,
        engine: SimonEngine,
        turn_request: SimonTurnRequest,
        body: dict[str, Any],
        event_emitter,
    ) -> str:
        max_events = int(getattr(self.valves, "max_status_events_per_turn", 8) or 8)
        status_count = 0

        chunks: list[str] = []
        final_text = ""

        async for event in engine.run_turn(turn_request=turn_request, body=body):
            event_type = event.type

            if event_type == "status":
                if isinstance(event.data, dict):
                    status_count = await self._emit_status(
                        emitter=event_emitter,
                        payload=event.data,
                        emitted_count=status_count,
                        max_events=max_events,
                    )
                continue

            if event_type == "delta":
                chunks.append(str(event.data))
                continue

            if event_type == "final":
                final_text = str(event.data)
                continue

            if event_type == "error":
                message = (
                    event.data.get("message")
                    if isinstance(event.data, dict)
                    else str(event.data)
                )
                raise RuntimeError(message)

            if event_type == "done":
                break

        return final_text or "".join(chunks)

    async def pipe(
        self,
        body: dict,
        __metadata__: dict | None = None,
        __chat_id__: str | None = None,
        __task__: str | None = None,
        __event_emitter__=None,
        __user__: dict | None = None,
        __request__=None,
    ):
        if __request__ is None:
            raise RuntimeError("Simon pipe requires __request__ context")

        metadata = __metadata__ or {}
        user_payload = __user__ or {}
        messages = body.get("messages", [])

        if __task__:
            return await self._run_task_passthrough(
                body=body,
                metadata=metadata,
                user_payload=user_payload,
                request=__request__,
            )

        chat_id = str(
            __chat_id__
            or metadata.get("chat_id")
            or body.get("chat_id")
            or ""
        )
        lineage_anchor = metadata.get("parent_message_id")

        user_text = extract_latest_user_text(messages)
        if not user_text:
            raise RuntimeError("Simon pipe could not find the latest user text message")

        update_worker_settings(
            batch_size=getattr(self.valves, "lex_queue_batch_size", 20),
            poll_ms=getattr(self.valves, "lex_queue_poll_ms", 1200),
        )

        turn_request = SimonTurnRequest(
            chat_id=chat_id,
            lineage_anchor_message_id=str(lineage_anchor) if lineage_anchor else None,
            messages=messages,
            user_text=user_text,
            user=user_payload,
            metadata=metadata,
        )

        engine = SimonEngine(
            request=__request__,
            user_payload=user_payload,
            metadata=metadata,
            valves=self.valves,
        )

        if body.get("stream", False):
            return self._run_stream(
                engine=engine,
                turn_request=turn_request,
                body=body,
                event_emitter=__event_emitter__,
            )

        return await self._run_non_stream(
            engine=engine,
            turn_request=turn_request,
            body=body,
            event_emitter=__event_emitter__,
        )
