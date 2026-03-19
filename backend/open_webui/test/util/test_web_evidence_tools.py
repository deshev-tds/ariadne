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
        message_id="msg-1",
        query="genetic algorithms mutation crossover",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert queried["scope_mode"] == "explicit"
    assert queried["searched_artifact_count"] == 1
    assert queried["evidence_strength"] in {"adequate", "strong"}
    assert queried["narrow_count"] >= 1
    assert len(queried["snippets"]) >= 1
    first = queried["snippets"][0]
    assert first["artifact_id"] == stored["artifact_id"]
    assert "genetic" in first["text"].lower()


def test_query_web_evidence_store_implicit_scope_uses_exact_message(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    first = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/first",
        title="First",
        content="Hormuz chokepoint logistics and shipping insurance matter here.",
    )
    second = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-2",
        url="https://example.org/second",
        title="Second",
        content="This page only discusses a different region and topic.",
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-1",
        query="Hormuz shipping insurance logistics",
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert queried["scope_mode"] == "implicit_current_message"
    assert queried["searched_artifact_ids"] == [first["artifact_id"]]
    assert second["artifact_id"] not in queried["searched_artifact_ids"]
    assert queried["searched_domains"] == ["example.org"]
    assert queried["suggested_next_action"] in {
        "refine_query",
        "answer_with_current_evidence",
        "broaden_discovery",
    }


def test_query_web_evidence_store_empty_implicit_scope_is_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Web Evidence Test",
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-1",
        message_id="msg-empty",
        query="Hormuz shipping insurance logistics",
    )

    assert queried["status"] in {"ok", "not_found"}
    assert queried["searched_artifact_count"] == 0
    assert queried["evidence_strength"] == "weak"
    assert queried["suggested_next_action"] == "fetch_more"


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
    assert payload["available_to"] == "query_web_evidence"
    assert payload["evidence_query_scope"]["chat_id"] == "chat-1"
    assert payload["evidence_query_scope"]["message_id"] == "msg-1"
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
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 1,
            "searched_artifact_ids": ["wp_1"],
            "searched_domains": ["example.org"],
            "missing_artifact_ids": [],
            "evidence_strength": "adequate",
            "suggested_next_action": "answer_with_current_evidence",
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
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "ok"
    assert payload["chat_id"] == "chat-1"
    assert payload["message_id"] == "msg-1"
    assert payload["scope_mode"] == "implicit_current_message"
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
