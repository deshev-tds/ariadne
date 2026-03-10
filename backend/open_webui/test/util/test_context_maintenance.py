from types import SimpleNamespace

import pytest

from open_webui.utils.context_maintenance import (
    build_summary_message,
    build_summary_prompt,
    build_context_maintenance_payload,
    extract_model_ctx_cap,
    extract_n_ctx_from_props,
    is_summary_refresh_needed,
    normalize_summary_snapshot,
    parse_prometheus_metrics,
    resolve_effective_ctx_cap,
    resolve_history_budgets,
    resolve_live_prompt_cap,
)


def _make_request(**overrides):
    config_values = {
        "CONTEXT_MAINTENANCE_MAX_CTX_CAP": "",
        "CONTEXT_MAINTENANCE_OUTPUT_RESERVE_TOKENS": 8192,
        "CONTEXT_MAINTENANCE_SAFETY_RESERVE_TOKENS": 4096,
        "CONTEXT_MAINTENANCE_RAG_RESERVE_TOKENS": 12288,
        "CONTEXT_MAINTENANCE_SOFT_MARGIN_TOKENS": 8192,
        "CONTEXT_MAINTENANCE_ANCHOR_BUDGET_TOKENS": 2048,
    }
    config_values.update(overrides)
    config = SimpleNamespace(**config_values)
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


def test_parse_prometheus_metrics_ignores_non_kv_metrics():
    metrics = parse_prometheus_metrics(
        """
        llamacpp:prompt_tokens_total 5061
        llamacpp:requests_processing 1
        llamacpp:n_tokens_max 3618
        """
    )

    assert metrics == {}


def test_extract_n_ctx_from_props_supports_llamacpp_shape():
    props = {
        "default_generation_settings": {
            "params": {
                "n_ctx": 65536,
            }
        }
    }

    assert extract_n_ctx_from_props(props) == 65536


def test_build_summary_prompt_requests_structured_state_snapshot():
    prompt = build_summary_prompt(transcript="user: hi", max_tokens=512)

    assert "structured state snapshot" in prompt
    assert "Do not write a narrative summary." in prompt
    assert "User Objectives:" in prompt
    assert "Constraints and Preferences:" in prompt
    assert "Decisions and Conclusions:" in prompt
    assert "Open Questions and Unresolved Work:" in prompt
    assert "Stable Facts and Assumptions:" in prompt


def test_normalize_summary_snapshot_canonicalizes_sections():
    normalized = normalize_summary_snapshot(
        """
        Goals:
        - ship context maintenance
        Constraints:
        - keep it backend-owned
        Decisions:
        - use async maintenance
        Open Questions:
        - add exact recall later
        System Assumptions:
        - llama.cpp metrics may be missing
        """
    )

    assert "User Objectives:\n- ship context maintenance" in normalized
    assert "Constraints and Preferences:\n- keep it backend-owned" in normalized
    assert "Decisions and Conclusions:\n- use async maintenance" in normalized
    assert "Open Questions and Unresolved Work:\n- add exact recall later" in normalized
    assert "Stable Facts and Assumptions:\n- llama.cpp metrics may be missing" in normalized


def test_normalize_summary_snapshot_falls_back_to_stable_facts_block():
    normalized = normalize_summary_snapshot("Keep ffuf as the primary tool.")

    assert "User Objectives:\n- None recorded." in normalized
    assert "Stable Facts and Assumptions:\n- Keep ffuf as the primary tool." in normalized


def test_build_summary_message_uses_state_snapshot_wrapper():
    message = build_summary_message("User Objectives:\n- keep the chat stable")

    assert message["role"] == "system"
    assert "Conversation state snapshot for earlier turns." in message["content"]


def test_resolve_effective_ctx_cap_respects_admin_cap():
    request = _make_request(CONTEXT_MAINTENANCE_MAX_CTX_CAP="65536")
    model = {"status": {"args": ["--ctx-size", "131072"]}}

    assert extract_model_ctx_cap(model) == 131072
    assert resolve_effective_ctx_cap(request, model, {"n_ctx": 131072}) == 65536


def test_resolve_live_prompt_cap_prefers_live_probe_over_model_args():
    request = _make_request()
    model = {"status": {"args": ["--ctx-size", "131072"]}}

    live_cap, source = resolve_live_prompt_cap(request, model, {"n_ctx": 32768, "source": "slots"})

    assert live_cap == 32768
    assert source == "probe:slots"


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

    assert budgets["live_prompt_cap"] == 32768
    assert budgets["live_prompt_cap_source"] == "probe:probe"
    assert budgets["hot_context_target_tokens"] == budgets["hard_history_budget"]
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
    assert payload["telemetry"]["summary_included"] is True
    assert payload["telemetry"]["anchor_message_count"] >= 1


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
