import asyncio
from types import SimpleNamespace

import pytest

import open_webui.main as main_module


def _build_chat_completion_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                MODELS={"demo-model": {"id": "demo-model"}},
                config=SimpleNamespace(DEFAULT_MODELS=""),
            )
        ),
        state=SimpleNamespace(),
    )


@pytest.mark.asyncio
async def test_chat_completion_skips_model_call_when_direct_response_is_set(monkeypatch):
    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        metadata = {
            **metadata,
            "direct_response": {"choices": [{"message": {"content": "direct"}}]},
            "deep_research_commit_state": "committed_success",
        }
        return form_data, metadata, []

    async def fail_chat_completion_handler(request, form_data, user):
        raise AssertionError("chat_completion_handler should not run when direct_response is set")

    async def fake_process_chat_response(response, ctx):
        return {"response": response, "ctx": ctx}

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fail_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )

    request = _build_chat_completion_request()
    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert result["response"]["choices"][0]["message"]["content"] == "direct"


@pytest.mark.asyncio
async def test_chat_completion_ignores_late_cancel_after_committed_success(monkeypatch):
    async def fake_process_chat_payload(request, form_data, user, metadata, model):
        metadata = {
            **metadata,
            "direct_response": {"choices": [{"message": {"content": "direct"}}]},
            "deep_research_commit_state": "committed_success",
        }
        return form_data, metadata, []

    async def fail_chat_completion_handler(request, form_data, user):
        raise AssertionError("chat_completion_handler should not run when direct_response is set")

    process_calls = {"count": 0}

    async def fake_process_chat_response(response, ctx):
        process_calls["count"] += 1
        if process_calls["count"] == 1:
            raise asyncio.CancelledError()
        return {"response": response}

    monkeypatch.setattr(main_module, "process_chat_payload", fake_process_chat_payload)
    monkeypatch.setattr(
        main_module, "chat_completion_handler", fail_chat_completion_handler
    )
    monkeypatch.setattr(
        main_module, "process_chat_response", fake_process_chat_response
    )

    request = _build_chat_completion_request()
    user = SimpleNamespace(id="user-id", role="admin")
    form_data = {
        "model": "demo-model",
        "model_item": {"direct": True, "id": "demo-model", "owned_by": "openai"},
        "messages": [{"role": "user", "content": "hello"}],
    }

    result = await main_module.chat_completion(request, form_data, user=user)

    assert process_calls["count"] == 2
    assert result["response"]["choices"][0]["message"]["content"] == "direct"
