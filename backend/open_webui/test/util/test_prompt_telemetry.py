from types import SimpleNamespace

from open_webui.utils.prompt_telemetry import append_prompt_telemetry
from open_webui.utils.runtime_telemetry import runtime_telemetry


def test_append_prompt_telemetry_accumulates_on_request_metadata():
    request_metadata = {"params": {"debug_prompt_telemetry": True}}
    request = SimpleNamespace(state=SimpleNamespace(metadata=request_metadata))

    append_prompt_telemetry(
        request,
        {"params": {"debug_prompt_telemetry": True}, "task": "main"},
        provider="openai",
        request_url="https://example.test/v1/chat/completions",
        payload={"model": "demo", "messages": [{"role": "user", "content": "hello"}]},
    )
    append_prompt_telemetry(
        request,
        {"params": {"debug_prompt_telemetry": True}, "task": "rewriter"},
        provider="openai",
        request_url="https://example.test/v1/chat/completions",
        payload={"model": "demo", "messages": [{"role": "user", "content": "latest"}]},
    )

    telemetry = request_metadata["prompt_telemetry"]
    assert telemetry["enabled"] is True
    assert telemetry["version"] == 1
    assert len(telemetry["entries"]) == 2
    assert telemetry["entries"][0]["task"] == "main"
    assert telemetry["entries"][1]["task"] == "rewriter"


def test_append_prompt_telemetry_records_runtime_tap_without_debug_flag():
    request = SimpleNamespace(state=SimpleNamespace(metadata={}))

    runtime_telemetry.start()
    try:
        result = append_prompt_telemetry(
            request,
            {
                "task": "planner",
                "chat_id": "chat-1",
                "message_id": "msg-1",
                "user_id": "user-1",
            },
            provider="openai",
            request_url="https://example.test/v1/chat/completions",
            payload={"model": "demo", "messages": [{"role": "user", "content": "hello"}]},
        )

        assert result is None

        snapshot = runtime_telemetry.snapshot(limit=10)
        assert snapshot["enabled"] is True
        assert snapshot["kind_counts"]["prompt"] == 1
        assert snapshot["recent_messages"][0]["chat_id"] == "chat-1"
        assert snapshot["recent_messages"][0]["prompt_entry_count"] == 1
    finally:
        runtime_telemetry.stop()
        runtime_telemetry.clear()
