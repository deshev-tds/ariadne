from __future__ import annotations

import logging
import time
from typing import Any

from open_webui.extensions.simon_engine.context_builder import append_hot_turn
from open_webui.extensions.simon_engine.types import GateDecision, RetrievalProbe
from open_webui.internal.db import get_db_context
from open_webui.models.chats import Chat
from open_webui.models.simon_lex_index import get_queue_depth
from open_webui.models.users import UserModel, Users
from open_webui.routers.memories import AddMemoryForm, add_memory

log = logging.getLogger(__name__)


def _coerce_user_model(user_payload: dict[str, Any] | None) -> UserModel | None:
    payload = user_payload or {}
    try:
        return UserModel(**payload)
    except Exception:
        user_id = payload.get("id")
        if not user_id:
            return None
        return Users.get_user_by_id(user_id)


def _increment_counter(container: dict[str, Any], key: str, value: int = 1) -> None:
    container[key] = int(container.get(key, 0)) + int(value)


def _update_chat_meta(
    *,
    chat_id: str,
    gate_decision: GateDecision,
    retrieval_probe: RetrievalProbe,
    deep_triggered: bool,
    memory_save_success: bool,
    injection_trace: dict[str, Any] | None,
) -> None:
    if not chat_id or chat_id.startswith("local:"):
        return

    with get_db_context() as db:
        chat = db.get(Chat, chat_id)
        if not chat:
            return

        meta = dict(chat.meta or {})
        simon_meta = dict(meta.get("simon") or {})
        counters = dict(simon_meta.get("counters") or {})
        gate_reasons = dict(simon_meta.get("gate_reasons") or {})
        source_mix = dict(simon_meta.get("source_mix") or {})

        _increment_counter(counters, "turns")
        if deep_triggered:
            _increment_counter(counters, "deep_triggered")
        if memory_save_success:
            _increment_counter(counters, "memory_saved")

        reason = str(gate_decision.reason or "unknown")
        _increment_counter(gate_reasons, reason)

        mix = retrieval_probe.metrics.get("source_mix", {}) if retrieval_probe.metrics else {}
        for source_name, source_count in mix.items():
            _increment_counter(source_mix, str(source_name), int(source_count or 0))

        simon_meta["counters"] = counters
        simon_meta["gate_reasons"] = gate_reasons
        simon_meta["source_mix"] = source_mix
        simon_meta["lex_queue_depth"] = int(get_queue_depth())
        simon_meta["last"] = {
            "timestamp": int(time.time()),
            "gate_reason": reason,
            "deep_triggered": bool(deep_triggered),
            "memory_save_success": bool(memory_save_success),
            "retrieval": {
                "memory_hits": int(retrieval_probe.metrics.get("memory_hits", 0)),
                "lexical_hits": int(retrieval_probe.metrics.get("lexical_hits", 0)),
            },
            **({"injection": injection_trace} if injection_trace else {}),
        }

        if injection_trace:
            history = list(simon_meta.get("injection_history") or [])
            history.append(
                {
                    "timestamp": int(time.time()),
                    **injection_trace,
                }
            )
            simon_meta["injection_history"] = history[-40:]

        meta["simon"] = simon_meta
        chat.meta = meta
        db.commit()


async def persist_post_flight(
    *,
    request,
    user_payload: dict[str, Any] | None,
    chat_id: str,
    next_lineage_anchor_message_id: str | None,
    user_text: str,
    assistant_text: str,
    save_intent: bool,
    gate_decision: GateDecision,
    retrieval_probe: RetrievalProbe,
    deep_triggered: bool,
    hot_enabled: bool,
    injection_trace: dict[str, Any] | None = None,
) -> None:
    memory_save_success = False

    try:
        if hot_enabled and chat_id and next_lineage_anchor_message_id:
            append_hot_turn(
                chat_id=chat_id,
                lineage_anchor_message_id=next_lineage_anchor_message_id,
                user_text=user_text,
                assistant_text=assistant_text,
            )

        if save_intent and user_text.strip() and request is not None:
            user_model = _coerce_user_model(user_payload)
            if user_model is not None:
                await add_memory(
                    request,
                    AddMemoryForm(content=user_text.strip()),
                    user_model,
                )
                memory_save_success = True
    except Exception as exc:
        log.warning("Simon post-flight memory persistence failed: %s", exc)

    try:
        _update_chat_meta(
            chat_id=chat_id,
            gate_decision=gate_decision,
            retrieval_probe=retrieval_probe,
            deep_triggered=deep_triggered,
            memory_save_success=memory_save_success,
            injection_trace=injection_trace,
        )
    except Exception as exc:
        log.warning("Simon chat meta update failed: %s", exc)
