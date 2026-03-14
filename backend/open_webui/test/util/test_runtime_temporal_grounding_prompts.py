import json

from open_webui.retrieval.web.planner import build_rewriter_prompt, build_web_search_plan
from open_webui.utils.task import (
    RUNTIME_TIME_AUTHORITY_MARKER,
    RUNTIME_TIMESTAMP_MARKER,
    query_generation_template,
    tools_function_calling_generation_template,
)


def test_query_generation_template_appends_runtime_temporal_grounding():
    prompt = query_generation_template(
        "Generate queries for: {{prompt}}",
        [{"role": "user", "content": "latest OpenAI API changes"}],
    )

    assert RUNTIME_TIMESTAMP_MARKER in prompt
    assert RUNTIME_TIME_AUTHORITY_MARKER in prompt
    assert "Resolve relative time references like today, latest, current, and this year against this timestamp." in prompt


def test_tools_function_calling_generation_template_appends_runtime_temporal_grounding():
    prompt = tools_function_calling_generation_template(
        "Available Tools: {{TOOLS}}",
        '[{"name":"search_web"}]',
    )

    assert RUNTIME_TIMESTAMP_MARKER in prompt
    assert RUNTIME_TIME_AUTHORITY_MARKER in prompt
    assert "Use tools to verify unstable or current facts; this timestamp grounds time, not factual truth." in prompt


def test_build_rewriter_prompt_includes_runtime_temporal_context():
    plan = build_web_search_plan("latest Python packaging changes")
    prompt = build_rewriter_prompt(
        user_message="latest Python packaging changes",
        plan=plan,
        conversation_context="Need current information, not stale priors.",
        max_queries=3,
    )
    payload = json.loads(prompt)

    runtime_context = payload["runtime_context"]
    assert runtime_context["authoritative_for_temporal_grounding"] is True
    assert runtime_context["do_not_treat_as_simulation_or_test"] is True
    assert "Resolve relative time references" in runtime_context["relative_time_reference_policy"]
    assert any(
        "simulation or temporal-coherence test" in instruction
        for instruction in payload["instructions"]
    )
