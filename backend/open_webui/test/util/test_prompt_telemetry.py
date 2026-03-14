from types import SimpleNamespace

from open_webui.utils.prompt_telemetry import append_prompt_telemetry


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
