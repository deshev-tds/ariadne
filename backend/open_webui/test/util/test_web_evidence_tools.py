import json
from types import SimpleNamespace

import pytest

import open_webui.tools.builtin as builtin_tools
import open_webui.utils.web_evidence_store as web_store


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=SimpleNamespace(
                    WEB_SEARCH_ENGINE="test-engine",
                    WEB_SEARCH_RESULT_COUNT=None,
                    TIKTOKEN_ENCODING_NAME="cl100k_base",
                )
            )
        )
    )


def _chat_metadata(chat_id: str = "chat-1", message_id: str = "msg-1") -> dict:
    return {"chat_id": chat_id, "message_id": message_id, "params": {}}


def _install_temp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(web_store, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        web_store.Chats,
        "get_chat_title_by_id",
        lambda _chat_id: "Read First Test",
    )


def test_store_and_read_stored_web_page_roundtrip(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)

    stored = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/paper",
        title="Example Paper",
        content="This is a short scientific paper with enough context to fit in one read.",
    )

    payload = web_store.read_stored_web_page(
        chat_id="chat-1",
        artifact_id=stored["artifact_id"],
        max_tokens=12_000,
    )

    assert payload["status"] == "ok"
    assert payload["artifact_id"] == stored["artifact_id"]
    assert payload["whole_document_returned"] is True
    assert payload["done"] is True
    assert payload["next_cursor"] is None
    assert "short scientific paper" in payload["text"]


def test_read_stored_web_page_returns_next_cursor_with_overlap(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)

    long_text = " ".join(f"token{i}" for i in range(300))
    stored = web_store.store_web_page(
        chat_id="chat-1",
        message_id="msg-1",
        url="https://example.org/long-paper",
        title="Long Paper",
        content=long_text,
    )

    first = web_store.read_stored_web_page(
        chat_id="chat-1",
        artifact_id=stored["artifact_id"],
        max_tokens=40,
    )
    second = web_store.read_stored_web_page(
        chat_id="chat-1",
        cursor=first["next_cursor"],
        max_tokens=40,
    )

    assert first["status"] == "ok"
    assert first["whole_document_returned"] is False
    assert first["done"] is False
    assert first["next_cursor"]
    assert second["status"] == "ok"
    assert second["artifact_id"] == stored["artifact_id"]
    assert second["range_start_token"] == first["range_end_token"] - 39


@pytest.mark.asyncio
async def test_search_web_results_are_read_admission_inputs(monkeypatch):
    class _Result:
        def __init__(self, title, link, snippet):
            self.title = title
            self.link = link
            self.snippet = snippet

    def _fake_search_web(_request, _engine, _query, _user):
        return [_Result("Example", "https://example.org/page", "snippet text")]

    monkeypatch.setattr(builtin_tools, "_search_web", _fake_search_web)

    payload = await builtin_tools.search_web(
        "sleep onset latency",
        __request__=_request(),
        __user__=None,
    )
    parsed = json.loads(payload)
    assert parsed[0]["snippet_is_excerpt"] is True
    assert parsed[0]["full_text_requires_fetch"] is True
    assert set(parsed[0]).isdisjoint({"legacy_store_query_ready"})


@pytest.mark.asyncio
async def test_search_web_collapses_same_article_mirrors(monkeypatch):
    class _Result:
        def __init__(self, title, link, snippet):
            self.title = title
            self.link = link
            self.snippet = snippet

    def _fake_search_web(_request, _engine, _query, _user):
        return [
            _Result(
                "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes",
                "https://www.frontiersin.org/journals/neurology/articles/10.3389/fneur.2025.1699303/full",
                "Systematic review and meta-analysis of adults with sleep onset latency outcomes.",
            ),
            _Result(
                "Efficacy of blue-light blocking glasses on actigraphic sleep outcomes",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC12668929/",
                "Mirror page for the same systematic review and meta-analysis.",
            ),
            _Result(
                "Independent RCT",
                "https://example.org/independent-rct",
                "Randomized controlled trial in healthy adults.",
            ),
        ]

    monkeypatch.setattr(builtin_tools, "_search_web", _fake_search_web)

    payload = await builtin_tools.search_web(
        "blue light blocking glasses sleep latency adults",
        __request__=_request(),
        __user__=None,
    )
    parsed = json.loads(payload)
    assert len(parsed) == 2
    assert parsed[0]["mirror_family_collapsed"] is True
    assert parsed[0]["collapsed_mirror_count"] == 2


@pytest.mark.asyncio
async def test_fetch_url_store_points_to_read_web_page(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)

    def _fake_get_content_from_url(_request, _url):
        return (
            "# Paper Title\n\nMain text here.",
            [],
            {"content_source": "unit_test"},
        )

    monkeypatch.setattr(builtin_tools, "get_content_from_url", _fake_get_content_from_url)

    payload = await builtin_tools.fetch_url(
        "https://example.org/paper",
        mode="store",
        __request__=_request(),
        __metadata__=_chat_metadata(),
    )
    parsed = json.loads(payload)
    assert parsed["status"] == "stored"
    assert parsed["available_to"] == "read_web_page"


@pytest.mark.asyncio
async def test_read_web_page_url_auto_fetches_and_reads(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)

    def _fake_get_content_from_url(_request, _url):
        return (
            "# Paper Title\n\nThis is the full article text for the selected paper.",
            [],
            {"content_source": "unit_test"},
        )

    monkeypatch.setattr(builtin_tools, "get_content_from_url", _fake_get_content_from_url)

    payload = await builtin_tools.read_web_page(
        url="https://example.org/paper",
        __request__=_request(),
        __metadata__=_chat_metadata(),
    )
    parsed = json.loads(payload)
    assert parsed["status"] == "ok"
    assert parsed["artifact_id"].startswith("wp_")
    assert parsed["whole_document_returned"] is True
    assert "selected paper" in parsed["text"]


@pytest.mark.asyncio
async def test_read_web_page_reuses_existing_artifact_for_same_url(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)
    calls = {"count": 0}

    def _fake_get_content_from_url(_request, _url):
        calls["count"] += 1
        return (
            "# Paper Title\n\nReusable content.",
            [],
            {"content_source": "unit_test"},
        )

    monkeypatch.setattr(builtin_tools, "get_content_from_url", _fake_get_content_from_url)

    first = json.loads(
        await builtin_tools.read_web_page(
            url="https://example.org/paper",
            __request__=_request(),
            __metadata__=_chat_metadata(),
        )
    )
    second = json.loads(
        await builtin_tools.read_web_page(
            url="https://example.org/paper",
            __request__=_request(),
            __metadata__=_chat_metadata(),
        )
    )

    assert calls["count"] == 1
    assert second["artifact_id"] == first["artifact_id"]
    assert second["whole_document_returned"] is True


@pytest.mark.asyncio
async def test_read_web_page_cursor_continues_contiguous_slabs(tmp_path, monkeypatch):
    _install_temp_store(monkeypatch, tmp_path)

    long_text = " ".join(f"token{i}" for i in range(400))

    def _fake_get_content_from_url(_request, _url):
        return (
            long_text,
            [],
            {"content_source": "unit_test"},
        )

    monkeypatch.setattr(builtin_tools, "get_content_from_url", _fake_get_content_from_url)

    first = json.loads(
        await builtin_tools.read_web_page(
            url="https://example.org/long-paper",
            max_tokens=40,
            __request__=_request(),
            __metadata__=_chat_metadata(),
        )
    )
    second = json.loads(
        await builtin_tools.read_web_page(
            cursor=first["next_cursor"],
            max_tokens=40,
            __request__=_request(),
            __metadata__=_chat_metadata(),
        )
    )

    assert first["whole_document_returned"] is False
    assert first["done"] is False
    assert first["next_cursor"]
    assert second["artifact_id"] == first["artifact_id"]
    assert second["range_start_token"] == first["range_end_token"] - 39
