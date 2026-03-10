import asyncio
import time
from types import SimpleNamespace

from open_webui.utils.chat_recall import (
    build_evidence_message,
    detect_recall_need,
    enqueue_branch_backfill,
    inject_evidence_into_messages,
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
    assert result["mode"] == "fts"
    assert result["depth"] == 2


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
    assert result["mode"] == "fts"
    assert result["depth"] == 1
    assert "/srv/app/config.yaml" in result["query_text"]


def test_detect_recall_need_on_constraint_continuation_gap():
    result = detect_recall_need(
        [
            _message("assistant", "We have some old notes."),
            _message("user", "Continue with the same approach we used before."),
        ]
    )

    assert result["trigger"] is True
    assert result["reason"] == "constraint_continuation_gap"
    assert result["mode"] == "fts"
    assert result["depth"] == 1


def test_detect_recall_need_skips_when_live_context_is_sufficient():
    result = detect_recall_need(
        [
            _message("assistant", "We should keep using ffuf for fuzzing."),
            _message("user", "Continue with ffuf for the next step."),
        ]
    )

    assert result["trigger"] is False
    assert result["reason"] == "live_context_sufficient"
    assert result["mode"] == "none"


def test_detect_recall_need_on_ambiguous_referential_gap():
    result = detect_recall_need(
        [
            _message("assistant", "We compared several tools and configs.", "m1"),
            _message("user", "Какво стана с оня другия тул?", "m2"),
        ]
    )

    assert result["trigger"] is True
    assert result["reason"] == "ambiguous_referential_gap"
    assert result["mode"] == "branch_recent"
    assert result["depth"] == 0
    assert result["query_text"] == ""


def test_detect_recall_need_skips_ambiguous_referential_when_turn_is_long():
    result = detect_recall_need(
        [
            _message("assistant", "We compared several tools and configs.", "m1"),
            _message(
                "user",
                "Use the old config, add a new route, update auth, rewrite the middleware, "
                "add tests, and make sure the admin UI explains all of it clearly for operators and developers.",
                "m2",
            ),
        ]
    )

    assert result["trigger"] is False
    assert result["mode"] == "none"


def test_detect_recall_need_skips_single_vague_token():
    result = detect_recall_need(
        [
            _message("assistant", "We compared several tools and configs.", "m1"),
            _message("user", "другия", "m2"),
        ]
    )

    assert result["trigger"] is False
    assert result["mode"] == "none"


def test_detect_recall_need_skips_ambiguous_referential_when_locally_resolved():
    result = detect_recall_need(
        [
            _message("assistant", "We should keep using the old config for nginx.", "m1"),
            _message("user", "Use the old config.", "m2"),
        ]
    )

    assert result["trigger"] is False
    assert result["mode"] == "none"


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
            history_messages=[
                _message("assistant", "Earlier summary state", "m1"),
                _message("assistant", "We decided ffuf is the primary directory fuzzing tool.", "m2"),
                _message("assistant", "Recent summary state", "m9"),
                _message("user", "What did we decide earlier about ffuf?", "m10"),
            ],
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert result["evidence_injected"] is True
    assert result["mode"] == "fts"
    assert result["depth"] == 2
    assert result["evidence_tokens"] > 0
    assert result["usable_hit_count"] == 1
    assert updated[0]["role"] == "system"
    assert "Evidence from earlier conversation:" in updated[0]["content"]
    assert updated[1]["role"] == "assistant"
    assert updated[2]["role"] == "user"


def test_inject_evidence_into_messages_merges_with_existing_system():
    messages = [
        _message("system", "Base system prompt", "s1"),
        _message("user", "What happened earlier?", "u1"),
    ]
    evidence = build_evidence_message(
        [
            {
                "message_id": "m2",
                "role": "assistant",
                "content": "Earlier evidence content.",
            }
        ],
        query_text="earlier evidence",
        max_hits=1,
        snippet_token_budget=40,
    )

    updated = inject_evidence_into_messages(messages, evidence)

    assert len(updated) == 2
    assert updated[0]["role"] == "system"
    assert "Base system prompt" in updated[0]["content"]
    assert "Evidence from earlier conversation:" in updated[0]["content"]


def test_maybe_apply_chat_recall_branch_recent_skips_fts(monkeypatch):
    request = _make_request(ENABLE_CHAT_RECALL=True)
    messages = [
        _message("assistant", "We first tried nmap.", "m1"),
        _message("assistant", "Then we switched to ffuf because it worked better.", "m2"),
        _message("user", "Какво стана с оня другия тул?", "m3"),
    ]

    async def _emitter(_event):
        return None

    def _boom(*args, **kwargs):
        raise AssertionError("recursive_search should not be called for branch_recent mode")

    monkeypatch.setattr("open_webui.utils.chat_recall.is_supported_database", lambda: True)
    monkeypatch.setattr("open_webui.utils.chat_recall.recursive_search", _boom)

    updated, result = asyncio.run(
        maybe_apply_chat_recall(
            request=request,
            chat_id="chat-1",
            branch_message_ids=["m1", "m2", "m3"],
            history_messages=messages,
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert result["triggered"] is True
    assert result["reason"] == "ambiguous_referential_gap"
    assert result["mode"] == "branch_recent"
    assert result["evidence_injected"] is True
    assert result["evidence_tokens"] > 0
    assert updated[0]["role"] == "system"
    assert "[turn m2 | assistant]" in updated[0]["content"]
    assert updated[1]["role"] == "assistant"
    assert updated[3]["role"] == "user"


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
            history_messages=messages,
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert updated == messages
    assert result["timed_out"] is True
    assert result["evidence_injected"] is False


def test_maybe_apply_chat_recall_falls_back_to_raw_branch_scan_when_fts_misses(monkeypatch):
    request = _make_request(ENABLE_CHAT_RECALL=True)
    compact_messages = [
        _message("assistant", "Recent summary state", "m9"),
        _message("user", "What did we decide earlier about ffuf?", "m10"),
    ]
    history_messages = [
        _message("user", "Initial setup", "m1"),
        _message("assistant", "We decided ffuf is the primary directory fuzzing tool.", "m2"),
        _message("assistant", "Recent summary state", "m9"),
        _message("user", "What did we decide earlier about ffuf?", "m10"),
    ]

    async def _emitter(_event):
        return None

    monkeypatch.setattr("open_webui.utils.chat_recall.is_supported_database", lambda: True)
    monkeypatch.setattr(
        "open_webui.utils.chat_recall.get_indexed_message_ids",
        lambda *args, **kwargs: {"m1", "m2", "m9", "m10"},
    )
    monkeypatch.setattr("open_webui.utils.chat_recall.recursive_search", lambda *args, **kwargs: [])

    updated, result = asyncio.run(
        maybe_apply_chat_recall(
            request=request,
            chat_id="chat-1",
            branch_message_ids=["m1", "m2", "m9", "m10"],
            history_messages=history_messages,
            messages=compact_messages,
            event_emitter=_emitter,
        )
    )

    assert result["triggered"] is True
    assert result["reason"] == "explicit_reference"
    assert result["fallback_used"] is True
    assert result["fallback_mode"] == "raw_branch_scan"
    assert result["evidence_injected"] is True
    assert result["hit_count"] == 1
    assert result["usable_hit_count"] == 1
    assert updated[0]["role"] == "system"
    assert "ffuf is the primary directory fuzzing tool" in updated[0]["content"]


def test_maybe_apply_chat_recall_reports_index_coverage(monkeypatch):
    request = _make_request(ENABLE_CHAT_RECALL=True)
    messages = [
        _message("assistant", "Recent summary state", "m9"),
        _message("user", "What did we decide earlier about ffuf?", "m10"),
    ]

    async def _emitter(_event):
        return None

    monkeypatch.setattr("open_webui.utils.chat_recall.is_supported_database", lambda: True)

    def _fake_indexed(_chat_id, _message_ids, include_queue=True):
        return {"m1"} if not include_queue else {"m1", "m2"}

    monkeypatch.setattr("open_webui.utils.chat_recall.get_indexed_message_ids", _fake_indexed)
    monkeypatch.setattr("open_webui.utils.chat_recall.recursive_search", lambda *args, **kwargs: [])

    updated, result = asyncio.run(
        maybe_apply_chat_recall(
            request=request,
            chat_id="chat-1",
            branch_message_ids=["m1", "m2", "m3"],
            history_messages=[
                _message("assistant", "We decided ffuf is the primary directory fuzzing tool.", "m1"),
                _message("assistant", "Old detail", "m2"),
                _message("user", "What did we decide earlier about ffuf?", "m3"),
            ],
            messages=messages,
            event_emitter=_emitter,
        )
    )

    assert updated[0]["role"] == "system"
    assert result["indexed_message_count"] == 1
    assert result["queued_message_count"] == 1
    assert result["missing_message_count"] == 1
