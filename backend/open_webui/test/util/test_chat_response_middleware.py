import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from open_webui.retrieval.working_mode import normalize_working_mode
import open_webui.utils.misc as misc
import open_webui.utils.middleware as middleware
from open_webui.utils.middleware import (
    apply_params_to_form_data,
    background_tasks_handler,
    non_streaming_chat_response_handler,
    streaming_chat_response_handler,
)


def _build_request(
    *,
    enable_local_corpus: bool = False,
    local_corpus_root: str | None = None,
    offsec_corpus_root: str | None = None,
):
    if local_corpus_root:
        Path(local_corpus_root).mkdir(parents=True, exist_ok=True)
    if offsec_corpus_root:
        Path(offsec_corpus_root).mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(
        state=SimpleNamespace(direct=False),
        cookies={},
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={},
                config=SimpleNamespace(
                    ENABLE_LOCAL_CORPUS_TOOLS=enable_local_corpus,
                    LOCAL_CORPUS_ROOT=local_corpus_root,
                    OFFSEC_CORPUS_ROOT=offsec_corpus_root,
                    ENABLE_CACHE_PROMPT=False,
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL=False,
                )
            )
        )
    )


def _build_streaming_response(events: list[dict]) -> StreamingResponse:
    async def _iterator():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_iterator(), media_type="text/event-stream")


def _build_in_memory_chat_store():
    store = {}

    def _get_message(chat_id, message_id):
        payload = store.get((chat_id, message_id))
        if payload is None:
            return None
        return json.loads(json.dumps(payload))

    def _upsert_message(chat_id, message_id, payload):
        existing = dict(store.get((chat_id, message_id)) or {})
        existing.update(json.loads(json.dumps(payload)))
        store[(chat_id, message_id)] = existing
        return None

    return store, _get_message, _upsert_message


def test_process_messages_with_output_omits_history_reasoning_and_caps_tool_output(
    monkeypatch,
):
    monkeypatch.setattr(misc, "ENABLE_HISTORY_REASONING_REPLAY", False)
    monkeypatch.setattr(misc, "HISTORY_TOOL_OUTPUT_REPLAY_MAX_CHARS", 32)

    messages = [
        {
            "role": "assistant",
            "output": [
                {
                    "type": "reasoning",
                    "content": [{"type": "output_text", "text": "private scratchpad"}],
                },
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_web",
                    "arguments": '{"q":"bari"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": "X" * 200}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Final answer"}],
                },
            ],
        },
        {
            "role": "assistant",
            "content": (
                "Before\n"
                '<details type="reasoning" done="true"><summary>Thought</summary>hidden</details>\n'
                '<details type="tool_calls" done="true"><summary>Tool</summary>tool blob</details>\n'
                "After"
            ),
        },
    ]

    processed = middleware.process_messages_with_output(messages)

    assert len(processed) == 4
    assert processed[0]["role"] == "assistant"
    assert processed[0]["tool_calls"][0]["function"]["name"] == "search_web"
    assert "<think>" not in (processed[0].get("content") or "")
    assert processed[1]["role"] == "tool"
    assert "[historical tool output truncated for context replay]" in processed[1]["content"]
    assert "omitted_chars" in processed[1]["content"]
    assert "X" * 33 not in processed[1]["content"]
    assert processed[2]["content"] == "Final answer"
    assert "details type=\"reasoning\"" not in processed[3]["content"]
    assert "details type=\"tool_calls\"" not in processed[3]["content"]
    assert processed[3]["content"] == "Before\n\nAfter"


def test_process_messages_with_output_prefers_sanitized_assistant_content_over_turn_recap():
    messages = [
        {
            "role": "assistant",
            "content": (
                '<details type="reasoning" done="true"><summary>Thought</summary>internal</details>\n'
                '<details type="tool_calls" done="true"><summary>Tool Executed</summary>raw tool blob</details>\n'
                "Visible answer with full table row A.\nVisible answer with full table row B."
            ),
            "turn_recap": {
                "version": 1,
                "tools_used": [
                    {
                        "tool_name": "search_web",
                        "args_preview": '{"q":"bari cocktails"}',
                    }
                ],
                "artifact_refs": ["/tmp/tool-output.txt"],
                "assistant_takeaway": "Collected the most relevant cocktail-bar leads.",
            },
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_web",
                    "arguments": '{"q":"bari cocktails"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": "RAW TOOL OUTPUT"}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Visible answer"}],
                },
            ],
        }
    ]

    processed = middleware.process_messages_with_output(messages)

    assert processed == [
        {
            "role": "assistant",
            "content": "Visible answer with full table row A.\nVisible answer with full table row B.",
        }
    ]


def test_process_messages_with_output_falls_back_to_turn_recap_when_visible_content_missing():
    messages = [
        {
            "role": "assistant",
            "content": (
                '<details type="reasoning" done="true"><summary>Thought</summary>internal</details>\n'
                '<details type="tool_calls" done="true"><summary>Tool Executed</summary>raw tool blob</details>'
            ),
            "turn_recap": {
                "version": 1,
                "tools_used": [
                    {
                        "tool_name": "search_web",
                        "args_preview": '{"q":"bari cocktails"}',
                    }
                ],
                "artifact_refs": ["/tmp/tool-output.txt"],
                "assistant_takeaway": "Collected the most relevant cocktail-bar leads.",
            },
        }
    ]

    processed = middleware.process_messages_with_output(messages)

    assert processed == [
        {
            "role": "assistant",
            "content": (
                "[Turn recap]\n"
                "tools_used:\n"
                '- search_web args={\"q\":\"bari cocktails\"}\n'
                "artifact_refs:\n"
                "- /tmp/tool-output.txt\n"
                "assistant_takeaway:\n"
                "Collected the most relevant cocktail-bar leads."
            ),
        }
    ]


def test_process_messages_with_output_exact_rehydrates_pointer_output(tmp_path, monkeypatch):
    monkeypatch.setattr(misc, "AGENTIC_ARTIFACTS_DIR", tmp_path)

    artifact = tmp_path / "chat-1__demo" / "tool_outputs" / "result.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("FULL TOOL OUTPUT", encoding="utf-8")

    pointer_text = (
        "[tool output truncated and persisted to disk]\n"
        f"path: {artifact}\n"
        "bytes: 123\n"
        "sha256: deadbeef\n"
        "preview_chars: 5\n"
        "omitted_chars: 10\n\n"
        "preview:\n"
        "hello"
    )

    messages = [
        {
            "role": "assistant",
            "turn_recap": {
                "version": 1,
                "tools_used": [{"tool_name": "run_command", "args_preview": "ls -la"}],
                "artifact_refs": [str(artifact)],
                "assistant_takeaway": "Captured the command result.",
            },
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "run_command",
                    "arguments": '{"cmd":"ls -la"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": pointer_text}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            ],
        }
    ]

    processed = middleware.process_messages_with_output(
        messages,
        prefer_exact_tool_replay=True,
    )

    assert [item["role"] for item in processed] == ["assistant", "tool", "assistant"]
    assert processed[0]["tool_calls"][0]["function"]["name"] == "run_command"
    assert processed[1]["content"] == "FULL TOOL OUTPUT"
    assert processed[2]["content"] == "Done"


def test_process_messages_with_output_exact_replay_keeps_long_tool_output_untruncated(
    monkeypatch,
):
    monkeypatch.setattr(misc, "HISTORY_TOOL_OUTPUT_REPLAY_MAX_CHARS", 16)

    long_output = "0123456789" * 20
    messages = [
        {
            "role": "assistant",
            "turn_recap": {
                "version": 1,
                "tools_used": [{"tool_name": "search_web", "args_preview": '{"q":"bari"}'}],
                "artifact_refs": [],
                "assistant_takeaway": "Captured the exact search response.",
            },
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_web",
                    "arguments": '{"q":"bari"}',
                },
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": [{"type": "input_text", "text": long_output}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            ],
        }
    ]

    processed = middleware.process_messages_with_output(
        messages,
        prefer_exact_tool_replay=True,
    )

    assert processed[1]["role"] == "tool"
    assert processed[1]["content"] == long_output
    assert "[historical tool output truncated for context replay]" not in processed[1]["content"]


@pytest.mark.asyncio
async def test_non_streaming_chat_response_persists_without_event_emitter(monkeypatch):
    saved_messages = []
    background_called = False

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    async def _background_tasks(_ctx):
        nonlocal background_called
        background_called = True

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        _background_tasks,
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {"debug_memory_telemetry": True},
            "memory_telemetry": {"ledger": {"injected": False}},
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    result = await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"completion_tokens": 1},
        },
        ctx,
    )

    assert result["choices"][0]["message"]["content"] == "ok"
    assert result["memoryTelemetry"] == {"ledger": {"injected": False}}
    assert background_called is True
    assert saved_messages[0][0] == "chat-1"
    assert saved_messages[0][1] == "message-1"
    assert saved_messages[0][2]["role"] == "assistant"
    assert saved_messages[0][2]["content"] == "ok"
    assert saved_messages[0][2]["output"][0]["role"] == "assistant"
    assert saved_messages[0][2]["usage"]["completion_tokens"] == 1


@pytest.mark.asyncio
async def test_non_streaming_chat_response_persists_turn_recap_for_tool_turn(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    response = {
        "choices": [{"message": {"content": "Grounded answer"}}],
        "output": [
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "search_web",
                "arguments": '{"q":"bari cocktails"}',
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": [{"type": "input_text", "text": "RAW TOOL OUTPUT"}],
            },
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Grounded answer"}],
            },
        ],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert saved_payload["turn_recap"]["version"] == 1
    assert saved_payload["turn_recap"]["tools_used"][0]["tool_name"] == "search_web"
    assert saved_payload["turn_recap"]["assistant_takeaway"] == "Grounded answer"


@pytest.mark.asyncio
async def test_non_streaming_chat_response_persists_prompt_telemetry(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    prompt_telemetry = {
        "enabled": True,
        "version": 1,
        "entries": [{"provider": "openai", "payload": {"messages": []}}],
        "capped": False,
    }

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(metadata={"prompt_telemetry": prompt_telemetry}),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            ),
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {"debug_prompt_telemetry": True},
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    result = await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"completion_tokens": 1},
        },
        ctx,
    )

    assert result["promptTelemetry"] == prompt_telemetry
    assert saved_messages[0][2]["promptTelemetry"] == prompt_telemetry


@pytest.mark.asyncio
async def test_non_streaming_chat_response_persists_offsec_guided_state(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    guided_state = {
        "guided_run_id": "offsec-guided-test",
        "active_step_id": "step-1",
        "step_run_command_budget": 8,
    }
    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
            "offsec_guided_state_effective": guided_state,
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "ok"}}],
        },
        ctx,
    )

    assert saved_messages[0][2][middleware.GUIDED_STATE_KEY]["guided_run_id"] == "offsec-guided-test"


def test_normalize_workflow_saved_payload_is_consistent_for_equivalent_shapes():
    output = [
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "search_web",
            "arguments": '{"q":"bari cocktails"}',
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Grounded answer"}],
        },
    ]
    turn_recap = misc.build_turn_recap(output, assistant_content="Grounded answer")
    metadata = {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "user_prompt": "Find cocktail bars in Bari.",
        "params": {
            "working_mode": "science",
            "local_corpus_mode": "prefer",
            "function_calling": "default",
        },
    }
    payload_a = {
        "role": "assistant",
        "content": "Grounded answer",
        "output": output,
        "turn_recap": turn_recap,
        "toolJourneyTelemetry": {
            "events": [{"phase": "tool_called", "tool": "search_web"}],
            "capped": False,
        },
        "promptTelemetry": {
            "entries": [{"provider": "openai"}],
            "capped": False,
        },
    }
    payload_b = {
        "content": "Grounded answer",
        "output": json.loads(json.dumps(output)),
        "turn_recap": json.loads(json.dumps(turn_recap)),
        "toolJourneyTelemetry": {
            "events": [{"phase": "tool_called", "tool": "search_web"}],
            "capped": False,
        },
        "promptTelemetry": {
            "entries": [{"provider": "openai"}],
            "capped": False,
        },
    }

    normalized_a = middleware._normalize_workflow_saved_payload(
        metadata=metadata,
        saved_payload=payload_a,
    )
    normalized_b = middleware._normalize_workflow_saved_payload(
        metadata=metadata,
        saved_payload=payload_b,
    )

    assert normalized_a == normalized_b


def test_build_workflow_capture_packet_skips_termination_cause_without_other_signals():
    packet = middleware._build_workflow_capture_packet(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {},
        },
        saved_payload={
            "content": "partial",
            "terminationCause": {
                "kind": "task_cancelled",
                "phase": "streaming_chat_response_handler",
            },
        },
    )

    assert packet is None


def test_build_workflow_capture_packet_uses_turn_recap_fallback_when_output_missing():
    packet = middleware._build_workflow_capture_packet(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "offsec"},
        },
        saved_payload={
            "content": "Registered the step.",
            "turn_recap": {
                "version": 1,
                "tools_used": [
                    {"tool_name": "offsec_register_plan", "args_preview": "{}"},
                    {"tool_name": "run_command", "args_preview": '{"cmd":"id"}'},
                ],
                "omitted_tool_call_count": 1,
                "artifact_refs": [],
                "assistant_takeaway": "Registered plan and began execution.",
            },
            middleware.GUIDED_STATE_KEY: {
                "guided_run_id": "run-1",
                "active_step_id": "step-1",
                "remaining_step_run_command_budget": 7,
            },
        },
    )

    assert packet is not None
    assert packet["tooling"]["source"] == "turn_recap"
    assert packet["tooling"]["tool_names_partial"] is True
    assert packet["tooling"]["tool_call_count"] == 3
    assert packet["capture_reasons"] == ["offsec_guided_state", "turn_recap_present"]


def test_normalize_workflow_saved_payload_includes_research_snapshot():
    normalized = middleware._normalize_workflow_saved_payload(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "science"},
        },
        saved_payload={
            "content": "Grounded answer",
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "final_response",
                "domain_profile": "general_science",
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "status": "supported",
                        "resolution_basis": "contract_satisfied",
                        "disconfirmation_outcome": "not_found_under_budgeted_probe",
                        "coverage_pending_reason": "",
                        "probe_budget": {"required": {}, "observed": {}},
                    }
                ],
                "working_propositions": [
                    {"state": "leaning_support"},
                ],
                "candidate_claims": [{"label": "verified_fact"}],
                "duplicate_query_count": 1,
                "duplicate_fetch_count": 0,
                "negative_signal_count": 0,
                "blocked_access_count": 0,
                "family_alias_count": 1,
                "same_family_conflict_count": 0,
                "query_rewrite_count": 1,
                "low_novelty_query_count": 2,
                "stop_reason": "all_primary_goals_resolved",
                "ready_to_answer": True,
            },
        },
    )

    assert normalized["research_snapshot"]["present"] is True
    assert normalized["research_snapshot"]["phase"] == "final_response"
    assert normalized["research_snapshot"]["goal_statuses"][0]["required_probe_summary"] == {}
    assert normalized["research_snapshot"]["goal_statuses"][0]["observed_probe_summary"] == {}
    assert normalized["research_snapshot"]["family_alias_count"] == 1
    assert normalized["research_snapshot"]["query_rewrite_count"] == 1


def test_build_workflow_capture_packet_uses_research_guided_state_as_capture_reason():
    packet = middleware._build_workflow_capture_packet(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "science"},
        },
        saved_payload={
            "content": "Grounded answer",
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "final_response",
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "status": "supported",
                        "resolution_basis": "contract_satisfied",
                        "disconfirmation_outcome": "not_found_under_budgeted_probe",
                        "coverage_pending_reason": "",
                        "probe_budget": {"required": {}, "observed": {}},
                    }
                ],
                "working_propositions": [],
                "candidate_claims": [{"label": "verified_fact"}],
                "ready_to_answer": True,
            },
        },
    )

    assert packet is not None
    assert packet["research_snapshot"]["present"] is True
    assert packet["capture_reasons"] == ["research_guided_state"]


def test_build_workflow_capture_packet_includes_research_snapshot_debug_fields():
    packet = middleware._build_workflow_capture_packet(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "science"},
        },
        saved_payload={
            "content": "Grounded answer",
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "research",
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "status": "open",
                        "resolution_basis": "",
                        "disconfirmation_outcome": "",
                        "coverage_pending_reason": "required coverage probes still missing: broader fallback",
                        "probe_budget": {
                            "required": {
                                "target_aligned": True,
                                "disconfirming": True,
                                "strong_source": True,
                                "broader_fallback": True,
                            },
                            "observed": {
                                "target_aligned": 1,
                                "disconfirming": 1,
                                "strong_source": 1,
                                "broader_fallback": 0,
                            },
                        },
                    }
                ],
                "working_propositions": [{"state": "leaning_mixed"}],
                "candidate_claims": [],
                "family_alias_count": 2,
                "same_family_conflict_count": 1,
                "query_rewrite_count": 1,
                "low_novelty_query_count": 3,
                "ready_to_answer": False,
            },
        },
    )

    assert packet is not None
    snapshot = packet["research_snapshot"]
    assert snapshot["goal_statuses"][0]["coverage_pending_reason"] == (
        "required coverage probes still missing: broader fallback"
    )
    assert snapshot["goal_statuses"][0]["required_probe_summary"]["broader_fallback"] is True
    assert snapshot["goal_statuses"][0]["observed_probe_summary"]["broader_fallback"] == 0
    assert snapshot["family_alias_count"] == 2
    assert snapshot["same_family_conflict_count"] == 1
    assert snapshot["query_rewrite_count"] == 1
    assert snapshot["low_novelty_query_count"] == 3


def test_research_guided_transition_payload_surfaces_specific_runtime_events():
    previous = {
        "phase": "research",
        "ready_to_answer": False,
        "goals": [
            {
                "goal_id": "goal-1",
                "status": "open",
                "resolution_basis": "",
                "disconfirmation_outcome": "",
                "coverage_pending_reason": "",
                "probe_budget": {"required": {}, "observed": {}},
            }
        ],
        "working_propositions": [],
        "candidate_claims": [],
        "family_alias_count": 0,
        "same_family_conflict_count": 0,
        "query_rewrite_count": 0,
        "low_novelty_query_count": 0,
    }
    next_state = {
        **previous,
        "goals": [
            {
                "goal_id": "goal-1",
                "status": "open",
                "resolution_basis": "",
                "disconfirmation_outcome": "",
                "coverage_pending_reason": "required coverage probes still missing: broader fallback",
                "probe_budget": {
                    "required": {"broader_fallback": True},
                    "observed": {"broader_fallback": 0},
                },
            }
        ],
    }

    payload = middleware._research_guided_transition_payload(previous, next_state)
    assert payload["event"] == "strict_goal_coverage_pending"
    assert payload["coverage_pending_reason"] == (
        "required coverage probes still missing: broader fallback"
    )

    aliased = {**next_state, "family_alias_count": 1}
    payload = middleware._research_guided_transition_payload(next_state, aliased)
    assert payload["event"] == "family_alias_collapse"

    conflicted = {**aliased, "same_family_conflict_count": 1}
    payload = middleware._research_guided_transition_payload(aliased, conflicted)
    assert payload["event"] == "same_family_conflict_adjudicated"

    rewritten = {**conflicted, "query_rewrite_count": 1}
    payload = middleware._research_guided_transition_payload(conflicted, rewritten)
    assert payload["event"] == "query_rewrite_triggered"


def test_capture_workflow_packet_is_chat_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )

    path_a = middleware._capture_workflow_packet_from_saved_payload(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "offsec"},
        },
        saved_payload={
            "content": "A",
            middleware.GUIDED_STATE_KEY: {"guided_run_id": "run-a"},
        },
    )
    path_b = middleware._capture_workflow_packet_from_saved_payload(
        metadata={
            "chat_id": "chat-2",
            "message_id": "msg-1",
            "params": {"working_mode": "offsec"},
        },
        saved_payload={
            "content": "B",
            middleware.GUIDED_STATE_KEY: {"guided_run_id": "run-b"},
        },
    )

    assert path_a is not None
    assert path_b is not None
    assert path_a != path_b
    assert Path(path_a).exists()
    assert Path(path_b).exists()
    assert json.loads(Path(path_a).read_text(encoding="utf-8"))["chat_id"] == "chat-1"
    assert json.loads(Path(path_b).read_text(encoding="utf-8"))["chat_id"] == "chat-2"


def test_capture_workflow_packet_is_fail_open_when_writer_fails(monkeypatch):
    monkeypatch.setattr(
        middleware,
        "_write_workflow_capture_packet",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = middleware._capture_workflow_packet_from_saved_payload(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "offsec"},
        },
        saved_payload={
            "content": "ok",
            middleware.GUIDED_STATE_KEY: {"guided_run_id": "run-1"},
        },
    )

    assert result is None


def test_build_workflow_capture_packet_accepts_termination_cause_with_extra_signal():
    packet = middleware._build_workflow_capture_packet(
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {},
        },
        saved_payload={
            "content": "partial",
            "terminationCause": {
                "kind": "task_cancelled",
                "phase": "streaming_chat_response_handler",
            },
            "promptTelemetry": {"entries": [{"provider": "openai"}]},
        },
    )

    assert packet is not None
    assert packet["capture_reasons"] == ["termination_cause", "prompt_telemetry"]


def test_capture_workflow_packet_skips_local_chat(monkeypatch, tmp_path):
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )

    result = middleware._capture_workflow_packet_from_saved_payload(
        metadata={
            "chat_id": "local:chat-1",
            "message_id": "msg-1",
            "params": {"working_mode": "offsec"},
        },
        saved_payload={
            "content": "ok",
            middleware.GUIDED_STATE_KEY: {"guided_run_id": "run-1"},
        },
    )

    assert result is None
    assert not any(tmp_path.rglob("*.json"))


@pytest.mark.asyncio
async def test_non_streaming_tool_turn_writes_workflow_capture_packet(
    monkeypatch, tmp_path
):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
            "user_prompt": "Find Bari cocktail bars.",
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "Grounded answer"}}],
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "search_web",
                    "arguments": '{"q":"bari cocktails"}',
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Grounded answer"}],
                },
            ],
        },
        ctx,
    )

    packet_path = tmp_path / "chat-1" / "workflow_diary" / "packets" / "message-1.json"
    assert packet_path.exists()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["capture_reasons"] == ["tool_calls_in_output", "turn_recap_present"]
    assert packet["tooling"]["tool_call_count"] == 1
    assert packet["tooling"]["tool_kinds_count"] == 1
    assert packet["tooling"]["observed_tool_names"] == ["search_web"]
    assert packet["request_context"]["working_mode"] == "science"
    assert saved_messages[0][2]["turn_recap"]["version"] == 1


@pytest.mark.asyncio
async def test_non_streaming_offsec_guided_turn_writes_workflow_capture_packet(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        lambda _chat_id, _message_id, _payload: None,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {"working_mode": "offsec"},
            "offsec_guided_state_effective": {
                "guided_run_id": "offsec-guided-test",
                "active_step_id": "step-1",
                "remaining_step_run_command_budget": 8,
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "Registered the next step."}}],
        },
        ctx,
    )

    packet_path = tmp_path / "chat-1" / "workflow_diary" / "packets" / "message-1.json"
    assert packet_path.exists()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["capture_reasons"] == ["offsec_guided_state"]
    assert packet["offsec_snapshot"]["present"] is True
    assert packet["tooling"]["tool_call_count"] == 0


@pytest.mark.asyncio
async def test_non_streaming_prompt_telemetry_only_turn_does_not_write_workflow_capture_packet(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        lambda _chat_id, _message_id, _payload: None,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )

    prompt_telemetry = {
        "enabled": True,
        "entries": [{"provider": "openai", "payload": {"messages": []}}],
        "capped": False,
    }

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(metadata={"prompt_telemetry": prompt_telemetry}),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            ),
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {"debug_prompt_telemetry": True},
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    await non_streaming_chat_response_handler(
        {
            "choices": [{"message": {"content": "ok"}}],
        },
        ctx,
    )

    packet_path = tmp_path / "chat-1" / "workflow_diary" / "packets" / "message-1.json"
    assert not packet_path.exists()


@pytest.mark.asyncio
async def test_streaming_tool_turn_writes_workflow_capture_packet(monkeypatch, tmp_path):
    store, get_message, upsert_message = _build_in_memory_chat_store()
    emitted_events = []

    async def _event_emitter(event):
        emitted_events.append(event)

    async def _event_caller(_event):
        return None

    async def _process_filter_functions(
        request=None,
        filter_functions=None,
        filter_type=None,
        form_data=None,
        extra_params=None,
    ):
        return form_data, {}

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_message_by_id_and_message_id",
        get_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        upsert_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_artifacts_dir",
        lambda chat_id: tmp_path / chat_id,
    )
    monkeypatch.setattr(
        middleware,
        "process_filter_functions",
        _process_filter_functions,
    )
    monkeypatch.setattr(
        middleware,
        "get_sorted_filter_ids",
        lambda request, model, filter_ids: [],
    )
    monkeypatch.setattr(
        middleware,
        "ENABLE_REALTIME_CHAT_SAVE",
        False,
    )

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(direct=False),
            cookies={},
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    MODELS={"demo-model": {"id": "demo-model"}},
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            ),
        ),
        "form_data": {
            "model": "demo-model",
            "messages": [{"role": "user", "content": "hello"}],
        },
        "user": SimpleNamespace(id="user-1"),
        "model": {"id": "demo-model"},
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
            "system_prompt": None,
        },
        "events": [],
        "event_emitter": _event_emitter,
        "event_caller": _event_caller,
    }

    response = _build_streaming_response(
        [
            {
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "id": "fc_1",
                            "call_id": "call-1",
                            "name": "search_web",
                            "arguments": '{"q":"bari cocktails"}',
                            "status": "completed",
                        },
                        {
                            "type": "message",
                            "id": "msg_1",
                            "status": "completed",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "Grounded answer"}
                            ],
                        },
                    ],
                    "usage": {"output_tokens": 5},
                },
            }
        ]
    )

    await streaming_chat_response_handler(response, ctx)

    packet_path = tmp_path / "chat-1" / "workflow_diary" / "packets" / "message-1.json"
    assert packet_path.exists()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["capture_reasons"] == ["tool_calls_in_output", "turn_recap_present"]
    assert packet["tooling"]["tool_call_count"] == 1
    assert packet["tooling"]["observed_tool_names"] == ["search_web"]
    assert packet["tooling"]["tool_names_partial"] is False
    assert any(event.get("type") == "chat:completion" for event in emitted_events)


@pytest.mark.asyncio
async def test_background_tasks_handler_schedules_ledger_without_event_emitter(
    monkeypatch,
):
    scheduled = []

    def _create_task(coro):
        scheduled.append(coro.cr_code.co_name)
        coro.close()
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.asyncio.create_task",
        _create_task,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware._resolve_context_maintenance_enabled",
        lambda request, user, tasks=None: False,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {
            "u1": {
                "id": "u1",
                "role": "user",
                "content": "Use ffuf.",
            },
            "a1": {
                "id": "a1",
                "parentId": "u1",
                "role": "assistant",
                "content": "Using ffuf.",
                "model": "demo-model",
            },
        },
    )

    await background_tasks_handler(
        {
            "request": SimpleNamespace(
                app=SimpleNamespace(
                    state=SimpleNamespace(
                        MODELS={"demo-model": {"id": "demo-model"}},
                        config=SimpleNamespace(ENABLE_CONTEXT_MAINTENANCE=False),
                    )
                )
            ),
            "form_data": {},
            "user": SimpleNamespace(id="user-1"),
            "metadata": {
                "chat_id": "chat-1",
                "message_id": "a1",
            },
            "tasks": None,
            "event_emitter": None,
        }
    )

    assert scheduled == ["run_background_ledger_capture"]


def test_apply_params_strips_ledger_mode_from_openai_payload():
    form_data = {
        "params": {
            "ledger_mode": "agentic",
            "working_mode": "science",
            "research_guided_mode": True,
            "local_corpus_mode": "prefer",
            "temperature": 0.2,
        }
    }
    model = {"owned_by": "openai"}

    result = apply_params_to_form_data(form_data, model)

    assert "ledger_mode" not in result
    assert "working_mode" not in result
    assert "research_guided_mode" not in result
    assert "local_corpus_mode" not in result
    assert result["temperature"] == 0.2


def test_apply_params_strips_ledger_mode_from_ollama_options():
    form_data = {
        "params": {
            "ledger_mode": "agentic",
            "working_mode": "science",
            "research_guided_mode": True,
            "local_corpus_mode": "prefer",
            "temperature": 0.2,
        }
    }
    model = {"owned_by": "ollama"}

    result = apply_params_to_form_data(form_data, model)

    assert result["options"].get("temperature") == 0.2
    assert "ledger_mode" not in result["options"]
    assert "working_mode" not in result["options"]
    assert "research_guided_mode" not in result["options"]
    assert "local_corpus_mode" not in result["options"]


def test_normalize_working_mode_infers_science_from_legacy_local_corpus_mode():
    assert normalize_working_mode(None, local_corpus_mode="prefer") == "science"
    assert normalize_working_mode("", local_corpus_mode="auto") == "science"
    assert normalize_working_mode(None, local_corpus_mode="off") == "general"


def test_local_corpus_prefer_system_prompt_encourages_brief_human_preamble():
    prompt = middleware.LOCAL_CORPUS_PREFER_SYSTEM_PROMPT

    assert "Before your first local corpus tool call" in prompt
    assert "1 to 3 short sentences" in prompt
    assert "Do not answer the substance yet" in prompt
    assert "preserve the user's substantive topic terms" in prompt
    assert "what should I think about" in prompt


def test_tool_narration_system_prompt_mentions_phase_changes():
    prompt = middleware.TOOL_NARRATION_SYSTEM_PROMPT

    assert "first major tool phase" in prompt
    assert "meaningful phase changes" in prompt
    assert "do not restate tool names or raw status labels" in prompt
    assert "preserve the user's substantive topic terms" in prompt


def test_build_default_selector_guidance_adds_local_corpus_prefer_and_term_rules():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "prefer"},
        "features": {},
    }
    tools = {
        "local_corpus_frame_problem": {},
        "local_corpus_plan_axes": {},
    }

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "preserve the user's substantive topic terms" in guidance
    assert "Do not preserve conversational scaffolding" in guidance
    assert "prefer local corpus tools first" in guidance
    assert "Do not stay loyal to the local lane out of inertia" in guidance
    assert "prior-work sources" not in guidance


def test_build_default_selector_guidance_skips_science_rules_for_offsec_mode():
    metadata = {
        "params": {
            "function_calling": "default",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        },
        "features": {},
    }
    tools = {
        "local_corpus_frame_problem": {},
        "local_corpus_plan_axes": {},
    }

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "preserve the user's substantive topic terms" in guidance
    assert "prefer local corpus tools first" not in guidance
    assert "Do not stay loyal to the local lane out of inertia" not in guidance


def test_build_default_selector_guidance_adds_offsec_consult_rules():
    metadata = {
        "params": {
            "function_calling": "default",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        },
        "features": {},
    }
    tools = {
        "offsec_consult": {},
        "offsec_retrieve_evidence": {},
        "run_command": {},
    }

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "offsec_consult" in guidance
    assert "official or project/GitHub docs" in guidance
    assert "terminal as the primary execution lane" in guidance


def test_build_default_selector_guidance_adds_local_corpus_auto_shelf_check_rule():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "auto"},
        "features": {"focused_search": True},
    }
    tools = {
        "local_corpus_list_domains": {},
        "local_corpus_frame_problem": {},
        "web_research_strong": {},
    }

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "preserve the user's substantive topic terms" in guidance
    assert "single local_corpus_list_domains call" in guidance
    assert "before going to web search or model-only answering" in guidance
    assert "Do not drill down further just to confirm an empty, weak, or merely nominal shelf" in guidance
    assert "prefer local corpus tools first" not in guidance


def test_build_forced_default_selector_tool_call_returns_local_domain_probe_for_auto():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "auto"},
    }
    tools = {
        "local_corpus_list_domains": {},
        "web_research_strong": {},
    }

    forced = middleware._build_forced_default_selector_tool_call(metadata, tools)

    assert forced == {"name": "local_corpus_list_domains", "parameters": {}}


def test_build_forced_default_selector_tool_call_skips_offsec_mode():
    metadata = {
        "params": {
            "function_calling": "default",
            "working_mode": "offsec",
            "local_corpus_mode": "auto",
        },
    }
    tools = {
        "local_corpus_list_domains": {},
        "web_research_strong": {},
    }

    forced = middleware._build_forced_default_selector_tool_call(metadata, tools)

    assert forced is None


def test_build_forced_default_selector_tool_call_skips_non_auto_or_missing_tool():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "prefer"},
    }

    assert (
        middleware._build_forced_default_selector_tool_call(
            metadata, {"local_corpus_list_domains": {}}
        )
        is None
    )
    assert (
        middleware._build_forced_default_selector_tool_call(
            {"params": {"function_calling": "default", "local_corpus_mode": "auto"}},
            {"web_research_strong": {}},
        )
        is None
    )


def test_should_not_upgrade_default_search_web_tool_call_after_local_corpus_history():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "auto"},
    }
    tools = {"search_web": {}, "web_research_strong": {}}
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "local_corpus_frame_problem",
                        "arguments": "{\"query\":\"atrial fibrillation cold foods\"}",
                    },
                }
            ],
        }
    ]

    should_upgrade = middleware._should_upgrade_default_search_web_tool_call(
        metadata,
        tools,
        messages,
        {"name": "search_web", "parameters": {"query": "atrial fibrillation cold foods"}},
    )

    assert should_upgrade is False


def test_should_not_upgrade_default_search_web_tool_call_after_strong_web_history():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "auto"},
    }
    tools = {"search_web": {}, "web_research_strong": {}}
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "local_corpus_frame_problem",
                        "arguments": "{\"query\":\"atrial fibrillation cold foods\"}",
                    },
                }
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "web_research_strong",
                        "arguments": "{\"query\":\"atrial fibrillation cold foods\"}",
                    },
                }
            ],
        },
    ]

    should_upgrade = middleware._should_upgrade_default_search_web_tool_call(
        metadata,
        tools,
        messages,
        {"name": "search_web", "parameters": {"query": "atrial fibrillation cold foods"}},
    )

    assert should_upgrade is False


@pytest.mark.asyncio
async def test_chat_completion_tools_handler_forces_local_domain_probe_before_selector(
    monkeypatch,
):
    request = _build_request(enable_local_corpus=True, local_corpus_root="/tmp/local-corpus")
    user = SimpleNamespace(id="user-1")
    body = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "What can the local corpus help with?"}],
    }
    extra_params = {
        "__event_call__": None,
        "__event_emitter__": None,
        "__metadata__": {
            "params": {"function_calling": "default", "local_corpus_mode": "auto"},
        },
    }
    models = {"demo-model": {"id": "demo-model"}}
    called = {"selector": False, "tool": False}

    async def _should_not_run_selector(*_args, **_kwargs):
        called["selector"] = True
        raise AssertionError("selector model should not run before local shelf check")

    async def _fake_local_domains(**_kwargs):
        called["tool"] = True
        return json.dumps({"status": "ok", "domains": [{"domain": "medicine"}]})

    monkeypatch.setattr(
        "open_webui.utils.middleware.generate_chat_completion",
        _should_not_run_selector,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.get_task_model_id",
        lambda *_args, **_kwargs: "demo-model",
    )

    tools = {
        "local_corpus_list_domains": {
            "tool_id": "builtin:local_corpus_list_domains",
            "callable": _fake_local_domains,
            "spec": {"parameters": {"properties": {}}},
            "type": "builtin",
        },
        "web_research_strong": {
            "tool_id": "builtin:web_research_strong",
            "callable": _fake_local_domains,
            "spec": {"parameters": {"properties": {}}},
            "type": "builtin",
        },
    }

    _body, payload = await middleware.chat_completion_tools_handler(
        request, body, extra_params, user, models, tools
    )

    assert called["tool"] is True
    assert called["selector"] is False
    assert payload["sources"][0]["source"]["name"] == (
        "builtin:local_corpus_list_domains/local_corpus_list_domains"
    )


@pytest.mark.asyncio
async def test_chat_completion_tools_handler_keeps_search_web_as_broad_discovery_after_local_corpus(
    monkeypatch,
):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_LOCAL_CORPUS_TOOLS=True,
                    LOCAL_CORPUS_ROOT="/tmp/local-corpus",
                    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="",
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                )
            )
        )
    )
    user = SimpleNamespace(id="user-1")
    body = {
        "model": "demo-model",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "local_corpus_frame_problem",
                            "arguments": "{\"query\":\"atrial fibrillation cold foods\"}",
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{\"status\":\"ok\"}"},
            {
                "role": "user",
                "content": "Can eating cold foods trigger atrial fibrillation in certain individuals?",
            },
        ],
    }
    extra_params = {
        "__event_call__": None,
        "__event_emitter__": None,
        "__metadata__": {
            "params": {"function_calling": "default", "local_corpus_mode": "auto"},
            "features": {"web_search": True, "focused_search": True},
        },
    }
    models = {"demo-model": {"id": "demo-model"}}
    calls = {"strong": 0, "search": 0}

    async def _fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "name": "search_web",
                                "parameters": {
                                    "query": "atrial fibrillation cold foods trigger mechanism"
                                },
                            }
                        )
                    }
                }
            ]
        }

    async def _fake_strong_search(**_kwargs):
        calls["strong"] += 1
        return json.dumps({"phase": "completed", "items": []})

    async def _fake_search_web(**_kwargs):
        calls["search"] += 1
        return json.dumps(
            [
                {
                    "title": "Example Result",
                    "link": "https://example.test/result",
                    "snippet": "broad discovery result",
                }
            ]
        )

    monkeypatch.setattr(
        middleware, "generate_chat_completion", _fake_generate_chat_completion
    )
    monkeypatch.setattr(
        middleware, "get_task_model_id", lambda *_args, **_kwargs: "demo-model"
    )

    tools = {
        "web_research_strong": {
            "tool_id": "builtin:web_research_strong",
            "callable": _fake_strong_search,
            "spec": {"parameters": {"properties": {"query": {"type": "string"}}}},
            "type": "builtin",
        },
        "search_web": {
            "tool_id": "builtin:search_web",
            "callable": _fake_search_web,
            "spec": {"parameters": {"properties": {"query": {"type": "string"}}}},
            "type": "builtin",
        },
    }

    _body, payload = await middleware.chat_completion_tools_handler(
        request, body, extra_params, user, models, tools
    )

    assert calls["strong"] == 0
    assert calls["search"] == 1
    assert payload["sources"][0]["source"]["name"] == "builtin:search_web/search_web"


def test_selector_prior_work_signal_stays_none_for_short_fresh_question():
    messages = [{"role": "user", "content": "And why?"}]

    signal = middleware._selector_prior_work_signal(messages)

    assert signal == middleware.DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE


def test_selector_prior_work_signal_stays_none_for_incomplete_generic_question():
    messages = [{"role": "user", "content": "What broad causes should I think about?"}]

    signal = middleware._selector_prior_work_signal(messages)

    assert signal == middleware.DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE


def test_selector_prior_work_signal_is_weak_for_explicit_hint_with_recent_context():
    messages = [
        {"role": "user", "content": "We were discussing the issue."},
        {"role": "assistant", "content": "I have the context."},
        {"role": "user", "content": "Check my notes before answering."},
    ]

    signal = middleware._selector_prior_work_signal(messages)

    assert signal == middleware.DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_WEAK


def test_selector_prior_work_signal_is_strong_for_artifact_reference():
    messages = [
        {
            "role": "user",
            "content": "Use /c/e288f767-8dd3-4980-ab01-ae8eb107b07f before answering.",
        }
    ]

    signal = middleware._selector_prior_work_signal(messages)

    assert signal == middleware.DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG


def test_selector_prior_work_signal_is_strong_for_explicit_hint_after_prior_work_tool_use():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "search_chats",
                        "arguments": "{\"query\":\"atrial fibrillation\"}",
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "[]"},
        {"role": "user", "content": "Check my notes before answering."},
    ]

    signal = middleware._selector_prior_work_signal(messages)

    assert signal == middleware.DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG


def test_build_default_selector_guidance_adds_prior_work_fallback_only_when_strong_signal_and_primary_lanes_off():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "off"},
        "features": {"web_search": False, "focused_search": False},
    }
    tools = {
        "query_knowledge_files": {},
        "notes_lookup": {},
        "search_chats": {},
    }
    messages = [
        {
            "role": "user",
            "content": "Use /c/e288f767-8dd3-4980-ab01-ae8eb107b07f before answering.",
        }
    ]

    guidance = middleware._build_default_selector_guidance(metadata, tools, messages)

    assert "before answering from model knowledge alone" in guidance
    assert "knowledge files, notes, prior chats" in guidance
    assert "Treat them as prior work or leads" in guidance


def test_build_default_selector_guidance_skips_prior_work_on_structural_gate_alone():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "off"},
        "features": {"web_search": False, "focused_search": False},
    }
    tools = {
        "query_knowledge_files": {},
        "notes_lookup": {},
        "search_chats": {},
    }

    guidance = middleware._build_default_selector_guidance(
        metadata,
        tools,
        [{"role": "user", "content": "What broad causes should I think about?"}],
    )

    assert "before answering from model knowledge alone" not in guidance
    assert "knowledge files, notes, prior chats" not in guidance


def test_build_default_selector_guidance_skips_prior_work_when_web_is_enabled():
    metadata = {
        "params": {"function_calling": "default", "local_corpus_mode": "off"},
        "features": {"web_search": True, "focused_search": False},
    }
    tools = {
        "query_knowledge_files": {},
        "notes_lookup": {},
        "search_chats": {},
        "search_web": {},
    }

    guidance = middleware._build_default_selector_guidance(
        metadata,
        tools,
        [
            {
                "role": "user",
                "content": "Use /c/e288f767-8dd3-4980-ab01-ae8eb107b07f before answering.",
            }
        ],
    )

    assert "before answering from model knowledge alone" not in guidance
    assert "knowledge files, notes, prior chats" not in guidance
    assert "preserve the user's substantive topic terms" in guidance


def test_build_default_selector_guidance_is_default_only():
    metadata = {
        "params": {"function_calling": "native", "local_corpus_mode": "prefer"},
        "features": {},
    }
    tools = {"local_corpus_frame_problem": {}}

    assert middleware._build_default_selector_guidance(metadata, tools, []) == ""


def test_should_enable_shared_tool_narration_for_local_corpus_prefer():
    request = _build_request(
        enable_local_corpus=True, local_corpus_root="/tmp/local-corpus"
    )
    metadata = {"params": {"function_calling": "native", "local_corpus_mode": "prefer"}}

    assert (
        middleware._should_enable_shared_tool_narration(request, metadata, {})
        is True
    )


def test_should_enable_shared_tool_narration_for_offsec_prefer():
    request = _build_request(
        enable_local_corpus=True,
        offsec_corpus_root="/tmp/offsec-corpus",
    )
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        }
    }

    assert (
        middleware._should_enable_shared_tool_narration(request, metadata, {})
        is True
    )


def test_should_enable_shared_tool_narration_for_focused_search():
    request = _build_request()
    metadata = {"params": {"function_calling": "native"}}

    assert (
        middleware._should_enable_shared_tool_narration(
            request, metadata, {"focused_search": True}
        )
        is True
    )


def test_initialize_tool_narration_state_reserves_local_corpus_orientation():
    request = _build_request(
        enable_local_corpus=True, local_corpus_root="/tmp/local-corpus"
    )
    metadata = {"params": {"function_calling": "native", "local_corpus_mode": "prefer"}}

    state = middleware._initialize_tool_narration_state(request, metadata, {})

    assert state["enabled"] is True
    assert state["last_narrated_phase"] == "orientation"
    assert state["narration_count"] == 1


def test_register_tool_narration_phase_transition_requires_real_phase_change():
    state = {
        "enabled": True,
        "last_narrated_phase": "orientation",
        "current_major_phase": None,
        "narration_count": 1,
        "max_beats": 3,
    }

    assert (
        middleware._register_tool_narration_phase_transition(
            state, ["orientation", "orientation"]
        )
        is None
    )

    instruction = middleware._register_tool_narration_phase_transition(
        state, ["planning", "orientation"]
    )

    assert instruction is not None
    assert "framed the task and are narrowing the path" in instruction
    assert state["last_narrated_phase"] == "planning"
    assert state["narration_count"] == 2


def test_register_tool_narration_phase_transition_respects_beat_cap():
    state = {
        "enabled": True,
        "last_narrated_phase": "planning",
        "current_major_phase": "planning",
        "narration_count": 3,
        "max_beats": 3,
    }

    assert (
        middleware._register_tool_narration_phase_transition(
            state, ["evidence_gathering"]
        )
        is None
    )


def test_build_tool_continuation_messages_uses_temporary_system_append():
    form_messages = [{"role": "system", "content": "base prompt"}]
    output = [
        {
            "type": "message",
            "id": "msg_1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Working on it."}],
        }
    ]

    messages = middleware._build_tool_continuation_messages(
        form_messages,
        output,
        narration_instruction="phase guidance",
        research_instruction="research guidance",
    )

    assert "phase guidance" in messages[0]["content"]
    assert "research guidance" in messages[0]["content"]
    assert "phase guidance" not in form_messages[0]["content"]
    assert messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_process_chat_payload_injects_research_guided_prompt_for_science_turn(
    monkeypatch,
):
    request = _build_request(enable_local_corpus=False)
    request.app.state.config.ENABLE_RESEARCH_GUIDED = True
    user = SimpleNamespace(id="user-1")
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "science",
            "local_corpus_mode": "auto",
            "research_guided_mode": True,
        },
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
    }
    form_data = {
        "model": "demo-model",
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
        "messages": [
            {
                "role": "user",
                "content": "What evidence exists for whether evening blue light changes circadian phase?",
            }
        ],
    }
    model = {
        "id": "demo-model",
        "info": {"meta": {"capabilities": {"builtin_tools": False, "file_context": False}}},
    }

    monkeypatch.setattr(
        middleware,
        "apply_global_cache_prompt",
        lambda current_form_data, _model, _enabled: current_form_data,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_recall_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_context_maintenance_enabled",
        lambda *_args, **_kwargs: False,
    )

    async def _noop_ledger(**kwargs):
        return kwargs["messages"], {}

    async def _noop_pipeline(_request, current_form_data, _user, _models):
        return current_form_data

    async def _noop_filters(**kwargs):
        return kwargs["form_data"], {}

    monkeypatch.setattr(middleware, "maybe_apply_ledger", _noop_ledger)
    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", _noop_pipeline)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "open_webui.utils.middleware.Functions.get_functions_by_ids",
        lambda _ids: [],
    )
    monkeypatch.setattr(middleware, "process_filter_functions", _noop_filters)

    updated_form_data, updated_metadata, _events = await middleware.process_chat_payload(
        request,
        form_data,
        user,
        metadata,
        model,
    )

    system_message = next(
        (message for message in updated_form_data["messages"] if message.get("role") == "system"),
        None,
    )

    assert system_message is not None
    assert "research-guided loop" in system_message["content"]
    assert isinstance(updated_metadata[middleware.RESEARCH_GUIDED_STATE_KEY], dict)
    assert updated_metadata[middleware.RESEARCH_GUIDED_STATE_KEY]["phase"] == "plan"


@pytest.mark.asyncio
async def test_process_chat_payload_bypasses_research_guided_for_bali_travel_turn(
    monkeypatch,
):
    request = _build_request(enable_local_corpus=False)
    request.app.state.config.ENABLE_RESEARCH_GUIDED = True
    user = SimpleNamespace(id="user-1")
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "science",
            "local_corpus_mode": "auto",
            "research_guided_mode": True,
        },
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
    }
    form_data = {
        "model": "demo-model",
        "features": {"web_search": True, "focused_search": False, "deep_research": False},
        "messages": [{"role": "user", "content": "Where should I go for drinks in Bali today?"}],
    }
    model = {
        "id": "demo-model",
        "info": {"meta": {"capabilities": {"builtin_tools": False, "file_context": False}}},
    }

    monkeypatch.setattr(
        middleware,
        "apply_global_cache_prompt",
        lambda current_form_data, _model, _enabled: current_form_data,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_recall_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_context_maintenance_enabled",
        lambda *_args, **_kwargs: False,
    )

    async def _noop_ledger(**kwargs):
        return kwargs["messages"], {}

    async def _noop_pipeline(_request, current_form_data, _user, _models):
        return current_form_data

    async def _noop_filters(**kwargs):
        return kwargs["form_data"], {}

    monkeypatch.setattr(middleware, "maybe_apply_ledger", _noop_ledger)
    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", _noop_pipeline)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "open_webui.utils.middleware.Functions.get_functions_by_ids",
        lambda _ids: [],
    )
    monkeypatch.setattr(middleware, "process_filter_functions", _noop_filters)

    updated_form_data, updated_metadata, _events = await middleware.process_chat_payload(
        request,
        form_data,
        user,
        metadata,
        model,
    )

    system_message = next(
        (message for message in updated_form_data["messages"] if message.get("role") == "system"),
        None,
    )

    assert system_message is None or "research-guided loop" not in str(system_message["content"])
    assert middleware.RESEARCH_GUIDED_STATE_KEY not in updated_metadata
    assert updated_metadata["research_guided_ineligible_reason"] == "non_science_query"


@pytest.mark.asyncio
async def test_non_streaming_chat_response_appends_research_status_block(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "final_response",
                "ready_to_answer": True,
                "candidate_claims": [
                    {
                        "text": "Evening blue-light effects remain uncertain under ordinary device use",
                        "label": "reasonable_inference",
                        "basis_summary": "supported by direct empirical evidence for circadian proxy outcomes, not field outcomes",
                        "must_include_limitations": ["no patient-relevant downstream outcome evidence"],
                    }
                ],
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    response = {
        "choices": [{"message": {"content": "Grounded answer"}}],
        "output": [
            {
                "id": "msg-1",
                "status": "completed",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Grounded answer"}],
            },
        ],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert "### Research Status" in saved_payload["content"]
    assert saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["ready_to_answer"] is True


@pytest.mark.asyncio
async def test_non_streaming_chat_response_finalizes_research_guided_turn_and_strips_reasoning(
    monkeypatch,
):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Research Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "research",
                "ready_to_answer": False,
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "question": "Is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?",
                        "status": "supported",
                        "resolution_basis": "contract_satisfied",
                        "disconfirmation_outcome": "",
                    }
                ],
                "working_propositions": [],
                "candidate_claims": [],
                "evidence_ledger": [
                    {
                        "evidence_id": "ev-1",
                        "goal_ids": ["goal-1"],
                        "evidence_family_id": "doi:10.1000/example-doi",
                        "evidence_class": "systematic_synthesis",
                        "stance": "supports",
                        "directness": "direct",
                        "value_bucket": "high",
                        "context_fit": "strong",
                        "blocked": False,
                    }
                ],
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    response = {
        "choices": [
            {
                "message": {
                    "content": (
                        '<details type="reasoning" done="true"><summary>Thought</summary>internal</details>\n'
                        "**Verified facts:**\n- The evidence is favorable but limited."
                    )
                }
            }
        ],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert '<details type="reasoning"' not in saved_payload["content"]
    assert "**Current evidence:**" in saved_payload["content"]
    assert "### Research Status" in saved_payload["content"]
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["phase"]
        == "final_response"
    )
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["candidate_claims"][0][
            "label"
        ]
        == "reasonable_inference"
    )


@pytest.mark.asyncio
async def test_non_streaming_chat_response_blocks_unready_research_guided_draft(
    monkeypatch,
):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Research Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    async def fake_generate_chat_completion(*_args, **_kwargs):
        return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(
        "open_webui.utils.middleware.generate_chat_completion",
        fake_generate_chat_completion,
    )

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(direct=False),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"model-1": {"id": "model-1"}},
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(
                        WEBUI_URL="https://example.test",
                        ENABLE_RESEARCH_GUIDED_VERIFIER=False,
                        RESEARCH_GUIDED_VERIFIER_MAX_REPAIR_PASSES=1,
                    ),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "model_id": "model-1",
            "params": {},
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "research",
                "ready_to_answer": False,
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "question": "Is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?",
                        "is_strict": True,
                        "status": "open",
                        "resolution_basis": "",
                        "disconfirmation_outcome": "",
                        "coverage_requirement": "strict",
                        "coverage_pending_reason": "required coverage probes still missing: disconfirming, broader fallback",
                        "probe_budget": {
                            "required": {
                                "target_aligned": True,
                                "disconfirming": True,
                                "strong_source": True,
                                "broader_fallback": True,
                            },
                            "observed": {
                                "target_aligned": 0,
                                "disconfirming": 0,
                                "strong_source": 0,
                                "broader_fallback": 0,
                            },
                        },
                    }
                ],
                "working_propositions": [],
                "candidate_claims": [],
                "evidence_ledger": [],
                "repair_pass_count": 0,
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {
            "model": "model-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
        "tasks": None,
    }

    response = {
        "choices": [{"message": {"content": "Confident final answer."}}],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert middleware.RESEARCH_INCOMPLETE_MARKER in saved_payload["content"]
    assert "### Research Status" not in saved_payload["content"]
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["incomplete_reason"]
        == "unresolved_research"
    )
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["repair_pass_count"] == 1
    )


@pytest.mark.asyncio
async def test_non_streaming_chat_response_caps_research_guided_draft_when_verifier_rejects(
    monkeypatch,
):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Research Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    async def fake_generate_chat_completion(_request, form_data=None, **_kwargs):
        if form_data.get("metadata", {}).get("task") == "research_guided_verifier":
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "cap",
                                    "reasons": ["draft overstates the evidence strength"],
                                    "instructions": [],
                                    "unsupported_claims": [],
                                    "missing_limitations": [],
                                }
                            )
                        }
                    }
                ]
            }
        raise AssertionError("unexpected repair pass")

    monkeypatch.setattr(
        "open_webui.utils.middleware.generate_chat_completion",
        fake_generate_chat_completion,
    )

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(direct=False),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"model-1": {"id": "model-1"}},
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(
                        WEBUI_URL="https://example.test",
                        ENABLE_RESEARCH_GUIDED_VERIFIER=True,
                        RESEARCH_GUIDED_VERIFIER_MAX_REPAIR_PASSES=1,
                    ),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "model_id": "model-1",
            "params": {},
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "final_response",
                "ready_to_answer": True,
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "question": "Is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?",
                        "is_strict": True,
                        "status": "supported",
                        "resolution_basis": "contract_satisfied",
                        "disconfirmation_outcome": "not_found_under_budgeted_probe",
                        "coverage_requirement": "strict",
                    }
                ],
                "candidate_claims": [
                    {
                        "text": "Evidence for meaningful improvement in sleep latency remains limited.",
                        "label": "reasonable_inference",
                        "basis_summary": "supported by systematic synthesis and one corroborating source family",
                        "must_include_limitations": [
                            "support relied on snippet/summary evidence rather than full-text corroboration"
                        ],
                    }
                ],
                "evidence_ledger": [],
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {
            "model": "model-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
        "tasks": None,
    }

    response = {
        "choices": [{"message": {"content": "**Verified facts:** This is settled."}}],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert "**Current evidence:**" in saved_payload["content"]
    assert "### Research Status" in saved_payload["content"]
    assert "This is settled." not in saved_payload["content"]
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["verifier_verdict"]
        == "cap"
    )


@pytest.mark.asyncio
async def test_non_streaming_chat_response_revises_then_allows_research_guided_draft(
    monkeypatch,
):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Research Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    verifier_calls = {"count": 0}

    async def fake_generate_chat_completion(_request, form_data=None, **_kwargs):
        task = form_data.get("metadata", {}).get("task")
        if task == "research_guided_verifier":
            verifier_calls["count"] += 1
            if verifier_calls["count"] == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "verdict": "revise",
                                        "reasons": ["missing a required limitation"],
                                        "instructions": ["state that the evidence is not verified"],
                                        "unsupported_claims": [],
                                        "missing_limitations": [
                                            "support relied on snippet/summary evidence rather than full-text corroboration"
                                        ],
                                    }
                                )
                            }
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "allow",
                                    "reasons": [],
                                    "instructions": [],
                                    "unsupported_claims": [],
                                    "missing_limitations": [],
                                }
                            )
                        }
                    }
                ]
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": "The evidence is suggestive but not verified, and the support relied on snippet/summary evidence rather than full-text corroboration."
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "open_webui.utils.middleware.generate_chat_completion",
        fake_generate_chat_completion,
    )

    ctx = {
        "request": SimpleNamespace(
            state=SimpleNamespace(direct=False),
            app=SimpleNamespace(
                state=SimpleNamespace(
                    MODELS={"model-1": {"id": "model-1"}},
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(
                        WEBUI_URL="https://example.test",
                        ENABLE_RESEARCH_GUIDED_VERIFIER=True,
                        RESEARCH_GUIDED_VERIFIER_MAX_REPAIR_PASSES=1,
                    ),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "model_id": "model-1",
            "params": {},
            middleware.RESEARCH_GUIDED_STATE_KEY: {
                "phase": "final_response",
                "ready_to_answer": True,
                "goals": [
                    {
                        "goal_id": "goal-1",
                        "question": "Is there strong evidence that evening blue-light-blocking glasses improve sleep latency in adults?",
                        "is_strict": True,
                        "status": "supported",
                        "resolution_basis": "contract_satisfied",
                        "disconfirmation_outcome": "not_found_under_budgeted_probe",
                        "coverage_requirement": "strict",
                    }
                ],
                "candidate_claims": [
                    {
                        "text": "Evidence for meaningful improvement in sleep latency remains limited.",
                        "label": "reasonable_inference",
                        "basis_summary": "supported by systematic synthesis and one corroborating source family",
                        "must_include_limitations": [
                            "support relied on snippet/summary evidence rather than full-text corroboration"
                        ],
                    }
                ],
                "evidence_ledger": [],
                "repair_pass_count": 0,
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {
            "model": "model-1",
            "messages": [{"role": "user", "content": "hello"}],
        },
        "tasks": None,
    }

    response = {
        "choices": [{"message": {"content": "Evidence is verified and settled."}}],
        "usage": {"completion_tokens": 1},
    }

    await non_streaming_chat_response_handler(response, ctx)

    saved_payload = saved_messages[0][2]
    assert "suggestive but not verified" in saved_payload["content"]
    assert "### Research Status" in saved_payload["content"]
    assert middleware.RESEARCH_INCOMPLETE_MARKER not in saved_payload["content"]
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["verifier_verdict"]
        == "allow"
    )
    assert (
        saved_payload[middleware.RESEARCH_GUIDED_STATE_KEY]["repair_pass_count"] == 1
    )


@pytest.mark.asyncio
async def test_chat_completion_tools_handler_injects_default_selector_guidance(
    monkeypatch,
):
    captured_payloads = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        captured_payloads.append(
            {
                "form_data": form_data,
                "bypass_system_prompt": bypass_system_prompt,
                "user_id": getattr(user, "id", None),
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"tool_calls": []}),
                    }
                }
            ]
        }

    monkeypatch.setattr(
        middleware, "generate_chat_completion", fake_generate_chat_completion
    )
    monkeypatch.setattr(
        middleware,
        "get_task_model_id",
        lambda *_args, **_kwargs: "task-model",
    )

    async def _noop_event_call(_payload):
        return None

    async def _noop_event_emitter(_payload):
        return None

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="",
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                )
            )
        )
    )
    metadata = {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "params": {"function_calling": "default", "local_corpus_mode": "prefer"},
        "features": {"web_search": False, "focused_search": False},
    }
    body = {
        "model": "main-model",
        "messages": [{"role": "user", "content": "Headache plus mild anemia"}],
    }
    tools = {
        "local_corpus_frame_problem": {
            "spec": {
                "name": "local_corpus_frame_problem",
                "parameters": {"type": "object", "properties": {}},
            }
        },
        "query_knowledge_files": {
            "spec": {
                "name": "query_knowledge_files",
                "parameters": {"type": "object", "properties": {}},
            }
        },
    }

    result_body, flags = await middleware.chat_completion_tools_handler(
        request,
        body,
        {
            "__event_call__": _noop_event_call,
            "__event_emitter__": _noop_event_emitter,
            "__metadata__": metadata,
        },
        SimpleNamespace(id="user-1"),
        {"main-model": {"id": "main-model"}},
        tools,
    )

    assert result_body is body
    assert flags == {"sources": []}
    assert len(captured_payloads) == 1
    selector_prompt = captured_payloads[0]["form_data"]["messages"][0]["content"]
    assert "preserve the user's substantive topic terms" in selector_prompt
    assert "Do not preserve conversational scaffolding" in selector_prompt
    assert "prefer local corpus tools first" in selector_prompt
    assert "Do not stay loyal to the local lane out of inertia" in selector_prompt
    assert "Current runtime timestamp:" in selector_prompt


@pytest.mark.asyncio
async def test_chat_completion_tools_handler_emits_model_activity_telemetry(
    monkeypatch,
):
    emitted_events = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({"tool_calls": []}),
                    }
                }
            ]
        }

    monkeypatch.setattr(
        middleware, "generate_chat_completion", fake_generate_chat_completion
    )

    async def _noop_event_call(_payload):
        return None

    async def _event_emitter(payload):
        emitted_events.append(payload)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="",
                    TASK_MODEL="specialist-model",
                    TASK_MODEL_EXTERNAL="",
                )
            )
        )
    )
    metadata = {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "params": {
            "function_calling": "default",
            "local_corpus_mode": "prefer",
            "debug_tool_journey": True,
        },
        "features": {"web_search": False, "focused_search": False},
    }
    body = {
        "model": "main-model",
        "messages": [{"role": "user", "content": "Headache plus mild anemia"}],
    }
    tools = {
        "local_corpus_frame_problem": {
            "spec": {
                "name": "local_corpus_frame_problem",
                "parameters": {"type": "object", "properties": {}},
            }
        }
    }

    result_body, flags = await middleware.chat_completion_tools_handler(
        request,
        body,
        {
            "__event_call__": _noop_event_call,
            "__event_emitter__": _event_emitter,
            "__metadata__": metadata,
        },
        SimpleNamespace(id="user-1"),
        {
            "main-model": {"id": "main-model", "connection_type": "local"},
            "specialist-model": {
                "id": "specialist-model",
                "connection_type": "local",
            },
        },
        tools,
    )

    assert result_body is body
    assert flags == {"sources": [], "toolJourneyTelemetry": metadata["tool_journey_telemetry"]}

    model_events = [
        event["data"]
        for event in emitted_events
        if event.get("type") == "chat:tool:journey"
        and event.get("data", {}).get("kind") == "model_activity"
    ]
    assert len(model_events) == 2
    assert model_events[0]["phase"] == "model_task_start"
    assert model_events[0]["task_kind"] == "function_calling"
    assert model_events[0]["model_id"] == "specialist-model"
    assert model_events[0]["active_model_id"] == "main-model"
    assert model_events[0]["actor"] == "bounded_specialist"
    assert model_events[1]["phase"] == "model_task_done"
    assert model_events[1]["status"] == "ok"
    assert isinstance(model_events[1]["duration_ms"], int)


@pytest.mark.asyncio
async def test_process_chat_payload_injects_offsec_native_prompt_without_tool_scope(
    monkeypatch,
):
    request = _build_request(
        enable_local_corpus=True,
        offsec_corpus_root="/tmp/offsec-corpus",
    )
    user = SimpleNamespace(id="user-1")
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        }
    }
    form_data = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "Assess this target."}],
    }
    model = {
        "id": "demo-model",
        "info": {
            "meta": {
                "capabilities": {
                    "builtin_tools": False,
                    "file_context": False,
                }
            }
        },
    }

    monkeypatch.setattr(
        middleware,
        "apply_global_cache_prompt",
        lambda form_data, _model, _enabled: form_data,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_recall_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_context_maintenance_enabled",
        lambda *_args, **_kwargs: False,
    )
    async def _noop_ledger(**kwargs):
        return kwargs["messages"], {}

    monkeypatch.setattr(middleware, "maybe_apply_ledger", _noop_ledger)

    async def _noop_pipeline(_request, current_form_data, _user, _models):
        return current_form_data

    async def _noop_filters(**kwargs):
        return kwargs["form_data"], {}

    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", _noop_pipeline)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "open_webui.utils.middleware.Functions.get_functions_by_ids",
        lambda _ids: [],
    )
    monkeypatch.setattr(middleware, "process_filter_functions", _noop_filters)

    updated_form_data, updated_metadata, events = await middleware.process_chat_payload(
        request,
        form_data,
        user,
        metadata,
        model,
    )

    system_message = next(
        (message for message in updated_form_data["messages"] if message.get("role") == "system"),
        None,
    )

    assert system_message is not None
    assert middleware.OFFSEC_CONSULT_SYSTEM_PROMPT in system_message["content"]
    assert "Do not use generic knowledge-base, notes, or prior-chat tools" in system_message["content"]
    assert updated_metadata["system_prompt"] == system_message["content"]
    assert events == []


@pytest.mark.asyncio
async def test_process_chat_payload_injects_offsec_guided_entry_prompt_for_terminal_run(
    monkeypatch,
):
    request = _build_request(
        enable_local_corpus=True,
        offsec_corpus_root="/tmp/offsec-guided-entry",
    )
    user = SimpleNamespace(id="user-1")
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        }
    }
    form_data = {
        "model": "demo-model",
        "terminal_id": "term-1",
        "messages": [
            {
                "role": "user",
                "content": "Assess https://example.com and proceed with a first pass.",
            }
        ],
    }
    model = {
        "id": "demo-model",
        "info": {
            "meta": {
                "capabilities": {
                    "builtin_tools": True,
                    "file_context": False,
                }
            }
        },
    }

    monkeypatch.setattr(
        middleware,
        "apply_global_cache_prompt",
        lambda form_data, _model, _enabled: form_data,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_recall_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_context_maintenance_enabled",
        lambda *_args, **_kwargs: False,
    )

    async def _noop_ledger(**kwargs):
        return kwargs["messages"], {}

    async def _noop_pipeline(_request, current_form_data, _user, _models):
        return current_form_data

    async def _noop_filters(**kwargs):
        return kwargs["form_data"], {}

    async def _terminal_tools(*_args, **_kwargs):
        return {
            "run_command": {"spec": {"parameters": {"properties": {"command": {}}}}},
            "get_process_status": {"spec": {"parameters": {"properties": {"process_id": {}}}}},
        }

    monkeypatch.setattr(middleware, "maybe_apply_ledger", _noop_ledger)
    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", _noop_pipeline)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "open_webui.utils.middleware.Functions.get_functions_by_ids",
        lambda _ids: [],
    )
    monkeypatch.setattr(middleware, "process_filter_functions", _noop_filters)
    monkeypatch.setattr(middleware, "get_terminal_tools", _terminal_tools)
    monkeypatch.setattr(
        middleware,
        "get_builtin_tools",
        lambda *_args, **_kwargs: {
            "offsec_consult": {"spec": {"parameters": {"properties": {}}}},
            "offsec_register_plan": {"spec": {"parameters": {"properties": {}}}},
            "offsec_register_step_result": {"spec": {"parameters": {"properties": {}}}},
            "offsec_retrieve_evidence": {"spec": {"parameters": {"properties": {}}}},
        },
    )

    updated_form_data, updated_metadata, _events = await middleware.process_chat_payload(
        request,
        form_data,
        user,
        metadata,
        model,
    )

    system_message = next(
        (message for message in updated_form_data["messages"] if message.get("role") == "system"),
        None,
    )

    assert system_message is not None
    assert middleware.OFFSEC_GUIDED_ENTRY_SYSTEM_PROMPT in system_message["content"]
    assert "default execution_context is remote_observer" in system_message["content"]
    assert updated_metadata["offsec_guided_entry_expected"] is True
    assert updated_metadata["offsec_guided_requires_clarification"] is False


@pytest.mark.asyncio
async def test_process_chat_payload_advances_guided_state_on_continue(
    monkeypatch,
):
    request = _build_request(
        enable_local_corpus=True,
        offsec_corpus_root="/tmp/offsec-guided-continue",
    )
    user = SimpleNamespace(id="user-1")
    metadata = {
        "params": {
            "function_calling": "native",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
        }
    }
    form_data = {
        "model": "demo-model",
        "terminal_id": "term-1",
        "messages": [
            {
                "role": "assistant",
                "content": "Plan registered.",
                middleware.GUIDED_STATE_KEY: {
                    "guided_run_id": "offsec-guided-test",
                    "objective": "Assess https://example.com",
                    "phase": "first_pass",
                    "execution_context": "remote_observer",
                    "bound_terminal_id": "term-1",
                    "assumptions": [],
                    "steps": [
                        {
                            "id": "step-1",
                            "title": "Recon",
                            "purpose": "Map the target.",
                            "primary_action_classes": ["passive_recon"],
                            "suggested_tools": ["run_command"],
                            "acceptance_criteria": [
                                {"id": "headers", "text": "Headers checked"},
                                {"id": "routes", "text": "Routes sampled"},
                            ],
                            "forbidden_action_classes": [
                                "remediation",
                                "local_system_modification",
                            ],
                        },
                        {
                            "id": "step-2",
                            "title": "Validation",
                            "purpose": "Validate the best hypothesis.",
                            "primary_action_classes": ["focused_validation"],
                            "suggested_tools": ["run_command"],
                            "acceptance_criteria": [
                                {"id": "hypothesis", "text": "Hypothesis stated"},
                                {"id": "signal", "text": "Signal gathered"},
                            ],
                            "forbidden_action_classes": [
                                "remediation",
                                "local_system_modification",
                            ],
                        },
                    ],
                    "active_step_id": "step-1",
                    "completed_step_ids": ["step-1"],
                    "recommended_next_step_id": "step-2",
                    "latest_observations": [],
                    "waiting_for_confirmation": True,
                    "current_step_run_command_count": 7,
                    "step_run_command_budget": 8,
                    "remaining_step_run_command_budget": 1,
                },
            },
            {"role": "user", "content": "continue"},
        ],
    }
    model = {
        "id": "demo-model",
        "info": {
            "meta": {
                "capabilities": {
                    "builtin_tools": False,
                    "file_context": False,
                }
            }
        },
    }

    monkeypatch.setattr(
        middleware,
        "apply_global_cache_prompt",
        lambda form_data, _model, _enabled: form_data,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_chat_recall_enabled",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        middleware,
        "_resolve_context_maintenance_enabled",
        lambda *_args, **_kwargs: False,
    )

    async def _noop_ledger(**kwargs):
        return kwargs["messages"], {}

    async def _noop_pipeline(_request, current_form_data, _user, _models):
        return current_form_data

    async def _noop_filters(**kwargs):
        return kwargs["form_data"], {}

    monkeypatch.setattr(middleware, "maybe_apply_ledger", _noop_ledger)
    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", _noop_pipeline)
    monkeypatch.setattr(middleware, "get_sorted_filter_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "open_webui.utils.middleware.Functions.get_functions_by_ids",
        lambda _ids: [],
    )
    monkeypatch.setattr(middleware, "process_filter_functions", _noop_filters)

    updated_form_data, updated_metadata, _events = await middleware.process_chat_payload(
        request,
        form_data,
        user,
        metadata,
        model,
    )

    system_message = next(
        (message for message in updated_form_data["messages"] if message.get("role") == "system"),
        None,
    )

    assert system_message is not None
    assert "Active step: step-2 - Validation" in system_message["content"]
    assert updated_metadata["offsec_guided_state_effective"]["active_step_id"] == "step-2"
    assert updated_metadata["offsec_guided_state_effective"]["waiting_for_confirmation"] is False
    assert (
        updated_metadata["offsec_guided_state_effective"]["remaining_step_run_command_budget"]
        == 8
    )


def test_maybe_block_offsec_guided_tool_call_rejects_chained_run_command():
    metadata = {
        "offsec_guided_state_effective": {
            "active_step_id": "step-1",
            "waiting_for_confirmation": False,
            "current_step_run_command_count": 0,
            "step_run_command_budget": 8,
            "remaining_step_run_command_budget": 8,
        }
    }

    payload = middleware._maybe_block_offsec_guided_tool_call(
        metadata,
        "run_command",
        "run_command",
        {"command": "curl -I https://example.com && nikto -h https://example.com"},
    )

    assert payload is not None
    assert payload["kind"] == "command_payload_blocked"


def test_record_offsec_guided_run_command_tracks_budget_and_notice():
    metadata = {
        "offsec_guided_state_effective": {
            "active_step_id": "step-1",
            "waiting_for_confirmation": False,
            "current_step_run_command_count": 5,
            "step_run_command_budget": 8,
            "remaining_step_run_command_budget": 3,
        }
    }

    result = middleware._record_offsec_guided_run_command(
        metadata,
        json.dumps({"status": "ok"}),
    )

    assert metadata["offsec_guided_state_effective"]["current_step_run_command_count"] == 6
    assert metadata["offsec_guided_state_effective"]["remaining_step_run_command_budget"] == 2
    assert "guided_budget_notice" in json.loads(result)


def test_append_tool_journey_event_is_on_demand():
    metadata = {"chat_id": "chat-1", "message_id": "msg-1", "params": {}}
    assert (
        middleware._append_tool_journey_event(
            metadata, {"phase": "tool_execute_start", "tool": "search_strong_sources"}
        )
        is None
    )

    metadata["params"]["debug_tool_journey"] = True
    event = middleware._append_tool_journey_event(
        metadata, {"phase": "tool_execute_done", "tool": "search_strong_sources"}
    )

    assert event is not None
    assert event["phase"] == "tool_execute_done"
    assert (
        metadata["tool_journey_telemetry"]["events"][0]["tool"]
        == "search_strong_sources"
    )


def test_build_agent_loop_termination_cause_includes_error_metadata():
    cause = middleware._build_agent_loop_termination_cause(
        kind="continuation_exception",
        phase="tool_loop_continuation",
        exc=HTTPException(status_code=504, detail="upstream timed out"),
    )

    assert cause["kind"] == "continuation_exception"
    assert cause["phase"] == "tool_loop_continuation"
    assert cause["status_code"] == 504
    assert cause["exception_type"] == "HTTPException"
    assert cause["error"] == "upstream timed out"


def test_summarize_pending_tool_calls_returns_count_and_unique_names():
    pending_count, pending_names = middleware._summarize_pending_tool_calls(
        [
            [
                {"function": {"name": "run_command"}},
                {"function": {"name": "get_process_status"}},
            ],
            [
                {"function": {"name": "run_command"}},
                {"function": {"name": ""}},
            ],
        ]
    )

    assert pending_count == 4
    assert pending_names == ["run_command", "get_process_status"]


def test_build_agent_loop_limit_termination_cause_includes_retry_metadata():
    cause = middleware._build_agent_loop_limit_termination_cause(
        kind="tool_call_limit_reached",
        phase="tool_loop",
        retries=30,
        limit=30,
        pending_count=2,
        pending_names=["run_command", "get_process_status"],
    )

    assert cause["kind"] == "tool_call_limit_reached"
    assert cause["phase"] == "tool_loop"
    assert '"retries": 30' in cause["detail"]
    assert '"limit": 30' in cause["detail"]
    assert '"pending_count": 2' in cause["detail"]
    assert '"pending_names": ["run_command", "get_process_status"]' in cause["detail"]


@pytest.mark.asyncio
async def test_non_streaming_chat_response_includes_tool_journey_telemetry(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )

    async def _background_tasks(_ctx):
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        _background_tasks,
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {"debug_tool_journey": True},
            "tool_journey_telemetry": {
                "enabled": True,
                "events": [
                    {"phase": "tool_execute_done", "tool": "search_strong_sources"}
                ],
            },
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    result = await non_streaming_chat_response_handler(
        {"choices": [{"message": {"content": "ok"}}]},
        ctx,
    )

    assert "toolJourneyTelemetry" in result
    assert (
        result["toolJourneyTelemetry"]["events"][0]["tool"] == "search_strong_sources"
    )
    assert (
        saved_messages[0][2]["toolJourneyTelemetry"]["events"][0]["phase"]
        == "tool_execute_done"
    )


def test_search_strong_sources_citation_source_prefers_citation_items():
    tool_result = {
        "items": [
            {
                "title": "Candidate only",
                "link": "https://candidate.example/a",
                "snippet": "candidate",
            }
        ],
        "citation_items": [
            {
                "title": "Citation kept",
                "link": "https://citation.example/b",
                "snippet": "citation",
            }
        ],
    }

    sources = middleware.get_citation_source_from_tool_result(
        "search_strong_sources",
        {},
        tool_result,
    )

    assert len(sources) == 1
    metadata = sources[0]["metadata"]
    assert len(metadata) == 1
    assert metadata[0]["source"] == "https://citation.example/b"


def test_web_research_strong_citation_source_prefers_citation_items():
    tool_result = {
        "items": [
            {
                "title": "Candidate only",
                "link": "https://candidate.example/a",
                "snippet": "candidate",
            }
        ],
        "citation_items": [
            {
                "title": "Citation kept",
                "link": "https://citation.example/b",
                "snippet": "citation",
            }
        ],
    }

    sources = middleware.get_citation_source_from_tool_result(
        "web_research_strong",
        {},
        tool_result,
    )

    assert len(sources) == 1
    metadata = sources[0]["metadata"]
    assert len(metadata) == 1
    assert metadata[0]["source"] == "https://citation.example/b"


def test_query_web_evidence_citation_source_uses_snippets():
    tool_result = {
        "snippets": [
            {
                "title": "Evidence A",
                "url": "https://example.org/a",
                "text": "Window around match",
                "artifact_id": "wp_1",
                "domain": "example.org",
                "start": 10,
                "end": 42,
                "score": 0.91,
            }
        ]
    }

    sources = middleware.get_citation_source_from_tool_result(
        "query_web_evidence",
        {},
        tool_result,
    )

    assert len(sources) == 1
    metadata = sources[0]["metadata"]
    assert len(metadata) == 1
    assert metadata[0]["source"] == "https://example.org/a"
    assert metadata[0]["artifact_id"] == "wp_1"


def test_local_corpus_retrieve_evidence_citation_source_groups_by_book():
    tool_result = {
        "items": [
            {
                "domain": "medicine",
                "book_id": "med-guide",
                "title": "Hypertension management guideline",
                "page_no": 2,
                "section_path": "Management",
                "citation_label": "Hypertension management guideline | p. 2 | Management",
                "content": "Start treatment when blood pressure remains above threshold.",
            }
        ]
    }

    sources = middleware.get_citation_source_from_tool_result(
        "local_corpus_retrieve_evidence",
        {},
        tool_result,
    )

    assert len(sources) == 1
    assert sources[0]["source"]["id"] == "med-guide"
    assert sources[0]["metadata"][0]["page_no"] == 2


def test_local_corpus_view_table_citation_source_uses_content_text():
    tool_result = {
        "domain": "medicine",
        "book_id": "med-guide",
        "title": "Hypertension management guideline",
        "table_id": "table-001",
        "page_no": 2,
        "section_path": "Management",
        "content_text": "Drug | Threshold\nACE inhibitor | >=140/90",
    }

    sources = middleware.get_citation_source_from_tool_result(
        "local_corpus_view_table",
        {},
        tool_result,
    )

    assert len(sources) == 1
    assert "ACE inhibitor" in sources[0]["document"][0]
    assert sources[0]["metadata"][0]["book_id"] == "med-guide"


def test_local_corpus_collect_axis_evidence_citation_source_groups_by_book():
    tool_result = {
        "axis_results": [
            {
                "axis_id": "management_guidance",
                "evidence_items": [
                    {
                        "domain": "medicine",
                        "book_id": "med-guide",
                        "title": "Hypertension management guideline",
                        "page_no": 2,
                        "section_path": "Management",
                        "citation_label": "Hypertension management guideline | p. 2 | Management",
                        "content": "Start treatment when blood pressure remains above threshold."
                    }
                ]
            }
        ]
    }

    sources = middleware.get_citation_source_from_tool_result(
        "local_corpus_collect_axis_evidence",
        {},
        tool_result,
    )

    assert len(sources) == 1
    assert sources[0]["source"]["id"] == "med-guide"
    assert sources[0]["metadata"][0]["axis_id"] == "management_guidance"


def test_offsec_consult_citation_source_uses_source_documents():
    tool_result = {
        "source_documents": [
            {
                "id": "tool:burp_suite",
                "name": "Burp Suite",
                "type": "offsec_tool_card",
                "content": "# Burp Suite\n\nWorkflow guidance",
                "source_path": "/tmp/offsec/tools/burp_suite.md",
                "book_id": "web-application-pentesting",
                "domain": "web_security",
            }
        ]
    }

    sources = middleware.get_citation_source_from_tool_result(
        "offsec_consult",
        {},
        tool_result,
    )

    assert len(sources) == 1
    assert sources[0]["source"]["type"] == "offsec_tool_card"
    assert sources[0]["metadata"][0]["book_id"] == "web-application-pentesting"


def test_offsec_retrieve_evidence_citation_source_groups_by_book():
    tool_result = {
        "items": [
            {
                "book_id": "web-application-pentesting",
                "title": "Web Application PenTesting",
                "domain": "web_security",
                "page_no": 42,
                "section_path": "Burp Suite workflow",
                "citation_label": "Web Application PenTesting p.42 - Burp Suite workflow",
                "content": "Burp Suite helps with intercepting, replaying, and validating issues.",
            }
        ]
    }

    sources = middleware.get_citation_source_from_tool_result(
        "offsec_retrieve_evidence",
        {},
        tool_result,
    )

    assert len(sources) == 1
    assert sources[0]["source"]["id"] == "web-application-pentesting"
    assert sources[0]["metadata"][0]["page_no"] == 42


def test_is_empty_search_notes_result_detects_empty_payloads():
    assert middleware._is_empty_search_notes_result("[]") is True
    assert middleware._is_empty_search_notes_result([]) is True
    assert middleware._is_empty_search_notes_result("") is True
    assert middleware._is_empty_search_notes_result({}) is True
    assert middleware._is_empty_search_notes_result({"items": []}) is True


def test_is_empty_search_notes_result_rejects_non_empty_payloads():
    assert middleware._is_empty_search_notes_result('[{"id":"n1"}]') is False
    assert middleware._is_empty_search_notes_result(
        {"items": [{"id": "n1"}]}
    ) is False
    assert middleware._is_empty_search_notes_result({"error": "db failure"}) is False
    assert middleware._is_empty_search_notes_result("not-json") is False


def test_build_search_notes_loop_breaker_result_has_broad_search_hint():
    payload = json.loads(middleware._build_search_notes_loop_breaker_result(2))
    assert payload["tool"] == "notes_lookup"
    assert payload["empty_streak"] == 2
    assert payload["next_tool"] == "search_web"
    assert "searches user notes only" in payload["message"]


def test_build_search_notes_loop_breaker_result_keeps_tool_alias_when_passed():
    payload = json.loads(
        middleware._build_search_notes_loop_breaker_result(
            2, tool_name="search_notes"
        )
    )
    assert payload["tool"] == "search_notes"
    assert payload["next_tool"] == "search_web"


def test_build_search_notes_loop_breaker_result_without_web_tool():
    payload = json.loads(
        middleware._build_search_notes_loop_breaker_result(2, next_tool=None)
    )
    assert payload["next_tool"] is None
    assert payload["next_action"] == "enable_internet_access"
    assert "Internet tools are not available" in payload["hint"]


def test_build_research_loop_breaker_result_prefers_answering_with_uncertainty():
    payload = json.loads(
        middleware._build_research_loop_breaker_result(
            {
                "research_loop_breaker_reason": "repeated_weak_evidence",
                "weak_evidence_streak": 2,
                "recent_artifact_count": 3,
            }
        )
    )
    assert payload["reason"] == "repeated_weak_evidence"
    assert payload["weak_evidence_streak"] == 2
    assert payload["recent_artifact_count"] == 3
    assert payload["next_action"] == "answer_with_current_evidence"
    assert "remaining uncertainty" in payload["hint"]


def test_build_research_loop_breaker_result_mentions_empty_fetches():
    payload = json.loads(
        middleware._build_research_loop_breaker_result(
            {
                "research_loop_breaker_reason": "repeated_weak_evidence_with_empty_fetches",
                "weak_evidence_streak": 2,
                "recent_artifact_count": 2,
            },
            blocked=True,
            tool_name="search_web",
        )
    )
    assert payload["status"] == "loop_breaker_active"
    assert payload["tool"] == "search_web"
    assert "no usable stored content" in payload["message"]


def test_finalize_completed_reasoning_details_marks_done_and_duration():
    content = (
        '<details type="reasoning" done="false">\n'
        "<summary>Thinking…</summary>\n"
        "foo\n"
        "</details>"
    )

    normalized = middleware._finalize_completed_reasoning_details(content)

    assert 'done="true"' in normalized
    assert 'duration="0"' in normalized
    assert 'done="false"' not in normalized


@pytest.mark.asyncio
async def test_non_streaming_chat_response_normalizes_final_reasoning_details(monkeypatch):
    saved_messages = []

    def _save_message(chat_id, message_id, payload):
        saved_messages.append((chat_id, message_id, payload))
        return None

    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.upsert_message_to_chat_by_id_and_message_id",
        _save_message,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Chats.get_chat_title_by_id",
        lambda _chat_id: "Test Chat",
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.Users.is_user_active",
        lambda _user_id: True,
    )
    monkeypatch.setattr(
        "open_webui.utils.middleware.background_tasks_handler",
        lambda _ctx: asyncio.sleep(0),
    )

    ctx = {
        "request": SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    WEBUI_NAME="Open WebUI",
                    config=SimpleNamespace(WEBUI_URL="https://example.test"),
                )
            )
        ),
        "user": SimpleNamespace(id="user-1"),
        "metadata": {
            "chat_id": "chat-1",
            "message_id": "message-1",
            "params": {},
        },
        "events": [],
        "event_emitter": None,
        "form_data": {"messages": [{"role": "user", "content": "hello"}]},
        "tasks": None,
    }

    result = await non_streaming_chat_response_handler(
        {
            "choices": [
                {
                    "message": {
                        "content": (
                            '<details type="reasoning" done="false">\n'
                            "<summary>Thinking…</summary>\n"
                            "foo\n"
                            "</details>\n\nAnswer."
                        )
                    }
                }
            ]
        },
        ctx,
    )

    persisted = saved_messages[0][2]["content"]
    assert 'done="true"' in persisted
    assert 'duration="0"' in persisted
    assert 'done="false"' not in persisted
    assert 'done="true"' in result["choices"][0]["message"]["content"]


def test_update_research_turn_state_triggers_breaker_on_stagnant_weak_evidence():
    metadata = {"chat_id": "chat-1", "message_id": "msg-1"}

    middleware._update_research_turn_state(
        metadata,
        tool_name="search_web",
        tool_params={},
        tool_result=[{"link": "https://example.com/a", "title": "A"}],
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="fetch_url",
        tool_params={"mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1",
            "domain": "example.com",
            "content_chars": 1200,
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )

    state = middleware._research_turn_state(metadata)
    assert state["weak_evidence_streak"] == 2
    assert state["research_loop_breaker_triggered"] is True
    assert state["research_loop_breaker_reason"] == "repeated_weak_evidence"


def test_update_research_turn_state_does_not_trigger_breaker_when_artifact_scope_grows():
    metadata = {"chat_id": "chat-1", "message_id": "msg-1"}

    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 2,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 3,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )

    state = middleware._research_turn_state(metadata)
    assert state["weak_evidence_streak"] == 1
    assert state["research_loop_breaker_triggered"] is False


def test_update_research_turn_state_marks_empty_fetches_in_breaker_reason():
    metadata = {"chat_id": "chat-1", "message_id": "msg-1"}

    middleware._update_research_turn_state(
        metadata,
        tool_name="fetch_url",
        tool_params={"mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-1",
            "domain": "example.com",
            "content_chars": 0,
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="fetch_url",
        tool_params={"mode": "store"},
        tool_result={
            "status": "stored",
            "mode": "store",
            "artifact_id": "art-2",
            "domain": "example.org",
            "content_chars": 0,
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 2,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )
    middleware._update_research_turn_state(
        metadata,
        tool_name="query_web_evidence",
        tool_params={},
        tool_result={
            "status": "ok",
            "snippets": [],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 2,
            "evidence_strength": "weak",
            "suggested_next_action": "refine_query",
        },
    )

    state = middleware._research_turn_state(metadata)
    assert state["empty_fetch_streak"] == 2
    assert state["research_loop_breaker_reason"] == "repeated_weak_evidence_with_empty_fetches"


def test_tool_name_alias_maps_notes_research_strong():
    assert (
        middleware.TOOL_NAME_ALIASES.get("notes_research_strong")
        == "web_research_strong"
    )


@pytest.mark.asyncio
async def test_run_background_source_diary_generation_writes_markdown(
    monkeypatch, tmp_path
):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={
                    "main-model": {"id": "main-model", "connection_type": "local"},
                    "specialist-model": {
                        "id": "specialist-model",
                        "connection_type": "local",
                    },
                },
                config=SimpleNamespace(
                    TASK_MODEL="specialist-model",
                    TASK_MODEL_EXTERNAL="",
                ),
            )
        ),
        state=SimpleNamespace(metadata={}),
    )
    emitted_events = []

    async def _event_emitter(event):
        emitted_events.append(event)

    async def _fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        assert form_data["model"] == "specialist-model"
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            "## Metadata\n\n- ok\n\n## User Question\n\nQ\n\n"
                            "## Final Answer Synopsis\n\nA\n\n## Discovery Path\n\n"
                            "- search_web\n\n## Helpful Sources\n\n- Reuters\n\n"
                            "## Weak Or Unhelpful Sources\n\n- none\n\n"
                            "## Candidate Domains For Manual Curation\n\n- reuters.com\n"
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(
        middleware, "generate_chat_completion", _fake_generate_chat_completion
    )
    monkeypatch.setattr(
        middleware, "_resolve_chat_artifacts_dir", lambda _chat_id: tmp_path
    )

    result = await middleware.run_background_source_diary_generation(
        request=request,
        user=SimpleNamespace(id="user-1"),
        active_model_id="main-model",
        chat_id="chat-1",
        message_id="msg-1",
        context={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "stored_artifact_ids": ["wp_1"],
        },
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "user_prompt": "why has nobody seized the strait",
            "params": {"debug_tool_journey": True},
        },
        event_emitter=_event_emitter,
    )

    diary_path = tmp_path / "source_diary" / "msg-1.md"
    assert result["status"] == "written"
    assert diary_path.exists()
    assert "## Metadata" in diary_path.read_text(encoding="utf-8")

    phases = [
        event["data"]["phase"]
        for event in emitted_events
        if event.get("type") == "chat:tool:journey"
    ]
    assert phases == [
        "source_diary_generation_started",
        "source_diary_generation_done",
    ]


@pytest.mark.asyncio
async def test_run_background_source_diary_generation_skips_without_specialist(
    monkeypatch, tmp_path
):
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={
                    "main-model": {"id": "main-model", "connection_type": "local"},
                },
                config=SimpleNamespace(
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL="",
                ),
            )
        ),
        state=SimpleNamespace(metadata={}),
    )

    result = await middleware.run_background_source_diary_generation(
        request=request,
        user=SimpleNamespace(id="user-1"),
        active_model_id="main-model",
        chat_id="chat-1",
        message_id="msg-1",
        context={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "stored_artifact_ids": ["wp_1"],
        },
        metadata={
            "chat_id": "chat-1",
            "message_id": "msg-1",
        },
        event_emitter=None,
    )

    assert result["status"] == "skipped"
    assert not (tmp_path / "source_diary" / "msg-1.md").exists()
