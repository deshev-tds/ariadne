import json
from types import SimpleNamespace

import pytest

import open_webui.tools.builtin as builtin_tools
import open_webui.utils.web_evidence_store as web_store


def test_store_and_query_web_evidence_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    stored = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/page",
        title="Example Page",
        content=(
            "Evolutionary algorithms use mutation and crossover in a population-based "
            "search process. Genetic algorithms are a major class of evolutionary methods."
        ),
    )

    assert stored["status"] == "stored"
    assert stored["artifact_id"].startswith("wp_")

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        query="genetic algorithms mutation crossover",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert queried["narrow_count"] >= 1
    assert len(queried["snippets"]) >= 1
    first = queried["snippets"][0]
    assert first["artifact_id"] == stored["artifact_id"]
    assert "genetic" in first["text"].lower()


@pytest.mark.asyncio
async def test_fetch_url_store_mode_returns_pointer_metadata(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: ("A" * 1024, None),
    )
    monkeypatch.setattr(
        builtin_tools,
        "store_web_page",
        lambda **kwargs: {
            "status": "stored",
            "artifact_id": "wp_abc",
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "url": kwargs["url"],
            "domain": "example.org",
            "title": kwargs.get("title", ""),
            "path": "/tmp/fake.txt",
            "fetched_at": 1,
            "content_chars": 1024,
            "sha256": "deadbeef",
            "fts_indexed": True,
        },
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=SimpleNamespace(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "stored"
    assert payload["mode"] == "store"
    assert payload["artifact_id"] == "wp_abc"
    assert "A" * 100 not in output


@pytest.mark.asyncio
async def test_fetch_url_store_mode_requires_chat_id(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: ("content", None),
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=SimpleNamespace(),
        __metadata__={},
    )
    payload = json.loads(output)

    assert "error" in payload
    assert payload["mode"] == "store"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_uses_store(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "snippets": [
                {
                    "artifact_id": "wp_1",
                    "url": "https://example.org/page",
                    "domain": "example.org",
                    "title": "Example",
                    "start": 0,
                    "end": 42,
                    "score": 0.9,
                    "text": "evidence window",
                }
            ],
            "narrow_count": 1,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=SimpleNamespace(),
        __metadata__={"chat_id": "chat-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "ok"
    assert payload["chat_id"] == "chat-1"
    assert len(payload["snippets"]) == 1


@pytest.mark.asyncio
async def test_query_web_evidence_tool_requires_chat_id():
    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=SimpleNamespace(),
        __metadata__={},
    )
    payload = json.loads(output)

    assert "error" in payload
    assert payload["snippets"] == []
