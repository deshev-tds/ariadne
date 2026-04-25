import math

import pytest
from fastapi import HTTPException

import open_webui.utils.middleware as middleware


def test_stream_logprobs_normalization():
    state = {"tokens": [], "capped": False}
    choice = {
        "delta": {
            "content": "Given",
            "logprobs": {
                "content": [
                    {
                        "token": "Given",
                        "logprob": -0.03,
                        "top_logprobs": [
                            {"token": "Given", "logprob": -0.03},
                            {"token": "Since", "logprob": -2.1},
                        ],
                    }
                ]
            },
        }
    }

    middleware._append_token_telemetry_from_choice(state, choice, "Given")
    telemetry = middleware._build_token_telemetry_payload(state)

    assert telemetry is not None
    assert telemetry["provider"] == "openai_logprobs"
    assert telemetry["topK"] == 10
    assert telemetry["tokenCap"] == 1024
    assert telemetry["tokens"][0]["text"] == "Given"
    assert telemetry["tokens"][0]["index"] == 0
    assert len(telemetry["tokens"][0]["alternatives"]) == 2
    assert pytest.approx(telemetry["tokens"][0]["prob"], rel=1e-5) == math.exp(-0.03)


def test_non_stream_logprobs_normalization():
    response_data = {
        "choices": [
            {
                "message": {"content": "walk"},
                "logprobs": {
                    "content": [
                        {
                            "token": "walk",
                            "logprob": -0.2,
                            "top_logprobs": [
                                {"token": "walk", "logprob": -0.2},
                                {"token": "drive", "logprob": -1.4},
                            ],
                        }
                    ]
                },
            }
        ]
    }

    telemetry = middleware._extract_non_streaming_token_telemetry(response_data)

    assert telemetry is not None
    assert telemetry["tokens"][0]["text"] == "walk"
    assert telemetry["tokens"][0]["alternatives"][1]["text"] == "drive"


def test_load_request_history_from_db_prefers_direct_parent_hit(monkeypatch):
    expected = [{"id": "user-1", "role": "user", "content": "hello"}]

    monkeypatch.setattr(
        middleware,
        "load_raw_messages_from_db",
        lambda chat_id, message_id: expected if message_id == "user-1" else None,
    )

    result = middleware.load_request_history_from_db(
        "chat-1",
        {"id": "user-1", "parentId": "assistant-0", "role": "user", "content": "hello"},
    )

    assert result == expected


def test_load_request_history_from_db_falls_back_to_parent_and_appends_current_user(monkeypatch):
    monkeypatch.setattr(
        middleware,
        "load_raw_messages_from_db",
        lambda chat_id, message_id: (
            [{"id": "assistant-0", "role": "assistant", "content": "older context"}]
            if message_id == "assistant-0"
            else None
        ),
    )

    result = middleware.load_request_history_from_db(
        "chat-1",
        {
            "id": "user-1",
            "parentId": "assistant-0",
            "role": "user",
            "content": "new question",
        },
    )

    assert result is not None
    assert [message["id"] for message in result] == ["assistant-0", "user-1"]
    assert result[-1]["content"] == "new question"


def test_top_ten_alternatives_truncation():
    state = {"tokens": [], "capped": False}
    top_logprobs = [{"token": f"alt_{idx}", "logprob": -1.0 - idx} for idx in range(12)]
    choice = {
        "delta": {
            "content": "x",
            "logprobs": {"content": [{"token": "x", "logprob": -0.1, "top_logprobs": top_logprobs}]},
        }
    }

    middleware._append_token_telemetry_from_choice(state, choice, "x")
    telemetry = middleware._build_token_telemetry_payload(state)

    assert telemetry is not None
    assert len(telemetry["tokens"][0]["alternatives"]) == 10
    assert telemetry["tokens"][0]["alternatives"][0]["text"] == "x"


def test_token_telemetry_token_cap_sets_capped_flag():
    state = {"tokens": [], "capped": False}
    for idx in range(middleware.TOKEN_TELEMETRY_TOKEN_CAP + 1):
        choice = {
            "delta": {
                "content": f"t{idx}",
                "logprobs": {"content": [{"token": f"t{idx}", "logprob": -0.1}]},
            }
        }
        middleware._append_token_telemetry_from_choice(state, choice, f"t{idx}")

    telemetry = middleware._build_token_telemetry_payload(state)

    assert telemetry is not None
    assert len(telemetry["tokens"]) == middleware.TOKEN_TELEMETRY_TOKEN_CAP
    assert telemetry["capped"] is True


def test_prepare_branch_prefill_valid(monkeypatch):
    source_message = {
        "id": "assistant-msg-id",
        "role": "assistant",
        "parentId": "parent-user-id",
        "tokenTelemetry": {
            "tokens": [
                {
                    "index": 0,
                    "text": "Given",
                    "alternatives": [{"rank": 0, "text": "Given"}],
                },
                {
                    "index": 1,
                    "text": " drive",
                    "alternatives": [
                        {"rank": 0, "text": " drive"},
                        {"rank": 1, "text": " walk"},
                    ],
                },
            ]
        },
    }

    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: source_message,
    )

    prefix, branch = middleware._prepare_branch_prefill(
        {
            "chat_id": "chat-id",
            "parent_message_id": "parent-user-id",
            "branch": {
                "source_message_id": "assistant-msg-id",
                "fork_index": 1,
                "alt_rank": 1,
            },
        }
    )

    assert prefix == "Given walk"
    assert branch["sourceMessageId"] == "assistant-msg-id"
    assert branch["forkIndex"] == 1
    assert branch["chosenAltRank"] == 1
    assert branch["chosenTokenText"] == " walk"
    assert branch["forcingStrategy"] == "assistant_prefix_fallback"


def test_prepare_branch_prefill_extends_existing_branch_prefix(monkeypatch):
    source_message = {
        "id": "branched-assistant-msg-id",
        "role": "assistant",
        "parentId": "parent-user-id",
        "tokenBranchDisplayPrefix": "During",
        "tokenTelemetry": {
            "tokens": [
                {
                    "index": 0,
                    "text": "the",
                    "alternatives": [{"rank": 0, "text": "the"}],
                },
                {
                    "index": 1,
                    "text": " crisis",
                    "alternatives": [
                        {"rank": 0, "text": " crisis"},
                        {"rank": 1, "text": " market"},
                    ],
                },
            ]
        },
    }

    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: source_message,
    )

    prefix, branch = middleware._prepare_branch_prefill(
        {
            "chat_id": "chat-id",
            "parent_message_id": "parent-user-id",
            "branch": {
                "source_message_id": "branched-assistant-msg-id",
                "fork_index": 1,
                "alt_rank": 1,
            },
        }
    )

    assert prefix == "During the market"
    assert branch["displayPrefix"] == "During the market"


@pytest.mark.parametrize(
    "metadata,expected_detail",
    [
        (
            {
                "chat_id": "chat-id",
                "parent_message_id": "parent-user-id",
                "branch": {
                    "source_message_id": "assistant-msg-id",
                    "fork_index": 99,
                    "alt_rank": 0,
                },
            },
            "fork_index",
        ),
        (
            {
                "chat_id": "chat-id",
                "parent_message_id": "wrong-parent-id",
                "branch": {
                    "source_message_id": "assistant-msg-id",
                    "fork_index": 0,
                    "alt_rank": 0,
                },
            },
            "parent does not match",
        ),
        (
            {
                "chat_id": "chat-id",
                "parent_message_id": "parent-user-id",
                "branch": {
                    "source_message_id": "assistant-msg-id",
                    "fork_index": 0,
                    "alt_rank": 99,
                },
            },
            "alt_rank",
        ),
    ],
)
def test_prepare_branch_prefill_invalid_ranges(monkeypatch, metadata, expected_detail):
    source_message = {
        "id": "assistant-msg-id",
        "role": "assistant",
        "parentId": "parent-user-id",
        "tokenTelemetry": {
            "tokens": [
                {
                    "index": 0,
                    "text": "Given",
                    "alternatives": [{"rank": 0, "text": "Given"}],
                }
            ]
        },
    }

    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: source_message,
    )

    with pytest.raises(HTTPException) as exc:
        middleware._prepare_branch_prefill(metadata)

    assert expected_detail in str(exc.value.detail)


def test_prepare_branch_prefill_invalid_source(monkeypatch):
    monkeypatch.setattr(
        middleware.Chats,
        "get_message_by_id_and_message_id",
        lambda chat_id, message_id: None,
    )

    with pytest.raises(HTTPException) as exc:
        middleware._prepare_branch_prefill(
            {
                "chat_id": "chat-id",
                "parent_message_id": "parent-user-id",
                "branch": {
                    "source_message_id": "assistant-msg-id",
                    "fork_index": 0,
                    "alt_rank": 0,
                },
            }
        )

    assert "not found" in str(exc.value.detail)
