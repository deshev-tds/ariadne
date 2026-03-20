from types import SimpleNamespace

from open_webui.models.chats import (
    derive_chat_messages_from_history,
    normalize_chat_payload,
)
from open_webui.routers.chats import _to_chat_response_with_rehydration


def _build_chat_payload():
    user_message = {
        "id": "user-1",
        "role": "user",
        "content": "Tell me a story",
        "parentId": None,
        "childrenIds": ["assistant-1"],
    }
    assistant_message = {
        "id": "assistant-1",
        "role": "assistant",
        "content": "A very long answer that should stay authoritative.",
        "parentId": "user-1",
        "childrenIds": [],
        "done": True,
    }

    return {
        "title": "Story chat",
        "history": {
            "currentId": "assistant-1",
            "messages": {
                "user-1": user_message,
                "assistant-1": assistant_message,
            },
        },
        "messages": [
            user_message,
            {
                **assistant_message,
                "content": "",
            },
        ],
    }


def test_derive_chat_messages_from_history_uses_current_branch():
    payload = _build_chat_payload()

    messages = derive_chat_messages_from_history(payload)

    assert [message["id"] for message in messages] == ["user-1", "assistant-1"]
    assert messages[-1]["content"] == "A very long answer that should stay authoritative."


def test_normalize_chat_payload_replaces_stale_messages_with_history_branch():
    payload = _build_chat_payload()

    normalized = normalize_chat_payload(payload)

    assert normalized["messages"][-1]["content"] == (
        "A very long answer that should stay authoritative."
    )
    assert payload["messages"][-1]["content"] == ""


def test_normalize_chat_payload_returns_empty_messages_for_missing_branch():
    normalized = normalize_chat_payload(
        {
            "title": "Broken chat",
            "history": {"currentId": "missing", "messages": {}},
            "messages": [{"id": "ghost", "role": "assistant", "content": "stale"}],
        }
    )

    assert normalized["messages"] == []


def test_to_chat_response_with_rehydration_uses_normalized_history_messages():
    chat_model = SimpleNamespace(
        model_dump=lambda: {
            "id": "chat-1",
            "user_id": "user-1",
            "title": "Story chat",
            "chat": _build_chat_payload(),
            "updated_at": 1,
            "created_at": 1,
            "share_id": None,
            "archived": False,
            "pinned": False,
            "meta": {},
            "folder_id": None,
        }
    )

    response = _to_chat_response_with_rehydration(chat_model)

    assert response.chat["messages"][-1]["content"] == (
        "A very long answer that should stay authoritative."
    )
