from open_webui.utils.runtime_telemetry import RuntimeTelemetryTap


def test_runtime_telemetry_tracks_model_activity_and_fallbacks():
    tap = RuntimeTelemetryTap()
    tap.start()

    tap.record(
        kind="tool_journey",
        chat_id="chat-1",
        message_id="msg-1",
        user_id="user-1",
        model_id="qwen-specialist",
        payload={
            "kind": "model_activity",
            "phase": "model_task_done",
            "task_kind": "web_search_query_rewriter",
            "operation": "planner_rewrite",
            "actor": "bounded_specialist",
            "model_id": "qwen-specialist",
            "active_model_id": "qwen-35b",
            "selected_via": "task_model",
            "route_source": "bounded_specialist_v1",
            "fallback_used": True,
            "duration_ms": 187,
            "status": "fallback_to_active_model",
        },
    )

    snapshot = tap.snapshot(limit=10)

    assert snapshot["enabled"] is True
    assert snapshot["tool_journey_count"] == 1
    assert snapshot["model_activity_count"] == 1
    assert snapshot["fallback_count"] == 1
    assert snapshot["kind_counts"]["tool_journey"] == 1
    assert snapshot["recent_messages"][0]["model_activity_count"] == 1
    assert snapshot["recent_messages"][0]["fallback_count"] == 1
    assert snapshot["recent_messages"][0]["models"] == ["qwen-specialist"]
    assert snapshot["recent_messages"][0]["active_models"] == ["qwen-35b"]
    assert snapshot["recent_messages"][0]["task_kinds"] == ["web_search_query_rewriter"]


def test_runtime_telemetry_tracks_memory_payloads_by_message():
    tap = RuntimeTelemetryTap()
    tap.start()

    tap.record(
        kind="memory",
        chat_id="chat-2",
        message_id="msg-2",
        payload={
            "working_memory": {
                "summary_included": True,
                "request_tokens": 512,
                "anchor_message_count": 4,
            },
            "recall": {
                "triggered": True,
                "reason": "user asked about prior decision",
                "hit_count": 2,
            },
            "ledger": {"injected": True, "injected_kind": "recent"},
        },
    )

    snapshot = tap.snapshot(limit=10)

    assert snapshot["kind_counts"]["memory"] == 1
    assert snapshot["recent_messages"][0]["memory"]["working_memory"]["summary_included"] is True
    assert snapshot["recent_messages"][0]["memory"]["recall"]["triggered"] is True
    assert snapshot["recent_messages"][0]["memory"]["ledger"]["injected"] is True
