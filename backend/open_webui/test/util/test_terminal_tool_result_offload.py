import json

from open_webui.utils import middleware


def test_slugify_chat_title_transliterates_and_strips_emoji():
    slug = middleware._slugify_chat_title("Тест 🔥 чат №1")
    assert slug == "test-chat-1"


def test_process_tool_result_offloads_large_terminal_output(tmp_path, monkeypatch):
    monkeypatch.setattr(middleware, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(middleware, "TERMINAL_TOOL_RESULT_INLINE_MAX_BYTES", 64)
    monkeypatch.setattr(middleware, "TERMINAL_TOOL_RESULT_PREVIEW_CHARS", 20)
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda _chat_id: "Пробен чат 🚀"
    )
    middleware._CHAT_ARTIFACT_DIR_CACHE.clear()

    tool_result, tool_files, tool_embeds = middleware.process_tool_result(
        request=None,
        tool_function_name="run_command",
        tool_result="A" * 500,
        tool_type="terminal",
        metadata={"chat_id": "chat-abc", "message_id": "msg-1"},
        user=None,
    )

    assert tool_files == []
    assert tool_embeds == []
    assert "[tool output truncated and persisted to disk]" in tool_result
    assert "chat-abc__proben-chat" in tool_result

    chat_dir = tmp_path / "chat-abc__proben-chat"
    stored_files = list((chat_dir / "tool_outputs").glob("*.txt"))
    assert len(stored_files) == 1
    assert stored_files[0].read_text(encoding="utf-8") == ("A" * 500)

    index_path = chat_dir / "tool_outputs.index.jsonl"
    payload = json.loads(index_path.read_text(encoding="utf-8").strip())
    assert payload["chat_id"] == "chat-abc"
    assert payload["tool"] == "run_command"
    assert payload["bytes"] > 64
    assert payload["path"].endswith(stored_files[0].name)


def test_process_tool_result_keeps_small_terminal_output_inline(tmp_path, monkeypatch):
    monkeypatch.setattr(middleware, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(middleware, "TERMINAL_TOOL_RESULT_INLINE_MAX_BYTES", 256)
    monkeypatch.setattr(middleware, "TERMINAL_TOOL_RESULT_PREVIEW_CHARS", 32)
    middleware._CHAT_ARTIFACT_DIR_CACHE.clear()

    tool_result, tool_files, tool_embeds = middleware.process_tool_result(
        request=None,
        tool_function_name="run_command",
        tool_result="short output",
        tool_type="terminal",
        metadata={"chat_id": "chat-inline", "message_id": "msg-2"},
        user=None,
    )

    assert tool_result == "short output"
    assert tool_files == []
    assert tool_embeds == []
    assert list(tmp_path.glob("*")) == []


def test_chat_artifact_dir_reuses_existing_slug_after_cache_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(middleware, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda _chat_id: "Първо Име"
    )
    middleware._CHAT_ARTIFACT_DIR_CACHE.clear()

    first = middleware._resolve_chat_artifacts_dir("chat-stable")
    assert first is not None
    assert first.name == "chat-stable__parvo-ime"

    # Simulate a process-local cache miss and changed title later.
    middleware._CHAT_ARTIFACT_DIR_CACHE.clear()
    monkeypatch.setattr(
        middleware.Chats, "get_chat_title_by_id", lambda _chat_id: "Ново Име"
    )

    second = middleware._resolve_chat_artifacts_dir("chat-stable")
    assert second is not None
    assert second.name == "chat-stable__parvo-ime"
