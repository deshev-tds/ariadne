import json
from types import SimpleNamespace

import pytest

import open_webui.utils.middleware as middleware
from open_webui.retrieval.web.planner import (
    build_web_search_plan,
    parse_rewriter_output,
)


def _make_request(
    *,
    enable_planner: bool,
    enable_task_model_planner: bool = False,
    task_model: str = "",
) -> SimpleNamespace:
    config = SimpleNamespace(
        ENABLE_WEB_SEARCH_PLANNER=enable_planner,
        ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER=enable_task_model_planner,
        WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE=4,
        WEB_SEARCH_PLANNER_MODE="hybrid_rewriter",
        WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES=6,
        WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS=3500,
        WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS=1,
        WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS=384,
        WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE=0.0,
        QUERY_GENERATION_PROMPT_TEMPLATE='{"queries":["{{prompt}}"]}',
        TASK_MODEL=task_model,
        TASK_MODEL_EXTERNAL="",
    )
    app_state = SimpleNamespace(
        MODELS={
            "active-model": {
                "id": "active-model",
                "connection_type": "local",
            },
            **(
                {
                    task_model: {
                        "id": task_model,
                        "connection_type": "local",
                    }
                }
                if task_model
                else {}
            ),
        },
        config=config,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=app_state),
        state=SimpleNamespace(metadata={"trace_id": "test"}),
    )


@pytest.mark.asyncio
async def test_run_web_search_rewriter_uses_active_model_only_and_bypasses_system_prompt(
    monkeypatch,
):
    request = _make_request(enable_planner=True)
    user = SimpleNamespace(id="u1")
    context = (
        'I am on EKS 1.30 with aws-vpc-cni 1.16.4 and logs show "plugin type=\\"aws-cni\\" '
        'failed (add)".'
    )
    user_message = "is this a known issue and what is the current fix?"
    plan = build_web_search_plan(user_message, conversation_context=context)
    plan.preserve_tokens = []

    calls = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        calls.append(
            {
                "model": form_data.get("model"),
                "think": form_data.get("think"),
                "enable_thinking": form_data.get("params", {})
                .get("custom_params", {})
                .get("chat_template_kwargs", {})
                .get("enable_thinking"),
                "bypass_system_prompt": bypass_system_prompt,
            }
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "exact",
                                        "query": "EKS aws-vpc-cni known issue current fix",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("get_task_model_id should not be called by rewriter")

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)
    monkeypatch.setattr(middleware, "get_task_model_id", fail_if_called)

    queries, meta = await middleware._run_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert len(queries) == 1
    assert calls
    assert all(call["model"] == "active-model" for call in calls)
    assert all(call["bypass_system_prompt"] is True for call in calls)
    assert all(call["think"] is False for call in calls)
    assert all(call["enable_thinking"] is False for call in calls)
    assert meta["model_used"] == "active-model"
    assert meta["fallback_used"] is False
    assert meta["retry_count"] == 0


@pytest.mark.asyncio
async def test_run_web_search_rewriter_retries_once_on_same_model(monkeypatch):
    request = _make_request(enable_planner=True)
    user = SimpleNamespace(id="u1")
    user_message = "is this a known issue and what is the current fix?"
    context = "EKS 1.30 aws-vpc-cni 1.16.4 failed to assign an IP address to container"
    plan = build_web_search_plan(user_message, conversation_context=context)
    plan.preserve_tokens = []

    call_models = []
    call_count = {"n": 0}

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        assert bypass_system_prompt is True
        call_count["n"] += 1
        call_models.append(form_data.get("model"))
        if call_count["n"] == 1:
            return {"choices": [{"message": {"content": "not-json"}}]}

        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "exact",
                                        "query": "EKS aws-vpc-cni known issue",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert len(queries) == 1
    assert call_count["n"] == 2
    assert call_models == ["active-model", "active-model"]
    assert meta["retry_count"] == 1


@pytest.mark.asyncio
async def test_run_web_search_rewriter_raises_after_two_failures(monkeypatch):
    request = _make_request(enable_planner=True)
    user = SimpleNamespace(id="u1")
    plan = build_web_search_plan("check known issue", conversation_context="EKS aws-cni")
    plan.preserve_tokens = []

    async def always_invalid(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        assert bypass_system_prompt is True
        return {"choices": [{"message": {"content": "still-not-json"}}]}

    monkeypatch.setattr(middleware, "generate_chat_completion", always_invalid)

    with pytest.raises(ValueError):
        await middleware._run_web_search_rewriter(
            request,
            user=user,
            active_model_id="active-model",
            user_message="check known issue",
            conversation_context="EKS aws-cni",
            plan=plan,
            max_queries=4,
            timeout_ms=3000,
            max_repair_attempts=1,
            max_completion_tokens=256,
            temperature=0.0,
            chat_id="chat-1",
        )


@pytest.mark.asyncio
async def test_active_model_query_generation_retries_and_bypasses_system_prompt(monkeypatch):
    request = _make_request(enable_planner=False)
    user = SimpleNamespace(id="u1")
    messages = [{"role": "user", "content": "best query for aws cni issue"}]

    calls = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        calls.append(
            {
                "model": form_data.get("model"),
                "bypass_system_prompt": bypass_system_prompt,
                "think": form_data.get("think"),
                "enable_thinking": form_data.get("params", {})
                .get("custom_params", {})
                .get("chat_template_kwargs", {})
                .get("enable_thinking"),
            }
        )
        if len(calls) == 1:
            return {"choices": [{"message": {"content": ""}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"queries":["aws cni known issue github","aws cni current fix docs"]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_active_model_web_query_generation(
        request,
        user=user,
        active_model_id="active-model",
        messages=messages,
        chat_id="chat-1",
        timeout_ms=3000,
        max_completion_tokens=128,
    )

    assert queries == ["aws cni known issue github", "aws cni current fix docs"]
    assert len(calls) == 2
    assert all(call["model"] == "active-model" for call in calls)
    assert all(call["bypass_system_prompt"] is True for call in calls)
    assert all(call["think"] is False for call in calls)
    assert all(call["enable_thinking"] is False for call in calls)
    assert meta["model_used"] == "active-model"
    assert meta["retry_count"] == 1


@pytest.mark.asyncio
async def test_chat_web_search_handler_uses_active_model_query_generation_fallback(monkeypatch):
    request = _make_request(enable_planner=False)
    user = SimpleNamespace(id="u1")

    form_data = {
        "model": "active-model",
        "messages": [{"role": "user", "content": "difference between throttled and deprecated"}],
    }
    events = []

    async def event_emitter(event):
        events.append(event)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__chat_id__": "chat-1",
    }

    async def fake_run_active_model_generation(*_args, **_kwargs):
        return ["q1", "q2"], {"model_used": "active-model", "retry_count": 0}

    async def fake_process_web_search(_request, search_form, user=None):
        assert search_form.queries == ["q1", "q2"]
        return {
            "queries": search_form.queries,
            "docs": [{"content": "stub doc"}],
            "filenames": ["https://example.com/doc"],
            "items": [],
        }

    def fail_generate_queries(*_args, **_kwargs):
        raise AssertionError("generate_queries must not be called in this fallback")

    monkeypatch.setattr(
        middleware,
        "_run_active_model_web_query_generation",
        fake_run_active_model_generation,
    )
    monkeypatch.setattr(middleware, "process_web_search", fake_process_web_search)
    monkeypatch.setattr(middleware, "generate_queries", fail_generate_queries)

    output = await middleware.chat_web_search_handler(
        request, form_data, extra_params, user
    )

    assert output["files"][0]["queries"] == ["q1", "q2"]
    assert any(
        event.get("data", {}).get("action") == "web_search_queries_generated"
        for event in events
    )


@pytest.mark.asyncio
async def test_bounded_specialist_web_search_rewriter_uses_task_model_first(monkeypatch):
    request = _make_request(
        enable_planner=True,
        enable_task_model_planner=True,
        task_model="specialist-model",
    )
    user = SimpleNamespace(id="u1")
    context = "EKS 1.30 aws-vpc-cni 1.16.4 failed to assign an IP address to container"
    user_message = "is this a known issue and what is the current fix?"
    plan = build_web_search_plan(user_message, conversation_context=context)
    plan.preserve_tokens = []

    calls = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        calls.append(form_data.get("model"))
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "exact",
                                        "query": "EKS aws-vpc-cni known issue current fix",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_bounded_specialist_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert len(queries) == 1
    assert calls == ["specialist-model"]
    assert meta["selected_model"] == "specialist-model"
    assert meta["selected_via"] == "task_model"
    assert meta["route_source"] == "bounded_specialist_v1"
    assert meta["fallback_used"] is False
    assert meta["reason"] == "planner_query_rewriter"


@pytest.mark.asyncio
async def test_bounded_specialist_web_search_rewriter_falls_back_to_active_model(monkeypatch):
    request = _make_request(
        enable_planner=True,
        enable_task_model_planner=True,
        task_model="specialist-model",
    )
    user = SimpleNamespace(id="u1")
    context = "EKS 1.30 aws-vpc-cni 1.16.4 failed to assign an IP address to container"
    user_message = "is this a known issue and what is the current fix?"
    plan = build_web_search_plan(user_message, conversation_context=context)
    plan.preserve_tokens = []

    calls = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        model = form_data.get("model")
        calls.append(model)
        if model == "specialist-model":
            return {"choices": [{"message": {"content": "not-json"}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "exact",
                                        "query": "EKS aws-vpc-cni known issue current fix",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_bounded_specialist_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert len(queries) == 1
    assert calls == ["specialist-model", "specialist-model", "active-model"]
    assert meta["selected_model"] == "active-model"
    assert meta["selected_via"] == "active_model"
    assert meta["route_source"] == "bounded_specialist_v1"
    assert meta["fallback_used"] is True
    assert meta["error_class"] == "ValueError"


@pytest.mark.asyncio
async def test_bounded_specialist_web_search_rewriter_emits_model_activity_timeline(
    monkeypatch,
):
    request = _make_request(
        enable_planner=True,
        enable_task_model_planner=True,
        task_model="specialist-model",
    )
    user = SimpleNamespace(id="u1")
    context = "EKS 1.30 aws-vpc-cni 1.16.4 failed to assign an IP address to container"
    user_message = "is this a known issue and what is the current fix?"
    plan = build_web_search_plan(user_message, conversation_context=context)
    plan.preserve_tokens = []
    metadata = {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "params": {"debug_tool_journey": True},
    }
    emitted_events = []

    async def event_emitter(event):
        emitted_events.append(event)

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        model = form_data.get("model")
        if model == "specialist-model":
            return {"choices": [{"message": {"content": "not-json"}}]}
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "exact",
                                        "query": "EKS aws-vpc-cni known issue current fix",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_bounded_specialist_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
        metadata=metadata,
        event_emitter=event_emitter,
    )

    assert len(queries) == 1
    assert meta["selected_model"] == "active-model"
    model_events = [
        event["data"]
        for event in emitted_events
        if event.get("type") == "chat:tool:journey"
        and event.get("data", {}).get("kind") == "model_activity"
    ]

    assert [event["phase"] for event in model_events] == [
        "model_task_start",
        "model_task_done",
        "model_task_start",
        "model_task_done",
    ]
    assert model_events[0]["model_id"] == "specialist-model"
    assert model_events[0]["active_model_id"] == "active-model"
    assert model_events[0]["actor"] == "bounded_specialist"
    assert model_events[1]["status"] == "error"
    assert model_events[1]["error_class"] == "ValueError"
    assert model_events[2]["model_id"] == "active-model"
    assert model_events[2]["fallback_used"] is True
    assert model_events[3]["status"] == "ok"
    assert isinstance(model_events[3]["duration_ms"], int)


@pytest.mark.asyncio
async def test_bounded_specialist_query_generation_emits_model_activity_events(
    monkeypatch,
):
    request = _make_request(
        enable_planner=False,
        enable_task_model_planner=True,
        task_model="specialist-model",
    )
    user = SimpleNamespace(id="u1")
    messages = [{"role": "user", "content": "best query for aws cni issue"}]
    metadata = {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "params": {"debug_tool_journey": True},
    }
    emitted_events = []

    async def event_emitter(event):
        emitted_events.append(event)

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        assert form_data.get("model") == "specialist-model"
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"queries":["aws cni known issue github","aws cni current fix docs"]}'
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, meta = await middleware._run_bounded_specialist_web_query_generation(
        request,
        user=user,
        active_model_id="active-model",
        messages=messages,
        chat_id="chat-1",
        timeout_ms=3000,
        max_completion_tokens=128,
        metadata=metadata,
        event_emitter=event_emitter,
    )

    assert queries == ["aws cni known issue github", "aws cni current fix docs"]
    assert meta["selected_model"] == "specialist-model"
    model_events = [
        event["data"]
        for event in emitted_events
        if event.get("type") == "chat:tool:journey"
        and event.get("data", {}).get("kind") == "model_activity"
    ]

    assert len(model_events) == 2
    assert model_events[0]["phase"] == "model_task_start"
    assert model_events[0]["task_kind"] == "web_search_query_generation"
    assert model_events[0]["model_id"] == "specialist-model"
    assert model_events[0]["active_model_id"] == "active-model"
    assert model_events[1]["phase"] == "model_task_done"
    assert model_events[1]["status"] == "ok"
    assert isinstance(model_events[1]["duration_ms"], int)


@pytest.mark.asyncio
async def test_chat_web_search_handler_uses_bounded_specialist_query_generation_when_enabled(
    monkeypatch,
):
    request = _make_request(
        enable_planner=False,
        enable_task_model_planner=True,
        task_model="specialist-model",
    )
    user = SimpleNamespace(id="u1")

    form_data = {
        "model": "active-model",
        "messages": [{"role": "user", "content": "difference between throttled and deprecated"}],
    }
    events = []

    async def event_emitter(event):
        events.append(event)

    extra_params = {
        "__event_emitter__": event_emitter,
        "__chat_id__": "chat-1",
    }

    async def fake_run_bounded_generation(*_args, **_kwargs):
        return (
            ["q1", "q2"],
            {
                "selected_model": "specialist-model",
                "selected_via": "task_model",
                "route_source": "bounded_specialist_v1",
                "fallback_used": False,
            },
        )

    async def fail_active_generation(*_args, **_kwargs):
        raise AssertionError("active-model query generation should not run when specialist routing is enabled")

    async def fake_process_web_search(_request, search_form, user=None):
        assert search_form.queries == ["q1", "q2"]
        return {
            "queries": search_form.queries,
            "docs": [{"content": "stub doc"}],
            "filenames": ["https://example.com/doc"],
            "items": [],
        }

    monkeypatch.setattr(
        middleware,
        "_run_bounded_specialist_web_query_generation",
        fake_run_bounded_generation,
    )
    monkeypatch.setattr(
        middleware,
        "_run_active_model_web_query_generation",
        fail_active_generation,
    )
    monkeypatch.setattr(middleware, "process_web_search", fake_process_web_search)

    output = await middleware.chat_web_search_handler(
        request, form_data, extra_params, user
    )

    assert output["files"][0]["queries"] == ["q1", "q2"]
    assert any(
        event.get("data", {}).get("action") == "web_search_queries_generated"
        for event in events
    )


def test_build_web_search_plan_uses_conversation_context_for_pronoun_query():
    conversation_context = (
        'I am on EKS 1.30 with aws-vpc-cni 1.16.4 and kubelet logs show "plugin '
        'type=\\"aws-cni\\" failed (add)" and failed to assign an IP address to container.'
    )
    user_message = "is this a known issue and what is the current fix?"
    plan = build_web_search_plan(
        user_message,
        conversation_context=conversation_context,
        max_targeted_domains=4,
    )

    lowered_tokens = {token.lower() for token in plan.preserve_tokens}
    assert plan.intent == "technical_debug"
    assert plan.topic == "software_apis_devops"
    assert "eks" in lowered_tokens
    assert "aws-vpc-cni" in lowered_tokens
    assert "1.30" in lowered_tokens
    assert plan.base_exact_query.lower().startswith(
        "is this a known issue and what is the current fix?"
    )


@pytest.mark.asyncio
async def test_rewriter_context_disambiguation_supports_dryer_community_query(monkeypatch):
    request = _make_request(enable_planner=True)
    user = SimpleNamespace(id="u1")
    conversation_context = (
        "We are discussing spiky dryer balls for laundry dryers and whether they save "
        "energy. Need real user feedback."
    )
    user_message = "check community signals for those"
    plan = build_web_search_plan(user_message, conversation_context=conversation_context)
    plan.preserve_tokens = []

    async def fake_generate_chat_completion(
        _request, form_data=None, user=None, bypass_system_prompt=False, **_kwargs
    ):
        prompt = form_data.get("messages", [{}])[-1].get("content", "")
        assert "conversation_context" in prompt
        assert "dryer balls" in prompt.lower()
        assert bypass_system_prompt is True
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "queries": [
                                    {
                                        "kind": "community",
                                        "query": "dryer balls energy savings user feedback site:reddit.com",
                                        "domain": "reddit.com",
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(middleware, "generate_chat_completion", fake_generate_chat_completion)

    queries, _ = await middleware._run_web_search_rewriter(
        request,
        user=user,
        active_model_id="active-model",
        user_message=user_message,
        conversation_context=conversation_context,
        plan=plan,
        max_queries=4,
        timeout_ms=3000,
        max_repair_attempts=1,
        max_completion_tokens=256,
        temperature=0.0,
        chat_id="chat-1",
    )

    assert queries
    query_text = queries[0].query.lower()
    assert "dryer" in query_text
    assert "site:reddit.com" in query_text


def test_parse_rewriter_output_supports_line_protocol():
    raw = """
    exact||spiky dryer balls reduce drying time energy
    targeted|reddit.com|spiky dryer balls user feedback site:reddit.com
    current_fix: dryer balls effectiveness recent tests 2025 2026
    """
    queries = parse_rewriter_output(raw)

    assert len(queries) == 3
    assert queries[0].kind == "exact"
    assert queries[1].domain == "reddit.com"
    assert "site:reddit.com" in queries[1].query.lower()


def test_parse_rewriter_output_keeps_json_compatibility():
    raw = json.dumps(
        {
            "queries": [
                {"kind": "exact", "query": "eks aws-vpc-cni known issue"},
                "eks aws cni current fix",
            ]
        }
    )
    queries = parse_rewriter_output(raw)

    assert len(queries) == 2
    assert queries[0].kind == "exact"
    assert queries[1].kind == "general"
