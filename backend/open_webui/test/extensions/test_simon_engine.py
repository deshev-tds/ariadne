from open_webui.extensions.simon_engine.context_builder import (
    build_context_messages,
    resolve_hot_cache_mode,
)
from open_webui.extensions.simon_engine.gatekeeper import RLMGatekeeper
from open_webui.extensions.simon_engine.memory_intents import (
    detect_archive_recall,
    detect_memory_save,
)
from open_webui.extensions.simon_engine.retrieval import rehydrate_lexical_candidates
from open_webui.extensions.simon_engine.types import GateContext, SimonRuntimeContext
from open_webui.models.simon_lex_index import _build_subqueries, flatten_content


def test_memory_intents_detect_recall_and_save_en_bg():
    recall, explicit, query = detect_archive_recall("remember when we discussed rate limits?")
    assert recall is True
    assert explicit is False
    assert "rate limits" in query

    recall_bg, explicit_bg, _ = detect_archive_recall("помниш ли какво говорихме")
    assert recall_bg is True
    assert explicit_bg is False

    assert detect_memory_save("remember this: my ssh key rotates monthly") is True
    assert detect_memory_save("запомни това: pin е 1234") is True


def test_gatekeeper_triggers_on_high_debt_recall_with_retrieval_gap():
    gatekeeper = RLMGatekeeper()
    context = GateContext(
        session_tokens=3800,
        window_tokens=4096,
        vector_scores=[0.12],
        fts_hit_count=0,
        query_len=48,
    )

    decision = gatekeeper.evaluate(
        context,
        user_query="what did we decide about auth keys earlier",
        explicit_recall=True,
        soft_recall=True,
        recent_history=[],
    )

    assert decision.trigger is True
    assert decision.reason in {"explicit_recall", "high_debt_override"}


def test_hot_cache_auto_disables_for_multi_worker(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    enabled, reason = resolve_hot_cache_mode("auto")
    assert enabled is False
    assert "disabled" in reason


def test_rehydrate_lexical_candidates_rehydrates_from_message_map():
    runtime = SimonRuntimeContext(
        chat_id="chat-1",
        hot_key="chat-1:m1",
        messages_map={
            "m1": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Canonical assistant text"}],
            }
        },
    )

    hits, lines = rehydrate_lexical_candidates(
        runtime=runtime,
        candidates=[
            {
                "message_id": "m1",
                "role": "assistant",
                "content": "stale-index-content",
                "score": 0.42,
            }
        ],
    )

    assert len(hits) == 1
    assert hits[0]["content"] == "Canonical assistant text"
    assert lines
    assert "Canonical assistant text" in lines[0]


def test_recursive_subquery_builder_dedupes_and_limits():
    subqueries = _build_subqueries(["alpha", "beta", "gamma", "delta"], max_branches=4)
    assert len(subqueries) <= 4
    assert len(set(subqueries)) == len(subqueries)


def test_flatten_content_handles_string_list_and_dict():
    assert flatten_content(" hello  world ") == "hello world"
    assert flatten_content({"text": "  hi there  "}) == "hi there"
    assert (
        flatten_content([{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}])
        == "hello world"
    )


def test_context_builder_clamps_history_with_anchor_budget():
    runtime = SimonRuntimeContext(
        chat_id="chat-1",
        hot_key="chat-1:m2",
        warm_history=[
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "second turn"},
            {"role": "user", "content": "third turn"},
        ],
        hot_history=[
            {"role": "assistant", "content": "cached note"},
        ],
    )

    result = build_context_messages(
        original_messages=[
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "new user question"},
        ],
        runtime=runtime,
        user_text="new user question",
        anchor_lines=["anchor one", "anchor two"],
        kv_budget_tokens=128,
        anchor_budget_tokens=40,
    )

    assert result[0]["role"] == "system"
    assert result[-1]["role"] == "user"
    assert result[-1]["content"] == "new user question"
