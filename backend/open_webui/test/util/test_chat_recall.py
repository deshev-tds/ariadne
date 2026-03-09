import asyncio
import time
from types import SimpleNamespace

from open_webui.utils.chat_recall import (
    build_evidence_message,
    detect_recall_need,
    enqueue_branch_backfill,
    maybe_apply_chat_recall,
    resolve_chat_recall_enabled,
)


def _make_request(**overrides):
    config_values = {
        "ENABLE_CHAT_RECALL": False,
        "CHAT_RECALL_TIMEOUT_MS": 150,
        "CHAT_RECALL_MAX_HITS": 3,
        "CHAT_RECALL_SNIPPET_TOKEN_BUDGET": 300,
    }
    config_values.update(overrides)
    config = SimpleNamespace(**config_values)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _message(role: str, content: str, message_id: str | None = None) -> dict:
    message = {"role": role, "content": content}
    if message_id is not None:
        message["id"] = message_id
    return message


def test_resolve_chat_recall_enabled_prefers_user_setting():
    request = _make_request(ENABLE_CHAT_RECALL=False)
    user = SimpleNamespace(settings=SimpleNamespace(ui={"chatRecall": True}))

    assert resolve_chat_recall_enabled(request, user) is True


def test_detect_recall_need_on_explicit_reference():
    result = detect_recall_need(
        [
            _message("assistant", "We already discussed the rollout."),
            _message("user", "What did we decide earlier about the rollout?"),
        ]
    )

    assert result["trigger"] is True
    assert result["reason"] == "explicit_reference"
    assert result["explicit"] is True


def test_detect_recall_need_on_entity_lookup_gap():
    result = detect_recall_need(
        [
            _message("assistant", "Let's revisit deployment details."),
            _message("user", "Use `/srv/app/config.yaml` and set `DEPLOY_ENV` correctly."),
        ]
    )

    assert result["trigger"] is True
    assert result["reason"] == "entity_lookup_gap"
    assert "/srv/app/config.yaml" in result["entities"]


def test_detect_recall_need_on_constraint_continuation_gap():
    result = detect_recall_need(
        [
            _message("assistant", "We have some old notes."),
            _message("user", "Continue with the same approach we used before."),
        ]
    )

    assert result["trigger"] is True
    assert result["reason"] == "constraint_continuation_gap"


def test_detect_recall_need_skips_when_live_context_is_sufficient():
    result = detect_recall_need(
        [
            _message("assistant", "We should keep using ffuf for fuzzing."),
            _message("user", "Continue with ffuf for the next step."),
        ]
    )

    assert result["trigger"] is False
    assert result["reason"] == "live_context_sufficient"


def test_build_evidence_message_formats_hits_as_evidence():
    message = build_evidence_message(
        [
            {
                "message_id": "142",
                "role": "user",
                "content": "ffuf is the primary directory fuzzing tool because dirsearch had dependency issues.",
            }
        ],
        query_text="ffuf fuzzing tool",
        max_hits=3,
        snippet_token_budget=40,
    )

    assert message is not None
    assert message["role"] == "system"
    assert "Evidence from earlier conversation:" in message["content"]
    assert "[turn 142 | user]" in message["content"]
    assert "Prefer them over guesses." in message["content"]


def test_enqueue_branch_backfill_ignores_local_chat():
    assert enqueue_branch_backfill("local:chat-1", [{"id": "m1"}]) == 0


def test_maybe_apply_chat_recall_injects_evidence(monkeypatch):
    request = _make_request(ENABLE_CHAT_RECALL=True)
    messages = [
        _message("assistant", "Recent summary state", "m9"),
        _message("user", "What did we decide earlier about ffuf?", "m10"),
    ]

    async def _emitter(_event):
        return None

    monkeypatch.setattr("open_webui.utils.chat_recall.is_supported_database", lambda: True)
    monkeypatch.setattr(
        "open_webui.utils.chat_recall.recursive_search",
        lambda *args, **kwargs: [
            {
                "message_id": "m2",
                "role": "assistant",
                "content": "We decided ffuf is the primary directory fuzzing tool.",
            }
        ],
    )

    updated, result = asyncio.run(
        maybe_apply_chat_recall(
            request=request,
            chat_id="chat-1",
            branch_message_ids=["m1", "m2", "m9", "m10"],
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert result["evidence_injected"] is True
    assert updated[1]["role"] == "system"
    assert "Evidence from earlier conversation:" in updated[1]["content"]
    assert updated[2]["role"] == "user"


def test_maybe_apply_chat_recall_times_out_and_falls_back(monkeypatch):
    request = _make_request(
        ENABLE_CHAT_RECALL=True,
        CHAT_RECALL_TIMEOUT_MS=1,
    )
    messages = [
        _message("assistant", "Recent summary state", "m9"),
        _message("user", "What did we decide earlier about ffuf?", "m10"),
    ]

    async def _emitter(_event):
        return None

    def _slow_search(*args, **kwargs):
        time.sleep(0.05)
        return []

    monkeypatch.setattr("open_webui.utils.chat_recall.is_supported_database", lambda: True)
    monkeypatch.setattr("open_webui.utils.chat_recall.recursive_search", _slow_search)

    updated, result = asyncio.run(
        maybe_apply_chat_recall(
            request=request,
            chat_id="chat-1",
            branch_message_ids=["m1", "m2", "m9", "m10"],
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert updated == messages
    assert result["timed_out"] is True
    assert result["evidence_injected"] is False
