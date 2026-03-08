from types import SimpleNamespace

import pytest

from open_webui.utils.context_maintenance import (
    build_context_maintenance_payload,
    extract_model_ctx_cap,
    is_summary_refresh_needed,
    parse_prometheus_metrics,
    resolve_effective_ctx_cap,
    resolve_history_budgets,
)


def _make_request(**overrides):
    config = SimpleNamespace(
        CONTEXT_MAINTENANCE_MAX_CTX_CAP="",
        CONTEXT_MAINTENANCE_OUTPUT_RESERVE_TOKENS=8192,
        CONTEXT_MAINTENANCE_SAFETY_RESERVE_TOKENS=4096,
        CONTEXT_MAINTENANCE_RAG_RESERVE_TOKENS=12288,
        CONTEXT_MAINTENANCE_SOFT_MARGIN_TOKENS=8192,
        CONTEXT_MAINTENANCE_ANCHOR_BUDGET_TOKENS=2048,
        **overrides,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _message(message_id: str, role: str, content: str) -> dict:
    return {"id": message_id, "role": role, "content": content}


def test_parse_prometheus_metrics_extracts_kv_fields():
    metrics = parse_prometheus_metrics(
        """
        # HELP llamacpp:kv_cache_usage_ratio KV cache usage
        llamacpp:kv_cache_usage_ratio 0.81
        llamacpp:kv_cache_tokens 32768
        unrelated_metric 12
        """
    )

    assert metrics == {
        "llamacpp:kv_cache_usage_ratio": 0.81,
        "llamacpp:kv_cache_tokens": 32768.0,
    }


def test_resolve_effective_ctx_cap_respects_admin_cap():
    request = _make_request(CONTEXT_MAINTENANCE_MAX_CTX_CAP="65536")
    model = {"status": {"args": ["--ctx-size", "131072"]}}

    assert extract_model_ctx_cap(model) == 131072
    assert resolve_effective_ctx_cap(request, model, {"n_ctx": 131072}) == 65536


def test_resolve_history_budgets_uses_rag_reserve_when_files_present():
    request = _make_request()
    model = {"status": {"args": ["--ctx-size", "32768"]}}

    budgets = resolve_history_budgets(
        request,
        model=model,
        form_data={"model": "demo", "files": [{"id": "f1"}]},
        metadata={},
        probe={"n_ctx": 32768},
    )

    assert budgets["effective_ctx_cap"] == 32768
    assert budgets["rag_reserve_tokens"] == 12288
    assert budgets["hard_history_budget"] < 32768


def test_build_context_payload_reuses_summary_state_without_cascade():
    history = [
        _message("m1", "user", "initial task"),
        _message("m2", "assistant", "initial plan"),
        _message("m3", "user", "constraint one"),
        _message("m4", "assistant", "implementation detail"),
        _message("m5", "user", "latest question"),
    ]
    budgets = {
        "anchor_budget_tokens": 128,
        "hard_history_budget": 128,
    }
    summary_state = {
        "summary_text": "summary over m1..m4",
        "summarized_through_message_id": "m4",
    }

    payload = build_context_maintenance_payload(
        system_message={"role": "system", "content": "You are helpful"},
        history_messages=history,
        summary_state=summary_state,
        budgets=budgets,
    )

    assert payload["used_summary_state"] is True
    assert payload["summary_text"] == "summary over m1..m4"
    assert payload["tail_messages"] == [history[-1]]


def test_summary_refresh_needs_meaningful_growth():
    history = [_message(f"m{i}", "user" if i % 2 else "assistant", "x" * 1000) for i in range(1, 8)]

    assert (
        is_summary_refresh_needed(
            history,
            {
                "summary_text": "old summary",
                "summarized_through_message_id": "m1",
            },
        )
        is True
    )

    assert (
        is_summary_refresh_needed(
            history[:2],
            {
                "summary_text": "old summary",
                "summarized_through_message_id": "m1",
            },
        )
        is False
    )
