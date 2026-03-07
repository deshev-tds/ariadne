from types import SimpleNamespace

import pytest

import open_webui.utils.middleware as middleware


@pytest.fixture(autouse=True)
def _clear_web_full_context_once_cache():
    middleware._WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT.clear()
    yield
    middleware._WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT.clear()


async def _noop_event_emitter(_event):
    return None


def _make_request() -> SimpleNamespace:
    config = SimpleNamespace(
        TOP_K=4,
        TOP_K_RERANKER=2,
        RELEVANCE_THRESHOLD=0.0,
        HYBRID_BM25_WEIGHT=0.5,
        ENABLE_RAG_HYBRID_SEARCH=False,
        RAG_FULL_CONTEXT=False,
        ENABLE_WEB_SEARCH_EVIDENCE_SATURATION=False,
        RAG_TEMPLATE="<context>{{CONTEXT}}</context>",
    )
    app_state = SimpleNamespace(
        config=config,
        EMBEDDING_FUNCTION=lambda *_args, **_kwargs: [0.0],
        RERANKING_FUNCTION=None,
    )
    return SimpleNamespace(app=SimpleNamespace(state=app_state))


def _make_web_item(url: str) -> dict:
    return {
        "type": "text",
        "context": "full",
        "name": url,
        "url": url,
        "file": {"meta": {"source": url, "name": url}, "data": {"content": f"content:{url}"}},
    }


def _make_non_web_item() -> dict:
    return {
        "type": "file",
        "context": "full",
        "id": "file-1",
        "name": "doc-1.md",
        "file": {"data": {"content": "hello world", "metadata": {"source": "doc-1.md"}}},
    }


def _make_source(url: str) -> dict:
    return {
        "source": {"type": "text", "url": url, "name": url},
        "document": [f"document:{url}"],
        "metadata": [{"source": url, "name": url}],
    }


def _build_messages_with_source(request: SimpleNamespace, url: str) -> list[dict]:
    messages = [{"role": "user", "content": "summarize"}]
    return middleware.apply_source_context_to_messages(
        request=request,
        messages=messages,
        sources=[_make_source(url)],
        user_message="summarize",
    )


@pytest.mark.asyncio
async def test_web_full_context_once_skips_same_attachment_on_follow_up(monkeypatch):
    request = _make_request()
    user = SimpleNamespace(id="u1")
    events = []
    calls = []
    url = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"

    async def event_emitter(event):
        events.append(event)

    async def fake_get_sources_from_items(*_args, **kwargs):
        calls.append(kwargs["items"])
        return [_make_source(url)]

    monkeypatch.setattr(middleware, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(middleware, "RAG_WEB_FULL_CONTEXT_ONCE", True)

    body_first = {
        "model": "active-model",
        "messages": [{"role": "user", "content": "summarize"}],
        "metadata": {"files": [_make_web_item(url)]},
    }
    _, flags_first = await middleware.chat_completion_files_handler(
        request=request,
        body=body_first,
        extra_params={"__event_emitter__": event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert len(flags_first["sources"]) == 1

    body_second = {
        "model": "active-model",
        "messages": _build_messages_with_source(request, url),
        "metadata": {"files": [_make_web_item(url)]},
    }
    _, flags_second = await middleware.chat_completion_files_handler(
        request=request,
        body=body_second,
        extra_params={"__event_emitter__": event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert flags_second["sources"] == []
    assert any(
        event.get("data", {}).get("action") == "sources_retrieved"
        and event.get("data", {}).get("count") == 0
        for event in events
    )


@pytest.mark.asyncio
async def test_web_full_context_once_skips_via_chat_cache_without_source_markers(
    monkeypatch,
):
    request = _make_request()
    user = SimpleNamespace(id="u1")
    events = []
    calls = []
    url = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"
    chat_id = "chat-42"

    async def event_emitter(event):
        events.append(event)

    async def fake_get_sources_from_items(*_args, **kwargs):
        calls.append(kwargs["items"])
        return [_make_source(url)]

    monkeypatch.setattr(middleware, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(middleware, "RAG_WEB_FULL_CONTEXT_ONCE", True)

    body_first = {
        "model": "active-model",
        "messages": [{"role": "user", "content": "summarize"}],
        "metadata": {"chat_id": chat_id, "files": [_make_web_item(url)]},
    }
    _, flags_first = await middleware.chat_completion_files_handler(
        request=request,
        body=body_first,
        extra_params={"__event_emitter__": event_emitter},
        user=user,
    )
    assert len(calls) == 1
    assert len(flags_first["sources"]) == 1

    body_second = {
        "model": "active-model",
        "messages": [{"role": "user", "content": "follow-up question"}],
        "metadata": {"chat_id": chat_id, "files": [_make_web_item(url)]},
    }
    _, flags_second = await middleware.chat_completion_files_handler(
        request=request,
        body=body_second,
        extra_params={"__event_emitter__": event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert flags_second["sources"] == []
    assert any(
        event.get("data", {}).get("action") == "sources_retrieved"
        and event.get("data", {}).get("count") == 0
        for event in events
    )


@pytest.mark.asyncio
async def test_web_full_context_once_includes_only_new_web_attachment(monkeypatch):
    request = _make_request()
    user = SimpleNamespace(id="u1")
    calls = []
    url_1 = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"
    url_2 = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=3"

    async def fake_get_sources_from_items(*_args, **kwargs):
        calls.append(kwargs["items"])
        new_url = kwargs["items"][0]["url"]
        return [_make_source(new_url)]

    monkeypatch.setattr(middleware, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(middleware, "RAG_WEB_FULL_CONTEXT_ONCE", True)

    body = {
        "model": "active-model",
        "messages": _build_messages_with_source(request, url_1),
        "metadata": {"files": [_make_web_item(url_1), _make_web_item(url_2)]},
    }
    _, flags = await middleware.chat_completion_files_handler(
        request=request,
        body=body,
        extra_params={"__event_emitter__": _noop_event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0]["url"] == url_2
    assert len(flags["sources"]) == 1
    assert flags["sources"][0]["metadata"][0]["source"] == url_2


@pytest.mark.asyncio
async def test_web_full_context_once_keeps_non_web_files_in_retrieval(monkeypatch):
    request = _make_request()
    user = SimpleNamespace(id="u1")
    calls = []
    url = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"
    non_web_item = _make_non_web_item()

    async def fake_get_sources_from_items(*_args, **kwargs):
        calls.append(kwargs["items"])
        return []

    monkeypatch.setattr(middleware, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(middleware, "RAG_WEB_FULL_CONTEXT_ONCE", True)

    body = {
        "model": "active-model",
        "messages": _build_messages_with_source(request, url),
        "metadata": {"files": [_make_web_item(url), non_web_item]},
    }
    await middleware.chat_completion_files_handler(
        request=request,
        body=body,
        extra_params={"__event_emitter__": _noop_event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert len(calls[0]) == 1
    assert calls[0][0]["type"] == "file"


@pytest.mark.asyncio
async def test_web_full_context_once_flag_off_keeps_legacy_behavior(monkeypatch):
    request = _make_request()
    user = SimpleNamespace(id="u1")
    calls = []
    url = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"

    async def fake_get_sources_from_items(*_args, **kwargs):
        calls.append(kwargs["items"])
        return [_make_source(url)]

    monkeypatch.setattr(middleware, "get_sources_from_items", fake_get_sources_from_items)
    monkeypatch.setattr(middleware, "RAG_WEB_FULL_CONTEXT_ONCE", False)

    body = {
        "model": "active-model",
        "messages": _build_messages_with_source(request, url),
        "metadata": {"files": [_make_web_item(url)]},
    }
    await middleware.chat_completion_files_handler(
        request=request,
        body=body,
        extra_params={"__event_emitter__": _noop_event_emitter},
        user=user,
    )

    assert len(calls) == 1
    assert calls[0][0]["url"] == url


def test_web_full_context_once_file_key_delta_is_deterministic_and_deduplicates():
    url_1 = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=2"
    url_2 = "https://www.bbc.com/news/live/ceqvwrydzpqt?page=3"
    injected_keys = {middleware._build_web_source_key_from_url(url_1)}

    files_a = [_make_web_item(url_1), _make_web_item(url_2)]
    files_b = [_make_web_item(url_2), _make_web_item(url_1)]

    _, _, pending_a = middleware._build_effective_files_for_web_full_context_once(
        files_a, injected_keys
    )
    _, _, pending_b = middleware._build_effective_files_for_web_full_context_once(
        files_b, injected_keys
    )

    pending_keys_a = {
        tuple(sorted(middleware._build_web_source_keys_from_item(item))) for item in pending_a
    }
    pending_keys_b = {
        tuple(sorted(middleware._build_web_source_keys_from_item(item))) for item in pending_b
    }
    assert pending_keys_a == pending_keys_b

    files_dup = [_make_web_item(url_2), _make_web_item(f"{url_2}#section-1")]
    _, _, pending_dup = middleware._build_effective_files_for_web_full_context_once(
        files_dup, set()
    )
    assert len(pending_dup) == 1
