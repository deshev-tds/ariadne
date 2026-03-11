from pathlib import Path

from open_webui.routers import chats as chats_router


def _pointer(path: Path) -> str:
    return (
        "[tool output truncated and persisted to disk]\n"
        f"path: {path}\n"
        "bytes: 123\n"
        "sha256: deadbeef\n"
        "preview_chars: 5\n"
        "omitted_chars: 10\n\n"
        "preview:\n"
        "hello"
    )


def test_rehydrate_pointer_text_reads_from_allowed_root(tmp_path, monkeypatch):
    monkeypatch.setattr(chats_router, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    artifact = tmp_path / "chat-1__test" / "tool_outputs" / "sample.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("FULL OUTPUT", encoding="utf-8")

    assert chats_router._rehydrate_pointer_text(_pointer(artifact)) == "FULL OUTPUT"


def test_rehydrate_pointer_text_ignores_path_outside_root(tmp_path, monkeypatch):
    monkeypatch.setattr(chats_router, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("OUTSIDE", encoding="utf-8")

    pointer = _pointer(outside)
    assert chats_router._rehydrate_pointer_text(pointer) == pointer


def test_rehydrate_chat_payload_updates_function_call_output_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(chats_router, "AGENTIC_ARTIFACTS_DIR", tmp_path)
    artifact = tmp_path / "chat-2__demo" / "tool_outputs" / "result.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("REHYDRATED RESULT", encoding="utf-8")

    payload = {
        "history": {
            "messages": {
                "m1": {
                    "output": [
                        {
                            "type": "function_call_output",
                            "output": [{"type": "input_text", "text": _pointer(artifact)}],
                        }
                    ]
                }
            }
        }
    }

    hydrated = chats_router._rehydrate_chat_payload(payload)
    block_text = (
        hydrated["history"]["messages"]["m1"]["output"][0]["output"][0]["text"]
    )
    assert block_text == "REHYDRATED RESULT"
