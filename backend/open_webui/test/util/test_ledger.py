import asyncio

import open_webui.internal.fork_memory_db as fork_memory_db
from open_webui.internal.fork_memory_db import initialize_fork_memory_db
from open_webui.models.ledger import Ledgers
from open_webui.utils.ledger import (
    _infer_agentic_mode,
    maybe_apply_ledger,
    run_background_ledger_capture,
)


def _reset_fork_db(tmp_path):
    fork_memory_db._DATABASE_URL = f"sqlite:///{tmp_path}/fork_memory_test.db"
    fork_memory_db._engine = None
    fork_memory_db._SessionFactory = None
    fork_memory_db._AVAILABLE = None
    initialize_fork_memory_db()


def _message(role: str, content: str, message_id: str) -> dict:
    return {"id": message_id, "role": role, "content": content}


def _assistant_message_with_output(content: str, message_id: str, parent_id: str) -> dict:
    return {
        "id": message_id,
        "role": "assistant",
        "content": content,
        "parentId": parent_id,
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            }
        ],
    }


def test_agentic_ledger_commits_tooling_choice(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)

    messages = [
        _message("user", "Use ffuf instead of dirsearch for this task.", "u1"),
        _message("assistant", "Understood, switching to ffuf.", "a1"),
    ]

    monkeypatch.setattr(
        "open_webui.utils.ledger.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {"u1": messages[0], "a1": messages[1]},
    )
    monkeypatch.setattr(
        "open_webui.utils.ledger.get_message_list",
        lambda _messages_map, _message_id: list(messages),
    )

    result = asyncio.run(
        run_background_ledger_capture(
            chat_id="chat-agentic",
            message_id="a1",
            metadata={},
        )
    )

    entries = Ledgers.get_active_entries("chat-agentic", "agentic")
    assert result["commits"] >= 1
    assert any(entry.entry_type == "tooling" and entry.content == "ffuf" for entry in entries)


def test_vibe_ledger_commits_repeated_refrain_only_after_repetition(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)

    messages = [
        _message("user", 'Keep it "dry and sharp".', "u1"),
        _message("assistant", "I will.", "a1"),
        _message("user", 'Still, keep it "dry and sharp".', "u2"),
        _message("assistant", "Understood.", "a2"),
    ]

    monkeypatch.setattr(
        "open_webui.utils.ledger.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {message["id"]: message for message in messages},
    )
    monkeypatch.setattr(
        "open_webui.utils.ledger.get_message_list",
        lambda _messages_map, _message_id: list(messages),
    )

    result = asyncio.run(
        run_background_ledger_capture(
            chat_id="chat-vibe",
            message_id="a2",
            metadata={},
        )
    )

    entries = Ledgers.get_active_entries("chat-vibe", "vibe")
    assert result["commits"] >= 1
    assert any(entry.entry_type == "refrain" and "dry and sharp" in entry.content for entry in entries)


def test_plain_assistant_output_does_not_force_agentic_mode():
    messages = [
        _message("user", 'Keep it "dry and sharp".', "u1"),
        _assistant_message_with_output("The truth cuts deep.", "a1", "u1"),
    ]

    assert _infer_agentic_mode(messages) is False


def test_vibe_capture_still_commits_with_persisted_assistant_output(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)

    messages = [
        _message("user", 'Keep it "dry and sharp".', "u1"),
        _assistant_message_with_output("The truth cuts deep.", "a1", "u1"),
        _message("user", 'Still, keep it "dry and sharp".', "u2"),
        _assistant_message_with_output("The blade is cold.", "a2", "u2"),
    ]

    monkeypatch.setattr(
        "open_webui.utils.ledger.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {message["id"]: message for message in messages},
    )
    monkeypatch.setattr(
        "open_webui.utils.ledger.get_message_list",
        lambda _messages_map, _message_id: list(messages),
    )

    result = asyncio.run(
        run_background_ledger_capture(
            chat_id="chat-vibe-output",
            message_id="a2",
            metadata={},
        )
    )

    entries = Ledgers.get_active_entries("chat-vibe-output", "vibe")
    assert result["kind_considered"] == "vibe"
    assert result["commits"] >= 1
    assert any(entry.entry_type == "refrain" and "dry and sharp" in entry.content for entry in entries)


def test_one_off_emotional_phrase_does_not_become_ledger(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)

    messages = [
        _message("user", "This makes me sad.", "u1"),
        _message("assistant", "I hear you.", "a1"),
    ]

    monkeypatch.setattr(
        "open_webui.utils.ledger.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {message["id"]: message for message in messages},
    )
    monkeypatch.setattr(
        "open_webui.utils.ledger.get_message_list",
        lambda _messages_map, _message_id: list(messages),
    )

    result = asyncio.run(
        run_background_ledger_capture(
            chat_id="chat-emotion",
            message_id="a1",
            metadata={},
        )
    )

    assert result["commits"] == 0
    assert Ledgers.get_active_entries("chat-emotion", "vibe") == []


def test_transient_marker_never_commits_to_ledger(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)

    messages = [
        _message("user", "remember this: obsidian-sparrow-4172", "u1"),
        _message("assistant", "I will remember it.", "a1"),
    ]

    monkeypatch.setattr(
        "open_webui.utils.ledger.Chats.get_messages_map_by_chat_id",
        lambda _chat_id: {message["id"]: message for message in messages},
    )
    monkeypatch.setattr(
        "open_webui.utils.ledger.get_message_list",
        lambda _messages_map, _message_id: list(messages),
    )

    result = asyncio.run(
        run_background_ledger_capture(
            chat_id="chat-marker",
            message_id="a1",
            metadata={},
        )
    )

    assert result["commits"] == 0
    assert Ledgers.get_active_entries("chat-marker", "agentic") == []
    assert Ledgers.get_active_entries("chat-marker", "vibe") == []


def test_agentic_ledger_injects_after_compaction_when_relevant(tmp_path):
    _reset_fork_db(tmp_path)

    Ledgers.upsert_entry(
        chat_id="chat-inject",
        ledger_kind="agentic",
        entry_type="tooling",
        content="ffuf",
        rationale="Stable tool choice.",
        source_message_ids=["u1"],
        confidence=0.9,
    )

    original_system = {"role": "system", "content": "Base system prompt"}
    current_messages = [
        {
            "role": "system",
            "content": "Base system prompt\n\nConversation state snapshot for earlier turns.\n\nUser Objectives:\n- Continue reconnaissance",
        },
        _message("user", "Continue with the same tool for the next step.", "u2"),
    ]

    updated, telemetry = asyncio.run(
        maybe_apply_ledger(
            chat_id="chat-inject",
            raw_history_messages=current_messages,
            messages=current_messages,
            original_system_message=original_system,
            working_memory_telemetry={
                "summary_included": True,
                "compaction_version": 10,
            },
        )
    )

    assert telemetry["injected"] is True
    assert telemetry["injected_kind"] == "agentic"
    assert "Internal continuity note." in updated[0]["content"]
    assert "Durable task state:" in updated[0]["content"]
    assert "Conversation state snapshot" in updated[0]["content"]


def test_ledger_is_not_injected_on_every_turn(tmp_path):
    _reset_fork_db(tmp_path)

    Ledgers.upsert_entry(
        chat_id="chat-skip",
        ledger_kind="agentic",
        entry_type="tooling",
        content="ffuf",
        rationale="Stable tool choice.",
        source_message_ids=["u1"],
        confidence=0.9,
    )

    original_system = {"role": "system", "content": "Base system prompt"}
    first_messages = [
        {"role": "system", "content": "Base system prompt"},
        _message("user", "Use ffuf for the next step.", "u2"),
    ]
    asyncio.run(
        maybe_apply_ledger(
            chat_id="chat-skip",
            raw_history_messages=first_messages,
            messages=first_messages,
            original_system_message=original_system,
            working_memory_telemetry={
                "summary_included": True,
                "compaction_version": 12,
            },
        )
    )

    second_messages = [
        {"role": "system", "content": "Base system prompt"},
        _message("user", "ok", "u3"),
    ]
    updated, telemetry = asyncio.run(
        maybe_apply_ledger(
            chat_id="chat-skip",
            raw_history_messages=second_messages,
            messages=second_messages,
            original_system_message=original_system,
            working_memory_telemetry={
                "summary_included": False,
                "compaction_version": 12,
            },
        )
    )

    assert telemetry["injected"] is False
    assert telemetry["injection_reason"] in {"not_policy_relevant", "recent_context_sufficient"}
    assert updated == second_messages


def test_only_one_ledger_kind_is_injected_per_turn(tmp_path):
    _reset_fork_db(tmp_path)

    Ledgers.upsert_entry(
        chat_id="chat-priority",
        ledger_kind="agentic",
        entry_type="tooling",
        content="ffuf",
        rationale="Stable tool choice.",
        source_message_ids=["u1"],
        confidence=0.9,
    )
    Ledgers.upsert_entry(
        chat_id="chat-priority",
        ledger_kind="vibe",
        entry_type="tone_profile",
        content="concise, dry",
        rationale="Stable style.",
        source_message_ids=["u1"],
        confidence=0.8,
    )

    messages = [
        {"role": "system", "content": "Base system prompt"},
        _message("user", "Use ffuf and give me a report.", "u2"),
    ]
    updated, telemetry = asyncio.run(
        maybe_apply_ledger(
            chat_id="chat-priority",
            raw_history_messages=messages,
            messages=messages,
            original_system_message={"role": "system", "content": "Base system prompt"},
            working_memory_telemetry={
                "summary_included": True,
                "compaction_version": 3,
            },
        )
    )

    assert telemetry["injected"] is True
    assert telemetry["injected_kind"] == "agentic"
    assert "Durable task state:" in updated[0]["content"]
    assert "Relevant conversation style notes:" not in updated[0]["content"]


def test_ledger_gracefully_disables_when_fork_db_is_unavailable(tmp_path, monkeypatch):
    _reset_fork_db(tmp_path)
    monkeypatch.setattr("open_webui.utils.ledger.is_fork_memory_available", lambda: False)

    messages = [
        {"role": "system", "content": "Base system prompt"},
        _message("user", "Use ffuf.", "u1"),
    ]

    updated, telemetry = asyncio.run(
        maybe_apply_ledger(
            chat_id="chat-disabled",
            raw_history_messages=messages,
            messages=messages,
            original_system_message={"role": "system", "content": "Base system prompt"},
            working_memory_telemetry={"summary_included": True, "compaction_version": 1},
        )
    )

    assert updated == messages
    assert telemetry["injected"] is False
    assert telemetry["injection_reason"] == "disabled"
