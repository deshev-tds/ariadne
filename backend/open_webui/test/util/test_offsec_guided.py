import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import open_webui.tools.builtin as builtin_tools
import open_webui.utils.tools as tool_utils
from open_webui.utils.offsec_guided import (
    apply_continue_signal_to_state,
    apply_guided_step_result,
    build_guided_plan_state,
    should_block_command_payload,
)


def _guided_request():
    Path("/tmp/offsec-guided-spec").mkdir(parents=True, exist_ok=True)
    config = SimpleNamespace(
        ENABLE_LOCAL_CORPUS_TOOLS=True,
        OFFSEC_CORPUS_ROOT="/tmp/offsec-guided-spec",
        LOCAL_CORPUS_ROOT="",
        OFFSEC_GUIDED_STEP_RUN_COMMAND_BUDGET=8,
    )
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)))


def _valid_steps():
    return [
        {
            "id": "step-1",
            "title": "Light recon",
            "purpose": "Fingerprint the remote target before deeper validation.",
            "primary_action_classes": ["passive_recon", "light_probe"],
            "suggested_tools": ["run_command", "offsec_retrieve_evidence"],
            "acceptance_criteria": [
                {"id": "headers", "text": "Headers inspected"},
                {"id": "routes", "text": "One or more routes mapped"},
            ],
            "forbidden_action_classes": ["remediation", "local_system_modification"],
        },
        {
            "id": "step-2",
            "title": "Focused validation",
            "purpose": "Validate the strongest early hypothesis.",
            "primary_action_classes": ["focused_validation"],
            "suggested_tools": ["run_command"],
            "acceptance_criteria": [
                {"id": "hypothesis", "text": "Hypothesis stated"},
                {"id": "signal", "text": "Signal captured or disproved"},
            ],
            "forbidden_action_classes": ["remediation", "local_system_modification"],
        },
    ]


def test_build_guided_plan_state_rejects_remote_remediation():
    state, error = build_guided_plan_state(
        objective="Assess https://example.com",
        phase="first_pass",
        execution_context="remote_observer",
        bound_terminal_id="term-1",
        assumptions=[],
        active_step_id="step-1",
        steps=[
            {
                "id": "step-1",
                "title": "Fix server",
                "purpose": "Apply local remediation on the target host.",
                "primary_action_classes": ["remediation"],
                "suggested_tools": ["run_command"],
                "acceptance_criteria": [
                    {"id": "fix", "text": "Fix applied"},
                    {"id": "verify", "text": "Fix verified"},
                ],
                "forbidden_action_classes": ["passive_recon"],
            },
            {
                "id": "step-2",
                "title": "Report",
                "purpose": "Summarize results.",
                "primary_action_classes": ["focused_validation"],
                "suggested_tools": [],
                "acceptance_criteria": [
                    {"id": "sum", "text": "Summary written"},
                    {"id": "risk", "text": "Risk stated"},
                ],
                "forbidden_action_classes": ["remediation"],
            },
        ],
    )

    assert state is None
    assert "remote_observer" in error


@pytest.mark.asyncio
async def test_builtin_register_plan_sets_guided_state_metadata():
    metadata = {}

    payload = json.loads(
        await builtin_tools.offsec_register_plan(
            objective="Assess https://example.com",
            phase="first_pass",
            execution_context="remote_observer",
            bound_terminal_id="term-1",
            assumptions=["Remote target only"],
            active_step_id="step-1",
            steps=_valid_steps(),
            __request__=_guided_request(),
            __metadata__=metadata,
        )
    )

    assert payload["phase"] == "planning"
    assert payload["guided_state"]["active_step_id"] == "step-1"
    assert metadata["offsec_guided_state_effective"]["execution_context"] == "remote_observer"
    assert metadata["offsec_guided_state_effective"]["step_run_command_budget"] == 8


def test_apply_guided_step_result_defaults_reorder_next_step():
    state, error = build_guided_plan_state(
        objective="Assess https://example.com",
        phase="first_pass",
        execution_context="remote_observer",
        bound_terminal_id="term-1",
        assumptions=[],
        active_step_id="step-1",
        steps=_valid_steps(),
        budget=8,
    )
    assert error is None

    next_state, error = apply_guided_step_result(
        state=state,
        step_id="step-1",
        status="needs_reorder",
        observations=[
            {
                "id": "obs-1",
                "summary": "Headers expose a likely app stack.",
                "source_type": "terminal_result",
                "source_ref": {"tool": "run_command", "command": "curl -I https://example.com"},
                "confidence": 0.8,
                "implication": "Move to focused validation next.",
            }
        ],
        criteria_met_ids=["headers"],
        criteria_unmet_ids=["routes"],
        plan_update={
            "type": "reorder",
            "ordered_step_ids": ["step-2", "step-1"],
            "active_step_id": "step-2",
        },
    )

    assert error is None
    assert next_state["recommended_next_step_id"] == "step-2"
    assert next_state["waiting_for_confirmation"] is True


def test_apply_continue_signal_advances_active_step_and_resets_budget():
    state = {
        "active_step_id": "step-1",
        "recommended_next_step_id": "step-2",
        "waiting_for_confirmation": True,
        "current_step_run_command_count": 7,
        "step_run_command_budget": 8,
        "remaining_step_run_command_budget": 1,
    }

    next_state = apply_continue_signal_to_state(state, "continue")

    assert next_state["active_step_id"] == "step-2"
    assert next_state["waiting_for_confirmation"] is False
    assert next_state["current_step_run_command_count"] == 0
    assert next_state["remaining_step_run_command_budget"] == 8


def test_should_block_command_payload_rejects_chaining_and_background():
    assert should_block_command_payload("nmap -sV example.com && nikto -h https://example.com") is True
    assert should_block_command_payload("curl -I https://example.com &") is True
    assert should_block_command_payload("curl -I https://example.com | head") is False


def test_builtin_offsec_guided_plan_schema_exposes_object_steps():
    tools = tool_utils.get_builtin_tools(
        _guided_request(),
        {
            "__metadata__": {
                "params": {
                    "working_mode": "offsec",
                    "local_corpus_mode": "prefer",
                }
            }
        },
        features={},
        model={"info": {"meta": {"capabilities": {}}}},
    )

    spec = tools["offsec_register_plan"]["spec"]
    steps_schema = spec["parameters"]["properties"]["steps"]
    step_item = steps_schema["items"]

    assert steps_schema["type"] == "array"
    assert step_item["type"] == "object"
    assert set(step_item["required"]) >= {
        "id",
        "title",
        "purpose",
        "primary_action_classes",
        "acceptance_criteria",
        "forbidden_action_classes",
    }
    assert "suggested_tools" in step_item["properties"]
