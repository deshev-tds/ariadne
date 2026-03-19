import json
from types import SimpleNamespace

import pytest

import open_webui.tools.builtin as builtin_tools
import open_webui.utils.web_evidence_store as web_store


def _request_with_retrieval_mode(mode: str = "legacy_store_retrieval"):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(WEB_EVIDENCE_RETRIEVAL_MODE=mode)
            )
        )
    )


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


def test_query_web_evidence_store_large_artifact_returns_multiple_relevant_chunks(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Large Artifact Test",
    )

    filler_a = "alpha filler " * 450
    filler_b = "beta filler " * 450
    content = (
        f"{filler_a}\n\n"
        "BOXED WARNING: tirzepatide causes thyroid C-cell tumors in rats.\n\n"
        f"{filler_b}\n\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-large",
        message_id="msg-large",
        url="https://example.org/large",
        title="Large Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-large",
        message_id="msg-large",
        query="thyroid tumors recommended starting dosage",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        window_chars=240,
    )

    assert queried["status"] == "ok"
    assert len(queried["snippets"]) >= 2
    assert all(snippet["artifact_id"] == stored["artifact_id"] for snippet in queried["snippets"])
    assert any("thyroid c-cell tumors" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any("recommended starting dosage is 2.5 mg" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any(snippet.get("chunked") for snippet in queried["snippets"])


def test_query_web_evidence_store_segmented_mode_uses_focus_retrieval_for_large_document(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Segmented Evidence Test",
    )

    filler_a = "alpha filler " * 520
    filler_b = "beta filler " * 520
    content = (
        "# BOXED WARNING\n"
        "BOXED WARNING: tirzepatide causes thyroid C-cell tumors in rats.\n\n"
        f"{filler_a}\n\n"
        "# CONTRAINDICATIONS\n"
        "Mounjaro is contraindicated in patients with a personal or family history of medullary thyroid carcinoma.\n\n"
        f"{filler_b}\n\n"
        "# DOSAGE AND ADMINISTRATION\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-segmented",
        message_id="msg-segmented",
        url="https://example.org/mounjaro",
        title="Mounjaro Label",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-segmented",
        message_id="msg-segmented",
        query="According to the label, what is the boxed warning, and what is the starting dose?",
        artifact_ids=[stored["artifact_id"]],
        top_k=1,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
    )

    assert queried["status"] == "ok"
    assert queried["retrieval_mode_effective"] == web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED
    assert queried["structured_index_used"] is True
    assert queried["focus_retrieval_used"] is True
    assert queried["coverage_after_merge"] >= queried["coverage_before_merge"]
    assert any("boxed warning" in snippet["text"].lower() for snippet in queried["snippets"])
    assert any("recommended starting dosage is 2.5 mg" in snippet["text"].lower() for snippet in queried["snippets"])


def test_query_web_evidence_store_segmented_mode_falls_back_to_chunk_for_unstructured_large_document(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Chunk Fallback Test",
    )

    filler_a = "loose narrative filler " * 600
    filler_b = "more loose narrative filler " * 600
    content = (
        f"{filler_a}\n\n"
        "tirzepatide causes thyroid c-cell tumors in rats.\n\n"
        f"{filler_b}\n\n"
        "The recommended starting dosage is 2.5 mg injected subcutaneously once weekly.\n"
    )

    stored = web_store.store_web_page(
        chat_id="chat-fallback",
        message_id="msg-fallback",
        url="https://example.org/fallback",
        title="Fallback Example",
        content=content,
    )

    queried = web_store.query_web_evidence_store(
        chat_id="chat-fallback",
        message_id="msg-fallback",
        query="thyroid tumors recommended starting dosage",
        artifact_ids=[stored["artifact_id"]],
        top_k=4,
        retrieval_mode=web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
    )

    assert queried["status"] == "ok"
    assert queried["retrieval_mode_effective"] == web_store.WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED
    assert queried["structured_index_used"] is False
    assert queried["fallback_chunk_mode"] is True
    assert any(snippet.get("chunked") for snippet in queried["snippets"])


@pytest.mark.asyncio
async def test_fetch_url_store_mode_returns_pointer_metadata(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "A" * 1024,
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
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
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "stored"
    assert payload["mode"] == "store"
    assert payload["artifact_id"] == "wp_abc"
    assert payload["content_source"] == "primary_loader"
    assert payload["resource_kind"] == "html"
    assert payload["retrieval_mode_effective"] == "legacy_store_retrieval"
    assert payload["retrieval_mode_source"] == "global_default"
    assert payload["available_to"] == "query_web_evidence"
    assert payload["evidence_query_scope"]["chat_id"] == "chat-1"
    assert payload["evidence_query_scope"]["message_id"] == "msg-1"
    assert "A" * 100 not in output


@pytest.mark.asyncio
async def test_fetch_url_store_mode_requires_chat_id(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "content",
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=_request_with_retrieval_mode(),
        __metadata__={},
    )
    payload = json.loads(output)

    assert "error" in payload
    assert payload["mode"] == "store"


@pytest.mark.asyncio
async def test_fetch_url_returns_typed_result_for_unsupported_binary(monkeypatch):
    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "",
            [],
            {
                "status": "unsupported_binary",
                "resource_kind": "xlsx",
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "content_source": "document_extractor",
                "binary_handling": "unsupported_binary",
                "retry_recommended": False,
                "next_action": "choose_another_source",
                "message": "Direct fetch/extraction is not supported for .xlsx resources. Choose another source.",
            },
        ),
    )

    output = await builtin_tools.fetch_url(
        url="https://example.org/file.xlsx",
        mode="store",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "unsupported_binary"
    assert payload["mode"] == "store"
    assert payload["retry_recommended"] is False


@pytest.mark.asyncio
async def test_fetch_url_store_mode_honors_chat_retrieval_override(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "get_content_from_url",
        lambda _request, _url: (
            "content",
            None,
            {"status": "ok", "resource_kind": "html", "content_source": "primary_loader"},
        ),
    )

    def fake_store_web_page(**kwargs):
        captured.update(kwargs)
        return {
            "status": "stored",
            "artifact_id": "wp_override",
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "url": kwargs["url"],
            "domain": "example.org",
            "title": kwargs.get("title", ""),
            "path": "/tmp/fake.txt",
            "fetched_at": 1,
            "content_chars": 7,
            "sha256": "deadbeef",
            "fts_indexed": True,
        }

    monkeypatch.setattr(builtin_tools, "store_web_page", fake_store_web_page)

    output = await builtin_tools.fetch_url(
        url="https://example.org/page",
        mode="store",
        __request__=_request_with_retrieval_mode("legacy_store_retrieval"),
        __metadata__={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {
                "custom_params": {
                    "web_evidence_retrieval_mode": "segmented_confidence_gated"
                }
            },
        },
    )
    payload = json.loads(output)

    assert captured["retrieval_mode"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_effective"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_source"] == "chat_override"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_uses_store(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: captured.update(kwargs) or {
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
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=_request_with_retrieval_mode(),
        __metadata__={"chat_id": "chat-1", "message_id": "msg-1"},
    )
    payload = json.loads(output)

    assert payload["status"] == "ok"
    assert payload["chat_id"] == "chat-1"
    assert payload["message_id"] == "msg-1"
    assert payload["scope_mode"] == "implicit_current_message"
    assert len(payload["snippets"]) == 1
    assert captured["retrieval_mode"] == "legacy_store_retrieval"
    assert payload["retrieval_mode_source"] == "global_default"


@pytest.mark.asyncio
async def test_query_web_evidence_tool_honors_chat_override(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        builtin_tools,
        "query_web_evidence_store",
        lambda **kwargs: captured.update(kwargs) or {
            "status": "ok",
            "query": kwargs["query"],
            "chat_id": kwargs["chat_id"],
            "message_id": kwargs["message_id"],
            "scope_mode": "implicit_current_message",
            "searched_artifact_count": 0,
            "searched_artifact_ids": [],
            "searched_domains": [],
            "missing_artifact_ids": [],
            "evidence_strength": "weak",
            "suggested_next_action": "fetch_more",
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": True,
            "retrieval_mode_effective": kwargs.get("retrieval_mode"),
        },
    )

    output = await builtin_tools.query_web_evidence(
        query="evidence",
        __request__=_request_with_retrieval_mode(),
        __metadata__={
            "chat_id": "chat-1",
            "message_id": "msg-1",
            "params": {"custom_params": {"web_evidence_retrieval_mode": "segmented_confidence_gated"}},
        },
    )
    payload = json.loads(output)

    assert captured["retrieval_mode"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_effective"] == "segmented_confidence_gated"
    assert payload["retrieval_mode_source"] == "chat_override"


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
