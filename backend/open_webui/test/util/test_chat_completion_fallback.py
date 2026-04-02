from types import SimpleNamespace

import pytest

import open_webui.main as main_module


@pytest.mark.asyncio
async def test_chat_completion_retries_without_logprob_params(monkeypatch):
    handler_calls = []

    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def fake_chat_completion_handler(request, form_data, user):
        handler_calls.append(dict(form_data))
        if form_data.get("logprobs") is True:
            return {
                "error": {
                    "message": "This provider does not support logprobs/top_logprobs"
                }
            }
        return {"choices": [{"message": {"content": "ok"}}]}

    async def fake_process_chat_response(response, ctx):
        return {
            "response": response,
            "events": ctx["events"],
            "form_data": ctx["form_data"],
        }

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fake_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"demo-model": {"id": "demo-model"}},
                config=SimpleNamespace(DEFAULT_MODELS=""),
            )
        ),
        state=SimpleNamespace(),
    )

    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
        "logprobs": True,
        "top_logprobs": 5,
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert len(handler_calls) == 2
    assert handler_calls[0]["logprobs"] is True
    assert "logprobs" not in handler_calls[1]
    assert "top_logprobs" not in handler_calls[1]
    assert result["response"]["choices"][0]["message"]["content"] == "ok"
    assert any(
        event.get("tokenTelemetryUnavailable") is True for event in result["events"]
    )


@pytest.mark.asyncio
async def test_chat_completion_retries_after_context_overflow(monkeypatch):
    handler_calls = []

    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def fake_chat_completion_handler(request, form_data, user):
        handler_calls.append(dict(form_data))
        if len(handler_calls) == 1:
            return {
                "error": {
                    "message": "request (30461 tokens) exceeds the available context size (24320 tokens), try increasing it"
                }
            }
        return {"choices": [{"message": {"content": "ok-after-compaction"}}]}

    async def fake_inline_maintenance(*args, **kwargs):
        assert kwargs.get("force_inline_compaction") is True
        return (
            [{"role": "user", "content": "compacted"}],
            {"telemetry": {"compaction_mode": "forced_overflow_retry"}},
        )

    async def fake_process_chat_response(response, ctx):
        return {
            "response": response,
            "events": ctx["events"],
            "form_data": ctx["form_data"],
        }

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fake_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "build_inline_maintained_messages", fake_inline_maintenance
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )
    monkeypatch.setattr(main_module, "get_event_emitter", lambda _metadata: None)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"demo-model": {"id": "demo-model"}},
                config=SimpleNamespace(DEFAULT_MODELS=""),
            )
        ),
        state=SimpleNamespace(),
    )

    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert len(handler_calls) == 2
    assert handler_calls[1]["messages"][0]["content"] == "compacted"
    assert (
        result["response"]["choices"][0]["message"]["content"] == "ok-after-compaction"
    )
    assert any(event.get("contextOverflowRetry") is True for event in result["events"])
    assert any(
        event.get("contextOverflowRetrySucceeded") is True for event in result["events"]
    )


@pytest.mark.asyncio
async def test_chat_completion_reports_clear_error_when_overflow_retry_fails(
    monkeypatch,
):
    handler_calls = []

    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        return form_data, metadata, []

    async def fake_chat_completion_handler(request, form_data, user):
        handler_calls.append(dict(form_data))
        return {
            "error": {
                "message": "request (30000 tokens) exceeds the available context size (24000 tokens), try increasing it"
            }
        }

    async def fake_inline_maintenance(*args, **kwargs):
        return (
            [{"role": "user", "content": "still-too-large"}],
            {"telemetry": {"compaction_mode": "forced_overflow_retry"}},
        )

    async def fake_process_chat_response(response, ctx):
        return {"response": response, "events": ctx["events"]}

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fake_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "build_inline_maintained_messages", fake_inline_maintenance
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )
    monkeypatch.setattr(main_module, "get_event_emitter", lambda _metadata: None)

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"demo-model": {"id": "demo-model"}},
                config=SimpleNamespace(DEFAULT_MODELS=""),
            )
        ),
        state=SimpleNamespace(),
    )

    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert len(handler_calls) == 2
    assert "error" in result["response"]
    assert (
        "Context window exceeded: request used 30000 tokens, available context is 24000 tokens."
        in result["response"]["error"]["detail"]
    )
    assert any(
        event.get("contextOverflowRetrySucceeded") is False
        for event in result["events"]
    )


@pytest.mark.asyncio
async def test_chat_completion_preserves_debug_tool_journey_in_metadata(monkeypatch):
    captured_metadata = {}

    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        captured_metadata.update(metadata)
        return form_data, metadata, []

    async def fake_chat_completion_handler(request, form_data, user):
        return {"choices": [{"message": {"content": "ok"}}]}

    async def fake_process_chat_response(response, ctx):
        return {
            "response": response,
            "metadata": ctx["metadata"],
        }

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fake_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"demo-model": {"id": "demo-model"}},
                config=SimpleNamespace(DEFAULT_MODELS=""),
            )
        ),
        state=SimpleNamespace(),
    )

    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
        "params": {"debug_tool_journey": True},
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert captured_metadata["params"]["debug_tool_journey"] is True
    assert result["metadata"]["params"]["debug_tool_journey"] is True
