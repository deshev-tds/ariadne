import asyncio
from types import SimpleNamespace

import pytest

import open_webui.utils.middleware as middleware
from open_webui.utils.deep_research import LocalDeepResearchError


def _build_request(config_overrides=None):
    config = SimpleNamespace(
        ENABLE_CACHE_PROMPT=False,
        TASK_MODEL="",
        TASK_MODEL_EXTERNAL=False,
        ENABLE_DEEP_RESEARCH=True,
        DEEP_RESEARCH_SIDECAR_URL="http://ldr.test",
        DEEP_RESEARCH_SIDECAR_USERNAME="demo",
        DEEP_RESEARCH_SIDECAR_PASSWORD="secret",
        DEEP_RESEARCH_POLL_INTERVAL_MS=1,
        DEEP_RESEARCH_TIMEOUT_SECONDS=30,
        DEEP_RESEARCH_EXPORT_FORMAT="pdf",
        USER_PERMISSIONS={},
    )
    if config_overrides:
        for key, value in config_overrides.items():
            setattr(config, key, value)

    model = {"id": "demo-model", "owned_by": "openai", "info": {"meta": {}}}
    return (
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    config=config,
                    MODELS={"demo-model": model},
                )
            ),
            state=SimpleNamespace(),
        ),
        model,
    )


@pytest.mark.asyncio
async def test_process_chat_payload_returns_early_for_deep_research(monkeypatch):
    request, model = _build_request()
    user = SimpleNamespace(id="user-1", role="admin")
    metadata = {"params": {}}
    form_data = {
        "model": "demo-model",
        "messages": [{"role": "user", "content": "hello"}],
        "features": {"deep_research": True, "web_search": True},
    }

    async def fake_convert(form_data):
        return form_data

    async def fake_oauth(request, user):
        return None

    async def fake_pipeline(request, form_data, user, models):
        return form_data

    async def fake_filters(**kwargs):
        return kwargs["form_data"], {}

    async def fake_ledger(**kwargs):
        return kwargs["messages"], {}

    deep_calls = []

    async def fake_deep_handler(request, form_data, extra_params, user):
        deep_calls.append(True)
        metadata = {**extra_params["__metadata__"], "direct_response": {"choices": []}}
        return form_data, metadata, []

    async def fail_web_handler(*args, **kwargs):
        raise AssertionError("web search handler should not run after deep research starts")

    monkeypatch.setattr(middleware, "convert_url_images_to_base64", fake_convert)
    monkeypatch.setattr(middleware, "get_system_oauth_token", fake_oauth)
    monkeypatch.setattr(middleware, "process_pipeline_inlet_filter", fake_pipeline)
    monkeypatch.setattr(middleware, "process_filter_functions", fake_filters)
    monkeypatch.setattr(middleware, "maybe_apply_ledger", fake_ledger)
    monkeypatch.setattr(middleware, "_resolve_context_maintenance_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(middleware, "_resolve_chat_recall_enabled", lambda *args, **kwargs: False)
    monkeypatch.setattr(middleware, "get_event_emitter", lambda metadata: None)
    monkeypatch.setattr(middleware, "get_event_call", lambda metadata: None)
    monkeypatch.setattr(middleware, "get_task_model_id", lambda *args, **kwargs: "demo-model")
    monkeypatch.setattr(middleware.Functions, "get_functions_by_ids", lambda ids: [])
    monkeypatch.setattr(middleware, "chat_deep_research_handler", fake_deep_handler)
    monkeypatch.setattr(middleware, "chat_web_search_handler", fail_web_handler)

    _, returned_metadata, _ = await middleware.process_chat_payload(
        request, form_data, user, metadata, model
    )

    assert deep_calls == [True]
    assert returned_metadata["direct_response"] == {"choices": []}


@pytest.mark.asyncio
async def test_chat_deep_research_handler_commits_success_after_file_registration(
    monkeypatch, tmp_path
):
    request, model = _build_request()
    metadata = {"chat_id": "chat-1", "message_id": "msg-1", "params": {}}
    extra_params = {
        "__metadata__": metadata,
        "__event_emitter__": None,
        "__model__": model,
    }
    form_data = {"messages": [{"role": "user", "content": "research this"}]}
    user = SimpleNamespace(id="user-1", role="admin")
    observed_order = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def start_research(self, query, mode="detailed"):
            observed_order.append("start")
            return {"research_id": "research-1"}

        async def get_research_status(self, research_id):
            observed_order.append("status")
            return {"status": "completed", "progress": 100}

        async def get_report(self, research_id):
            observed_order.append("report")
            return {"content": "# Report", "sources": ["https://example.org/a"]}

        async def export_report(self, research_id, export_format):
            observed_order.append("export")
            return SimpleNamespace(content=b"%PDF", content_type="application/pdf")

        async def close(self):
            observed_order.append("close")

    async def event_emitter(payload):
        if payload["type"] == "files":
            observed_order.append("files-event")
            assert metadata.get("direct_response") is None

    def fake_register(*args, **kwargs):
        observed_order.append(f"register:{kwargs['filename']}")
        assert metadata.get("direct_response") is None
        return {"id": kwargs["filename"], "type": "file", "url": kwargs["filename"]}

    def fake_link(chat_id, message_id, user, message_files):
        observed_order.append("link")
        assert metadata.get("direct_response") is None
        return message_files

    extra_params["__event_emitter__"] = event_emitter

    monkeypatch.setattr(middleware, "LocalDeepResearchClient", FakeClient)
    monkeypatch.setattr(middleware, "_resolve_chat_artifacts_dir", lambda chat_id: tmp_path)
    monkeypatch.setattr(middleware, "_register_deep_research_artifact", fake_register)
    monkeypatch.setattr(middleware, "_link_deep_research_message_files", fake_link)

    _, returned_metadata, _ = await middleware.chat_deep_research_handler(
        request, form_data, extra_params, user
    )

    assert returned_metadata["deep_research_commit_state"] == "committed_success"
    assert (
        returned_metadata["direct_response"]["choices"][0]["message"]["content"]
        == "Deep research completed. Reports attached."
    )
    assert observed_order == [
        "start",
        "status",
        "report",
        "export",
        "register:report.md",
        "register:report.pdf",
        "link",
        "files-event",
        "close",
    ]


@pytest.mark.asyncio
async def test_chat_deep_research_handler_salvages_markdown_when_export_fails(
    monkeypatch, tmp_path
):
    request, model = _build_request()
    metadata = {"chat_id": "chat-1", "message_id": "msg-1", "params": {}}
    extra_params = {
        "__metadata__": metadata,
        "__model__": model,
    }
    form_data = {"messages": [{"role": "user", "content": "research this"}]}
    user = SimpleNamespace(id="user-1", role="admin")
    registered_files = []
    events = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def start_research(self, query, mode="detailed"):
            return {"research_id": "research-1"}

        async def get_research_status(self, research_id):
            return {"status": "completed", "progress": 100}

        async def get_report(self, research_id):
            return {"content": "# Report", "sources": ["https://example.org/a"]}

        async def export_report(self, research_id, export_format):
            raise LocalDeepResearchError("export backend unavailable")

        async def close(self):
            return None

    async def event_emitter(payload):
        if payload["type"] == "files":
            events.append(payload)
            assert metadata.get("direct_response") is None

    def fake_register(*args, **kwargs):
        registered_files.append(kwargs["filename"])
        return {"id": kwargs["filename"], "type": "file", "url": kwargs["filename"]}

    def fake_link(chat_id, message_id, user, message_files):
        return message_files

    extra_params["__event_emitter__"] = event_emitter

    monkeypatch.setattr(middleware, "LocalDeepResearchClient", FakeClient)
    monkeypatch.setattr(middleware, "_resolve_chat_artifacts_dir", lambda chat_id: tmp_path)
    monkeypatch.setattr(middleware, "_register_deep_research_artifact", fake_register)
    monkeypatch.setattr(middleware, "_link_deep_research_message_files", fake_link)

    _, returned_metadata, _ = await middleware.chat_deep_research_handler(
        request, form_data, extra_params, user
    )

    assert registered_files == ["report.md"]
    assert len(events) == 1
    assert returned_metadata["deep_research_commit_state"] == "committed_failure"
    assert (
        returned_metadata["direct_response"]["error"]["detail"]
        == "Deep research completed but export failed."
    )


@pytest.mark.asyncio
async def test_chat_deep_research_handler_cancels_during_polling(monkeypatch):
    request, model = _build_request({"DEEP_RESEARCH_TIMEOUT_SECONDS": 300})
    metadata = {"chat_id": "chat-1", "message_id": "msg-1", "params": {}}
    extra_params = {
        "__metadata__": metadata,
        "__model__": model,
    }
    form_data = {"messages": [{"role": "user", "content": "research this"}]}
    user = SimpleNamespace(id="user-1", role="admin")
    terminate_calls = []
    cancel_commits = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def start_research(self, query, mode="detailed"):
            return {"research_id": "research-1"}

        async def get_research_status(self, research_id):
            await asyncio.sleep(30)
            return {"status": "in_progress", "progress": 10}

        async def terminate_research(self, research_id):
            terminate_calls.append(research_id)
            return {"status": "success"}

        async def close(self):
            return None

    async def fake_cancel_commit(request, form_data, user, model, metadata):
        cancel_commits.append(metadata["deep_research_commit_state"])

    monkeypatch.setattr(middleware, "LocalDeepResearchClient", FakeClient)
    monkeypatch.setattr(middleware, "_commit_deep_research_cancel_response", fake_cancel_commit)

    task = asyncio.create_task(
        middleware.chat_deep_research_handler(request, form_data, extra_params, user)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert terminate_calls == ["research-1"]
    assert cancel_commits == ["committed_cancel"]
