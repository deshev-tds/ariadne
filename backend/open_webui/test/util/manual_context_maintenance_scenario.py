from __future__ import annotations

import asyncio
from types import SimpleNamespace

from open_webui.utils.context_maintenance import (
    inspect_context_pressure,
    is_summary_refresh_needed,
    should_force_inline_maintenance,
    should_schedule_maintenance,
)


def _make_request(models_map: dict[str, dict]):
    config = SimpleNamespace(
        CONTEXT_MAINTENANCE_MAX_CTX_CAP="",
        CONTEXT_MAINTENANCE_OUTPUT_RESERVE_TOKENS=8192,
        CONTEXT_MAINTENANCE_SAFETY_RESERVE_TOKENS=4096,
        CONTEXT_MAINTENANCE_RAG_RESERVE_TOKENS=12288,
        CONTEXT_MAINTENANCE_SOFT_MARGIN_TOKENS=8192,
        CONTEXT_MAINTENANCE_ANCHOR_BUDGET_TOKENS=2048,
        TIKTOKEN_ENCODING_NAME="cl100k_base",
        OPENAI_API_BASE_URLS=[],
    )
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                config=config,
                MODELS=models_map,
            )
        )
    )


def _repeat_block(label: str, repeat: int) -> str:
    sentence = (
        f"{label}. Keep the state exact, mention constraints, and preserve source-aware details. "
    )
    return (sentence * max(1, repeat)).strip()


def _user_message(message_id: str, content: str, *, files: list[dict] | None = None) -> dict:
    message = {
        "id": message_id,
        "role": "user",
        "content": content,
    }
    if files:
        message["files"] = files
    return message


def _assistant_output_message(
    message_id: str,
    *,
    model_id: str,
    reasoning_repeat: int,
    tool_output_repeat: int,
    answer_repeat: int,
) -> dict:
    return {
        "id": message_id,
        "role": "assistant",
        "model": model_id,
        "output": [
            {
                "type": "reasoning",
                "id": f"{message_id}-r1",
                "status": "completed",
                "start_tag": "<think>",
                "end_tag": "</think>",
                "content": [
                    {
                        "type": "output_text",
                        "text": _repeat_block(
                            "Reason through the retrieved evidence and compare competing claims",
                            reasoning_repeat,
                        ),
                    }
                ],
            },
            {
                "type": "function_call",
                "id": f"{message_id}-fc1",
                "call_id": f"{message_id}-call1",
                "name": "fetch_url",
                "arguments": '{"url":"https://example.com/long-web-page"}',
                "status": "completed",
            },
            {
                "type": "function_call_output",
                "id": f"{message_id}-fco1",
                "call_id": f"{message_id}-call1",
                "status": "completed",
                "output": [
                    {
                        "type": "input_text",
                        "text": _repeat_block(
                            "Fetched web page excerpt with detailed evidence and implementation notes",
                            tool_output_repeat,
                        ),
                    }
                ],
            },
            {
                "type": "message",
                "id": f"{message_id}-msg1",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": _repeat_block(
                            "Final answer that synthesizes the evidence into an actionable recommendation",
                            answer_repeat,
                        ),
                    }
                ],
            },
        ],
    }


def _context_files() -> list[dict]:
    return [
        {
            "id": "file-spec-pdf",
            "type": "file",
            "name": "specification.pdf",
            "context": "full",
        },
        {
            "id": "web-search-batch",
            "type": "web_search",
            "name": "competitive scan",
            "context": "full",
        },
    ]


async def _evaluate_stage(
    *,
    request,
    models_map: dict[str, dict],
    active_model: dict[str, dict],
    main_model_ids: list[str],
    system_message: dict | None,
    history_messages: list[dict],
    files: list[dict] | None,
    summary_state: dict | None,
    label: str,
) -> dict:
    pressure = await inspect_context_pressure(
        request,
        model=active_model,
        form_data={
            "model": active_model["id"],
            "main_model_ids": main_model_ids,
            "files": files or [],
        },
        metadata={
            "chat_id": "local:manual-sim",
            "main_model_ids": main_model_ids,
            "files": files or [],
            "parent_message": history_messages[-1] if history_messages else None,
        },
        system_message=system_message,
        history_messages=history_messages,
        summary_state=summary_state or {},
        maintenance_enabled=True,
    )
    if pressure is None:
        raise RuntimeError("Context pressure snapshot was not produced.")

    budgets = pressure["budgets"]
    raw_request_tokens = int(
        pressure.get("raw_request_tokens")
        or pressure.get("current_request_tokens")
        or 0
    )
    maintained_request_tokens = int(
        pressure.get("maintained_request_tokens")
        or pressure.get("current_request_tokens")
        or 0
    )
    soft_budget = int(budgets["soft_request_budget"])
    hard_budget = int(budgets["hard_request_budget"])
    scheduled = should_schedule_maintenance(
        history_messages=history_messages,
        summary_state=summary_state or {},
        budgets=budgets,
        probe=pressure["probe"],
        current_request_tokens=raw_request_tokens,
    )
    forced = should_force_inline_maintenance(
        history_messages=history_messages,
        budgets=budgets,
        probe=pressure["probe"],
        current_request_tokens=raw_request_tokens,
    )
    inline_compaction = forced
    refresh_needed = is_summary_refresh_needed(history_messages, summary_state or {})

    return {
        "label": label,
        "current_request_tokens": raw_request_tokens,
        "raw_request_tokens": raw_request_tokens,
        "maintained_request_tokens": maintained_request_tokens,
        "soft_budget": soft_budget,
        "hard_budget": hard_budget,
        "live_prompt_cap": int(pressure["live_prompt_cap"]),
        "rag_reserve_tokens": int(budgets["rag_reserve_tokens"]),
        "scheduled": scheduled,
        "forced": forced,
        "inline_compaction": inline_compaction,
        "refresh_needed": refresh_needed,
        "limiting_model_id": pressure["limiting_model_id"],
        "token_count_source": pressure["token_count_source"],
        "token_count_confidence": pressure["token_count_confidence"],
    }


async def run_manual_context_maintenance_scenario() -> list[dict]:
    models_map = {
        "Qwen3.5-35B-A3B-Q8_0": {
            "id": "Qwen3.5-35B-A3B-Q8_0",
            "name": "Qwen3.5-35B-A3B-Q8_0",
            "owned_by": "openai",
            "urlIdx": 0,
            "status": {"args": ["/usr/local/bin/llama-server", "--ctx-size", "131072"]},
            "openai": {"id": "Qwen3.5-35B-A3B-Q8_0"},
        }
    }
    request = _make_request(models_map)
    active_model = models_map["Qwen3.5-35B-A3B-Q8_0"]
    main_model_ids = [active_model["id"]]
    system_message = {
        "role": "system",
        "content": _repeat_block(
            "System rules for exact state tracking and source-grounded answers",
            160,
        ),
    }

    history: list[dict] = []
    timeline: list[dict] = []

    history.append(
        _user_message(
            "u1",
            _repeat_block(
                "User asks for a long strategic comparison with many constraints",
                280,
            ),
        )
    )
    history.append(
        _assistant_output_message(
            "a1",
            model_id=active_model["id"],
            reasoning_repeat=420,
            tool_output_repeat=180,
            answer_repeat=140,
        )
    )
    timeline.append(
        await _evaluate_stage(
            request=request,
            models_map=models_map,
            active_model=active_model,
            main_model_ids=main_model_ids,
            system_message=system_message,
            history_messages=history,
            files=[],
            summary_state={},
            label="After first assistant turn",
        )
    )

    history.append(
        _user_message(
            "u2",
            _repeat_block(
                "User follows up with a broader brief and attaches local documents plus web search",
                260,
            ),
            files=_context_files(),
        )
    )
    timeline.append(
        await _evaluate_stage(
            request=request,
            models_map=models_map,
            active_model=active_model,
            main_model_ids=main_model_ids,
            system_message=system_message,
            history_messages=history,
            files=_context_files(),
            summary_state={},
            label="Before second assistant turn, with files attached",
        )
    )

    history.append(
        _assistant_output_message(
            "a2",
            model_id=active_model["id"],
            reasoning_repeat=1500,
            tool_output_repeat=1100,
            answer_repeat=420,
        )
    )
    timeline.append(
        await _evaluate_stage(
            request=request,
            models_map=models_map,
            active_model=active_model,
            main_model_ids=main_model_ids,
            system_message=system_message,
            history_messages=history,
            files=_context_files(),
            summary_state={},
            label="Immediately after second assistant turn",
        )
    )

    history.append(
        _user_message(
            "u3",
            _repeat_block(
                "User asks for one more revision while keeping all source-grounded details alive",
                180,
            ),
        )
    )
    timeline.append(
        await _evaluate_stage(
            request=request,
            models_map=models_map,
            active_model=active_model,
            main_model_ids=main_model_ids,
            system_message=system_message,
            history_messages=history,
            files=[],
            summary_state={},
            label="Before third assistant turn, no fresh files",
        )
    )

    return timeline


def format_timeline(timeline: list[dict]) -> str:
    lines = []
    for step in timeline:
        lines.append(step["label"])
        lines.append(
            "  request="
            f'{step["raw_request_tokens"]} '
            f'(maintained={step["maintained_request_tokens"]}, '
            f'soft={step["soft_budget"]}, hard={step["hard_budget"]}, cap={step["live_prompt_cap"]})'
        )
        lines.append(
            "  reserve="
            f'{step["rag_reserve_tokens"]} '
            f'| limiting_model={step["limiting_model_id"]} '
            f'| tokenizer={step["token_count_source"]}/{step["token_count_confidence"]}'
        )
        lines.append(
            "  trigger="
            f'scheduled={step["scheduled"]} '
            f'forced={step["forced"]} '
            f'inline_compaction={step["inline_compaction"]} '
            f'refresh_needed={step["refresh_needed"]}'
        )
    return "\n".join(lines)


async def _main() -> None:
    timeline = await run_manual_context_maintenance_scenario()
    print(format_timeline(timeline))


if __name__ == "__main__":
    asyncio.run(_main())
