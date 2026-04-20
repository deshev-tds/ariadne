import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import open_webui.utils.misc as misc
import open_webui.utils.middleware as middleware
from open_webui.utils.middleware import (
    apply_params_to_form_data,
    background_tasks_handler,
    non_streaming_chat_response_handler,
)


def _build_request(
    *,
    enable_local_corpus: bool = False,
    local_corpus_root: str | None = None,
    offsec_corpus_root: str | None = None,
    enable_same_turn_tool_output_compaction: bool = False,
):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    ENABLE_LOCAL_CORPUS_TOOLS=enable_local_corpus,
                    ENABLE_SAME_TURN_TOOL_OUTPUT_COMPACTION=enable_same_turn_tool_output_compaction,
                    LOCAL_CORPUS_ROOT=local_corpus_root,
                    OFFSEC_CORPUS_ROOT=offsec_corpus_root,
                    TASK_MODEL="",
                    TASK_MODEL_EXTERNAL=False,
                )
            )
        )
)


def test_process_messages_with_output_omits_history_reasoning_and_caps_tool_output(
    monkeypatch,
):
    monkeypatch.setattr(misc, "ENABLE_HISTORY_REASONING_REPLAY", False)

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
    assert "[prior tool output omitted from cross-turn replay]" in processed[1]["content"]
    assert "If exact earlier tool details matter, make a fresh tool call." in processed[1]["content"]
    assert "X" not in processed[1]["content"]
    assert processed[2]["content"] == "Final answer"
    assert "details type=\"reasoning\"" not in processed[3]["content"]
    assert processed[3]["content"] == "Before\n\nAfter"


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
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
            "temperature": 0.2,
        }
    }
    model = {"owned_by": "openai"}

    result = apply_params_to_form_data(form_data, model)

    assert "ledger_mode" not in result
    assert "working_mode" not in result
    assert "local_corpus_mode" not in result
    assert result["temperature"] == 0.2


def test_apply_params_strips_ledger_mode_from_ollama_options():
    form_data = {
        "params": {
            "ledger_mode": "agentic",
            "working_mode": "offsec",
            "local_corpus_mode": "prefer",
            "temperature": 0.2,
        }
    }
    model = {"owned_by": "ollama"}

    result = apply_params_to_form_data(form_data, model)

    assert result["options"].get("temperature") == 0.2
    assert "ledger_mode" not in result["options"]
    assert "working_mode" not in result["options"]
    assert "local_corpus_mode" not in result["options"]


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


def test_build_forced_default_selector_tool_call_returns_medical_gate_for_medical_mode():
    metadata = {
        "params": {"function_calling": "default", "working_mode": "medical"},
    }
    tools = {
        "medical_corpus_sufficiency": {},
    }

    forced = middleware._build_forced_default_selector_tool_call(
        metadata,
        tools,
        [{"role": "user", "content": "How do I use xylometazoline?"}],
    )

    assert forced == {
        "name": "medical_corpus_sufficiency",
        "parameters": {"query": "How do I use xylometazoline?"},
    }


def test_build_forced_default_selector_tool_call_skips_non_medical_or_missing_tool():
    metadata = {
        "params": {"function_calling": "default", "working_mode": "general"},
    }

    assert (
        middleware._build_forced_default_selector_tool_call(
            metadata, {"medical_corpus_sufficiency": {}}
        )
        is None
    )
    assert (
        middleware._build_forced_default_selector_tool_call(
            {"params": {"function_calling": "default", "working_mode": "medical"}},
            {"web_research_strong": {}},
            [{"role": "user", "content": "test"}],
        )
        is None
    )


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
    }

    guidance = middleware._build_default_selector_guidance(metadata, tools, [])

    assert "start with offsec_consult" in guidance
    assert "project/GitHub docs before broad web search" in guidance


def test_should_enable_shared_tool_narration_for_local_corpus_prefer():
    request = _build_request(
        enable_local_corpus=True, local_corpus_root="/tmp/local-corpus"
    )
    metadata = {"params": {"function_calling": "native", "local_corpus_mode": "prefer"}}

    assert (
        middleware._should_enable_shared_tool_narration(request, metadata, {})
        is True
    )


def test_should_enable_shared_tool_narration_for_offsec_mode():
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
    )

    assert "phase guidance" in messages[0]["content"]
    assert "phase guidance" not in form_messages[0]["content"]
    assert messages[-1]["role"] == "assistant"


def test_build_tool_continuation_messages_compacts_older_tool_outputs_when_enabled():
    request = _build_request(enable_same_turn_tool_output_compaction=True)
    metadata = {
        "persona_effective_capabilities": {
            "same_turn_tool_output_compaction": True,
        }
    }
    older_text = "x" * 4000
    latest_text = "LATEST RAW TOOL OUTPUT"
    form_messages = [{"role": "system", "content": "base prompt"}]
    output = [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_old",
            "name": "fetch_url",
            "arguments": json.dumps({"url": "https://example.com/old"}),
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "id": "fco_1",
            "call_id": "call_old",
            "output": [{"type": "input_text", "text": older_text}],
            "status": "completed",
        },
        {
            "type": "function_call",
            "id": "fc_2",
            "call_id": "call_latest",
            "name": "search_web",
            "arguments": json.dumps({"query": "lecce nightlife"}),
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "id": "fco_2",
            "call_id": "call_latest",
            "output": [{"type": "input_text", "text": latest_text}],
            "status": "completed",
        },
    ]

    messages = middleware._build_tool_continuation_messages(
        form_messages,
        output,
        request=request,
        metadata=metadata,
        latest_tool_call_ids=["call_latest"],
    )

    tool_messages = [message for message in messages if message.get("role") == "tool"]
    assert len(tool_messages) == 2
    assert "[same-turn tool output compacted for context budget]" in tool_messages[0][
        "content"
    ]
    assert "original_chars: 4000" in tool_messages[0]["content"]
    assert tool_messages[1]["content"] == latest_text
    assert metadata["same_turn_tool_output_compaction"]["compacted_call_count"] == 1
    assert metadata["same_turn_tool_output_compaction"]["saved_chars"] > 0


def test_build_tool_continuation_messages_skips_compaction_when_persona_capability_off():
    request = _build_request(enable_same_turn_tool_output_compaction=True)
    metadata = {
        "persona_effective_capabilities": {
            "same_turn_tool_output_compaction": False,
        }
    }
    raw_text = "RAW SEARCH OUTPUT"
    form_messages = [{"role": "system", "content": "base prompt"}]
    output = [
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "search_web",
            "arguments": json.dumps({"query": "lecce nightlife"}),
            "status": "completed",
        },
        {
            "type": "function_call_output",
            "id": "fco_1",
            "call_id": "call_1",
            "output": [{"type": "input_text", "text": raw_text}],
            "status": "completed",
        },
    ]

    messages = middleware._build_tool_continuation_messages(
        form_messages,
        output,
        request=request,
        metadata=metadata,
    )

    tool_messages = [message for message in messages if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["content"] == raw_text
    assert "same_turn_tool_output_compaction" not in metadata


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


def test_build_search_notes_loop_breaker_result_has_strong_source_hint():
    payload = json.loads(middleware._build_search_notes_loop_breaker_result(2))
    assert payload["tool"] == "notes_lookup"
    assert payload["empty_streak"] == 2
    assert payload["next_tool"] == "web_research_strong"
    assert "searches user notes only" in payload["message"]


def test_build_search_notes_loop_breaker_result_keeps_tool_alias_when_passed():
    payload = json.loads(
        middleware._build_search_notes_loop_breaker_result(
            2, tool_name="search_notes"
        )
    )
    assert payload["tool"] == "search_notes"
    assert payload["next_tool"] == "web_research_strong"


def test_build_search_notes_loop_breaker_result_without_web_tool():
    payload = json.loads(
        middleware._build_search_notes_loop_breaker_result(2, next_tool=None)
    )
    assert payload["next_tool"] is None
    assert payload["next_action"] == "enable_internet_access"
    assert "Internet tools are not available" in payload["hint"]


def test_tool_name_alias_maps_notes_research_strong():
    assert (
        middleware.TOOL_NAME_ALIASES.get("notes_research_strong")
        == "web_research_strong"
    )
