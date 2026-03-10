from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Optional

import aiohttp

from open_webui.extensions.simon_engine.token_budget import (
    estimate_tokens_from_message,
    estimate_tokens_from_messages,
    estimate_tokens_from_text,
)
from open_webui.internal.db import get_db_context
from open_webui.models.chats import Chat, Chats
from open_webui.routers.pipelines import process_pipeline_inlet_filter
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.misc import (
    convert_output_to_messages,
    get_content_from_message,
    get_message_list,
)
from open_webui.utils.task import get_task_model_id

log = logging.getLogger(__name__)

CONTEXT_MAINTENANCE_VERSION = 1
CONTEXT_MAINTENANCE_STRATEGY = "anchor_summary_tail"
DEFAULT_CONTEXT_CAP = 32768
DEFAULT_SUMMARY_MAX_TOKENS = 1536
DEFAULT_SUMMARY_MIN_REFRESH_TOKENS = 2048
DEFAULT_SUMMARY_MIN_REFRESH_MESSAGES = 6
SOFT_PRESSURE_RATIO = 0.72
HARD_PRESSURE_RATIO = 0.85
_PROBE_TIMEOUT_SECONDS = 1.5
SUMMARY_SNAPSHOT_SECTIONS = [
    (
        "User Objectives",
        {
            "user objectives",
            "user objective",
            "goals",
            "goal",
            "objectives",
            "objective",
        },
    ),
    (
        "Constraints and Preferences",
        {
            "constraints and preferences",
            "constraints",
            "constraint",
            "preferences",
            "preferences and constraints",
        },
    ),
    (
        "Decisions and Conclusions",
        {
            "decisions and conclusions",
            "decisions",
            "decision",
            "conclusions",
            "conclusion",
        },
    ),
    (
        "Open Questions and Unresolved Work",
        {
            "open questions and unresolved work",
            "open questions",
            "unresolved work",
            "open work",
            "pending work",
        },
    ),
    (
        "Stable Facts and Assumptions",
        {
            "stable facts and assumptions",
            "stable facts",
            "facts and assumptions",
            "system assumptions",
            "assumptions",
            "facts",
        },
    ),
]

_ACTIVE_MAINTENANCE_JOBS: set[str] = set()
_ACTIVE_MAINTENANCE_LOCK = asyncio.Lock()


def parse_ctx_size_from_args(args: list[Any] | None) -> Optional[int]:
    if not isinstance(args, list):
        return None

    tokens = [str(arg) for arg in args]
    for idx, token in enumerate(tokens):
        if token in {"--ctx-size", "-c"} and idx + 1 < len(tokens):
            try:
                return int(tokens[idx + 1])
            except Exception:
                continue

        match = re.match(r"^(?:--ctx-size|-c)=(\d+)$", token)
        if match:
            try:
                return int(match.group(1))
            except Exception:
                continue

    return None


def extract_model_ctx_cap(model: dict[str, Any]) -> Optional[int]:
    candidates = [
        model.get("status", {}).get("args"),
        model.get("openai", {}).get("status", {}).get("args"),
    ]
    for args in candidates:
        ctx = parse_ctx_size_from_args(args)
        if ctx:
            return ctx
    return None


def parse_prometheus_metrics(text: str) -> dict[str, float]:
    metrics: dict[str, float] = {}
    if not text:
        return metrics

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        name, value = parts[0], parts[-1]
        if name not in {
            "llamacpp:kv_cache_usage_ratio",
            "llamacpp:kv_cache_tokens",
        }:
            continue

        try:
            metrics[name] = float(value)
        except Exception:
            continue

    return metrics


def extract_n_ctx_from_props(props: dict[str, Any] | None) -> Optional[int]:
    if not isinstance(props, dict):
        return None

    candidates = [
        props.get("n_ctx"),
        props.get("default_generation_settings", {})
        .get("params", {})
        .get("n_ctx"),
    ]

    for value in candidates:
        if value is None:
            continue
        try:
            return int(value)
        except Exception:
            continue

    return None


def estimate_tokens_from_history_message(message: dict[str, Any]) -> int:
    if not isinstance(message, dict):
        return 0

    if isinstance(message.get("output"), list):
        return estimate_tokens_from_messages(
            convert_output_to_messages(message["output"], raw=True)
        )

    return estimate_tokens_from_message(message)


def estimate_tokens_from_history_messages(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens_from_history_message(message) for message in messages or [])


def history_message_to_llm_messages(message: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(message.get("output"), list):
        return convert_output_to_messages(message["output"], raw=True)

    clean_message = {
        key: value
        for key, value in message.items()
        if key not in {"id", "parentId", "childrenIds", "files"}
    }
    return [clean_message]


def flatten_history_message_content(message: dict[str, Any]) -> str:
    if isinstance(message.get("output"), list):
        rendered: list[str] = []
        for item in convert_output_to_messages(message["output"], raw=True):
            role = item.get("role", "assistant")
            content = get_content_from_message(item) or ""
            if item.get("tool_calls"):
                names = ", ".join(
                    call.get("function", {}).get("name", "")
                    for call in item["tool_calls"]
                    if isinstance(call, dict)
                )
                if names:
                    content = f"{content}\nTool calls: {names}".strip()
            if content:
                rendered.append(f"{role}: {content}")
        return "\n".join(rendered).strip()

    content = get_content_from_message(message) or ""
    role = message.get("role", "assistant")
    return f"{role}: {content}".strip() if content else ""


def normalize_history_message(
    message: dict[str, Any],
    *,
    include_files: bool = True,
) -> dict[str, Any]:
    data = dict(message)
    if not include_files:
        data.pop("files", None)
    return data


def inject_image_files_into_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages or []:
        item = normalize_history_message(message)
        image_files = [
            f
            for f in item.get("files", [])
            if f.get("type") == "image"
            or (f.get("content_type") or "").startswith("image/")
        ]
        if item.get("role") == "user" and image_files:
            text_content = item.get("content", "")
            if isinstance(text_content, str):
                item["content"] = [
                    {"type": "text", "text": text_content},
                    *[
                        {"type": "image_url", "image_url": {"url": f["url"]}}
                        for f in image_files
                        if f.get("url")
                    ],
                ]
        item.pop("files", None)
        normalized.append(item)
    return normalized


def select_anchor_messages(
    history_messages: list[dict[str, Any]], anchor_budget_tokens: int
) -> list[dict[str, Any]]:
    remaining = max(0, int(anchor_budget_tokens))
    anchors: list[dict[str, Any]] = []
    seen_user = False
    seen_assistant_after_user = False

    for message in history_messages:
        role = str(message.get("role") or "")
        cost = estimate_tokens_from_history_message(message)

        if anchors and remaining - cost < 0:
            break

        anchors.append(message)
        remaining -= cost

        if role == "user" and not seen_user:
            seen_user = True
            continue

        if role == "assistant" and seen_user and not seen_assistant_after_user:
            seen_assistant_after_user = True
            continue

        if role == "user" and seen_assistant_after_user:
            break

    return anchors


def select_tail_messages(
    history_messages: list[dict[str, Any]],
    *,
    anchor_count: int,
    tail_budget_tokens: int,
) -> list[dict[str, Any]]:
    remaining = max(0, int(tail_budget_tokens))
    selected: list[dict[str, Any]] = []

    for message in reversed(history_messages[anchor_count:]):
        cost = estimate_tokens_from_history_message(message)
        if selected and remaining - cost < 0:
            break
        selected.insert(0, message)
        remaining -= cost

    return selected


def resolve_summary_boundary(
    history_messages: list[dict[str, Any]],
    *,
    anchor_count: int,
    tail_count: int,
) -> Optional[int]:
    boundary = len(history_messages) - tail_count - 1
    if boundary < anchor_count:
        return None
    return boundary


def build_summary_message(summary_text: str) -> dict[str, Any]:
    return {
        "role": "system",
        "content": (
            "Conversation state snapshot for earlier turns. Preserve the objectives, "
            "constraints, decisions, unresolved work, and stable facts below when "
            "continuing.\n\n"
            f"{summary_text.strip()}"
        ),
    }


def merge_system_message(
    system_message: dict[str, Any] | None,
    appended_blocks: list[str] | None = None,
) -> dict[str, Any] | None:
    blocks = [str(block).strip() for block in (appended_blocks or []) if str(block).strip()]
    if not system_message and not blocks:
        return None

    if system_message:
        merged = dict(system_message)
        base_content = str(merged.get("content") or "").strip()
        additions = "\n\n".join(blocks).strip()
        merged["content"] = (
            f"{base_content}\n\n{additions}".strip() if additions else base_content
        )
        merged["role"] = "system"
        return merged

    return {
        "role": "system",
        "content": "\n\n".join(blocks).strip(),
    }


def build_summary_source_text(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages or []:
        text = flatten_history_message_content(message)
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def build_summary_prompt(
    *,
    transcript: str,
    max_tokens: int,
) -> str:
    bounded_transcript = transcript
    transcript_budget = max(4096, max_tokens * 12)
    if estimate_tokens_from_text(bounded_transcript) > transcript_budget:
        chars = transcript_budget * 4
        bounded_transcript = bounded_transcript[-chars:]

    return (
        "Convert the earlier conversation into a structured state snapshot that can "
        "replace the original turns in future prompts.\n"
        "Return exactly these sections in order, each with concise bullet points:\n"
        "User Objectives:\n"
        "Constraints and Preferences:\n"
        "Decisions and Conclusions:\n"
        "Open Questions and Unresolved Work:\n"
        "Stable Facts and Assumptions:\n"
        "Preserve durable information only.\n"
        "Do not restate temporary retrieval excerpts unless they became lasting facts.\n"
        "Do not write a narrative summary.\n"
        f"Keep the result concise and under roughly {max_tokens} tokens.\n\n"
        "Conversation:\n"
        f"{bounded_transcript}"
    )


def _normalize_summary_heading(heading: str) -> Optional[str]:
    normalized = re.sub(r"[^a-z0-9 ]+", " ", heading.lower()).strip()
    normalized = re.sub(r"\s+", " ", normalized)
    for canonical, aliases in SUMMARY_SNAPSHOT_SECTIONS:
        if normalized in aliases:
            return canonical
    return None


def normalize_summary_snapshot(summary_text: str) -> str:
    raw = str(summary_text or "").strip()
    if not raw:
        return ""

    sections = {canonical: [] for canonical, _ in SUMMARY_SNAPSHOT_SECTIONS}
    current_section: Optional[str] = None
    matched_heading = False

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        heading_match = re.match(r"^([A-Za-z][A-Za-z /&_-]{1,80}):\s*(.*)$", stripped)
        if heading_match:
            canonical = _normalize_summary_heading(heading_match.group(1))
            if canonical:
                matched_heading = True
                current_section = canonical
                remainder = heading_match.group(2).strip()
                if remainder:
                    sections[canonical].append(remainder)
                continue

        if current_section:
            sections[current_section].append(stripped)

    if not matched_heading:
        sections["Stable Facts and Assumptions"].append(raw)

    blocks: list[str] = []
    for canonical, _ in SUMMARY_SNAPSHOT_SECTIONS:
        values = sections[canonical]
        if values:
            formatted = [
                value if value.startswith("- ") else f"- {value.lstrip('- ').strip()}"
                for value in values
                if value.strip()
            ]
        else:
            formatted = ["- None recorded."]

        blocks.append(f"{canonical}:\n" + "\n".join(formatted))

    return "\n\n".join(blocks)


def summarize_history_growth(
    history_messages: list[dict[str, Any]],
    summarized_through_message_id: Optional[str],
) -> tuple[int, int]:
    if not summarized_through_message_id:
        return estimate_tokens_from_history_messages(history_messages), len(history_messages)

    seen_boundary = False
    growth_messages: list[dict[str, Any]] = []

    for message in history_messages:
        if seen_boundary:
            growth_messages.append(message)
        elif str(message.get("id")) == str(summarized_through_message_id):
            seen_boundary = True

    return estimate_tokens_from_history_messages(growth_messages), len(growth_messages)


def is_summary_refresh_needed(
    history_messages: list[dict[str, Any]],
    maintenance_state: dict[str, Any] | None,
) -> bool:
    state = maintenance_state or {}
    summary_text = str(state.get("summary_text") or "").strip()
    if not summary_text:
        return True

    growth_tokens, growth_messages = summarize_history_growth(
        history_messages, state.get("summarized_through_message_id")
    )
    return (
        growth_tokens >= DEFAULT_SUMMARY_MIN_REFRESH_TOKENS
        or growth_messages >= DEFAULT_SUMMARY_MIN_REFRESH_MESSAGES
    )


def build_request_messages(
    *,
    system_message: dict[str, Any] | None,
    anchor_messages: list[dict[str, Any]],
    summary_text: str | None,
    tail_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    merged_system_message = merge_system_message(
        system_message,
        [build_summary_message(summary_text)["content"]] if summary_text else [],
    )
    if merged_system_message:
        messages.append(merged_system_message)

    for message in anchor_messages:
        messages.extend(history_message_to_llm_messages(message))

    for message in tail_messages:
        messages.extend(history_message_to_llm_messages(message))

    return messages


def trim_messages_to_budget(
    *,
    system_message: dict[str, Any] | None,
    anchor_messages: list[dict[str, Any]],
    summary_text: str | None,
    tail_messages: list[dict[str, Any]],
    hard_history_budget: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    trimmed_tail = list(tail_messages)

    while True:
        request_messages = build_request_messages(
            system_message=system_message,
            anchor_messages=anchor_messages,
            summary_text=summary_text,
            tail_messages=trimmed_tail,
        )
        if estimate_tokens_from_messages(request_messages) <= hard_history_budget:
            return request_messages, trimmed_tail

        if not trimmed_tail:
            return request_messages, trimmed_tail

        trimmed_tail.pop(0)


def get_chat_maintenance_state(chat_id: str) -> dict[str, Any]:
    if not chat_id or chat_id.startswith("local:"):
        return {}

    chat = Chats.get_chat_by_id(chat_id)
    if not chat:
        return {}

    return dict((chat.meta or {}).get("context_maintenance") or {})


def save_chat_maintenance_state(chat_id: str, state: dict[str, Any]) -> None:
    if not chat_id or chat_id.startswith("local:"):
        return

    with get_db_context() as db:
        chat = db.get(Chat, chat_id)
        if not chat:
            return

        meta = dict(chat.meta or {})
        meta["context_maintenance"] = state
        chat.meta = meta
        chat.updated_at = int(time.time())
        db.commit()


def clear_chat_maintenance_state(chat_id: str) -> None:
    if not chat_id or chat_id.startswith("local:"):
        return

    with get_db_context() as db:
        chat = db.get(Chat, chat_id)
        if not chat:
            return

        meta = dict(chat.meta or {})
        if "context_maintenance" in meta:
            del meta["context_maintenance"]
            chat.meta = meta
            chat.updated_at = int(time.time())
            db.commit()


async def load_llamacpp_probe(
    request,
    model: dict[str, Any],
) -> dict[str, Any]:
    result = {
        "source": "estimate",
        "kv_cache_usage_ratio": None,
        "kv_cache_tokens": None,
        "n_ctx": extract_model_ctx_cap(model),
    }

    if model.get("owned_by") != "openai":
        return result

    url_idx = model.get("urlIdx")
    base_urls = getattr(request.app.state.config, "OPENAI_API_BASE_URLS", []) or []
    if url_idx is None or url_idx >= len(base_urls):
        return result

    base_url = str(base_urls[url_idx]).rstrip("/")
    if not base_url:
        return result

    model_id = model.get("openai", {}).get("id") or model.get("id")
    timeout = aiohttp.ClientTimeout(total=_PROBE_TIMEOUT_SECONDS)

    try:
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            try:
                async with session.get(
                    f"{base_url}/metrics", params={"model": model_id}
                ) as response:
                    if response.status == 200:
                        metrics = parse_prometheus_metrics(await response.text())
                        if metrics:
                            result["source"] = "metrics"
                            result["kv_cache_usage_ratio"] = metrics.get(
                                "llamacpp:kv_cache_usage_ratio"
                            )
                            result["kv_cache_tokens"] = metrics.get(
                                "llamacpp:kv_cache_tokens"
                            )
            except Exception:
                pass

            if result["n_ctx"] is None:
                try:
                    async with session.get(
                        f"{base_url}/props", params={"model": model_id}
                    ) as response:
                        if response.status == 200:
                            props = await response.json()
                            ctx = extract_n_ctx_from_props(props)
                            if ctx:
                                result["n_ctx"] = ctx
                except Exception:
                    pass

            try:
                async with session.get(
                    f"{base_url}/slots", params={"model": model_id}
                ) as response:
                    if response.status == 200:
                        slots = await response.json()
                        if isinstance(slots, list) and slots:
                            result["source"] = (
                                result["source"] if result["source"] == "metrics" else "slots"
                            )
                            if result["n_ctx"] is None:
                                slot_ctxs = [
                                    int(slot.get("n_ctx"))
                                    for slot in slots
                                    if slot.get("n_ctx")
                                ]
                                if slot_ctxs:
                                    result["n_ctx"] = max(slot_ctxs)
            except Exception:
                pass
    except Exception:
        return result

    return result


def resolve_live_prompt_cap(
    request,
    model: dict[str, Any],
    probe: dict[str, Any] | None = None,
) -> tuple[int, str]:
    app_config = request.app.state.config
    configured_cap = getattr(app_config, "CONTEXT_MAINTENANCE_MAX_CTX_CAP", None)
    provider_cap = extract_model_ctx_cap(model)
    probed_cap = (probe or {}).get("n_ctx")

    source = "default"
    live_cap = DEFAULT_CONTEXT_CAP

    if probed_cap:
        try:
            live_cap = int(probed_cap)
            probe_source = str((probe or {}).get("source") or "probe")
            source = f"probe:{probe_source}"
        except Exception:
            pass
    elif provider_cap:
        try:
            live_cap = int(provider_cap)
            source = "model_args"
        except Exception:
            pass

    if configured_cap:
        try:
            live_cap = min(live_cap, int(configured_cap))
            source = f"{source}+admin_cap"
        except Exception:
            pass

    return max(4096, int(live_cap)), source


def resolve_effective_ctx_cap(
    request,
    model: dict[str, Any],
    probe: dict[str, Any] | None = None,
) -> int:
    live_cap, _ = resolve_live_prompt_cap(request, model, probe)
    return live_cap


def resolve_history_budgets(
    request,
    *,
    model: dict[str, Any],
    form_data: dict[str, Any],
    metadata: dict[str, Any],
    probe: dict[str, Any] | None = None,
) -> dict[str, int]:
    config = request.app.state.config
    live_prompt_cap, live_prompt_cap_source = resolve_live_prompt_cap(
        request, model, probe
    )
    output_reserve = max(
        int(form_data.get("max_tokens") or 0),
        int(getattr(config, "CONTEXT_MAINTENANCE_OUTPUT_RESERVE_TOKENS", 8192)),
    )
    safety_reserve = int(
        getattr(config, "CONTEXT_MAINTENANCE_SAFETY_RESERVE_TOKENS", 4096)
    )
    rag_default = int(
        getattr(config, "CONTEXT_MAINTENANCE_RAG_RESERVE_TOKENS", 12288)
    )
    soft_margin = int(
        getattr(config, "CONTEXT_MAINTENANCE_SOFT_MARGIN_TOKENS", 8192)
    )
    anchor_budget = int(
        getattr(config, "CONTEXT_MAINTENANCE_ANCHOR_BUDGET_TOKENS", 2048)
    )

    has_rag = bool(form_data.get("files") or metadata.get("files"))
    rag_reserve = rag_default if has_rag else 2048
    hard_history_budget = max(
        1024, live_prompt_cap - output_reserve - safety_reserve - rag_reserve
    )
    soft_history_budget = max(512, hard_history_budget - soft_margin)

    return {
        "live_prompt_cap": live_prompt_cap,
        "live_prompt_cap_source": live_prompt_cap_source,
        "hot_context_target_tokens": hard_history_budget,
        "effective_ctx_cap": live_prompt_cap,
        "output_reserve_tokens": output_reserve,
        "safety_reserve_tokens": safety_reserve,
        "rag_reserve_tokens": rag_reserve,
        "soft_margin_tokens": soft_margin,
        "hard_history_budget": hard_history_budget,
        "soft_history_budget": soft_history_budget,
        "anchor_budget_tokens": anchor_budget,
    }


def should_schedule_maintenance(
    *,
    history_messages: list[dict[str, Any]],
    summary_state: dict[str, Any] | None,
    budgets: dict[str, int],
    probe: dict[str, Any] | None,
) -> bool:
    history_tokens = estimate_tokens_from_history_messages(history_messages)
    kv_ratio = (probe or {}).get("kv_cache_usage_ratio")
    if kv_ratio is not None and kv_ratio >= SOFT_PRESSURE_RATIO:
        return True
    if history_tokens >= budgets["soft_history_budget"]:
        return True
    if summary_state and is_summary_refresh_needed(history_messages, summary_state):
        return history_tokens >= max(
            budgets["soft_history_budget"] - DEFAULT_SUMMARY_MIN_REFRESH_TOKENS,
            1024,
        )
    return False


def should_force_inline_maintenance(
    *,
    history_messages: list[dict[str, Any]],
    budgets: dict[str, int],
    probe: dict[str, Any] | None,
) -> bool:
    history_tokens = estimate_tokens_from_history_messages(history_messages)
    kv_ratio = (probe or {}).get("kv_cache_usage_ratio")
    return bool(
        (kv_ratio is not None and kv_ratio >= HARD_PRESSURE_RATIO)
        or history_tokens >= budgets["hard_history_budget"]
    )


def _build_summary_state(
    *,
    summary_text: str,
    summarized_through_message_id: str,
    anchor_message_ids: list[str],
    source_message_count: int,
) -> dict[str, Any]:
    return {
        "version": CONTEXT_MAINTENANCE_VERSION,
        "strategy": CONTEXT_MAINTENANCE_STRATEGY,
        "summary_text": summary_text,
        "summarized_through_message_id": summarized_through_message_id,
        "anchor_message_ids": anchor_message_ids,
        "updated_at": int(time.time()),
        "source_message_count": int(source_message_count),
    }


async def generate_history_summary(
    request,
    *,
    user,
    model_id: str,
    chat_id: str | None,
    history_messages: list[dict[str, Any]],
    max_tokens: int = DEFAULT_SUMMARY_MAX_TOKENS,
) -> Optional[str]:
    transcript = build_summary_source_text(history_messages)
    if not transcript:
        return None

    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {request.state.model["id"]: request.state.model}
    else:
        models = request.app.state.MODELS

    if model_id not in models:
        return None

    task_model_id = get_task_model_id(
        model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    payload = {
        "model": task_model_id,
        "messages": [
            {
                "role": "user",
                "content": build_summary_prompt(
                    transcript=transcript,
                    max_tokens=max_tokens,
                ),
            }
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": "context_maintenance",
            "chat_id": chat_id,
        },
    }

    try:
        payload = await process_pipeline_inlet_filter(request, payload, user, models)
        response = await generate_chat_completion(request, form_data=payload, user=user)
    except Exception as exc:
        log.warning("Context summary generation failed: %s", exc)
        return None

    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            summary = message.get("content") or message.get("reasoning_content")
            if isinstance(summary, str) and summary.strip():
                return normalize_summary_snapshot(summary)
    return None


def _resolve_anchor_ids_from_messages(messages: list[dict[str, Any]]) -> list[str]:
    return [str(message.get("id")) for message in messages if message.get("id")]


def build_context_maintenance_payload(
    *,
    system_message: dict[str, Any] | None,
    history_messages: list[dict[str, Any]],
    summary_state: dict[str, Any] | None,
    budgets: dict[str, int],
) -> dict[str, Any]:
    anchors = select_anchor_messages(history_messages, budgets["anchor_budget_tokens"])
    anchor_ids = _resolve_anchor_ids_from_messages(anchors)
    summary_text = None
    tail_messages: list[dict[str, Any]] = list(history_messages[len(anchors) :])
    used_summary_state = False

    state = summary_state or {}
    state_summary = str(state.get("summary_text") or "").strip()
    boundary_id = state.get("summarized_through_message_id")

    if state_summary and boundary_id:
        boundary_index = next(
            (
                idx
                for idx, message in enumerate(history_messages)
                if str(message.get("id")) == str(boundary_id)
            ),
            None,
        )
        if boundary_index is not None and boundary_index >= len(anchors) - 1:
            summary_text = state_summary
            tail_messages = history_messages[boundary_index + 1 :]
            used_summary_state = True

    request_messages, trimmed_tail = trim_messages_to_budget(
        system_message=system_message,
        anchor_messages=anchors,
        summary_text=summary_text,
        tail_messages=tail_messages,
        hard_history_budget=budgets["hard_history_budget"],
    )

    system_tokens = (
        estimate_tokens_from_messages([system_message]) if system_message else 0
    )
    anchor_tokens = estimate_tokens_from_history_messages(anchors)
    summary_tokens = estimate_tokens_from_text(summary_text or "")
    tail_tokens = estimate_tokens_from_history_messages(trimmed_tail)
    request_tokens = estimate_tokens_from_messages(request_messages)

    return {
        "messages": request_messages,
        "anchor_messages": anchors,
        "anchor_message_ids": anchor_ids,
        "tail_messages": trimmed_tail,
        "summary_text": summary_text,
        "used_summary_state": used_summary_state,
        "telemetry": {
            "live_prompt_cap": budgets.get("live_prompt_cap"),
            "live_prompt_cap_source": budgets.get("live_prompt_cap_source"),
            "hot_context_target_tokens": budgets.get(
                "hot_context_target_tokens", budgets.get("hard_history_budget")
            ),
            "hard_history_budget": budgets.get("hard_history_budget"),
            "soft_history_budget": budgets.get("soft_history_budget"),
            "system_tokens": system_tokens,
            "anchor_tokens": anchor_tokens,
            "summary_tokens": summary_tokens,
            "tail_tokens": tail_tokens,
            "request_tokens": request_tokens,
            "anchor_message_count": len(anchors),
            "tail_message_count": len(trimmed_tail),
            "summary_included": bool(summary_text),
        },
    }


async def build_inline_maintained_messages(
    request,
    *,
    user,
    model: dict[str, Any],
    form_data: dict[str, Any],
    metadata: dict[str, Any],
    system_message: dict[str, Any] | None,
    history_messages: list[dict[str, Any]],
    summary_state: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    probe = await load_llamacpp_probe(request, model)
    budgets = resolve_history_budgets(
        request, model=model, form_data=form_data, metadata=metadata, probe=probe
    )

    payload = build_context_maintenance_payload(
        system_message=system_message,
        history_messages=history_messages,
        summary_state=summary_state,
        budgets=budgets,
    )
    result = {
        "probe": probe,
        "budgets": budgets,
        "used_summary_state": payload["used_summary_state"],
        "summary_refreshed": False,
        "fallback_used": False,
        "telemetry": dict(payload.get("telemetry") or {}),
    }

    if not should_force_inline_maintenance(
        history_messages=history_messages, budgets=budgets, probe=probe
    ):
        return payload["messages"], result

    tail_budget = max(
        512,
        budgets["hard_history_budget"]
        - estimate_tokens_from_messages([system_message] if system_message else [])
        - estimate_tokens_from_history_messages(payload["anchor_messages"])
        - DEFAULT_SUMMARY_MAX_TOKENS,
    )
    tail_messages = select_tail_messages(
        history_messages,
        anchor_count=len(payload["anchor_messages"]),
        tail_budget_tokens=tail_budget,
    )
    boundary_index = resolve_summary_boundary(
        history_messages,
        anchor_count=len(payload["anchor_messages"]),
        tail_count=len(tail_messages),
    )

    summary_text = None
    if boundary_index is not None:
        summary_source = history_messages[: boundary_index + 1]
        summary_text = await generate_history_summary(
            request,
            user=user,
            model_id=form_data["model"],
            chat_id=metadata.get("chat_id"),
            history_messages=summary_source,
            max_tokens=DEFAULT_SUMMARY_MAX_TOKENS,
        )

        if summary_text and metadata.get("chat_id") and summary_source:
            state = _build_summary_state(
                summary_text=summary_text,
                summarized_through_message_id=str(summary_source[-1].get("id")),
                anchor_message_ids=payload["anchor_message_ids"],
                source_message_count=len(summary_source),
            )
            save_chat_maintenance_state(metadata["chat_id"], state)
            result["summary_refreshed"] = True

    request_messages, trimmed_tail = trim_messages_to_budget(
        system_message=system_message,
        anchor_messages=payload["anchor_messages"],
        summary_text=summary_text,
        tail_messages=tail_messages,
        hard_history_budget=budgets["hard_history_budget"],
    )

    if not summary_text:
        result["fallback_used"] = True
        request_messages, trimmed_tail = trim_messages_to_budget(
            system_message=system_message,
            anchor_messages=payload["anchor_messages"],
            summary_text=None,
            tail_messages=history_messages[len(payload["anchor_messages"]) :],
            hard_history_budget=budgets["hard_history_budget"],
        )

    result["telemetry"].update(
        {
            "summary_refreshed": result["summary_refreshed"],
            "fallback_used": result["fallback_used"],
            "used_summary_state": result["used_summary_state"],
            "request_tokens": estimate_tokens_from_messages(request_messages),
            "tail_tokens": estimate_tokens_from_history_messages(trimmed_tail),
            "tail_message_count": len(trimmed_tail),
            "summary_tokens": estimate_tokens_from_text(summary_text or ""),
            "summary_included": bool(summary_text),
        }
    )

    return request_messages, result


async def emit_context_status(event_emitter, description: str, *, done: bool) -> None:
    if not event_emitter:
        return
    try:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "context_maintenance",
                    "description": description,
                    "done": done,
                },
            }
        )
    except Exception:
        pass


async def run_background_context_maintenance(
    *,
    request,
    user,
    model: dict[str, Any],
    chat_id: str,
    message_id: str,
    event_emitter,
) -> None:
    job_key = f"{chat_id}:{message_id}"
    async with _ACTIVE_MAINTENANCE_LOCK:
        if job_key in _ACTIVE_MAINTENANCE_JOBS:
            return
        _ACTIVE_MAINTENANCE_JOBS.add(job_key)

    try:
        messages_map = Chats.get_messages_map_by_chat_id(chat_id) or {}
        history_messages = inject_image_files_into_history(
            get_message_list(messages_map, message_id)
        )
        history_messages = [
            message
            for message in history_messages
            if message.get("role") in {"user", "assistant", "tool"}
        ]
        if not history_messages:
            return

        probe = await load_llamacpp_probe(request, model)
        budgets = resolve_history_budgets(
            request,
            model=model,
            form_data={"model": model["id"]},
            metadata={"chat_id": chat_id},
            probe=probe,
        )

        state = get_chat_maintenance_state(chat_id)
        if not should_schedule_maintenance(
            history_messages=history_messages,
            summary_state=state,
            budgets=budgets,
            probe=probe,
        ) or not is_summary_refresh_needed(history_messages, state):
            return

        await emit_context_status(
            event_emitter, "Context maintenance scheduled", done=False
        )
        await emit_context_status(
            event_emitter, "Condensing earlier turns...", done=False
        )

        anchors = select_anchor_messages(history_messages, budgets["anchor_budget_tokens"])
        tail_budget = max(
            512,
            budgets["hard_history_budget"]
            - estimate_tokens_from_history_messages(anchors)
            - DEFAULT_SUMMARY_MAX_TOKENS,
        )
        tail_messages = select_tail_messages(
            history_messages,
            anchor_count=len(anchors),
            tail_budget_tokens=tail_budget,
        )
        boundary_index = resolve_summary_boundary(
            history_messages,
            anchor_count=len(anchors),
            tail_count=len(tail_messages),
        )
        if boundary_index is None:
            return

        summary_source = history_messages[: boundary_index + 1]
        summary_text = await generate_history_summary(
            request,
            user=user,
            model_id=model["id"],
            chat_id=chat_id,
            history_messages=summary_source,
            max_tokens=DEFAULT_SUMMARY_MAX_TOKENS,
        )
        if not summary_text:
            await emit_context_status(
                event_emitter,
                "Context maintenance failed; using recent context only",
                done=True,
            )
            return

        latest_chat = Chats.get_chat_by_id(chat_id)
        current_id = (
            latest_chat.chat.get("history", {}).get("currentId") if latest_chat else None
        )
        if str(current_id) != str(message_id):
            return

        save_chat_maintenance_state(
            chat_id,
            _build_summary_state(
                summary_text=summary_text,
                summarized_through_message_id=str(summary_source[-1].get("id")),
                anchor_message_ids=_resolve_anchor_ids_from_messages(anchors),
                source_message_count=len(summary_source),
            ),
        )

        await emit_context_status(
            event_emitter, "Condensing earlier turns...", done=True
        )
    except Exception as exc:
        log.warning("Background context maintenance failed: %s", exc)
        await emit_context_status(
            event_emitter,
            "Context maintenance failed; using recent context only",
            done=True,
        )
    finally:
        async with _ACTIVE_MAINTENANCE_LOCK:
            _ACTIVE_MAINTENANCE_JOBS.discard(job_key)
