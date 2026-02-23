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
    monkeypatch.setattr(main_module, "chat_completion_handler", fake_chat_completion_handler)
    monkeypatch.setattr(main_module, "process_chat_response", fake_process_chat_response)

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
