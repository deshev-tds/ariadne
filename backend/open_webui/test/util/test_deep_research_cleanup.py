from pathlib import Path

from open_webui.models import chats as chats_module


def test_delete_chat_artifact_dirs_removes_matching_chat_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(chats_module, "AGENTIC_ARTIFACTS_DIR", str(tmp_path))

    chat_1_a = tmp_path / "chat-1__alpha"
    chat_1_b = tmp_path / "chat-1__beta"
    chat_2 = tmp_path / "chat-2__gamma"

    chat_1_a.mkdir()
    chat_1_b.mkdir()
    chat_2.mkdir()

    chats_module._delete_chat_artifact_dirs(["chat-1"])

    assert not chat_1_a.exists()
    assert not chat_1_b.exists()
    assert chat_2.exists()
