from types import SimpleNamespace

import pytest

import open_webui.utils.context_maintenance as context_maintenance
from open_webui.utils.context_maintenance import (
    build_aggregate_context_window_preview,
    build_summary_message,
    build_summary_prompt,
    build_context_maintenance_payload,
    build_context_window_model_preview,
    build_request_messages,
    count_preview_messages_tokens,
    extract_model_ctx_cap,
    extract_n_ctx_from_props,
    is_summary_refresh_needed,
    merge_system_message,
    normalize_summary_snapshot,
    parse_prometheus_metrics,
    render_preview_prompt,
    resolve_effective_ctx_cap,
    resolve_history_budgets,
    resolve_live_prompt_cap,
)


def _make_request(**overrides):
    config_values = {
        "CONTEXT_MAINTENANCE_MAX_CTX_CAP": "",
        "CONTEXT_MAINTENANCE_OUTPUT_RESERVE_TOKENS": 8192,
        "CONTEXT_MAINTENANCE_SAFETY_RESERVE_TOKENS": 4096,
        "CONTEXT_MAINTENANCE_RAG_RESERVE_TOKENS": 12288,
        "CONTEXT_MAINTENANCE_SOFT_MARGIN_TOKENS": 8192,
        "CONTEXT_MAINTENANCE_ANCHOR_BUDGET_TOKENS": 2048,
    }
    config_values.update(overrides)
    config = SimpleNamespace(**config_values)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _message(message_id: str, role: str, content: str) -> dict:
    return {"id": message_id, "role": role, "content": content}


def test_parse_prometheus_metrics_extracts_kv_fields():
    metrics = parse_prometheus_metrics("""
        # HELP llamacpp:kv_cache_usage_ratio KV cache usage
        llamacpp:kv_cache_usage_ratio 0.81
        llamacpp:kv_cache_tokens 32768
        unrelated_metric 12
        """)

    assert metrics == {
        "llamacpp:kv_cache_usage_ratio": 0.81,
        "llamacpp:kv_cache_tokens": 32768.0,
    }


def test_parse_prometheus_metrics_ignores_non_kv_metrics():
    metrics = parse_prometheus_metrics("""
        llamacpp:prompt_tokens_total 5061
        llamacpp:requests_processing 1
        llamacpp:n_tokens_max 3618
        """)

    assert metrics == {}


def test_extract_n_ctx_from_props_supports_llamacpp_shape():
    props = {
        "default_generation_settings": {
            "params": {
                "n_ctx": 65536,
            }
        }
    }

    assert extract_n_ctx_from_props(props) == 65536


def test_render_preview_prompt_includes_roles_and_content():
    rendered = render_preview_prompt(
        [
            {"role": "system", "content": "keep state"},
            {"role": "user", "content": "ship the ring"},
        ]
    )

    assert "<|system|>" in rendered
    assert "keep state" in rendered
    assert "<|user|>" in rendered
    assert "ship the ring" in rendered


def test_build_summary_prompt_requests_structured_state_snapshot():
    prompt = build_summary_prompt(transcript="user: hi", max_tokens=512)

    assert "structured state snapshot" in prompt
    assert "Do not write a narrative summary." in prompt
    assert "User Objectives:" in prompt
    assert "Constraints and Preferences:" in prompt
    assert "Decisions and Conclusions:" in prompt
    assert "Open Questions and Unresolved Work:" in prompt
    assert "Stable Facts and Assumptions:" in prompt
    assert "never preserve its literal value" in prompt


def test_normalize_summary_snapshot_canonicalizes_sections():
    normalized = normalize_summary_snapshot("""
        Goals:
        - ship context maintenance
        Constraints:
        - keep it backend-owned
        Decisions:
        - use async maintenance
        Open Questions:
        - add exact recall later
        System Assumptions:
        - llama.cpp metrics may be missing
        """)

    assert "User Objectives:\n- ship context maintenance" in normalized
    assert "Constraints and Preferences:\n- keep it backend-owned" in normalized
    assert "Decisions and Conclusions:\n- use async maintenance" in normalized
    assert "Open Questions and Unresolved Work:\n- add exact recall later" in normalized
    assert (
        "Stable Facts and Assumptions:\n- llama.cpp metrics may be missing"
        in normalized
    )


def test_normalize_summary_snapshot_falls_back_to_stable_facts_block():
    normalized = normalize_summary_snapshot("Keep ffuf as the primary tool.")

    assert "User Objectives:\n- None recorded." in normalized
    assert (
        "Stable Facts and Assumptions:\n- Keep ffuf as the primary tool." in normalized
    )


def test_normalize_summary_snapshot_redacts_transient_marker_values():
    normalized = normalize_summary_snapshot("""
        Stable Facts and Assumptions:
        - The transient marker is basalt-signal-8841 and should be recoverable only from raw history.

        Decisions and Conclusions:
        - Test marker logged: `basalt-signal-8841` was introduced for later recall.
        """)

    assert "basalt-signal-8841" not in normalized
    assert "<transient value>" in normalized


def test_build_summary_message_uses_state_snapshot_wrapper():
    message = build_summary_message("User Objectives:\n- keep the chat stable")

    assert message["role"] == "system"
    assert "Conversation state snapshot for earlier turns." in message["content"]


def test_merge_system_message_appends_blocks_into_single_system_prompt():
    message = merge_system_message(
        {"role": "system", "content": "Base system prompt"},
        [
            "Conversation state snapshot for earlier turns.\n\nUser Objectives:\n- keep stable"
        ],
    )

    assert message["role"] == "system"
    assert "Base system prompt" in message["content"]
    assert "Conversation state snapshot for earlier turns." in message["content"]


def test_build_request_messages_keeps_summary_in_single_leading_system_message():
    messages = build_request_messages(
        system_message={"role": "system", "content": "Base system prompt"},
        anchor_messages=[_message("m1", "user", "initial task")],
        summary_text="User Objectives:\n- keep the chat stable",
        tail_messages=[_message("m2", "user", "latest question")],
    )

    assert messages[0]["role"] == "system"
    assert "Base system prompt" in messages[0]["content"]
    assert "Conversation state snapshot for earlier turns." in messages[0]["content"]
    assert sum(1 for message in messages if message.get("role") == "system") == 1


def test_resolve_effective_ctx_cap_respects_admin_cap():
    request = _make_request(CONTEXT_MAINTENANCE_MAX_CTX_CAP="65536")
    model = {"status": {"args": ["--ctx-size", "131072"]}}

    assert extract_model_ctx_cap(model) == 131072
    assert resolve_effective_ctx_cap(request, model, {"n_ctx": 131072}) == 65536


def test_resolve_live_prompt_cap_prefers_live_probe_over_model_args():
    request = _make_request()
    model = {"status": {"args": ["--ctx-size", "131072"]}}

    live_cap, source = resolve_live_prompt_cap(
        request, model, {"n_ctx": 32768, "source": "slots"}
    )

    assert live_cap == 32768
    assert source == "probe:slots"


def test_resolve_history_budgets_uses_rag_reserve_when_files_present():
    request = _make_request()
    model = {"status": {"args": ["--ctx-size", "32768"]}}

    budgets = resolve_history_budgets(
        request,
        model=model,
        form_data={"model": "demo", "files": [{"id": "f1"}]},
        metadata={},
        probe={"n_ctx": 32768},
    )

    assert budgets["live_prompt_cap"] == 32768
    assert budgets["live_prompt_cap_source"] == "probe:probe"
    assert budgets["hot_context_target_tokens"] == budgets["hard_history_budget"]
    assert budgets["effective_ctx_cap"] == 32768
    assert budgets["rag_reserve_tokens"] == 12288
    assert budgets["hard_history_budget"] < 32768


def test_build_context_payload_reuses_summary_state_without_cascade():
    history = [
        _message("m1", "user", "initial task"),
        _message("m2", "assistant", "initial plan"),
        _message("m3", "user", "constraint one"),
        _message("m4", "assistant", "implementation detail"),
        _message("m5", "user", "latest question"),
    ]
    budgets = {
        "anchor_budget_tokens": 128,
        "hard_history_budget": 128,
    }
    summary_state = {
        "summary_text": "summary over m1..m4",
        "summarized_through_message_id": "m4",
    }

    payload = build_context_maintenance_payload(
        system_message={"role": "system", "content": "You are helpful"},
        history_messages=history,
        summary_state=summary_state,
        budgets=budgets,
    )

    assert payload["used_summary_state"] is True
    assert payload["summary_text"] == "summary over m1..m4"
    assert payload["tail_messages"] == [history[-1]]
    assert payload["telemetry"]["summary_included"] is True
    assert payload["telemetry"]["anchor_message_count"] >= 1


def test_summary_refresh_needs_meaningful_growth():
    history = [
        _message(f"m{i}", "user" if i % 2 else "assistant", "x" * 1000)
        for i in range(1, 8)
    ]

    assert (
        is_summary_refresh_needed(
            history,
            {
                "summary_text": "old summary",
                "summarized_through_message_id": "m1",
            },
        )
        is True
    )

    assert (
        is_summary_refresh_needed(
            history[:2],
            {
                "summary_text": "old summary",
                "summarized_through_message_id": "m1",
            },
        )
        is False
    )


@pytest.mark.asyncio
async def test_background_context_maintenance_closes_status_on_early_return(
    monkeypatch,
):
    history = [
        _message("u1", "user", "question"),
        _message("a1", "assistant", "answer"),
    ]

    monkeypatch.setattr(
        context_maintenance.Chats,
        "get_messages_map_by_chat_id",
        lambda _chat_id: {"u1": history[0], "a1": history[1]},
    )
    monkeypatch.setattr(
        context_maintenance, "get_message_list", lambda *_args, **_kwargs: history
    )
    monkeypatch.setattr(
        context_maintenance,
        "inject_image_files_into_history",
        lambda messages: messages,
    )

    async def fake_probe(*_args, **_kwargs):
        return {"n_ctx": 8192}

    monkeypatch.setattr(context_maintenance, "load_llamacpp_probe", fake_probe)
    monkeypatch.setattr(
        context_maintenance,
        "resolve_history_budgets",
        lambda *_args, **_kwargs: {
            "anchor_budget_tokens": 256,
            "hard_history_budget": 2048,
        },
    )
    monkeypatch.setattr(
        context_maintenance, "get_chat_maintenance_state", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        context_maintenance, "should_schedule_maintenance", lambda **_kwargs: True
    )
    monkeypatch.setattr(
        context_maintenance, "is_summary_refresh_needed", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        context_maintenance, "select_anchor_messages", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        context_maintenance,
        "estimate_tokens_from_history_messages",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        context_maintenance, "select_tail_messages", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        context_maintenance, "resolve_summary_boundary", lambda *_args, **_kwargs: 0
    )

    async def fake_summary(*_args, **_kwargs):
        return "User Objectives:\n- keep stable"

    monkeypatch.setattr(context_maintenance, "generate_history_summary", fake_summary)
    monkeypatch.setattr(
        context_maintenance.Chats,
        "get_chat_by_id",
        lambda _chat_id: SimpleNamespace(
            chat={"history": {"currentId": "different-message"}}
        ),
    )

    saved_states = []
    monkeypatch.setattr(
        context_maintenance,
        "save_chat_maintenance_state",
        lambda *args, **kwargs: saved_states.append((args, kwargs)),
    )

    events = []

    async def event_emitter(event):
        events.append(event)

    request = _make_request()
    await context_maintenance.run_background_context_maintenance(
        request=request,
        user=SimpleNamespace(id="u1"),
        model={"id": "demo"},
        chat_id="chat-1",
        message_id="message-1",
        event_emitter=event_emitter,
    )

    status_events = [
        event.get("data", {})
        for event in events
        if event.get("type") == "status"
        and event.get("data", {}).get("action") == "context_maintenance"
    ]

    assert status_events
    assert status_events[0]["description"] == "Context maintenance scheduled"
    assert status_events[0]["done"] is False
    assert status_events[-1]["done"] is True


@pytest.mark.asyncio
async def test_inline_maintenance_proactively_pretrims_when_near_live_cap(monkeypatch):
    request = _make_request()
    model = {"id": "demo", "status": {"args": ["--ctx-size", "8192"]}}
    history = [
        _message("u1", "user", "question"),
        _message("a1", "assistant", "answer"),
    ]

    async def fake_probe(*_args, **_kwargs):
        return {"n_ctx": 8192}

    monkeypatch.setattr(context_maintenance, "load_llamacpp_probe", fake_probe)
    monkeypatch.setattr(
        context_maintenance,
        "resolve_history_budgets",
        lambda *_args, **_kwargs: {
            "live_prompt_cap": 8192,
            "hard_history_budget": 4096,
            "anchor_budget_tokens": 128,
        },
    )
    monkeypatch.setattr(
        context_maintenance,
        "build_context_maintenance_payload",
        lambda **_kwargs: {
            "messages": [{"role": "user", "content": "oversized"}],
            "used_summary_state": False,
            "telemetry": {},
            "anchor_messages": [],
            "anchor_message_ids": [],
            "tail_messages": history,
        },
    )
    monkeypatch.setattr(
        context_maintenance,
        "should_force_inline_maintenance",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        context_maintenance,
        "estimate_tokens_from_messages",
        lambda *_args, **_kwargs: 7000,
    )
    monkeypatch.setattr(
        context_maintenance,
        "estimate_tokens_from_history_messages",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        context_maintenance, "select_tail_messages", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        context_maintenance, "resolve_summary_boundary", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        context_maintenance,
        "trim_messages_to_budget",
        lambda **_kwargs: ([{"role": "assistant", "content": "compacted"}], []),
    )

    messages, result = await context_maintenance.build_inline_maintained_messages(
        request,
        user=SimpleNamespace(id="u1"),
        model=model,
        form_data={"model": "demo"},
        metadata={},
        system_message=None,
        history_messages=history,
        summary_state={},
    )

    assert messages[0]["content"] == "compacted"
    assert result["telemetry"]["proactive_pretrim_triggered"] is True
    assert (
        result["telemetry"]["proactive_pretrim_reason"]
        == "estimated_prompt_near_ctx_cap"
    )


@pytest.mark.asyncio
async def test_inline_maintenance_force_flag_skips_pressure_gate(monkeypatch):
    request = _make_request()
    model = {"id": "demo", "status": {"args": ["--ctx-size", "8192"]}}
    history = [
        _message("u1", "user", "question"),
        _message("a1", "assistant", "answer"),
    ]

    async def fake_probe(*_args, **_kwargs):
        return {"n_ctx": 8192}

    monkeypatch.setattr(context_maintenance, "load_llamacpp_probe", fake_probe)
    monkeypatch.setattr(
        context_maintenance,
        "resolve_history_budgets",
        lambda *_args, **_kwargs: {
            "live_prompt_cap": 8192,
            "hard_history_budget": 4096,
            "anchor_budget_tokens": 128,
        },
    )
    monkeypatch.setattr(
        context_maintenance,
        "build_context_maintenance_payload",
        lambda **_kwargs: {
            "messages": [{"role": "user", "content": "small"}],
            "used_summary_state": False,
            "telemetry": {},
            "anchor_messages": [],
            "anchor_message_ids": [],
            "tail_messages": history,
        },
    )
    monkeypatch.setattr(
        context_maintenance,
        "should_force_inline_maintenance",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        context_maintenance,
        "estimate_tokens_from_messages",
        lambda *_args, **_kwargs: 1000,
    )
    monkeypatch.setattr(
        context_maintenance,
        "estimate_tokens_from_history_messages",
        lambda *_args, **_kwargs: 0,
    )
    monkeypatch.setattr(
        context_maintenance, "select_tail_messages", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        context_maintenance, "resolve_summary_boundary", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        context_maintenance,
        "trim_messages_to_budget",
        lambda **_kwargs: ([{"role": "assistant", "content": "forced"}], []),
    )

    messages, result = await context_maintenance.build_inline_maintained_messages(
        request,
        user=SimpleNamespace(id="u1"),
        model=model,
        form_data={"model": "demo"},
        metadata={},
        system_message=None,
        history_messages=history,
        summary_state={},
        force_inline_compaction=True,
    )

    assert messages[0]["content"] == "forced"
    assert result["telemetry"]["force_inline_compaction"] is True


def test_count_preview_messages_tokens_caches_rendered_prompt(monkeypatch):
    context_maintenance._PREVIEW_TOKEN_CACHE.clear()

    class FakeEncoding:
        def __init__(self):
            self.calls = 0

        def encode(self, text: str):
            self.calls += 1
            return list(range(max(1, len(text) // 8)))

    fake_encoding = FakeEncoding()
    monkeypatch.setattr(
        context_maintenance, "_get_tiktoken_encoding", lambda _encoding_name: fake_encoding
    )

    request = _make_request(TIKTOKEN_ENCODING_NAME="cl100k_base")
    model = {"id": "demo", "owned_by": "openai"}
    messages = [{"role": "user", "content": "cache me"}]

    first_count, first_source, first_confidence = count_preview_messages_tokens(
        request, model, messages
    )
    second_count, second_source, second_confidence = count_preview_messages_tokens(
        request, model, messages
    )

    assert first_count == second_count
    assert first_source == second_source == "tiktoken"
    assert first_confidence == second_confidence == "model_tokenizer"
    assert fake_encoding.calls == 1


def test_count_preview_messages_tokens_degrades_openai_compatible_llamacpp_models(monkeypatch):
    context_maintenance._PREVIEW_TOKEN_CACHE.clear()

    class FakeEncoding:
        def encode(self, text: str):
            return list(range(max(1, len(text) // 8)))

    monkeypatch.setattr(
        context_maintenance,
        "_get_tiktoken_encoding",
        lambda _encoding_name: FakeEncoding(),
    )

    request = _make_request(TIKTOKEN_ENCODING_NAME="cl100k_base")
    model = {
        "id": "qwen-local-router",
        "owned_by": "openai",
        "status": {"args": ["/usr/local/bin/llama-server", "--ctx-size", "131072"]},
    }

    _, source, confidence = count_preview_messages_tokens(
        request,
        model,
        [{"role": "user", "content": "local router should not claim exact tokenizer"}],
    )

    assert source == "tiktoken"
    assert confidence == "fallback"


@pytest.mark.asyncio
async def test_build_context_window_model_preview_degrades_confidence_for_local_models():
    request = _make_request(TIKTOKEN_ENCODING_NAME="cl100k_base")
    model = {
        "id": "llama-local",
        "name": "Llama Local",
        "owned_by": "ollama",
        "status": {"args": ["--ctx-size", "32768"]},
    }

    preview = await build_context_window_model_preview(
        request,
        model=model,
        system_message={"role": "system", "content": "Be precise"},
        history_messages=[_message("m1", "user", "Long mixed кирилица and code payload")],
        form_data={"files": []},
        metadata={},
        summary_state={},
        maintenance_enabled=True,
    )

    assert preview["token_count_confidence"] == "fallback"
    assert preview["soft_trigger_tokens"] is not None
    assert preview["hard_trigger_tokens"] is not None


@pytest.mark.asyncio
async def test_build_aggregate_context_window_preview_selects_limiting_model():
    request = _make_request(TIKTOKEN_ENCODING_NAME="cl100k_base")
    models_map = {
        "small": {
            "id": "small",
            "name": "Small",
            "owned_by": "openai",
            "status": {"args": ["--ctx-size", "32768"]},
        },
        "large": {
            "id": "large",
            "name": "Large",
            "owned_by": "openai",
            "status": {"args": ["--ctx-size", "81920"]},
        },
    }

    preview = await build_aggregate_context_window_preview(
        request,
        models_map=models_map,
        main_model_ids=["large", "small"],
        system_message={"role": "system", "content": "Keep state"},
        history_messages=[
            _message("m1", "user", "message one"),
            _message("m2", "assistant", "message two"),
        ],
        form_data={"files": []},
        metadata={},
        summary_state={},
        maintenance_enabled=True,
    )

    assert preview is not None
    assert preview["limiting_model_id"] == "small"
    assert preview["multi_model"] is True
    assert preview["active_main_model_ids"] == ["large", "small"]
