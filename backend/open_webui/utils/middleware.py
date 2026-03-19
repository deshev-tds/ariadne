import copy
import time
import logging
import sys
import os
import base64
import io
import mimetypes
import textwrap

import asyncio
from aiocache import cached
from typing import Any, Optional
import random
import json
import html
import inspect
import re
import ast
import math
import hashlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor


from fastapi import Request, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from starlette.responses import Response, StreamingResponse, JSONResponse


from open_webui.utils.misc import is_string_allowed
from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.models.users import Users
from open_webui.socket.main import (
    get_event_call,
    get_event_emitter,
)
from open_webui.routers.tasks import (
    generate_queries,
    generate_title,
    generate_follow_ups,
    generate_image_prompt,
    generate_chat_tags,
)
from open_webui.routers.retrieval import (
    process_web_search,
    SearchForm,
)
from open_webui.utils.tools import get_builtin_tools
from open_webui.routers.images import (
    image_generations,
    CreateImageForm,
    image_edits,
    EditImageForm,
)
from open_webui.routers.pipelines import (
    process_pipeline_inlet_filter,
    process_pipeline_outlet_filter,
)
from open_webui.routers.memories import query_memory, QueryMemoryForm

from open_webui.utils.webhook import post_webhook
from open_webui.utils.files import (
    convert_markdown_base64_images,
    get_file_url_from_base64,
    get_image_base64_from_url,
    get_image_url_from_base64,
)


from open_webui.models.users import UserModel
from open_webui.models.functions import Functions
from open_webui.models.models import Models

from open_webui.retrieval.utils import get_sources_from_items
from open_webui.retrieval.web.planner import (
    PLANNER_MODES,
    build_planned_queries_from_rewriter,
    build_base_planned_queries,
    build_rewriter_prompt,
    build_web_search_plan,
    load_normalized_source_registry,
    normalize_domain,
    parse_rewriter_output,
    validate_or_repair_rewriter_queries,
)


from open_webui.utils.sanitize import sanitize_code
from open_webui.utils.chat import generate_chat_completion
from open_webui.utils.context_maintenance import (
    build_inline_maintained_messages,
    get_chat_maintenance_state,
    inject_image_files_into_history,
    run_background_context_maintenance,
)
from open_webui.utils.chat_recall import (
    enqueue_branch_backfill,
    extract_branch_message_ids,
    maybe_apply_chat_recall,
    resolve_chat_recall_enabled,
)
from open_webui.utils.ledger import (
    maybe_apply_ledger,
    run_background_ledger_capture,
)
from open_webui.utils.task import (
    BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
    BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
    BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
    BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_REWRITER,
    RUNTIME_TIMESTAMP_MARKER,
    append_runtime_temporal_grounding,
    get_bounded_specialist_model_selection,
    get_task_model_id,
    query_generation_template,
    rag_template,
    tools_function_calling_generation_template,
)
from open_webui.utils.misc import (
    deep_update,
    extract_urls,
    get_message_list,
    add_or_update_system_message,
    add_or_update_user_message,
    set_last_user_message_content,
    get_last_user_message,
    get_last_user_message_item,
    get_last_assistant_message,
    get_system_message,
    replace_system_message_content,
    prepend_to_first_user_message_content,
    convert_logit_bias_input_to_json,
    get_content_from_message,
    convert_output_to_messages,
)
from open_webui.utils.tools import (
    get_tools,
    get_updated_tool_function,
    get_terminal_tools,
)
from open_webui.utils.access_control import has_connection_access, has_permission
from open_webui.utils.plugin import load_function_module_by_id
from open_webui.utils.filter import (
    get_sorted_filter_ids,
    process_filter_functions,
)
from open_webui.utils.code_interpreter import execute_code_jupyter
from open_webui.utils.payload import apply_system_prompt_to_body
from open_webui.utils.prompt_telemetry import (
    get_prompt_telemetry,
    is_prompt_telemetry_enabled,
)
from open_webui.utils.runtime_telemetry import runtime_telemetry
from open_webui.utils.response import normalize_usage
from open_webui.utils.mcp.client import MCPClient
from open_webui.utils.deep_research import (
    LocalDeepResearchAuthError,
    LocalDeepResearchClient,
    LocalDeepResearchError,
)
from open_webui.routers.files import upload_file_handler
from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode


from open_webui.config import (
    CACHE_DIR,
    DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE,
    DEFAULT_VOICE_MODE_PROMPT_TEMPLATE,
    DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE,
    DEFAULT_CODE_INTERPRETER_PROMPT,
    CODE_INTERPRETER_PYODIDE_PROMPT,
    CODE_INTERPRETER_BLOCKED_MODULES,
)

LOCAL_CORPUS_PREFER_SYSTEM_PROMPT = (
    "This chat prefers the local corpus when it is compatible with the question. "
    "When local corpus tools are available, prefer grounded local evidence over unsupported "
    "answering from model weights alone. Before your first local corpus tool call, write a brief "
    "orientation preamble of 1 to 3 short sentences stating what you are about to check and how "
    "you will approach it. Do not answer the substance yet, and do not present conclusions before "
    "checking the local corpus. When calling local corpus framing or retrieval tools, preserve the user's "
    "substantive topic terms. Do not rewrite them into vague advisory phrasing such as 'what should I think "
    "about' or similar filler. A light touch of warmth or humor is allowed only when the topic is "
    "low-stakes and the user's tone clearly invites it. If local corpus evidence is weak or unavailable, "
    "say so plainly."
)
TOOL_NARRATION_SYSTEM_PROMPT = (
    "For compatible tool-heavy runs, you may give the user brief journey updates in the assistant text. "
    "When entering the first major tool phase, you may begin with a short orientation preamble. After that, "
    "only narrate meaningful phase changes, not every tool call. Keep each update to 1 to 3 short sentences, "
    "stay high-level and user-facing, and do not restate tool names or raw status labels. If you call framing "
    "or retrieval tools, preserve the user's substantive topic terms instead of restating the question as vague "
    "advice-seeking filler. Warmth is allowed. Humor is optional and only when the topic is low-stakes and "
    "the user's tone clearly invites it."
)
TOOL_NARRATION_MAX_BEATS = 3
TOOL_NARRATION_PHASE_ORDER = {
    "orientation": 1,
    "planning": 2,
    "evidence_gathering": 3,
    "evidence_check": 4,
    "final_response": 5,
}
TOOL_NARRATION_TOOL_PHASES = {
    "local_corpus_list_domains": "orientation",
    "local_corpus_list_disciplines": "orientation",
    "local_corpus_frame_problem": "orientation",
    "local_corpus_plan_axes": "planning",
    "local_corpus_shortlist_books": "planning",
    "local_corpus_view_book_cards": "planning",
    "local_corpus_collect_axis_evidence": "evidence_gathering",
    "local_corpus_retrieve_evidence": "evidence_gathering",
    "local_corpus_view_table": "evidence_gathering",
    "local_corpus_view_figure_metadata": "evidence_gathering",
    "local_corpus_assess_evidence": "evidence_check",
    "web_research_strong": "evidence_gathering",
    "search_strong_sources": "evidence_gathering",
    "query_web_evidence": "evidence_gathering",
    "fetch_url": "evidence_gathering",
    "search_web": "evidence_gathering",
}
TOOL_NARRATION_PHASE_PROMPTS = {
    "planning": (
        "A new major phase has begun. Before continuing, write 1 to 3 short sentences that orient the user "
        "at a high level. Explain that you have framed the task and are narrowing the path. Do not mention tool names, "
        "do not restate mechanical status text, and do not answer the substance yet."
    ),
    "evidence_gathering": (
        "A new major phase has begun. Before continuing, write 1 to 3 short sentences that orient the user "
        "at a high level. Explain that you have narrowed the path and are now checking evidence. Do not mention tool names, "
        "do not restate mechanical status text, and do not answer the substance yet."
    ),
    "evidence_check": (
        "A new major phase has begun. Before continuing, write 1 to 3 short sentences that orient the user "
        "at a high level. Explain that you have candidate evidence and are now checking what actually holds up. "
        "Do not mention tool names, do not restate mechanical status text, and do not present final conclusions yet."
    ),
}

DEFAULT_SELECTOR_TERM_PRESERVATION_GUIDANCE = (
    "When choosing framing or retrieval tools, preserve the user's substantive topic "
    "terms. Do not preserve conversational scaffolding, advisory filler, or rhetorical "
    "framing unless it changes source selection. Prefer topic-shaped retrieval phrasing "
    "over conversational restatement."
)

DEFAULT_SELECTOR_LOCAL_CORPUS_PREFER_GUIDANCE = (
    "When the chat prefers the local corpus and the question is compatible with it, "
    "prefer local corpus tools first. If local evidence appears weak, incompatible, or "
    "obviously noisy, escalate cleanly. Do not stay loyal to the local lane out of inertia."
)

DEFAULT_SELECTOR_LOCAL_CORPUS_AUTO_GUIDANCE = (
    "When local corpus tools are available in auto mode, first inspect the available "
    "local domains with a single local_corpus_list_domains call before going to web "
    "search or model-only answering. Continue in the local lane only if the returned "
    "usable domains show a plausible thematic fit. Do not drill down further just to "
    "confirm an empty, weak, or merely nominal shelf."
)

DEFAULT_SELECTOR_PRIOR_WORK_FALLBACK_GUIDANCE = (
    "When primary evidence lanes are unavailable, before answering from model knowledge "
    "alone, check user-owned prior work when it is likely to contain relevant leads. "
    "Prefer prior-work sources in this order: knowledge files, notes, prior chats. "
    "Treat them as prior work or leads, not automatically authoritative evidence."
)

DEFAULT_SELECTOR_RETRIEVAL_TOOL_NAMES = {
    "local_corpus_frame_problem",
    "local_corpus_plan_axes",
    "local_corpus_collect_axis_evidence",
    "local_corpus_assess_evidence",
    "local_corpus_shortlist_books",
    "local_corpus_retrieve_evidence",
    "search_web",
    "web_research_strong",
    "search_strong_sources",
    "query_web_evidence",
    "fetch_url",
    "query_knowledge_files",
    "search_knowledge_files",
    "query_knowledge_bases",
    "search_knowledge_bases",
    "notes_lookup",
    "search_notes",
    "search_chats",
}

DEFAULT_SELECTOR_LOCAL_CORPUS_TOOL_NAMES = {
    "local_corpus_list_domains",
    "local_corpus_list_disciplines",
    "local_corpus_frame_problem",
    "local_corpus_plan_axes",
    "local_corpus_collect_axis_evidence",
    "local_corpus_assess_evidence",
    "local_corpus_shortlist_books",
    "local_corpus_view_book_cards",
    "local_corpus_retrieve_evidence",
    "local_corpus_view_table",
    "local_corpus_view_figure_metadata",
}

DEFAULT_SELECTOR_PRIOR_WORK_TOOL_PREFERENCE = (
    ("knowledge", ("query_knowledge_files", "search_knowledge_files", "query_knowledge_bases", "search_knowledge_bases")),
    ("notes", ("notes_lookup", "search_notes")),
    ("chats", ("search_chats",)),
)

DEFAULT_SELECTOR_WEB_FEATURE_NAMES = ("web_search", "focused_search")
DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE = "none"
DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_WEAK = "weak"
DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG = "strong"
DEFAULT_SELECTOR_PRIOR_WORK_EXPLICIT_TERMS = (
    "note",
    "notes",
    "chat",
    "chats",
    "knowledge",
    "knowledge base",
    "knowledge file",
    "prior work",
    "previous",
    "earlier",
    "saved",
)
DEFAULT_SELECTOR_PRIOR_WORK_FILE_REF_PATTERN = re.compile(
    r"\b[\w.-]+\.(?:md|txt|pdf|docx|pptx|csv|json|yaml|yml)\b",
    re.IGNORECASE,
)
DEFAULT_SELECTOR_PRIOR_WORK_CHAT_LINK_PATTERN = re.compile(
    r"/c/[0-9a-fA-F-]{8,}"
)
DEFAULT_SELECTOR_PRIOR_WORK_UUID_PATTERN = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
DEFAULT_SELECTOR_PRIOR_WORK_TOOL_NAMES = {
    "query_knowledge_files",
    "search_knowledge_files",
    "query_knowledge_bases",
    "search_knowledge_bases",
    "view_knowledge_file",
    "notes_lookup",
    "search_notes",
    "view_note",
    "search_chats",
    "view_chat",
}


def _tool_names_from_selector_tools(tools: dict[str, Any]) -> set[str]:
    return {str(name) for name in (tools or {}).keys()}


def _canonical_tool_name(tool_name: str | None) -> str:
    return TOOL_NAME_ALIASES.get(str(tool_name or ""), str(tool_name or ""))


def _selector_has_any_tool(tools: dict[str, Any], tool_names: set[str] | tuple[str, ...]) -> bool:
    available = _tool_names_from_selector_tools(tools)
    return any(name in available for name in tool_names)


def _recent_selector_messages(messages: list[dict], *, limit: int = 8) -> list[dict]:
    if not messages:
        return []
    return messages[-limit:]


def _selector_message_has_prior_work_tool_calls(message: dict) -> bool:
    if message.get("role") != "assistant":
        return False

    return bool(
        _selector_message_tool_call_names(message) & DEFAULT_SELECTOR_PRIOR_WORK_TOOL_NAMES
    )


def _selector_message_tool_call_names(message: dict) -> set[str]:
    if message.get("role") != "assistant":
        return set()

    names: set[str] = set()
    for tool_call in message.get("tool_calls", []) or []:
        function = tool_call.get("function", {}) or {}
        name = _canonical_tool_name(function.get("name"))
        if name:
            names.add(name)
    return names


def _selector_recent_tool_call_names(messages: list[dict], *, limit: int = 10) -> set[str]:
    names: set[str] = set()
    for message in _recent_selector_messages(messages, limit=limit):
        names.update(_selector_message_tool_call_names(message))
    return names


def _selector_prompt_has_explicit_prior_work_hint(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(term in normalized for term in DEFAULT_SELECTOR_PRIOR_WORK_EXPLICIT_TERMS)


def _selector_prompt_has_prior_work_artifact_reference(text: str) -> bool:
    source = str(text or "")
    return bool(
        DEFAULT_SELECTOR_PRIOR_WORK_CHAT_LINK_PATTERN.search(source)
        or DEFAULT_SELECTOR_PRIOR_WORK_UUID_PATTERN.search(source)
        or DEFAULT_SELECTOR_PRIOR_WORK_FILE_REF_PATTERN.search(source)
    )


def _selector_prior_work_signal(messages: list[dict]) -> str:
    recent_messages = _recent_selector_messages(messages)
    if not recent_messages:
        return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE

    last_user_message = get_last_user_message(recent_messages) or ""
    if not last_user_message.strip():
        return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE

    recent_user_turns = [msg for msg in recent_messages if msg.get("role") == "user"]
    has_recent_conversation_context = len(recent_user_turns) > 1 or any(
        msg.get("role") == "assistant" for msg in recent_messages[:-1]
    )
    has_recent_prior_work_tool_usage = any(
        _selector_message_has_prior_work_tool_calls(message)
        for message in recent_messages[:-1]
    )
    has_explicit_prior_work_hint = _selector_prompt_has_explicit_prior_work_hint(
        last_user_message
    )
    has_artifact_reference = _selector_prompt_has_prior_work_artifact_reference(
        last_user_message
    )

    if has_artifact_reference:
        return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG

    if has_recent_prior_work_tool_usage and has_explicit_prior_work_hint:
        return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG

    if has_recent_conversation_context and has_explicit_prior_work_hint:
        return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_WEAK

    return DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_NONE


def _build_default_selector_guidance(
    metadata: dict, tools: dict[str, Any], messages: list[dict] | None = None
) -> str:
    params = metadata.get("params", {}) or {}
    if params.get("function_calling") != "default":
        return ""

    features = metadata.get("features", {}) or {}
    local_corpus_mode = normalize_local_corpus_mode(params.get("local_corpus_mode"))

    clauses: list[str] = []

    if _selector_has_any_tool(tools, DEFAULT_SELECTOR_RETRIEVAL_TOOL_NAMES):
        clauses.append(DEFAULT_SELECTOR_TERM_PRESERVATION_GUIDANCE)

    if (
        local_corpus_mode == "prefer"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_LOCAL_CORPUS_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_LOCAL_CORPUS_PREFER_GUIDANCE)
    elif (
        local_corpus_mode == "auto"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_LOCAL_CORPUS_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_LOCAL_CORPUS_AUTO_GUIDANCE)

    web_enabled = any(bool(features.get(name)) for name in DEFAULT_SELECTOR_WEB_FEATURE_NAMES)
    if local_corpus_mode == "off" and not web_enabled:
        available_prior_work_lanes = [
            label
            for label, candidate_tools in DEFAULT_SELECTOR_PRIOR_WORK_TOOL_PREFERENCE
            if _selector_has_any_tool(tools, candidate_tools)
        ]
        prior_work_signal = _selector_prior_work_signal(messages or [])
        if (
            available_prior_work_lanes
            and prior_work_signal == DEFAULT_SELECTOR_PRIOR_WORK_SIGNAL_STRONG
        ):
            clauses.append(DEFAULT_SELECTOR_PRIOR_WORK_FALLBACK_GUIDANCE)

    if not clauses:
        return ""

    return "Additional runtime guidance:\n- " + "\n- ".join(clauses)


def _build_forced_default_selector_tool_call(
    metadata: dict, tools: dict[str, Any]
) -> dict[str, Any] | None:
    params = metadata.get("params", {}) or {}
    if params.get("function_calling") != "default":
        return None

    if normalize_local_corpus_mode(params.get("local_corpus_mode")) != "auto":
        return None

    if "local_corpus_list_domains" not in _tool_names_from_selector_tools(tools):
        return None

    return {"name": "local_corpus_list_domains", "parameters": {}}


def _should_upgrade_default_search_web_tool_call(
    metadata: dict,
    tools: dict[str, Any],
    messages: list[dict],
    tool_call: dict[str, Any],
    *,
    executed_tool_names: set[str] | None = None,
    pending_tool_names: set[str] | None = None,
) -> bool:
    # Ariadne no longer auto-upgrades broad discovery into focused strong search.
    # Search discipline should come from tool contracts and explicit hardening
    # triggers, not from a silent selector rewrite.
    return False


def _upgrade_default_search_web_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    upgraded = dict(tool_call)
    upgraded["name"] = "web_research_strong"
    return upgraded
from open_webui.env import (
    AGENTIC_ARTIFACTS_DIR,
    GLOBAL_LOG_LEVEL,
    ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION,
    CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
    CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES,
    BYPASS_MODEL_ACCESS_CONTROL,
    ENABLE_REALTIME_CHAT_SAVE,
    TERMINAL_TOOL_RESULT_INLINE_MAX_BYTES,
    TERMINAL_TOOL_RESULT_PREVIEW_CHARS,
    ENABLE_QUERIES_CACHE,
    RAG_SYSTEM_CONTEXT,
    RAG_WEB_FULL_CONTEXT_ONCE,
    ENABLE_FORWARD_USER_INFO_HEADERS,
    FORWARD_SESSION_INFO_HEADER_CHAT_ID,
    FORWARD_SESSION_INFO_HEADER_MESSAGE_ID,
    AIOHTTP_CLIENT_TIMEOUT,
)
from open_webui.utils.headers import include_user_info_headers
from open_webui.constants import TASKS

logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)


DEFAULT_REASONING_TAGS = [
    ("<think>", "</think>"),
    ("<thinking>", "</thinking>"),
    ("<reason>", "</reason>"),
    ("<reasoning>", "</reasoning>"),
    ("<thought>", "</thought>"),
    ("<Thought>", "</Thought>"),
    ("<|begin_of_thought|>", "<|end_of_thought|>"),
    ("◁think▷", "◁/think▷"),
]


def _should_enable_shared_tool_narration(
    request: Request, metadata: dict, features: dict
) -> bool:
    params = metadata.get("params", {}) or {}
    if params.get("function_calling") != "native":
        return False

    local_corpus_mode = normalize_local_corpus_mode(params.get("local_corpus_mode"))
    local_corpus_enabled = (
        local_corpus_mode == "prefer"
        and getattr(request.app.state.config, "ENABLE_LOCAL_CORPUS_TOOLS", False)
        and getattr(request.app.state.config, "LOCAL_CORPUS_ROOT", None)
    )
    focused_search_enabled = bool(features.get("focused_search"))
    return bool(local_corpus_enabled or focused_search_enabled)


def _initialize_tool_narration_state(
    request: Request, metadata: dict, features: dict
) -> dict[str, Any]:
    params = metadata.get("params", {}) or {}
    local_corpus_mode = normalize_local_corpus_mode(params.get("local_corpus_mode"))
    local_corpus_prefer = (
        local_corpus_mode == "prefer"
        and getattr(request.app.state.config, "ENABLE_LOCAL_CORPUS_TOOLS", False)
        and getattr(request.app.state.config, "LOCAL_CORPUS_ROOT", None)
    )
    focused_search_enabled = bool(features.get("focused_search"))
    return {
        "enabled": _should_enable_shared_tool_narration(request, metadata, features),
        "last_narrated_phase": "orientation" if local_corpus_prefer else None,
        "current_major_phase": None,
        "narration_count": 1 if local_corpus_prefer else 0,
        "max_beats": TOOL_NARRATION_MAX_BEATS,
        "initial_preamble_expected": bool(local_corpus_prefer or focused_search_enabled),
    }


def _tool_narration_phase_for_tool(tool_name: str) -> str | None:
    return TOOL_NARRATION_TOOL_PHASES.get(str(tool_name or ""))


def _coalesce_tool_narration_phase(phases: list[str]) -> str | None:
    if not phases:
        return None
    unique = [phase for phase in phases if phase in TOOL_NARRATION_PHASE_ORDER]
    if not unique:
        return None
    return max(unique, key=lambda item: TOOL_NARRATION_PHASE_ORDER.get(item, 0))


def _build_tool_narration_instruction(phase: str | None) -> str | None:
    if not phase:
        return None
    return TOOL_NARRATION_PHASE_PROMPTS.get(phase)


def _register_tool_narration_phase_transition(
    state: dict[str, Any], phases: list[str]
) -> str | None:
    if not state.get("enabled"):
        return None

    next_phase = _coalesce_tool_narration_phase(phases)
    if not next_phase:
        return None

    state["current_major_phase"] = next_phase
    if next_phase == state.get("last_narrated_phase"):
        return None
    if int(state.get("narration_count", 0) or 0) >= int(state.get("max_beats", 0) or 0):
        return None

    instruction = _build_tool_narration_instruction(next_phase)
    if not instruction:
        return None

    state["last_narrated_phase"] = next_phase
    state["narration_count"] = int(state.get("narration_count", 0) or 0) + 1
    return instruction


def _build_tool_continuation_messages(
    form_data_messages: list[dict[str, Any]],
    output: list[dict[str, Any]],
    narration_instruction: str | None = None,
) -> list[dict[str, Any]]:
    messages = [
        *copy.deepcopy(form_data_messages),
        *convert_output_to_messages(output, raw=True),
    ]
    if narration_instruction:
        messages = add_or_update_system_message(
            narration_instruction,
            messages,
            append=True,
        )
    return messages


def _inject_runtime_timestamp_once(messages: list[dict]) -> list[dict]:
    system_message = get_system_message(messages)
    if system_message:
        system_content = get_content_from_message(system_message) or ""
        if RUNTIME_TIMESTAMP_MARKER in system_content:
            return messages

    return add_or_update_system_message(
        append_runtime_temporal_grounding(""),
        messages,
        append=True,
    )
DEFAULT_SOLUTION_TAGS = [("<|begin_of_solution|>", "<|end_of_solution|>")]
DEFAULT_CODE_INTERPRETER_TAGS = [("<code_interpreter>", "</code_interpreter>")]


def _stringify_termination_detail(value: Any, *, limit: int = 300) -> str:
    if value is None:
        return ""

    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        text = str(value)

    text = text.replace("\r", " ").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _build_agent_loop_termination_cause(
    *,
    kind: str,
    phase: str,
    detail: Any = None,
    exc: Exception | None = None,
) -> dict[str, Any]:
    cause: dict[str, Any] = {
        "kind": kind,
        "phase": phase,
        "timestamp": int(time.time()),
    }

    if AIOHTTP_CLIENT_TIMEOUT is not None:
        cause["client_timeout_seconds"] = AIOHTTP_CLIENT_TIMEOUT

    if detail is not None:
        rendered_detail = _stringify_termination_detail(detail)
        if rendered_detail:
            cause["detail"] = rendered_detail

    if exc is not None:
        cause["exception_type"] = exc.__class__.__name__

        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            cause["status_code"] = status_code

        exc_detail = getattr(exc, "detail", None)
        rendered_exc_detail = _stringify_termination_detail(exc_detail)
        if rendered_exc_detail:
            cause["error"] = rendered_exc_detail
        else:
            rendered_exc = _stringify_termination_detail(str(exc))
            if rendered_exc:
                cause["error"] = rendered_exc

    return cause

TOKEN_TELEMETRY_VERSION = 1
TOKEN_TELEMETRY_PROVIDER = "openai_logprobs"
TOKEN_TELEMETRY_TOP_K = 10
TOKEN_TELEMETRY_TOKEN_CAP = 1024

TOKEN_BRANCH_VERSION = 1
TOKEN_BRANCH_FORCING_STRATEGY = "assistant_prefix_fallback"

_BG_CYRILLIC_TO_LATIN = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sht",
    "ъ": "a",
    "ь": "y",
    "ю": "yu",
    "я": "ya",
}
_BG_CYRILLIC_TO_LATIN.update({k.upper(): v.title() for k, v in _BG_CYRILLIC_TO_LATIN.items()})

_CHAT_ARTIFACT_DIR_CACHE: dict[str, Path] = {}
_DEEP_RESEARCH_TERMINAL_STATUSES = {"completed", "failed", "suspended", "error"}


def _transliterate_cyrillic_to_latin(text: str) -> str:
    return "".join(_BG_CYRILLIC_TO_LATIN.get(char, char) for char in str(text or ""))


def _slugify_chat_title(title: str, max_len: int = 80) -> str:
    transliterated = _transliterate_cyrillic_to_latin(title)
    ascii_only = transliterated.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")
    if not slug:
        slug = "chat"
    return slug[:max_len].strip("-") or "chat"


def _safe_path_component(value: Any, fallback: str, max_len: int = 64) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:max_len]


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _resolve_chat_artifacts_dir(chat_id: str) -> Optional[Path]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id or normalized_chat_id.startswith("local:"):
        return None

    cached = _CHAT_ARTIFACT_DIR_CACHE.get(normalized_chat_id)
    if cached is not None:
        return cached

    root = Path(AGENTIC_ARTIFACTS_DIR)
    root.mkdir(parents=True, exist_ok=True)

    existing_dirs = sorted(root.glob(f"{normalized_chat_id}__*"))
    if existing_dirs:
        resolved = existing_dirs[0]
        _CHAT_ARTIFACT_DIR_CACHE[normalized_chat_id] = resolved
        return resolved

    chat_title = Chats.get_chat_title_by_id(normalized_chat_id) or "chat"
    slug = _slugify_chat_title(chat_title)
    resolved = root / f"{normalized_chat_id}__{slug}"
    resolved.mkdir(parents=True, exist_ok=True)

    try:
        _append_jsonl(
            resolved / "chat_index.jsonl",
            {
                "ts": int(time.time()),
                "event": "chat_dir_initialized",
                "chat_id": normalized_chat_id,
                "chat_title": chat_title,
                "chat_slug": slug,
                "path": str(resolved),
            },
        )
    except Exception as exc:
        log.warning("Failed to write chat artifact index for %s: %s", normalized_chat_id, exc)
    _CHAT_ARTIFACT_DIR_CACHE[normalized_chat_id] = resolved
    return resolved


def _set_deep_research_commit_state(metadata: dict[str, Any], state: str) -> str:
    metadata["deep_research_commit_state"] = state
    return state


def _get_deep_research_failure_message(status_payload: dict[str, Any]) -> str:
    metadata = status_payload.get("metadata") or {}
    error_info = metadata.get("error_info") or {}
    if error_info.get("message"):
        return str(error_info["message"])

    error = status_payload.get("error") or metadata.get("error")
    if error:
        return str(error)

    status_value = str(status_payload.get("status") or "failed").replace("_", " ")
    return f"Deep research {status_value}."


def _build_deep_research_status_description(
    status_payload: Optional[dict[str, Any]],
    *,
    fallback: str,
) -> str:
    payload = status_payload or {}
    log_entry = payload.get("log_entry") or {}
    if isinstance(log_entry, dict) and log_entry.get("message"):
        return str(log_entry["message"])

    metadata = payload.get("metadata") or {}
    error_info = metadata.get("error_info") or {}
    if error_info.get("message"):
        return str(error_info["message"])

    status_value = str(payload.get("status") or "").strip()
    if status_value:
        return status_value.replace("_", " ").capitalize()

    return fallback


async def _emit_deep_research_status(
    event_emitter,
    status_state: dict[str, Any],
    *,
    status_value: Optional[str] = None,
    progress: Optional[Any] = None,
    description: str,
    done: bool,
) -> None:
    if not event_emitter:
        return

    dedupe_key = (status_value, progress, description)
    if status_state.get("last_key") == dedupe_key and status_state.get("last_done") == done:
        return

    status_state["last_key"] = dedupe_key
    status_state["last_done"] = done
    payload = {
        "type": "status",
        "data": {
            "action": "deep_research",
            "description": description,
            "done": done,
        },
    }
    if status_value is not None:
        payload["data"]["status"] = status_value
    if progress is not None:
        payload["data"]["progress"] = progress

    await event_emitter(payload)


def _build_deep_research_sources(urls: list[Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    citations: list[dict[str, Any]] = []
    for item in urls or []:
        url = str(item or "").strip()
        if not url or url in seen:
            continue

        seen.add(url)
        source_name = urlsplit(url).netloc or url
        citations.append(
            {
                "source": {"id": url, "name": source_name, "url": url},
                "document": [url],
                "metadata": [{"source": url, "url": url, "name": source_name}],
            }
        )
    return citations


def _resolve_deep_research_export_filename(export_format: str) -> str:
    normalized = _safe_path_component(export_format, "pdf").lower()
    if normalized == "md":
        return "report.export.md"
    return f"report.{normalized}"


def _persist_deep_research_raw_artifacts(
    chat_id: str,
    research_id: str,
    markdown_content: str,
    export_filename: str,
    export_content: bytes,
) -> tuple[Path, Path]:
    chat_artifacts_dir = _resolve_chat_artifacts_dir(chat_id)
    if chat_artifacts_dir is None:
        raise LocalDeepResearchError("Deep research requires a persisted chat.")

    artifact_dir = chat_artifacts_dir / "deep_research" / _safe_path_component(
        research_id, "research"
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = artifact_dir / "report.md"
    export_path = artifact_dir / export_filename
    markdown_path.write_text(markdown_content, encoding="utf-8")
    export_path.write_bytes(export_content)
    return markdown_path, export_path


def _message_file_from_upload(uploaded_file: Any, *, filename: str) -> dict[str, Any]:
    file_payload = (
        uploaded_file.model_dump()
        if hasattr(uploaded_file, "model_dump")
        else dict(uploaded_file or {})
    )
    meta = file_payload.get("meta") or {}
    file_id = file_payload.get("id")
    if not file_id:
        raise LocalDeepResearchError(
            "Deep research artifact registration did not return a file ID."
        )

    content_type = meta.get("content_type") or file_payload.get("content_type")
    return {
        "type": "file",
        "file": file_payload,
        "id": file_id,
        "url": str(file_id),
        "name": meta.get("name") or file_payload.get("filename") or filename,
        "size": meta.get("size"),
        "content_type": content_type,
        **(
            {"collection_name": meta.get("collection_name")}
            if meta.get("collection_name")
            else {}
        ),
    }


def _register_deep_research_artifact(
    request: Request,
    user: Any,
    *,
    chat_id: str,
    message_id: str,
    filename: str,
    content: bytes,
    content_type: Optional[str],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    guessed_content_type = (
        content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    )
    upload = UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers={"content-type": guessed_content_type},
    )
    uploaded_file = upload_file_handler(
        request,
        file=upload,
        metadata={
            "chat_id": chat_id,
            "message_id": message_id,
            "deep_research": {
                "research_id": metadata.get("deep_research_id"),
                "artifact_name": filename,
            },
        },
        process=False,
        user=user,
    )
    return _message_file_from_upload(uploaded_file, filename=filename)


def _link_deep_research_message_files(
    chat_id: str,
    message_id: str,
    user: Any,
    message_files: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    file_ids = [item.get("id") for item in message_files if item.get("id")]
    if not file_ids:
        raise LocalDeepResearchError(
            "Deep research artifact registration did not return any file IDs."
        )

    Chats.insert_chat_files(chat_id, message_id, file_ids, user.id)
    saved_files = Chats.add_message_files_by_id_and_message_id(
        chat_id,
        message_id,
        message_files,
    )
    return saved_files if saved_files is not None else message_files


async def _run_deep_research_awaitable(
    awaitable_factory,
    metadata: dict[str, Any],
) -> Any:
    while True:
        try:
            return await awaitable_factory()
        except asyncio.CancelledError:
            if metadata.get("deep_research_commit_state") != "polling":
                task = asyncio.current_task()
                if task and hasattr(task, "uncancel"):
                    task.uncancel()
                    continue
            raise


async def _commit_deep_research_cancel_response(
    request: Request,
    form_data: dict[str, Any],
    user: Any,
    model: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    response = {"choices": [{"message": {"content": "Deep research canceled."}}]}
    ctx = build_chat_response_context(request, form_data, user, model, metadata, None, [])
    await asyncio.shield(non_streaming_chat_response_handler(response, ctx))


async def _close_deep_research_client(
    client: LocalDeepResearchClient,
    metadata: dict[str, Any],
) -> None:
    try:
        await client.close()
    except asyncio.CancelledError:
        if metadata.get("deep_research_commit_state") == "polling":
            raise
        task = asyncio.current_task()
        if task and hasattr(task, "uncancel"):
            task.uncancel()
        return


async def chat_deep_research_handler(request, form_data, extra_params, user):
    metadata = extra_params["__metadata__"]
    event_emitter = extra_params.get("__event_emitter__")
    model = extra_params["__model__"]
    config = request.app.state.config
    chat_id = metadata.get("chat_id")
    message_id = metadata.get("message_id")

    if not chat_id or str(chat_id).startswith("local:"):
        raise LocalDeepResearchError("Deep research is only available for persisted chats.")
    if not message_id:
        raise LocalDeepResearchError("Deep research requires a target assistant message.")
    if not config.ENABLE_DEEP_RESEARCH:
        raise LocalDeepResearchError("Deep research is currently disabled.")
    if user.role != "admin" and not has_permission(
        user.id,
        "features.deep_research",
        request.app.state.config.USER_PERMISSIONS,
    ):
        raise LocalDeepResearchError("You do not have permission to use deep research.")

    query = get_last_user_message(form_data.get("messages", []))
    if not str(query or "").strip():
        raise LocalDeepResearchError("Deep research requires a user message.")

    export_format = _safe_path_component(
        getattr(config, "DEEP_RESEARCH_EXPORT_FORMAT", "pdf") or "pdf",
        "pdf",
    ).lower()
    poll_interval_ms = max(
        250, int(getattr(config, "DEEP_RESEARCH_POLL_INTERVAL_MS", 3000))
    )
    timeout_seconds = max(
        30, int(getattr(config, "DEEP_RESEARCH_TIMEOUT_SECONDS", 900))
    )

    client = LocalDeepResearchClient(
        base_url=getattr(config, "DEEP_RESEARCH_SIDECAR_URL", ""),
        username=getattr(config, "DEEP_RESEARCH_SIDECAR_USERNAME", ""),
        password=getattr(config, "DEEP_RESEARCH_SIDECAR_PASSWORD", ""),
    )

    status_state: dict[str, Any] = {"last_key": None, "last_done": None}
    started_at = time.monotonic()
    research_id: Optional[str] = None
    _set_deep_research_commit_state(metadata, "polling")

    try:
        await _emit_deep_research_status(
            event_emitter,
            status_state,
            status_value="queued",
            description="Starting deep research...",
            progress=0,
            done=False,
        )

        start_payload = await client.start_research(query=query, mode="detailed")
        research_id = start_payload.get("research_id")
        metadata["deep_research_id"] = research_id

        terminal_status: Optional[dict[str, Any]] = None
        while True:
            if (time.monotonic() - started_at) >= timeout_seconds:
                raise LocalDeepResearchError(
                    f"Deep research timed out after {timeout_seconds} seconds."
                )

            status_payload = await _run_deep_research_awaitable(
                lambda: client.get_research_status(research_id),
                metadata,
            )
            status_value = str(status_payload.get("status") or "").strip().lower()
            progress = status_payload.get("progress")
            description = _build_deep_research_status_description(
                status_payload,
                fallback="Deep research in progress...",
            )

            await _emit_deep_research_status(
                event_emitter,
                status_state,
                status_value=status_value or "in_progress",
                progress=progress,
                description=description,
                done=status_value in _DEEP_RESEARCH_TERMINAL_STATUSES,
            )

            if status_value in _DEEP_RESEARCH_TERMINAL_STATUSES:
                terminal_status = status_payload
                break

            await asyncio.sleep(poll_interval_ms / 1000)

        terminal_value = str(terminal_status.get("status") or "").strip().lower()
        if terminal_value != "completed":
            salvage_files: list[dict[str, Any]] = []
            report_path = terminal_status.get("report_path")
            if report_path:
                try:
                    report_payload = await _run_deep_research_awaitable(
                        lambda: client.get_report(research_id),
                        metadata,
                    )
                    markdown_content = str(
                        report_payload.get("content") or report_payload.get("summary") or ""
                    )
                    if markdown_content:
                        chat_artifacts_dir = _resolve_chat_artifacts_dir(chat_id)
                        artifact_dir = (
                            chat_artifacts_dir
                            / "deep_research"
                            / _safe_path_component(research_id, "research")
                        )
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                        markdown_path = artifact_dir / "report.md"
                        markdown_path.write_text(markdown_content, encoding="utf-8")

                        markdown_file = _register_deep_research_artifact(
                            request,
                            user,
                            chat_id=chat_id,
                            message_id=message_id,
                            filename=markdown_path.name,
                            content=markdown_content.encode("utf-8"),
                            content_type="text/markdown",
                            metadata=metadata,
                        )
                        salvage_files = _link_deep_research_message_files(
                            chat_id,
                            message_id,
                            user,
                            [markdown_file],
                        )
                except Exception as exc:
                    log.warning(
                        "Failed to salvage deep research markdown for %s: %s",
                        research_id,
                        exc,
                    )

            if salvage_files and event_emitter:
                await _run_deep_research_awaitable(
                    lambda: event_emitter({"type": "files", "data": {"files": salvage_files}}),
                    metadata,
                )

            metadata["direct_response"] = {
                "error": {"detail": _get_deep_research_failure_message(terminal_status)}
            }
            _set_deep_research_commit_state(metadata, "committed_failure")
            return form_data, metadata, []

        _set_deep_research_commit_state(metadata, "finalizing")
        await _emit_deep_research_status(
            event_emitter,
            status_state,
            status_value="completed",
            progress=terminal_status.get("progress"),
            description="Preparing report files...",
            done=False,
        )

        report_payload = await _run_deep_research_awaitable(
            lambda: client.get_report(research_id),
            metadata,
        )
        markdown_content = str(
            report_payload.get("content") or report_payload.get("summary") or ""
        )
        if not markdown_content:
            raise LocalDeepResearchError("Deep research completed without a markdown report.")

        chat_artifacts_dir = _resolve_chat_artifacts_dir(chat_id)
        artifact_dir = chat_artifacts_dir / "deep_research" / _safe_path_component(
            research_id, "research"
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        markdown_path = artifact_dir / "report.md"
        markdown_path.write_text(markdown_content, encoding="utf-8")

        try:
            export_payload = await _run_deep_research_awaitable(
                lambda: client.export_report(research_id, export_format),
                metadata,
            )
            if not export_payload.content:
                raise LocalDeepResearchError("Deep research completed but export failed.")
        except (LocalDeepResearchAuthError, LocalDeepResearchError):
            markdown_file = _register_deep_research_artifact(
                request,
                user,
                chat_id=chat_id,
                message_id=message_id,
                filename=markdown_path.name,
                content=markdown_content.encode("utf-8"),
                content_type="text/markdown",
                metadata=metadata,
            )
            linked_files = _link_deep_research_message_files(
                chat_id,
                message_id,
                user,
                [markdown_file],
            )
            if event_emitter:
                await _run_deep_research_awaitable(
                    lambda: event_emitter({"type": "files", "data": {"files": linked_files}}),
                    metadata,
                )
            raise LocalDeepResearchError("Deep research completed but export failed.")

        export_filename = _resolve_deep_research_export_filename(export_format)
        export_path = artifact_dir / export_filename
        export_path.write_bytes(export_payload.content)

        markdown_file = _register_deep_research_artifact(
            request,
            user,
            chat_id=chat_id,
            message_id=message_id,
            filename=markdown_path.name,
            content=markdown_content.encode("utf-8"),
            content_type="text/markdown",
            metadata=metadata,
        )
        export_file = _register_deep_research_artifact(
            request,
            user,
            chat_id=chat_id,
            message_id=message_id,
            filename=export_path.name,
            content=export_payload.content,
            content_type=export_payload.content_type,
            metadata=metadata,
        )
        linked_files = _link_deep_research_message_files(
            chat_id,
            message_id,
            user,
            [markdown_file, export_file],
        )

        if event_emitter:
            await _run_deep_research_awaitable(
                lambda: event_emitter({"type": "files", "data": {"files": linked_files}}),
                metadata,
            )

        metadata["direct_response"] = {
            "choices": [
                {
                    "message": {"content": "Deep research completed. Reports attached."}
                }
            ],
            "sources": _build_deep_research_sources(report_payload.get("sources") or []),
        }
        _set_deep_research_commit_state(metadata, "committed_success")
        return form_data, metadata, []
    except asyncio.CancelledError:
        if metadata.get("deep_research_commit_state") == "polling":
            if research_id:
                try:
                    await asyncio.shield(client.terminate_research(research_id))
                except Exception as exc:
                    log.warning(
                        "Failed to terminate deep research %s after cancellation: %s",
                        research_id,
                        exc,
                    )

            _set_deep_research_commit_state(metadata, "committed_cancel")
            await _emit_deep_research_status(
                event_emitter,
                status_state,
                status_value="suspended",
                description="Deep research canceled.",
                done=True,
            )
            await _commit_deep_research_cancel_response(
                request, form_data, user, model, metadata
            )
        raise
    except (LocalDeepResearchAuthError, LocalDeepResearchError) as exc:
        if (
            metadata.get("deep_research_commit_state") == "finalizing"
            and "export failed" not in str(exc).lower()
            and research_id
        ):
            error_message = "Deep research completed but export failed."
        else:
            error_message = str(exc)

        metadata["direct_response"] = {"error": {"detail": error_message}}
        _set_deep_research_commit_state(metadata, "committed_failure")
        await _emit_deep_research_status(
            event_emitter,
            status_state,
            status_value="failed",
            description=error_message,
            done=True,
        )
        return form_data, metadata, []
    finally:
        await _close_deep_research_client(client, metadata)


def _normalize_terminal_tool_result_for_persistence(
    tool_function_name: str, tool_result: str
) -> str:
    """Convert structured terminal JSON payloads to human-readable text artifacts.

    Open Terminal `run_command`/`get_process_status` payloads embed stdout chunks in
    JSON string fields, so line breaks appear as escaped `\\r\\n` when the artifact is
    viewed directly. For persisted artifacts we flatten those chunks back to plain
    text while keeping key metadata headers.
    """
    if tool_function_name not in {"run_command", "get_process_status"}:
        return tool_result

    try:
        payload = json.loads(tool_result)
    except Exception:
        return tool_result

    if not isinstance(payload, dict):
        return tool_result

    output_chunks: list[str] = []
    for chunk in payload.get("output", []):
        if not isinstance(chunk, dict):
            continue
        data = chunk.get("data")
        if isinstance(data, str):
            output_chunks.append(data.replace("\r\n", "\n").replace("\r", "\n"))

    if not output_chunks:
        return tool_result

    header_lines: list[str] = []
    for key in ("id", "command", "status", "exit_code"):
        if key in payload:
            header_lines.append(f"{key}: {payload.get(key)}")
    if isinstance(payload.get("log_path"), str):
        header_lines.append(f"log_path: {payload.get('log_path')}")

    body = "".join(output_chunks)
    if header_lines:
        return f"{'\n'.join(header_lines)}\n\n{body}"
    return body


def _maybe_persist_terminal_tool_result(
    *,
    metadata: Optional[dict[str, Any]],
    tool_function_name: str,
    tool_result: str,
) -> str:
    if not isinstance(tool_result, str):
        return tool_result

    tool_result = _normalize_terminal_tool_result_for_persistence(
        tool_function_name, tool_result
    )

    if TERMINAL_TOOL_RESULT_INLINE_MAX_BYTES <= 0:
        return tool_result

    encoded = tool_result.encode("utf-8", "replace")
    total_bytes = len(encoded)
    if total_bytes <= TERMINAL_TOOL_RESULT_INLINE_MAX_BYTES:
        return tool_result

    chat_id = str((metadata or {}).get("chat_id") or "").strip()
    message_id = str((metadata or {}).get("message_id") or "").strip()
    if not chat_id:
        return tool_result

    chat_dir = _resolve_chat_artifacts_dir(chat_id)
    if chat_dir is None:
        return tool_result

    tool_outputs_dir = chat_dir / "tool_outputs"
    tool_outputs_dir.mkdir(parents=True, exist_ok=True)

    file_name = (
        f"{int(time.time() * 1000)}"
        f"_{_safe_path_component(message_id, 'message')}"
        f"_{_safe_path_component(tool_function_name, 'tool')}"
        f"_{uuid4().hex[:8]}.txt"
    )
    artifact_path = tool_outputs_dir / file_name

    try:
        artifact_path.write_text(tool_result, encoding="utf-8", errors="replace")
    except Exception as exc:
        log.warning("Failed to persist terminal tool result to disk: %s", exc)
        return tool_result

    preview_limit = max(0, TERMINAL_TOOL_RESULT_PREVIEW_CHARS)
    preview = tool_result[:preview_limit]
    omitted_chars = max(0, len(tool_result) - len(preview))
    sha256 = hashlib.sha256(encoded).hexdigest()

    pointer_payload = {
        "ts": int(time.time()),
        "kind": "terminal_tool_output_pointer",
        "chat_id": chat_id,
        "message_id": message_id or None,
        "tool": tool_function_name,
        "path": str(artifact_path),
        "bytes": total_bytes,
        "sha256": sha256,
        "preview_chars": len(preview),
        "omitted_chars": omitted_chars,
    }
    try:
        _append_jsonl(chat_dir / "tool_outputs.index.jsonl", pointer_payload)
    except Exception as exc:
        log.warning("Failed to update tool output index for %s: %s", chat_id, exc)

    pointer_text = (
        "[tool output truncated and persisted to disk]\n"
        f"path: {artifact_path}\n"
        f"bytes: {total_bytes}\n"
        f"sha256: {sha256}\n"
        f"preview_chars: {len(preview)}\n"
        f"omitted_chars: {omitted_chars}\n\n"
        "preview:\n"
        f"{preview}"
    )
    if omitted_chars > 0:
        pointer_text += f"\n...[preview truncated, {omitted_chars} chars omitted]"

    return pointer_text


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_token_text(item: Any) -> str:
    if isinstance(item, str):
        return item

    if not isinstance(item, dict):
        return ""

    token_text = item.get("token", item.get("text"))
    if isinstance(token_text, str):
        return token_text

    token_bytes = item.get("bytes")
    if isinstance(token_bytes, list):
        try:
            return bytes(int(b) for b in token_bytes).decode("utf-8", "replace")
        except Exception:
            return ""

    return ""


def _compute_probability(logprob: Optional[float], value: Any) -> Optional[float]:
    prob = _safe_float(value)
    if prob is not None:
        return max(0.0, min(1.0, prob))

    if logprob is None:
        return None

    try:
        return max(0.0, min(1.0, math.exp(logprob)))
    except (OverflowError, ValueError):
        return None


def _normalize_logprob_candidate(item: Any, rank: int) -> Optional[dict]:
    if isinstance(item, str):
        return {
            "rank": rank,
            "text": item,
            "tokenId": None,
            "logprob": None,
            "prob": None,
        }

    if not isinstance(item, dict):
        return None

    logprob = _safe_float(item.get("logprob", item.get("log_prob")))
    candidate = {
        "rank": rank,
        "text": _extract_token_text(item),
        "tokenId": _safe_int(item.get("token_id", item.get("id"))),
        "logprob": logprob,
        "prob": _compute_probability(
            logprob, item.get("prob", item.get("probability"))
        ),
    }
    return candidate


def _normalize_logprob_token(entry: dict) -> Optional[dict]:
    if not isinstance(entry, dict):
        return None

    logprob = _safe_float(entry.get("logprob", entry.get("log_prob")))
    selected_token = {
        "rank": 0,
        "text": _extract_token_text(entry),
        "tokenId": _safe_int(entry.get("token_id", entry.get("id"))),
        "logprob": logprob,
        "prob": _compute_probability(logprob, entry.get("prob", entry.get("probability"))),
    }

    top_logprobs = entry.get("top_logprobs", entry.get("alternatives", []))
    alternatives: list[dict] = [selected_token]

    seen = {(selected_token.get("text"), selected_token.get("tokenId"))}
    if isinstance(top_logprobs, list):
        for raw in top_logprobs:
            normalized = _normalize_logprob_candidate(raw, 0)
            if not normalized:
                continue

            key = (normalized.get("text"), normalized.get("tokenId"))
            if key in seen:
                continue
            seen.add(key)
            alternatives.append(normalized)

            if len(alternatives) >= TOKEN_TELEMETRY_TOP_K:
                break

    for idx, alt in enumerate(alternatives):
        alt["rank"] = idx

    return {
        "text": selected_token.get("text", ""),
        "tokenId": selected_token.get("tokenId"),
        "logprob": selected_token.get("logprob"),
        "prob": selected_token.get("prob"),
        "alternatives": alternatives[:TOKEN_TELEMETRY_TOP_K],
    }


def _get_choice_logprob_content(choice: dict) -> list[dict]:
    if not isinstance(choice, dict):
        return []

    candidates = [
        choice.get("logprobs", {}).get("content"),
        choice.get("delta", {}).get("logprobs", {}).get("content"),
        choice.get("message", {}).get("logprobs", {}).get("content"),
    ]

    for content in candidates:
        if isinstance(content, list):
            return content
    return []


def _append_token_telemetry_from_choice(
    telemetry_state: dict, choice: dict, visible_content: Optional[str]
) -> None:
    if not isinstance(visible_content, str) or visible_content == "":
        return

    token_entries = _get_choice_logprob_content(choice)
    if not token_entries:
        return

    for entry in token_entries:
        if len(telemetry_state["tokens"]) >= TOKEN_TELEMETRY_TOKEN_CAP:
            telemetry_state["capped"] = True
            break

        normalized_token = _normalize_logprob_token(entry)
        if not normalized_token:
            continue

        normalized_token["index"] = len(telemetry_state["tokens"])
        telemetry_state["tokens"].append(normalized_token)

    if len(token_entries) > 0 and len(telemetry_state["tokens"]) >= TOKEN_TELEMETRY_TOKEN_CAP:
        telemetry_state["capped"] = True


def _build_token_telemetry_payload(telemetry_state: dict) -> Optional[dict]:
    tokens = telemetry_state.get("tokens", [])
    if not tokens:
        return None

    return {
        "version": TOKEN_TELEMETRY_VERSION,
        "provider": TOKEN_TELEMETRY_PROVIDER,
        "topK": TOKEN_TELEMETRY_TOP_K,
        "tokenCap": TOKEN_TELEMETRY_TOKEN_CAP,
        "capped": bool(telemetry_state.get("capped", False)),
        "tokens": tokens,
    }


def _extract_non_streaming_token_telemetry(response_data: dict) -> Optional[dict]:
    choices = response_data.get("choices", [])
    if not choices:
        return None

    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message", {})
    visible_content = message.get("content")

    telemetry_state = {"tokens": [], "capped": False}
    _append_token_telemetry_from_choice(telemetry_state, choice, visible_content)
    return _build_token_telemetry_payload(telemetry_state)


def _prepare_branch_prefill(metadata: dict) -> tuple[str, dict]:
    branch = metadata.get("branch")
    if not isinstance(branch, dict):
        raise HTTPException(status_code=400, detail="Invalid branch payload")

    source_message_id = branch.get("source_message_id")
    fork_index = _safe_int(branch.get("fork_index"))
    alt_rank = _safe_int(branch.get("alt_rank"))

    if not isinstance(source_message_id, str) or not source_message_id.strip():
        raise HTTPException(status_code=400, detail="Invalid source_message_id")
    if fork_index is None or alt_rank is None:
        raise HTTPException(status_code=400, detail="Invalid branch fork_index/alt_rank")

    chat_id = metadata.get("chat_id")
    if not isinstance(chat_id, str) or not chat_id or chat_id.startswith("local:"):
        raise HTTPException(
            status_code=400,
            detail="Token branching requires a persisted chat context",
        )

    source_message = Chats.get_message_by_id_and_message_id(chat_id, source_message_id)
    if not source_message:
        raise HTTPException(status_code=400, detail="Branch source message not found")
    if source_message.get("role") != "assistant":
        raise HTTPException(
            status_code=400, detail="Branch source message must be an assistant message"
        )

    parent_message_id = metadata.get("parent_message_id")
    if source_message.get("parentId") != parent_message_id:
        raise HTTPException(
            status_code=400,
            detail="Branch source message parent does not match current parent",
        )

    token_telemetry = source_message.get("tokenTelemetry")
    if not isinstance(token_telemetry, dict):
        raise HTTPException(
            status_code=400, detail="Branch source message has no token telemetry"
        )

    tokens = token_telemetry.get("tokens")
    if not isinstance(tokens, list) or len(tokens) == 0:
        raise HTTPException(
            status_code=400, detail="Branch source message has no token telemetry"
        )
    if fork_index < 0 or fork_index >= len(tokens):
        raise HTTPException(status_code=400, detail="Branch fork_index is out of range")

    token_entry = tokens[fork_index]
    alternatives = token_entry.get("alternatives")
    if not isinstance(alternatives, list) or len(alternatives) == 0:
        raise HTTPException(
            status_code=400,
            detail="Branch source token has no alternatives",
        )
    if alt_rank < 0 or alt_rank >= len(alternatives):
        raise HTTPException(status_code=400, detail="Branch alt_rank is out of range")

    chosen_alt = alternatives[alt_rank]
    chosen_token_text = chosen_alt.get("text", "")
    if not isinstance(chosen_token_text, str):
        chosen_token_text = str(chosen_token_text)

    forced_prefix = "".join(
        (
            token.get("text", "")
            if isinstance(token.get("text", ""), str)
            else str(token.get("text", ""))
        )
        for token in tokens[:fork_index]
    )
    forced_prefix = f"{forced_prefix}{chosen_token_text}"

    token_branch = {
        "version": TOKEN_BRANCH_VERSION,
        "sourceMessageId": source_message_id,
        "forkIndex": fork_index,
        "chosenAltRank": alt_rank,
        "chosenTokenText": chosen_token_text,
        "chosenTokenId": _safe_int(chosen_alt.get("tokenId", chosen_alt.get("token_id"))),
        "forcingStrategy": TOKEN_BRANCH_FORCING_STRATEGY,
        "createdAt": int(time.time()),
    }

    return forced_prefix, token_branch


def output_id(prefix: str) -> str:
    """Generate OR-style ID: prefix + 24-char hex UUID."""
    return f"{prefix}_{uuid4().hex[:24]}"


def _split_tool_calls(
    tool_calls: list[dict],
) -> list[dict]:
    """Expand tool calls whose arguments contain multiple back-to-back JSON objects.

    Some models (e.g. GPT-5.4) send multiple complete JSON argument objects
    under the same tool call index, producing concatenated invalid JSON like:
        '{"query":"A","count":5}{"query":"B","count":5}'

    Each such tool call is split into separate entries so each gets executed
    independently. Single-object arguments pass through unchanged.
    """

    def split_json_objects(raw: str) -> list[str]:
        decoder = json.JSONDecoder()
        results = []
        position = 0

        while position < len(raw):
            while position < len(raw) and raw[position].isspace():
                position += 1
            if position >= len(raw):
                break
            try:
                _, end = decoder.raw_decode(raw, position)
                results.append(raw[position:end].strip())
                position = end
            except json.JSONDecodeError:
                return [raw]

        return results or [raw]

    expanded = []
    for tool_call in tool_calls:
        arguments = tool_call.get("function", {}).get("arguments", "")
        split_arguments = split_json_objects(arguments)

        if len(split_arguments) <= 1:
            expanded.append(tool_call)
        else:
            for argument in split_arguments:
                cloned = copy.deepcopy(tool_call)
                cloned["id"] = f"call_{uuid4().hex[:24]}"
                cloned["function"]["arguments"] = argument
                expanded.append(cloned)

    return expanded


def get_citation_source_from_tool_result(
    tool_name: str, tool_params: dict, tool_result: str, tool_id: str = ""
) -> list[dict]:
    """
    Parse a tool's result and convert it to source dicts for citation display.

    Follows the source format conventions from get_sources_from_items:
    - source: file/item info object with id, name, type
    - document: list of document contents
    - metadata: list of metadata objects with source, file_id, name fields

    Returns a list of sources (usually one, but query_knowledge_files may return multiple).
    """
    _EXPECTS_LIST = {"search_web", "query_knowledge_files"}
    _EXPECTS_DICT = {
        "view_knowledge_file",
        "search_strong_sources",
        "web_research_strong",
        "notes_research_strong",
        "query_web_evidence",
        "local_corpus_list_domains",
        "local_corpus_list_disciplines",
        "local_corpus_frame_problem",
        "local_corpus_plan_axes",
        "local_corpus_collect_axis_evidence",
        "local_corpus_assess_evidence",
        "local_corpus_shortlist_books",
        "local_corpus_view_book_cards",
        "local_corpus_retrieve_evidence",
        "local_corpus_view_table",
        "local_corpus_view_figure_metadata",
    }

    try:
        try:
            tool_result = json.loads(tool_result)
        except (json.JSONDecodeError, TypeError):
            pass  # keep tool_result as-is (e.g. fetch_url returns plain text)
        if isinstance(tool_result, dict) and "error" in tool_result:
            return []

        # Validate tool_result type based on what the branch expects
        if tool_name in _EXPECTS_LIST and not isinstance(tool_result, list):
            return []
        elif tool_name in _EXPECTS_DICT and not isinstance(tool_result, dict):
            return []

        if tool_name == "search_web":
            # Parse JSON array: [{"title": "...", "link": "...", "snippet": "..."}]
            results = tool_result
            documents = []
            metadata = []

            for result in results:
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")

                documents.append(f"{title}\n{snippet}")
                metadata.append(
                    {
                        "source": link,
                        "name": title,
                        "url": link,
                    }
                )

            return [
                {
                    "source": {"name": "search_web", "id": "search_web"},
                    "document": documents,
                    "metadata": metadata,
                }
            ]
        elif tool_name in {
            "search_strong_sources",
            "web_research_strong",
            "notes_research_strong",
        }:
            payload = tool_result if isinstance(tool_result, dict) else {}
            results = []
            if isinstance(payload, dict):
                if isinstance(payload.get("citation_items"), list):
                    results = payload.get("citation_items", [])
                elif isinstance(payload.get("items"), list):
                    results = payload.get("items", [])
            documents = []
            metadata = []

            for result in results:
                if not isinstance(result, dict):
                    continue
                title = result.get("title", "")
                link = result.get("link", "")
                snippet = result.get("snippet", "")
                if not link:
                    continue
                documents.append(f"{title}\n{snippet}")
                metadata.append(
                    {
                        "source": link,
                        "name": title or link,
                        "url": link,
                    }
                )

            return [
                {
                    "source": {
                        "name": "web_research_strong",
                        "id": "web_research_strong",
                    },
                    "document": documents,
                    "metadata": metadata,
                }
            ]
        elif tool_name == "query_web_evidence":
            payload = tool_result if isinstance(tool_result, dict) else {}
            snippets = payload.get("snippets", []) if isinstance(payload, dict) else []
            documents = []
            metadata = []
            for snippet in snippets:
                if not isinstance(snippet, dict):
                    continue
                link = snippet.get("url", "")
                text = snippet.get("text", "")
                if not link or not text:
                    continue
                title = snippet.get("title", "") or link
                documents.append(f"{title}\n{text}")
                metadata.append(
                    {
                        "source": link,
                        "name": title,
                        "url": link,
                        "artifact_id": snippet.get("artifact_id"),
                        "domain": snippet.get("domain"),
                        "start": snippet.get("start"),
                        "end": snippet.get("end"),
                        "score": snippet.get("score"),
                    }
                )
            return [
                {
                    "source": {
                        "name": "query_web_evidence",
                        "id": "query_web_evidence",
                    },
                    "document": documents,
                    "metadata": metadata,
                }
            ]
        elif tool_name == "local_corpus_retrieve_evidence":
            payload = tool_result if isinstance(tool_result, dict) else {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            grouped_sources = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title", "") or "local corpus"
                book_id = item.get("book_id", "")
                key = book_id or title
                if key not in grouped_sources:
                    grouped_sources[key] = {
                        "source": {
                            "id": book_id,
                            "name": title,
                            "type": "local_corpus_book",
                        },
                        "document": [],
                        "metadata": [],
                    }
                grouped_sources[key]["document"].append(item.get("content", ""))
                grouped_sources[key]["metadata"].append(
                    {
                        "source": item.get("citation_label", title),
                        "name": title,
                        "book_id": book_id,
                        "domain": item.get("domain", ""),
                        "page_no": item.get("page_no"),
                        "section_path": item.get("section_path", ""),
                    }
                )
            return list(grouped_sources.values())
        elif tool_name == "local_corpus_collect_axis_evidence":
            payload = tool_result if isinstance(tool_result, dict) else {}
            axis_results = (
                payload.get("axis_results", []) if isinstance(payload, dict) else []
            )
            grouped_sources = {}
            for axis in axis_results:
                if not isinstance(axis, dict):
                    continue
                for item in axis.get("evidence_items") or []:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title", "") or "local corpus"
                    book_id = item.get("book_id", "")
                    key = book_id or title
                    if key not in grouped_sources:
                        grouped_sources[key] = {
                            "source": {
                                "id": book_id,
                                "name": title,
                                "type": "local_corpus_book",
                            },
                            "document": [],
                            "metadata": [],
                        }
                    grouped_sources[key]["document"].append(item.get("content", ""))
                    grouped_sources[key]["metadata"].append(
                        {
                            "source": item.get("citation_label", title),
                            "name": title,
                            "book_id": book_id,
                            "domain": item.get("domain", ""),
                            "page_no": item.get("page_no"),
                            "section_path": item.get("section_path", ""),
                            "axis_id": axis.get("axis_id", ""),
                        }
                    )
            return list(grouped_sources.values())
        elif tool_name == "local_corpus_view_table":
            payload = tool_result if isinstance(tool_result, dict) else {}
            if payload.get("error"):
                return []
            title = payload.get("title", "local corpus table")
            table_id = payload.get("table_id", "")
            return [
                {
                    "source": {
                        "id": table_id,
                        "name": f"{title} {table_id}".strip(),
                        "type": "local_corpus_table",
                    },
                    "document": [payload.get("content_text", "")],
                    "metadata": [
                        {
                            "source": f"{title} | {table_id}",
                            "name": title,
                            "book_id": payload.get("book_id", ""),
                            "domain": payload.get("domain", ""),
                            "page_no": payload.get("page_no"),
                            "section_path": payload.get("section_path", ""),
                        }
                    ],
                }
            ]

        elif tool_name == "view_knowledge_file":
            file_data = tool_result
            filename = file_data.get("filename", "Unknown File")
            file_id = file_data.get("id", "")
            knowledge_name = file_data.get("knowledge_name", "")

            return [
                {
                    "source": {
                        "id": file_id,
                        "name": filename,
                        "type": "file",
                    },
                    "document": [file_data.get("content", "")],
                    "metadata": [
                        {
                            "file_id": file_id,
                            "name": filename,
                            "source": filename,
                            **(
                                {"knowledge_name": knowledge_name}
                                if knowledge_name
                                else {}
                            ),
                        }
                    ],
                }
            ]

        elif tool_name == "fetch_url":
            url = tool_params.get("url", "")
            if isinstance(tool_result, dict) and tool_result.get("mode") == "store":
                stored_url = tool_result.get("url") or url
                stored_name = tool_result.get("title") or stored_url or "fetch_url"
                return [
                    {
                        "source": {
                            "name": stored_name,
                            "id": stored_url or "fetch_url",
                        },
                        "document": [
                            f"Stored web page artifact for {stored_name}".strip()
                        ],
                        "metadata": [
                            {
                                "source": stored_url,
                                "name": stored_name,
                                "url": stored_url,
                                "artifact_id": tool_result.get("artifact_id"),
                                "domain": tool_result.get("domain"),
                            }
                        ],
                    }
                ]

            content = tool_result if isinstance(tool_result, str) else str(tool_result)
            snippet = content[:500] + ("..." if len(content) > 500 else "")

            return [
                {
                    "source": {"name": url or "fetch_url", "id": url or "fetch_url"},
                    "document": [snippet],
                    "metadata": [
                        {
                            "source": url,
                            "name": url,
                            "url": url,
                        }
                    ],
                }
            ]

        elif tool_name == "query_knowledge_files":
            chunks = tool_result

            # Group chunks by source for better citation display
            # Each unique source becomes a separate source entry
            sources_by_file = {}

            for chunk in chunks:
                source_name = chunk.get("source", "Unknown")
                file_id = chunk.get("file_id", "")
                note_id = chunk.get("note_id", "")
                chunk_type = chunk.get("type", "file")
                content = chunk.get("content", "")

                # Use file_id or note_id as the key
                key = file_id or note_id or source_name

                if key not in sources_by_file:
                    sources_by_file[key] = {
                        "source": {
                            "id": file_id or note_id,
                            "name": source_name,
                            "type": chunk_type,
                        },
                        "document": [],
                        "metadata": [],
                    }

                sources_by_file[key]["document"].append(content)
                sources_by_file[key]["metadata"].append(
                    {
                        "file_id": file_id,
                        "name": source_name,
                        "source": source_name,
                        **({"note_id": note_id} if note_id else {}),
                    }
                )

            # Return all grouped sources as a list
            if sources_by_file:
                return list(sources_by_file.values())

            # Empty result fallback
            return []

        else:
            # Fallback for other tools
            return [
                {
                    "source": {
                        "name": tool_name,
                        "type": "tool",
                        "id": tool_id or tool_name,
                    },
                    "document": [str(tool_result)],
                    "metadata": [{"source": tool_name, "name": tool_name}],
                }
            ]
    except Exception as e:
        log.exception(f"Error parsing tool result for {tool_name}: {e}")
        return [
            {
                "source": {"name": tool_name, "type": "tool"},
                "document": [str(tool_result)],
                "metadata": [{"source": tool_name}],
            }
        ]


TOOL_JOURNEY_EVENT_CAP = 120
TOOL_JOURNEY_PREVIEW_CHARS = 280
SEARCH_NOTES_EMPTY_STREAK_LIMIT = 2
STRONG_WEB_TOOL_NAMES = {
    "web_research_strong",
    "search_strong_sources",
    "notes_research_strong",
}
NOTES_LOOKUP_TOOL_NAMES = {"notes_lookup", "search_notes"}
TOOL_NAME_ALIASES = {
    "search_strong_sources": "web_research_strong",
    "search_notes": "notes_lookup",
    "notes_research_strong": "web_research_strong",
}


def _is_debug_flag_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _truncate_telemetry_value(value: Any, max_chars: int = TOOL_JOURNEY_PREVIEW_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...[truncated]"


def _parse_json_if_possible(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _normalize_research_domain(value: Any) -> str:
    normalized = normalize_domain(str(value or "").strip())
    return normalized if normalized else ""


def _get_curated_domain_family_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for source in load_normalized_source_registry():
        domain = _normalize_research_domain(getattr(source, "domain", ""))
        if not domain:
            continue
        family = str(getattr(source, "family", "") or "").strip()
        if domain not in mapping:
            mapping[domain] = family
    return mapping


def _extract_domains_from_search_results(results: list[Any]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for item in results or []:
        if not isinstance(item, dict):
            continue
        candidate = item.get("link") or item.get("url") or item.get("source")
        domain = _normalize_research_domain(urlsplit(str(candidate or "")).netloc or candidate)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        domains.append(domain)
    return domains


def _research_turn_state(metadata: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None
    return metadata.setdefault(
        "research_turn_state",
        {
            "tool_calls": [],
            "stored_artifacts": [],
            "search_domains": [],
            "search_curated_domains": [],
            "search_domain_families": [],
            "research_discovery_lane": None,
            "strong_hardening_triggered": False,
            "strong_hardening_reason": None,
            "strong_hardening_improved_bundle": None,
            "broad_fallback_after_strong": False,
            "evidence_empty_after_fetch": False,
            "evidence_scope_mode": None,
            "recent_artifact_count": 0,
            "evidence_queries": [],
        },
    )


def _looks_numeric_or_risk_sensitive(text: str) -> bool:
    normalized = str(text or "").lower()
    if not normalized:
        return False
    if re.search(r"\b\d+(?:[.,]\d+)?\b", normalized):
        return True
    risk_markers = (
        "risk",
        "chance",
        "odds",
        "probability",
        "cost",
        "price",
        "casualt",
        "losses",
        "timeline",
        "date",
        "dates",
        "sanction",
        "exposure",
        "medical",
        "legal",
        "financial",
    )
    return any(marker in normalized for marker in risk_markers)


def _infer_strong_hardening_reason(
    metadata: Optional[dict[str, Any]], state: dict[str, Any]
) -> str:
    prompt = str((metadata or {}).get("user_prompt") or "").lower()
    explicit_terms = (
        "strong signals",
        "trusted sources",
        "trusted source",
        "authoritative",
        "verify",
        "verification",
        "fact-check",
        "fact check",
        "expert review",
        "expert analysis",
    )
    if any(term in prompt for term in explicit_terms):
        return "explicit_verification_request"
    if state.get("evidence_empty_after_fetch"):
        return "weak_evidence_after_fetch"
    if _looks_numeric_or_risk_sensitive(prompt):
        return "numeric_date_or_risk_sensitive_claims"
    if state.get("search_domains") and not state.get("search_curated_domains"):
        return "no_curated_trusted_domains_after_broad_discovery"
    if len(state.get("search_domain_families") or []) <= 1 and state.get("search_domains"):
        return "single_domain_family_after_broad_discovery"
    return "model_selected_hardening"


def _build_research_tool_trace_entry(
    tool_name: str, tool_params: dict[str, Any], parsed_result: Any
) -> dict[str, Any]:
    entry: dict[str, Any] = {"tool": tool_name}
    if "query" in tool_params:
        entry["query"] = str(tool_params.get("query") or "")
    if "url" in tool_params:
        entry["url"] = str(tool_params.get("url") or "")
    if "mode" in tool_params:
        entry["mode"] = str(tool_params.get("mode") or "")

    if tool_name == "search_web" and isinstance(parsed_result, list):
        entry["result_count"] = len(parsed_result)
        entry["domains"] = _extract_domains_from_search_results(parsed_result)
    elif tool_name in STRONG_WEB_TOOL_NAMES and isinstance(parsed_result, dict):
        entry["phase"] = parsed_result.get("phase")
        entry["next_action"] = parsed_result.get("next_action")
        entry["citation_count"] = len(parsed_result.get("citation_items") or [])
        entry["final_trusted_domains"] = parsed_result.get("final_trusted_domains")
        entry["fallback_reason"] = parsed_result.get("fallback_reason")
    elif tool_name == "fetch_url" and isinstance(parsed_result, dict):
        entry["status"] = parsed_result.get("status")
        entry["artifact_id"] = parsed_result.get("artifact_id")
        entry["domain"] = parsed_result.get("domain")
    elif tool_name == "query_web_evidence" and isinstance(parsed_result, dict):
        entry["status"] = parsed_result.get("status")
        entry["scope_mode"] = parsed_result.get("scope_mode")
        entry["evidence_strength"] = parsed_result.get("evidence_strength")
        entry["suggested_next_action"] = parsed_result.get("suggested_next_action")
        entry["snippets"] = len(parsed_result.get("snippets") or [])
        entry["searched_artifact_count"] = parsed_result.get("searched_artifact_count")
    return entry


def _update_research_turn_state(
    metadata: Optional[dict[str, Any]],
    *,
    tool_name: str,
    tool_params: dict[str, Any],
    tool_result: Any,
) -> list[dict[str, Any]]:
    canonical_name = TOOL_NAME_ALIASES.get(tool_name, tool_name)
    if canonical_name not in {"search_web", "web_research_strong", "fetch_url", "query_web_evidence"}:
        return []

    state = _research_turn_state(metadata)
    if state is None:
        return []

    parsed = _parse_json_if_possible(tool_result)
    state["tool_calls"].append(_build_research_tool_trace_entry(canonical_name, tool_params, parsed))

    events: list[dict[str, Any]] = []
    curated_map = _get_curated_domain_family_map()

    if canonical_name == "search_web" and isinstance(parsed, list):
        domains = _extract_domains_from_search_results(parsed)
        state["search_domains"] = domains
        state["search_curated_domains"] = [domain for domain in domains if domain in curated_map]
        state["search_domain_families"] = sorted(
            {
                curated_map.get(domain, "")
                for domain in state["search_curated_domains"]
                if curated_map.get(domain, "")
            }
        )
        if not state.get("research_discovery_lane"):
            state["research_discovery_lane"] = "search_web"
            events.append(
                {
                    "phase": "research_discovery_lane_selected",
                    "research_discovery_lane": "search_web",
                }
            )
        if state.get("strong_hardening_triggered") and not state.get("broad_fallback_after_strong"):
            state["broad_fallback_after_strong"] = True
            events.append(
                {
                    "phase": "research_broad_fallback_after_strong",
                    "research_discovery_lane": state.get("research_discovery_lane"),
                    "broad_fallback_after_strong": True,
                }
            )

    elif canonical_name == "web_research_strong" and isinstance(parsed, dict):
        if not state.get("research_discovery_lane"):
            state["research_discovery_lane"] = "web_research_strong"
            events.append(
                {
                    "phase": "research_discovery_lane_selected",
                    "research_discovery_lane": "web_research_strong",
                }
            )
        reason = _infer_strong_hardening_reason(metadata, state)
        improved = bool(
            (parsed.get("final_trusted_domains") or 0)
            or (parsed.get("citation_items") or [])
            or (parsed.get("coverage_complete") or False)
        )
        state["strong_hardening_triggered"] = True
        state["strong_hardening_reason"] = reason
        state["strong_hardening_improved_bundle"] = improved
        events.append(
            {
                "phase": "research_hardening_triggered",
                "research_discovery_lane": state.get("research_discovery_lane"),
                "strong_hardening_triggered": True,
                "strong_hardening_reason": reason,
            }
        )
        events.append(
            {
                "phase": "research_hardening_evaluated",
                "research_discovery_lane": state.get("research_discovery_lane"),
                "strong_hardening_improved_bundle": improved,
            }
        )

    elif canonical_name == "fetch_url" and isinstance(parsed, dict):
        if parsed.get("mode") == "store" and parsed.get("artifact_id"):
            artifact_id = str(parsed.get("artifact_id") or "").strip()
            known_ids = {
                str(item.get("artifact_id") or "")
                for item in state.get("stored_artifacts") or []
            }
            if artifact_id and artifact_id not in known_ids:
                state.setdefault("stored_artifacts", []).append(
                    {
                        "artifact_id": artifact_id,
                        "url": parsed.get("url"),
                        "domain": parsed.get("domain"),
                        "title": parsed.get("title"),
                    }
                )
                state["recent_artifact_count"] = len(state.get("stored_artifacts") or [])

    elif canonical_name == "query_web_evidence" and isinstance(parsed, dict):
        snippets = parsed.get("snippets") or []
        artifact_count = int(parsed.get("searched_artifact_count") or 0)
        state["evidence_scope_mode"] = parsed.get("scope_mode")
        state["recent_artifact_count"] = artifact_count
        state["evidence_empty_after_fetch"] = bool(
            state.get("stored_artifacts") and len(snippets) == 0
        )
        state.setdefault("evidence_queries", []).append(
            {
                "query": parsed.get("query"),
                "scope_mode": parsed.get("scope_mode"),
                "searched_artifact_count": artifact_count,
                "searched_artifact_ids": parsed.get("searched_artifact_ids") or [],
                "searched_domains": parsed.get("searched_domains") or [],
                "missing_artifact_ids": parsed.get("missing_artifact_ids") or [],
                "evidence_strength": parsed.get("evidence_strength"),
                "suggested_next_action": parsed.get("suggested_next_action"),
                "snippets": len(snippets),
            }
        )
        events.append(
            {
                "phase": "research_evidence_diagnostics",
                "research_discovery_lane": state.get("research_discovery_lane"),
                "strong_hardening_triggered": bool(state.get("strong_hardening_triggered")),
                "strong_hardening_reason": state.get("strong_hardening_reason"),
                "evidence_empty_after_fetch": bool(state.get("evidence_empty_after_fetch")),
                "evidence_scope_mode": parsed.get("scope_mode"),
                "recent_artifact_count": artifact_count,
            }
        )

    return events


def _tool_result_summary(tool_name: str, tool_result: Any) -> dict[str, Any]:
    parsed: Any = _parse_json_if_possible(tool_result)

    if tool_name in STRONG_WEB_TOOL_NAMES and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "next_action": parsed.get("next_action"),
            "queries": len(parsed.get("queries") or []),
            "items": len(parsed.get("items") or []),
            "evidence_items": len(parsed.get("evidence_items") or []),
            "citation_items": len(parsed.get("citation_items") or []),
            "candidate_count": parsed.get("candidate_count"),
            "evidence_count": parsed.get("evidence_count"),
            "citation_count": parsed.get("citation_count"),
            "coverage_complete": bool(parsed.get("coverage_complete", False)),
            "quality_score": parsed.get("quality_score"),
            "local_phase_executed": bool(parsed.get("local_phase_executed", False)),
            "brave_fallback_used": bool(parsed.get("brave_fallback_used", False)),
            "fallback_reason": parsed.get("fallback_reason"),
        }

    if tool_name == "query_web_evidence" and isinstance(parsed, dict):
        return {
            "status": parsed.get("status"),
            "snippets": len(parsed.get("snippets") or []),
            "narrow_count": parsed.get("narrow_count"),
            "wide_count": parsed.get("wide_count"),
            "wide_pass_used": bool(parsed.get("wide_pass_used", False)),
            "weak_narrow_evidence": bool(parsed.get("weak_narrow_evidence", False)),
            "scope_mode": parsed.get("scope_mode"),
            "searched_artifact_count": parsed.get("searched_artifact_count"),
            "evidence_strength": parsed.get("evidence_strength"),
            "suggested_next_action": parsed.get("suggested_next_action"),
        }

    if tool_name == "local_corpus_retrieve_evidence" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "next_action": parsed.get("next_action"),
            "domain": parsed.get("domain"),
            "book_ids": len(parsed.get("book_ids") or []),
            "items": len(parsed.get("items") or []),
            "candidate_count": parsed.get("candidate_count"),
            "fts_enabled": bool(parsed.get("fts_enabled", False)),
            "freshness_note": bool(parsed.get("freshness_note")),
        }

    if tool_name == "local_corpus_collect_axis_evidence" and isinstance(parsed, dict):
        axis_results = parsed.get("axis_results") or []
        direct_axes = sum(
            1 for axis in axis_results if (axis or {}).get("directness") == "direct"
        )
        return {
            "phase": parsed.get("phase"),
            "domain": parsed.get("domain"),
            "task_type": parsed.get("task_type"),
            "axis_count": parsed.get("axis_count"),
            "axes_with_evidence": sum(
                1 for axis in axis_results if (axis or {}).get("evidence_items")
            ),
            "direct_axes": direct_axes,
        }

    if tool_name == "local_corpus_frame_problem" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "domain": parsed.get("domain"),
            "primary_task_type": parsed.get("primary_task_type"),
            "secondary_task_types": len(parsed.get("secondary_task_types") or []),
            "needs_clarification": bool(parsed.get("needs_clarification")),
        }

    if tool_name == "local_corpus_plan_axes" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "domain": parsed.get("domain"),
            "task_type": parsed.get("task_type"),
            "axis_budget": parsed.get("axis_budget"),
            "coverage_limited": bool(parsed.get("coverage_limited")),
        }

    if tool_name == "local_corpus_assess_evidence" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "domain": parsed.get("domain"),
            "task_type": parsed.get("task_type"),
            "evidence_sufficiency": parsed.get("evidence_sufficiency"),
            "covered_axes": len(parsed.get("covered_axes") or []),
            "missing_axes": len(parsed.get("missing_axes") or []),
        }

    if tool_name == "local_corpus_shortlist_books" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "next_action": parsed.get("next_action"),
            "domain": parsed.get("domain"),
            "items": len(parsed.get("items") or []),
            "candidate_count": parsed.get("candidate_count"),
        }

    if tool_name == "search_web" and isinstance(parsed, list):
        return {
            "items": len(parsed),
            "domains": len(_extract_domains_from_search_results(parsed)),
        }

    if tool_name == "fetch_url":
        if isinstance(parsed, dict):
            return {
                "status": parsed.get("status"),
                "mode": parsed.get("mode"),
                "artifact_id": parsed.get("artifact_id"),
                "domain": parsed.get("domain"),
                "content_chars": parsed.get("content_chars"),
            }
        content = parsed if isinstance(parsed, str) else str(parsed or "")
        return {"content_chars": len(content)}

    if isinstance(parsed, dict):
        return {"keys": sorted(list(parsed.keys()))[:8]}
    if isinstance(parsed, list):
        return {"items": len(parsed)}

    return {"preview": _truncate_telemetry_value(parsed)}


def _append_tool_journey_event(
    metadata: dict, payload: dict[str, Any]
) -> Optional[dict[str, Any]]:
    params = metadata.get("params", {}) if isinstance(metadata, dict) else {}
    debug_enabled = isinstance(params, dict) and _is_debug_flag_enabled(
        params.get("debug_tool_journey")
    )
    tap_enabled = runtime_telemetry.is_enabled()

    if not debug_enabled and not tap_enabled:
        return None

    event = {
        "ts": int(time.time()),
        **payload,
    }

    if tap_enabled:
        runtime_telemetry.record(
            kind="tool_journey",
            payload=event,
            chat_id=metadata.get("chat_id"),
            message_id=metadata.get("message_id"),
            user_id=metadata.get("user_id"),
            model_id=(payload or {}).get("model_id") or metadata.get("model_id"),
        )

    if not debug_enabled:
        return None

    telemetry = metadata.setdefault(
        "tool_journey_telemetry",
        {
            "enabled": True,
            "chat_id": metadata.get("chat_id"),
            "message_id": metadata.get("message_id"),
            "events": [],
            "capped": False,
            "started_at": int(time.time()),
        },
    )
    events = telemetry.get("events")
    if not isinstance(events, list):
        events = []
        telemetry["events"] = events

    if len(events) >= TOOL_JOURNEY_EVENT_CAP:
        telemetry["capped"] = True
        return None

    debug_event = {**event, "index": len(events)}
    events.append(debug_event)
    return debug_event


async def _emit_tool_journey_event(
    metadata: Optional[dict[str, Any]],
    event_emitter,
    payload: dict[str, Any],
) -> Optional[dict[str, Any]]:
    if not isinstance(metadata, dict):
        return None

    event = _append_tool_journey_event(metadata, payload)
    if event and event_emitter:
        await event_emitter({"type": "chat:tool:journey", "data": event})
    return event


def _resolve_model_activity_actor(
    *, selected_via: Optional[str], fallback_used: bool
) -> str:
    if not fallback_used and selected_via in {"task_model", "task_model_external"}:
        return "bounded_specialist"
    return "active_model"


def _build_model_activity_event(
    *,
    phase: str,
    task_kind: str,
    operation: str,
    model_id: Optional[str],
    active_model_id: Optional[str],
    selected_via: Optional[str],
    route_source: Optional[str],
    reason: Optional[str],
    fallback_used: bool = False,
    duration_ms: Optional[int] = None,
    retry_count: Optional[int] = None,
    error_class: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": phase,
        "kind": "model_activity",
        "task_kind": task_kind,
        "operation": operation,
        "actor": _resolve_model_activity_actor(
            selected_via=selected_via,
            fallback_used=fallback_used,
        ),
        "model_id": model_id,
        "active_model_id": active_model_id,
        "selected_via": selected_via,
        "route_source": route_source,
        "fallback_used": bool(fallback_used),
        "reason": reason,
    }
    if duration_ms is not None:
        payload["duration_ms"] = int(duration_ms)
    if retry_count is not None:
        payload["retry_count"] = int(retry_count)
    if error_class:
        payload["error_class"] = error_class
    if status:
        payload["status"] = status
    return payload


def _is_empty_search_notes_result(tool_result: Any) -> bool:
    parsed: Any = tool_result
    if isinstance(tool_result, str):
        stripped = tool_result.strip()
        if not stripped:
            return True
        try:
            parsed = json.loads(stripped)
        except Exception:
            return False

    if parsed is None:
        return True

    if isinstance(parsed, list):
        return len(parsed) == 0

    if isinstance(parsed, dict):
        if parsed.get("error"):
            return False
        for key in ("items", "notes", "results", "data"):
            value = parsed.get(key)
            if isinstance(value, list):
                return len(value) == 0
        return len(parsed) == 0

    return False


def _preferred_web_tool_for_loop_breaker(tools: dict[str, Any]) -> Optional[str]:
    if "search_web" in tools:
        return "search_web"
    if "web_research_strong" in tools:
        return "web_research_strong"
    return None


def _build_search_notes_loop_breaker_result(
    streak: int,
    blocked: bool = False,
    tool_name: str = "notes_lookup",
    next_tool: Optional[str] = "search_web",
) -> str:
    if next_tool:
        hint = (
            "Stop using notes tools for web discovery. "
            f"Use {next_tool} for web evidence."
        )
        next_action = "switch_tool"
    else:
        hint = (
            "Stop using notes tools for web discovery. "
            "Internet tools are not available in this chat. Enable Internet Access, "
            "then retry."
        )
        next_action = "enable_internet_access"

    payload = {
        "status": "loop_breaker_active" if blocked else "loop_breaker_triggered",
        "tool": tool_name,
        "empty_streak": streak,
        "message": (
            f"{tool_name} returned no matches multiple times. "
            "This tool searches user notes only, not the web."
        ),
        "hint": hint,
        "next_tool": next_tool,
        "next_action": next_action,
    }
    return json.dumps(payload, ensure_ascii=False)


def split_content_and_whitespace(content):
    content_stripped = content.rstrip()
    original_whitespace = (
        content[len(content_stripped) :] if len(content) > len(content_stripped) else ""
    )
    return content_stripped, original_whitespace


def is_opening_code_block(content):
    backtick_segments = content.split("```")
    # Even number of segments means the last backticks are opening a new block
    return len(backtick_segments) > 1 and len(backtick_segments) % 2 == 0


def serialize_output(output: list) -> str:
    """
    Convert OR-aligned output items to HTML for display.
    For LLM consumption, use convert_output_to_messages() instead.
    """
    content = ""

    # First pass: collect function_call_output items by call_id for lookup
    tool_outputs = {}
    for item in output:
        if item.get("type") == "function_call_output":
            tool_outputs[item.get("call_id")] = item

    # Second pass: render items in order
    for idx, item in enumerate(output):
        item_type = item.get("type", "")

        if item_type == "message":
            for content_part in item.get("content", []):
                if "text" in content_part:
                    text = content_part.get("text", "").strip()
                    if text:
                        content = f"{content}{text}\n"

        elif item_type == "function_call":
            # Render tool call inline with its result (if available)
            if content and not content.endswith("\n"):
                content += "\n"

            call_id = item.get("call_id", "")
            name = item.get("name", "")
            arguments = item.get("arguments", "")

            result_item = tool_outputs.get(call_id)
            if result_item:
                result_text = ""
                for result_output in result_item.get("output", []):
                    if "text" in result_output:
                        output_text = result_output.get("text", "")
                        result_text += (
                            str(output_text)
                            if not isinstance(output_text, str)
                            else output_text
                        )
                files = result_item.get("files")
                embeds = result_item.get("embeds", "")

                content += f'<details type="tool_calls" done="true" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}" result="{html.escape(json.dumps(result_text, ensure_ascii=False))}" files="{html.escape(json.dumps(files)) if files else ""}" embeds="{html.escape(json.dumps(embeds))}">\n<summary>Tool Executed</summary>\n</details>\n'
            else:
                content += f'<details type="tool_calls" done="false" id="{call_id}" name="{name}" arguments="{html.escape(json.dumps(arguments))}">\n<summary>Executing...</summary>\n</details>\n'

        elif item_type == "function_call_output":
            # Already handled inline with function_call above
            pass

        elif item_type == "reasoning":
            reasoning_content = ""
            # Check for 'summary' (new structure) or 'content' (legacy/fallback)
            source_list = item.get("summary", []) or item.get("content", [])
            for content_part in source_list:
                if "text" in content_part:
                    reasoning_content += content_part.get("text", "")
                elif "summary" in content_part:  # Handle potential nested logic if any
                    pass

            reasoning_content = reasoning_content.strip()

            duration = item.get("duration")
            status = item.get("status", "in_progress")

            # Infer completion: if this reasoning item is NOT the last item,
            # render as done (a subsequent item means reasoning is complete)
            is_last_item = idx == len(output) - 1

            if content and not content.endswith("\n"):
                content += "\n"

            display = html.escape(
                "\n".join(
                    (f"> {line}" if not line.startswith(">") else line)
                    for line in reasoning_content.splitlines()
                )
            )

            if status == "completed" or duration is not None or not is_last_item:
                content = f'{content}<details type="reasoning" done="true" duration="{duration or 0}">\n<summary>Thought for {duration or 0} seconds</summary>\n{display}\n</details>\n'
            else:
                content = f'{content}<details type="reasoning" done="false">\n<summary>Thinking…</summary>\n{display}\n</details>\n'

        elif item_type == "open_webui:code_interpreter":
            content_stripped, original_whitespace = split_content_and_whitespace(
                content
            )
            if is_opening_code_block(content_stripped):
                content = content_stripped.rstrip("`").rstrip() + original_whitespace
            else:
                content = content_stripped + original_whitespace

            if content and not content.endswith("\n"):
                content += "\n"

            # Render the code_interpreter item as a <details> block
            # so the frontend Collapsible renders "Analyzing..."/"Analyzed".
            code = item.get("code", "").strip()
            lang = item.get("lang", "python")
            status = item.get("status", "in_progress")
            duration = item.get("duration")
            is_last_item = idx == len(output) - 1

            # Build inner content: code block
            display = ""
            if code:
                display = f"```{lang}\n{code}\n```"

            # Build output attribute as HTML-escaped JSON for CodeBlock.svelte
            ci_output = item.get("output")
            output_attr = ""
            if ci_output:
                if isinstance(ci_output, dict):
                    output_json = json.dumps(ci_output, ensure_ascii=False)
                else:
                    output_json = json.dumps(
                        {"result": str(ci_output)}, ensure_ascii=False
                    )
                output_attr = f' output="{html.escape(output_json)}"'

            if status == "completed" or duration is not None or not is_last_item:
                content += f'<details type="code_interpreter" done="true" duration="{duration or 0}"{output_attr}>\n<summary>Analyzed</summary>\n{display}\n</details>\n'
            else:
                content += f'<details type="code_interpreter" done="false"{output_attr}>\n<summary>Analyzing…</summary>\n{display}\n</details>\n'

    return content.strip()


def deep_merge(target, source):
    """
    Merge source into target recursively (returning new structure).
    - Dicts: Recursive merge.
    - Strings: Concatenation.
    - Others: Overwrite.
    """
    if isinstance(target, dict) and isinstance(source, dict):
        new_target = target.copy()
        for k, v in source.items():
            if k in new_target:
                new_target[k] = deep_merge(new_target[k], v)
            else:
                new_target[k] = v
        return new_target
    elif isinstance(target, str) and isinstance(source, str):
        return target + source
    else:
        return source


def handle_responses_streaming_event(
    data: dict,
    current_output: list,
) -> tuple[list, dict | None]:
    """
    Handle Responses API streaming events in a pure functional way.

    Args:
        data: The event data
        current_output: List of output items (treated as immutable)

    Returns:
        tuple[list, dict | None]: (new_output, metadata)
        - new_output: The updated output list.
        - metadata: Metadata to emit (e.g. usage), {} if update occurred, None if skip.
    """
    # Default: no change
    # Note: treating current_output as immutable, but avoiding full deepcopy for perf.
    # We will shallow copy only if we need to modify the list structure or items.

    event_type = data.get("type", "")

    if event_type == "response.output_item.added":
        item = data.get("item", {})
        if item:
            new_output = list(current_output)
            new_output.append(item)
            return new_output, None
        return current_output, None

    elif event_type == "response.content_part.added":
        part = data.get("part", {})
        output_index = data.get("output_index", len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            # Copy the item to mutate it
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "content" not in item:
                item["content"] = []
            else:
                # Copy content list
                item["content"] = list(item["content"])

            if item.get("type") == "reasoning":
                # Reasoning items should not have content parts
                pass
            else:
                item["content"].append(part)
            return new_output, None
        return current_output, None

    elif event_type == "response.reasoning_summary_part.added":
        part = data.get("part", {})
        output_index = data.get("output_index", len(current_output) - 1)

        if current_output and 0 <= output_index < len(current_output):
            new_output = list(current_output)
            item = new_output[output_index].copy()
            new_output[output_index] = item

            if "summary" not in item:
                item["summary"] = []
            else:
                item["summary"] = list(item["summary"])

            item["summary"].append(part)
            return new_output, None
        return current_output, None

    elif event_type.startswith("response.") and event_type.endswith(".delta"):
        # Generic Delta Handling
        parts = event_type.split(".")
        if len(parts) >= 3:
            delta_type = parts[1]
            delta = data.get("delta", "")

            output_index = data.get("output_index", len(current_output) - 1)

            if current_output and 0 <= output_index < len(current_output):
                new_output = list(current_output)
                item = new_output[output_index].copy()
                new_output[output_index] = item
                item_type = item.get("type", "")

                # Determine target field and object based on delta_type and item_type
                if delta_type == "function_call_arguments":
                    key = "arguments"
                    if item_type == "function_call":
                        # Function call args are usually strings
                        item[key] = item.get(key, "") + str(delta)
                else:
                    # Generic handling, refined by item type below
                    pass

                    if item_type == "message":
                        # Message items: "text"/"output_text" -> "text"
                        # "reasoning_text" -> Skipped (should use reasoning item)
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        elif delta_type in ["reasoning_text", "reasoning_summary_text"]:
                            # Skip reasoning updates for message items
                            return new_output, None
                        else:
                            key = delta_type

                        content_index = data.get("content_index", 0)
                        if "content" not in item:
                            item["content"] = []
                        else:
                            item["content"] = list(item["content"])
                        content_list = item["content"]

                        while len(content_list) <= content_index:
                            content_list.append({"type": "text", "text": ""})

                        # Copy the part to mutate it
                        part = content_list[content_index].copy()
                        content_list[content_index] = part

                        current_val = part.get(key)
                        if current_val is None:
                            # Initialize based on delta type
                            current_val = {} if isinstance(delta, dict) else ""

                        part[key] = deep_merge(current_val, delta)

                    elif item_type == "reasoning":
                        # Reasoning items: "reasoning_text"/"reasoning_summary_text" -> "text"
                        # "text"/"output_text" -> Skipped (should use message item)
                        if delta_type == "reasoning_summary_text":
                            # Summary updates -> item['summary']
                            key = "text"
                            summary_index = data.get("summary_index", 0)
                            if "summary" not in item:
                                item["summary"] = []
                            else:
                                item["summary"] = list(item["summary"])
                            summary_list = item["summary"]

                            while len(summary_list) <= summary_index:
                                summary_list.append(
                                    {"type": "summary_text", "text": ""}
                                )

                            part = summary_list[summary_index].copy()
                            summary_list[summary_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type == "reasoning_text":
                            # Reasoning body updates -> item['content']
                            key = "text"
                            content_index = data.get("content_index", 0)
                            if "content" not in item:
                                item["content"] = []
                            else:
                                item["content"] = list(item["content"])
                            content_list = item["content"]

                            while len(content_list) <= content_index:
                                # Reasoning content parts default to text
                                content_list.append({"type": "text", "text": ""})

                            part = content_list[content_index].copy()
                            content_list[content_index] = part

                            target_val = part.get(key, "")
                            part[key] = deep_merge(target_val, delta)

                        elif delta_type in ["text", "output_text"]:
                            return new_output, None
                        else:
                            # Fallback just in case other deltas target reasoning?
                            pass

                    else:
                        # Fallback for other item types
                        if delta_type in ["text", "output_text"]:
                            key = "text"
                        else:
                            key = delta_type

                        current_val = item.get(key)
                        if current_val is None:
                            current_val = {} if isinstance(delta, dict) else ""
                        item[key] = deep_merge(current_val, delta)

            return new_output, None

    elif event_type.startswith("response.") and event_type.endswith(".done"):
        # Delta Events: response.content_part.done, response.text.done, etc.
        parts = event_type.split(".")
        if len(parts) >= 3:
            type_name = parts[1]

            # 1. Handle specific Delta "done" signals
            if type_name == "content_part":
                # "Signaling that no further changes will occur to a content part"
                # If payloads contains the full part, we could update it.
                # Usually purely signaling in standard implementation, but we check payload.
                part = data.get("part")
                output_index = data.get("output_index", len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "content" in item:
                        item["content"] = list(item["content"])
                        content_index = data.get(
                            "content_index", len(item["content"]) - 1
                        )
                        if 0 <= content_index < len(item["content"]):
                            item["content"][content_index] = part
                            return new_output, {}
                return current_output, None

            elif type_name == "reasoning_summary_part":
                part = data.get("part")
                output_index = data.get("output_index", len(current_output) - 1)

                if part and current_output and 0 <= output_index < len(current_output):
                    new_output = list(current_output)
                    item = new_output[output_index].copy()
                    new_output[output_index] = item

                    if "summary" in item:
                        item["summary"] = list(item["summary"])
                        summary_index = data.get(
                            "summary_index", len(item["summary"]) - 1
                        )
                        if 0 <= summary_index < len(item["summary"]):
                            item["summary"][summary_index] = part
                            return new_output, {}
                return current_output, None

            # 2. Skip Output Item done (handled specifically below)
            if type_name == "output_item":
                pass

            # 3. Generic Field Done (text.done, audio.done)
            elif type_name not in ["completed", "failed"]:
                output_index = data.get("output_index", len(current_output) - 1)
                if current_output and 0 <= output_index < len(current_output):

                    key = (
                        "text"
                        if type_name
                        in [
                            "text",
                            "output_text",
                            "reasoning_text",
                            "reasoning_summary_text",
                        ]
                        else type_name
                    )
                    if type_name == "function_call_arguments":
                        key = "arguments"

                    if key in data:
                        final_value = data[key]
                        new_output = list(current_output)
                        item = new_output[output_index].copy()
                        new_output[output_index] = item
                        item_type = item.get("type", "")

                        if type_name == "function_call_arguments":
                            if item_type == "function_call":
                                item["arguments"] = final_value
                        elif item_type == "message":
                            content_index = data.get("content_index", 0)
                            if "content" in item:
                                item["content"] = list(item["content"])
                                if len(item["content"]) > content_index:
                                    part = item["content"][content_index].copy()
                                    item["content"][content_index] = part
                                    part[key] = final_value
                        elif item_type == "reasoning":
                            item["status"] = "completed"
                        else:
                            item[key] = final_value

                        return new_output, {}

        return current_output, None

    elif event_type == "response.output_item.done":
        # Delta Event: Output item complete
        item = data.get("item")
        output_index = data.get("output_index", len(current_output) - 1)

        new_output = list(current_output)
        if item and 0 <= output_index < len(current_output):
            new_output[output_index] = item
        elif item:
            new_output.append(item)
        return new_output, {}

    elif event_type == "response.completed":
        # State Machine Event: Completed
        response_data = data.get("response", {})
        final_output = response_data.get("output")

        new_output = final_output if final_output is not None else current_output

        # Ensure reasoning items are marked as completed in the final output
        if new_output:
            for item in new_output:
                if (
                    item.get("type") == "reasoning"
                    and item.get("status") != "completed"
                ):
                    item["status"] = "completed"

        return new_output, {"usage": response_data.get("usage"), "done": True}

    elif event_type == "response.in_progress":
        # State Machine Event: In Progress
        # We could extract metadata if needed, but for now just acknowledge iteration
        return current_output, None

    elif event_type == "response.failed":
        # State Machine Event: Failed
        error = data.get("response", {}).get("error", {})
        return current_output, {"error": error}

    else:
        return current_output, None

OWUI_SOURCE_KEY_ATTR_RE = re.compile(r'\bowui_key="([^"]+)"')
OWUI_SOURCE_HASH_LEN = 32
OWUI_WEB_FULL_CONTEXT_ONCE_MAX_CHATS = 2048
_WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT: dict[str, set[str]] = {}


def _normalize_http_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None

    candidate = value.strip()
    if not candidate:
        return None

    try:
        parsed = urlsplit(candidate)
    except Exception:
        return None

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        return None

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    return urlunsplit((scheme, parsed.netloc.lower(), path, parsed.query, ""))


def _hash_source_key(prefix: str, payload: str) -> str:
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:OWUI_SOURCE_HASH_LEN]
    return f"{prefix}:{digest}"


def _build_web_source_key_from_url(value: Any) -> Optional[str]:
    normalized = _normalize_http_url(value)
    if not normalized:
        return None
    return _hash_source_key("web", normalized)


def _get_web_candidate_urls_from_item(item: dict) -> list[str]:
    if not isinstance(item, dict):
        return []

    candidates: list[Any] = []
    item_type = item.get("type")

    if item_type == "web_search":
        urls = item.get("urls")
        if isinstance(urls, list):
            candidates.extend(urls)

        docs = item.get("docs")
        if isinstance(docs, list):
            for doc in docs:
                if not isinstance(doc, dict):
                    continue
                metadata = doc.get("metadata")
                if isinstance(metadata, dict):
                    candidates.extend(
                        [metadata.get("source"), metadata.get("url"), metadata.get("link")]
                    )

        candidates.extend([item.get("url"), item.get("name")])

    elif item_type == "text" and item.get("context") == "full":
        candidates.extend([item.get("url"), item.get("name")])
        file_meta = (item.get("file", {}) or {}).get("meta", {})
        if isinstance(file_meta, dict):
            candidates.extend(
                [file_meta.get("source"), file_meta.get("url"), file_meta.get("name")]
            )

    normalized_urls: list[str] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        normalized = _normalize_http_url(candidate)
        if not normalized or normalized in seen_urls:
            continue
        seen_urls.add(normalized)
        normalized_urls.append(normalized)

    return normalized_urls


def _build_web_source_keys_from_item(item: dict) -> set[str]:
    keys = set()
    for normalized_url in _get_web_candidate_urls_from_item(item):
        key = _build_web_source_key_from_url(normalized_url)
        if key:
            keys.add(key)
    return keys


def _is_web_full_context_once_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False

    item_type = item.get("type")
    if item_type == "web_search":
        return bool(_get_web_candidate_urls_from_item(item))

    return (
        item_type == "text"
        and item.get("context") == "full"
        and bool(_get_web_candidate_urls_from_item(item))
    )


def _extract_owui_source_keys_from_messages(messages: list[dict]) -> set[str]:
    keys: set[str] = set()

    for message in messages or []:
        if not isinstance(message, dict):
            continue

        content = message.get("content")
        text_parts: list[str] = []
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if isinstance(text, str):
                    text_parts.append(text)

        for text in text_parts:
            for match in OWUI_SOURCE_KEY_ATTR_RE.finditer(text):
                keys.add(match.group(1))

    return keys


def _build_effective_files_for_web_full_context_once(
    files: list[dict], injected_keys: set[str]
) -> tuple[list[dict], list[dict], list[dict]]:
    non_web_files: list[dict] = []
    pending_web_files: list[dict] = []
    seen_pending_signatures: set[tuple[str, ...]] = set()

    for item in files:
        if not _is_web_full_context_once_item(item):
            non_web_files.append(item)
            continue

        web_keys = _build_web_source_keys_from_item(item)
        if not web_keys:
            non_web_files.append(item)
            continue

        if web_keys.issubset(injected_keys):
            continue

        signature = tuple(sorted(web_keys))
        if signature in seen_pending_signatures:
            continue
        seen_pending_signatures.add(signature)
        pending_web_files.append(item)

    return [*non_web_files, *pending_web_files], non_web_files, pending_web_files


def _get_chat_id_for_web_full_context_once(body: dict) -> Optional[str]:
    metadata = body.get("metadata") if isinstance(body, dict) else None
    if not isinstance(metadata, dict):
        return None
    chat_id = metadata.get("chat_id")
    return chat_id if isinstance(chat_id, str) and chat_id else None


def _get_cached_web_keys_for_chat(chat_id: Optional[str]) -> set[str]:
    if not chat_id:
        return set()
    return set(_WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT.get(chat_id, set()))


def _merge_cached_web_keys_for_chat(chat_id: Optional[str], keys: set[str]) -> None:
    if not chat_id or not keys:
        return

    web_keys = {key for key in keys if isinstance(key, str) and key.startswith("web:")}
    if not web_keys:
        return

    current = _WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT.get(chat_id, set())
    _WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT[chat_id] = set(current).union(web_keys)

    # Guard memory in long-lived processes. Remove oldest inserted key set.
    if len(_WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT) > OWUI_WEB_FULL_CONTEXT_ONCE_MAX_CHATS:
        oldest_chat_id = next(iter(_WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT))
        if oldest_chat_id != chat_id:
            _WEB_FULL_CONTEXT_ONCE_KEYS_BY_CHAT.pop(oldest_chat_id, None)


def _extract_web_source_keys_from_sources(sources: list[dict]) -> set[str]:
    keys: set[str] = set()

    for source in sources or []:
        documents = source.get("document", []) if isinstance(source, dict) else []
        metadatas = source.get("metadata", []) if isinstance(source, dict) else []
        for doc_index, doc in enumerate(documents):
            metadata = metadatas[doc_index] if doc_index < len(metadatas) else {}
            key = _build_source_owui_key(source, metadata, doc, doc_index)
            if key.startswith("web:"):
                keys.add(key)

    return keys


def _build_source_owui_key(
    source: dict, metadata: Optional[dict], doc: Any, doc_index: int
) -> str:
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    source_info = source.get("source", {}) if isinstance(source, dict) else {}
    if not isinstance(source_info, dict):
        source_info = {}

    url_candidates: list[Any] = [
        metadata_dict.get("source"),
        metadata_dict.get("url"),
        metadata_dict.get("link"),
        source_info.get("url"),
        source_info.get("name"),
    ]

    source_urls = source_info.get("urls")
    if isinstance(source_urls, list) and doc_index < len(source_urls):
        url_candidates.append(source_urls[doc_index])

    for candidate in url_candidates:
        key = _build_web_source_key_from_url(candidate)
        if key:
            return key

    fallback_payload = json.dumps(
        {
            "source": source_info,
            "metadata": metadata_dict,
            "doc_index": doc_index,
            "doc_preview": doc[:256] if isinstance(doc, str) else str(doc)[:256],
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    return _hash_source_key("src", fallback_payload)


def get_source_context(
    sources: list, source_ids: dict = None, include_content: bool = True
) -> str:
    """
    Build <source> tag context string from citation sources.
    """
    context_string = ""
    if source_ids is None:
        source_ids = {}
    for source in sources:
        for doc_index, (doc, meta) in enumerate(
            zip(source.get("document", []), source.get("metadata", []))
        ):
            source_id = (
                meta.get("source") or source.get("source", {}).get("id") or "N/A"
            )
            if source_id not in source_ids:
                source_ids[source_id] = len(source_ids) + 1
            src_name = source.get("source", {}).get("name")
            body = doc if include_content else ""
            owui_key = _build_source_owui_key(source, meta, doc, doc_index)
            context_string += (
                f'<source id="{source_ids[source_id]}"'
                + (f' name="{src_name}"' if src_name else "")
                + (f' owui_key="{owui_key}"' if owui_key else "")
                + f">{body}</source>\n"
            )
    return context_string


def apply_source_context_to_messages(
    request: Request,
    messages: list,
    sources: list,
    user_message: str,
    include_content: bool = True,
) -> list:
    """
    Build source context from citation sources and apply to messages.
    Uses RAG template to format context for model consumption.

    When include_content is False, emit <source> tags with id/name but no
    document body — useful when the content is already present elsewhere
    (e.g. in a tool result message) and only citation markers are needed.
    """
    if not sources or not user_message:
        return messages

    context = get_source_context(sources, include_content=include_content)
    context = context.strip()
    if not context:
        return messages

    if RAG_SYSTEM_CONTEXT:
        return add_or_update_system_message(
            rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=True,
        )
    else:
        return add_or_update_user_message(
            rag_template(request.app.state.config.RAG_TEMPLATE, context, user_message),
            messages,
            append=False,
        )


def process_tool_result(
    request,
    tool_function_name,
    tool_result,
    tool_type,
    direct_tool=False,
    metadata=None,
    user=None,
):
    tool_result_embeds = []
    EXTERNAL_TOOL_TYPES = ("external", "action", "terminal")

    if isinstance(tool_result, HTMLResponse):
        content_disposition = tool_result.headers.get("Content-Disposition", "")
        if "inline" in content_disposition:
            content = tool_result.body.decode("utf-8", "replace")
            tool_result_embeds.append(content)

            if 200 <= tool_result.status_code < 300:
                tool_result = {
                    "status": "success",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                }
            elif 400 <= tool_result.status_code < 500:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Client error {tool_result.status_code} from embedded UI result.",
                }
            elif 500 <= tool_result.status_code < 600:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Server error {tool_result.status_code} from embedded UI result.",
                }
            else:
                tool_result = {
                    "status": "error",
                    "code": "ui_component",
                    "message": f"{tool_function_name}: Unexpected status code {tool_result.status_code} from embedded UI result.",
                }
        else:
            tool_result = tool_result.body.decode("utf-8", "replace")

    elif (tool_type in EXTERNAL_TOOL_TYPES and isinstance(tool_result, tuple)) or (
        direct_tool and isinstance(tool_result, list) and len(tool_result) == 2
    ):
        tool_result, tool_response_headers = tool_result

        try:
            if not isinstance(tool_response_headers, dict):
                tool_response_headers = dict(tool_response_headers)
        except Exception as e:
            tool_response_headers = {}
            log.debug(e)

        if tool_response_headers and isinstance(tool_response_headers, dict):
            content_disposition = tool_response_headers.get(
                "Content-Disposition",
                tool_response_headers.get("content-disposition", ""),
            )

            if "inline" in content_disposition:
                content_type = tool_response_headers.get(
                    "Content-Type",
                    tool_response_headers.get("content-type", ""),
                )
                location = tool_response_headers.get(
                    "Location",
                    tool_response_headers.get("location", ""),
                )

                if "text/html" in content_type:
                    # Display as iframe embed
                    tool_result_embeds.append(tool_result)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }
                elif location:
                    tool_result_embeds.append(location)
                    tool_result = {
                        "status": "success",
                        "code": "ui_component",
                        "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                    }

    tool_result_files = []

    if isinstance(tool_result, list):
        if tool_type == "mcp":  # MCP
            tool_response = []
            for item in tool_result:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        if isinstance(text, str):
                            try:
                                text = json.loads(text)
                            except json.JSONDecodeError:
                                pass
                        tool_response.append(text)
                    elif item.get("type") in ["image", "audio"]:
                        file_url = get_file_url_from_base64(
                            request,
                            f"data:{item.get('mimeType')};base64,{item.get('data', item.get('blob', ''))}",
                            {
                                "chat_id": metadata.get("chat_id", None),
                                "message_id": metadata.get("message_id", None),
                                "session_id": metadata.get("session_id", None),
                                "result": item,
                            },
                            user,
                        )

                        tool_result_files.append(
                            {
                                "type": item.get("type", "data"),
                                "url": file_url,
                            }
                        )
            tool_result = tool_response[0] if len(tool_response) == 1 else tool_response
        else:  # OpenAPI
            for item in tool_result:
                if isinstance(item, str) and item.startswith("data:"):
                    tool_result_files.append(
                        {
                            "type": "data",
                            "content": item,
                        }
                    )
                    tool_result.remove(item)

    if isinstance(tool_result, list):
        tool_result = {"results": tool_result}

    if isinstance(tool_result, dict) or isinstance(tool_result, list):
        tool_result = json.dumps(tool_result, indent=2, ensure_ascii=False)

    # Safety: ensure tool_result is always a string (or None) to prevent
    # downstream TypeError when concatenating (e.g. if an upstream callable
    # returned a tuple that was not unpacked by the branches above).
    if tool_result is not None and not isinstance(tool_result, str):
        if isinstance(tool_result, tuple):
            # execute_tool_server returns (data, headers); unpack the data part
            tool_result = (
                json.dumps(tool_result[0], indent=2, ensure_ascii=False)
                if len(tool_result) > 0
                else ""
            )
        else:
            tool_result = str(tool_result)

    if tool_type == "terminal" and isinstance(tool_result, str):
        tool_result = _maybe_persist_terminal_tool_result(
            metadata=metadata,
            tool_function_name=tool_function_name,
            tool_result=tool_result,
        )

    return tool_result, tool_result_files, tool_result_embeds


async def terminal_event_handler(
    tool_function_name: str,
    tool_function_params: dict,
    tool_result,
    event_emitter,
):
    """Emit terminal:* events for Open Terminal tools.

    - display_file  → emits 'terminal:display_file' to open the file preview.
    - write_file / replace_file_content → emits 'terminal:write_file' to refresh.
    - run_command → emits 'terminal:run_command' with cwd to refresh if relevant.
    """
    if not event_emitter:
        return

    if tool_function_name == "display_file":
        path = tool_function_params.get("path", "")
        if not path:
            return
        # Only emit if the file actually exists
        parsed = tool_result
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
        if isinstance(parsed, dict) and parsed.get("exists") is False:
            return

        await event_emitter(
            {
                "type": f"terminal:{tool_function_name}",
                "data": {"path": path},
            }
        )
    elif tool_function_name in ("write_file", "replace_file_content"):
        path = tool_function_params.get("path", "")
        if not path:
            return
        await event_emitter(
            {
                "type": f"terminal:{tool_function_name}",
                "data": {"path": path},
            }
        )
    elif tool_function_name == "run_command":
        await event_emitter(
            {
                "type": "terminal:run_command",
                "data": {},
            }
        )


async def chat_completion_tools_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel, models, tools
) -> tuple[dict, dict]:
    async def get_content_from_response(response) -> Optional[str]:
        content = None
        if hasattr(response, "body_iterator"):
            async for chunk in response.body_iterator:
                data = json.loads(chunk.decode("utf-8", "replace"))
                content = data["choices"][0]["message"]["content"]

            # Cleanup any remaining background tasks if necessary
            if response.background is not None:
                await response.background()
        else:
            content = response["choices"][0]["message"]["content"]
        return content

    def get_tools_function_calling_payload(messages, task_model_id, content, selection):
        user_message = get_last_user_message(messages)

        if user_message and messages and messages[-1]["role"] == "user":
            # Remove the last user message to avoid duplication
            messages = messages[:-1]

        recent_messages = messages[-4:] if len(messages) > 4 else messages
        chat_history = "\n".join(
            f"{message['role'].upper()}: \"\"\"{get_content_from_message(message)}\"\"\""
            for message in recent_messages
        )

        prompt = (
            f"History:\n{chat_history}\nQuery: {user_message}"
            if chat_history
            else f"Query: {user_message}"
        )

        return {
            "model": task_model_id,
            "messages": [
                {"role": "system", "content": content},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "metadata": {
                "task": str(TASKS.FUNCTION_CALLING),
                "bounded_specialist": _build_bounded_specialist_telemetry(
                    selection,
                    selected_model=task_model_id,
                    reason="tool_selection",
                ),
            },
        }

    event_caller = extra_params["__event_call__"]
    event_emitter = extra_params["__event_emitter__"]
    metadata = extra_params["__metadata__"]
    debug_tool_journey = _is_debug_flag_enabled(
        metadata.get("params", {}).get("debug_tool_journey")
    )
    if debug_tool_journey:
        metadata["tool_journey_telemetry"] = {
            "enabled": True,
            "chat_id": metadata.get("chat_id"),
            "message_id": metadata.get("message_id"),
            "events": [],
            "capped": False,
            "started_at": int(time.time()),
        }

    bounded_specialist_selection = get_bounded_specialist_model_selection(
        body["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
        task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
    )
    task_model_id = bounded_specialist_selection["model_id"]

    skip_files = False
    sources = []
    executed_tool_names: set[str] = set()
    forced_tool_call = _build_forced_default_selector_tool_call(
        metadata, tools
    )

    async def tool_call_handler(tool_call):
        nonlocal skip_files

        log.debug(f"{tool_call=}")

        tool_function_name = tool_call.get("name", None)
        if tool_function_name not in tools:
            return body, {}

        tool_function_params = tool_call.get("parameters", {})

        tool = None
        tool_type = ""
        direct_tool = False
        started_at = time.time()

        start_event = _append_tool_journey_event(
            metadata,
            {
                "phase": "tool_execute_start",
                "tool": tool_function_name,
                "params_preview": _truncate_telemetry_value(tool_function_params),
            },
        )
        if start_event and event_emitter:
            await event_emitter({"type": "chat:tool:journey", "data": start_event})

        try:
            tool = tools[tool_function_name]
            tool_type = tool.get("type", "")
            direct_tool = tool.get("direct", False)

            spec = tool.get("spec", {})
            allowed_params = (
                spec.get("parameters", {}).get("properties", {}).keys()
            )
            tool_function_params = {
                k: v for k, v in tool_function_params.items() if k in allowed_params
            }

            if tool.get("direct", False):
                tool_result = await event_caller(
                    {
                        "type": "execute:tool",
                        "data": {
                            "id": str(uuid4()),
                            "name": tool_function_name,
                            "params": tool_function_params,
                            "server": tool.get("server", {}),
                            "session_id": metadata.get("session_id", None),
                        },
                    }
                )
            else:
                tool_function = tool["callable"]
                tool_result = await tool_function(**tool_function_params)

        except Exception as e:
            tool_result = str(e)

        tool_result, tool_result_files, tool_result_embeds = process_tool_result(
            request,
            tool_function_name,
            tool_result,
            tool_type,
            direct_tool,
            metadata,
            user,
        )

        completion_event = _append_tool_journey_event(
            metadata,
            {
                "phase": "tool_execute_done",
                "tool": tool_function_name,
                "duration_ms": int((time.time() - started_at) * 1000),
                "result_summary": _tool_result_summary(tool_function_name, tool_result),
            },
        )
        if completion_event and event_emitter:
            await event_emitter(
                {"type": "chat:tool:journey", "data": completion_event}
            )

        for research_event in _update_research_turn_state(
            metadata,
            tool_name=tool_function_name,
            tool_params=tool_function_params,
            tool_result=tool_result,
        ):
            await _emit_tool_journey_event(metadata, event_emitter, research_event)

        if event_emitter:
            await terminal_event_handler(
                tool_function_name,
                tool_function_params,
                tool_result,
                event_emitter,
            )

            if tool_result_files:
                await event_emitter(
                    {
                        "type": "files",
                        "data": {
                            "files": tool_result_files,
                        },
                    }
                )

            if tool_result_embeds:
                await event_emitter(
                    {
                        "type": "embeds",
                        "data": {
                            "embeds": tool_result_embeds,
                        },
                    }
                )

        if tool_result:
            tool = tools[tool_function_name]
            tool_id = tool.get("tool_id", "")

            tool_name = (
                f"{tool_id}/{tool_function_name}" if tool_id else f"{tool_function_name}"
            )

            sources.append(
                {
                    "source": {
                        "name": (f"{tool_name}"),
                    },
                    "document": [str(tool_result)],
                    "metadata": [
                        {
                            "source": (f"{tool_name}"),
                            "parameters": tool_function_params,
                        }
                    ],
                    "tool_result": True,
                }
            )

        executed_tool_names.add(_canonical_tool_name(tool_function_name))

        if (
            tools[tool_function_name].get("metadata", {}).get("file_handler", False)
        ):
            skip_files = True

    if forced_tool_call:
        await tool_call_handler(forced_tool_call)

        log.debug(f"tool_contexts: {sources}")

        if skip_files and "files" in body.get("metadata", {}):
            del body["metadata"]["files"]

        payload = {"sources": sources}
        if debug_tool_journey and isinstance(metadata.get("tool_journey_telemetry"), dict):
            metadata["tool_journey_telemetry"]["completed_at"] = int(time.time())
            payload["toolJourneyTelemetry"] = metadata["tool_journey_telemetry"]

        return body, payload

    specs = [tool["spec"] for tool in tools.values()]
    tools_specs = json.dumps(specs, ensure_ascii=False)

    if request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE != "":
        template = request.app.state.config.TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE
    else:
        template = DEFAULT_TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE

    selector_guidance = _build_default_selector_guidance(
        metadata, tools, body.get("messages", [])
    )
    if selector_guidance:
        template = f"{template.rstrip()}\n\n{selector_guidance}"

    tools_function_calling_prompt = tools_function_calling_generation_template(
        template, tools_specs
    )
    payload = get_tools_function_calling_payload(
        body["messages"],
        task_model_id,
        tools_function_calling_prompt,
        bounded_specialist_selection,
    )

    selector_started_at = time.monotonic()
    await _emit_tool_journey_event(
        metadata,
        event_emitter,
        _build_model_activity_event(
            phase="model_task_start",
            task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
            operation="tool_selection",
            model_id=task_model_id,
            active_model_id=body.get("model"),
            selected_via=bounded_specialist_selection.get(
                "selected_via", "active_model"
            ),
            route_source=bounded_specialist_selection.get(
                "route_source", "active_model"
            ),
            reason="tool_selection",
        ),
    )

    selector_error_class = None
    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        log.debug(f"{response=}")
        content = await get_content_from_response(response)
        log.debug(f"{content=}")

        if not content:
            await _emit_tool_journey_event(
                metadata,
                event_emitter,
                _build_model_activity_event(
                    phase="model_task_done",
                    task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
                    operation="tool_selection",
                    model_id=task_model_id,
                    active_model_id=body.get("model"),
                    selected_via=bounded_specialist_selection.get(
                        "selected_via", "active_model"
                    ),
                    route_source=bounded_specialist_selection.get(
                        "route_source", "active_model"
                    ),
                    reason="tool_selection",
                    duration_ms=int((time.monotonic() - selector_started_at) * 1000),
                    status="empty",
                ),
            )
            return body, {}

        try:
            content = content[content.find("{") : content.rfind("}") + 1]
            if not content:
                raise Exception("No JSON object found in the response")

            result = json.loads(content)

            # check if "tool_calls" in result
            selected_tool_calls = result.get("tool_calls") or [result]
            selected_tool_names = [
                _canonical_tool_name((tool_call or {}).get("name"))
                for tool_call in selected_tool_calls
            ]

            for index, tool_call in enumerate(selected_tool_calls):
                if _should_upgrade_default_search_web_tool_call(
                    metadata,
                    tools,
                    body.get("messages", []),
                    tool_call,
                    executed_tool_names=executed_tool_names,
                    pending_tool_names=set(selected_tool_names[index + 1 :]),
                ):
                    tool_call = _upgrade_default_search_web_tool_call(tool_call)

                    rewrite_event = _append_tool_journey_event(
                        metadata,
                        {
                            "phase": "tool_call_upgrade",
                            "from_tool": "search_web",
                            "to_tool": "web_research_strong",
                            "reason": "local_corpus_then_focused_search_ladder",
                        },
                    )
                    if rewrite_event and event_emitter:
                        await event_emitter(
                            {"type": "chat:tool:journey", "data": rewrite_event}
                        )

                await tool_call_handler(tool_call)

        except Exception as e:
            log.debug(f"Error: {e}")
            content = None
            selector_error_class = e.__class__.__name__
    except Exception as e:
        log.debug(f"Error: {e}")
        content = None
        selector_error_class = e.__class__.__name__
        await _emit_tool_journey_event(
            metadata,
            event_emitter,
            _build_model_activity_event(
                phase="model_task_done",
                task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
                operation="tool_selection",
                model_id=task_model_id,
                active_model_id=body.get("model"),
                selected_via=bounded_specialist_selection.get(
                    "selected_via", "active_model"
                ),
                route_source=bounded_specialist_selection.get(
                    "route_source", "active_model"
                ),
                reason="tool_selection",
                duration_ms=int((time.monotonic() - selector_started_at) * 1000),
                error_class=e.__class__.__name__,
                status="error",
            ),
        )
        selector_error_class = None

    log.debug(f"tool_contexts: {sources}")

    if content is not None:
        await _emit_tool_journey_event(
            metadata,
            event_emitter,
            _build_model_activity_event(
                phase="model_task_done",
                task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
                operation="tool_selection",
                model_id=task_model_id,
                active_model_id=body.get("model"),
                selected_via=bounded_specialist_selection.get(
                    "selected_via", "active_model"
                ),
                route_source=bounded_specialist_selection.get(
                    "route_source", "active_model"
                ),
                reason="tool_selection",
                duration_ms=int((time.monotonic() - selector_started_at) * 1000),
                status="ok",
            ),
        )
    elif selector_error_class:
        await _emit_tool_journey_event(
            metadata,
            event_emitter,
            _build_model_activity_event(
                phase="model_task_done",
                task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
                operation="tool_selection",
                model_id=task_model_id,
                active_model_id=body.get("model"),
                selected_via=bounded_specialist_selection.get(
                    "selected_via", "active_model"
                ),
                route_source=bounded_specialist_selection.get(
                    "route_source", "active_model"
                ),
                reason="tool_selection",
                duration_ms=int((time.monotonic() - selector_started_at) * 1000),
                error_class=selector_error_class,
                status="error",
            ),
        )

    if skip_files and "files" in body.get("metadata", {}):
        del body["metadata"]["files"]

    payload = {"sources": sources}
    if debug_tool_journey and isinstance(metadata.get("tool_journey_telemetry"), dict):
        metadata["tool_journey_telemetry"]["completed_at"] = int(time.time())
        payload["toolJourneyTelemetry"] = metadata["tool_journey_telemetry"]

    return body, payload


async def chat_memory_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    try:
        results = await query_memory(
            request,
            QueryMemoryForm(
                **{
                    "content": get_last_user_message(form_data["messages"]) or "",
                    "k": 3,
                }
            ),
            user,
        )
    except Exception as e:
        log.debug(e)
        results = None

    user_context = ""
    if results and hasattr(results, "documents"):
        if results.documents and len(results.documents) > 0:
            for doc_idx, doc in enumerate(results.documents[0]):
                created_at_date = "Unknown Date"

                if results.metadatas[0][doc_idx].get("created_at"):
                    created_at_timestamp = results.metadatas[0][doc_idx]["created_at"]
                    created_at_date = time.strftime(
                        "%Y-%m-%d", time.localtime(created_at_timestamp)
                    )

                user_context += f"{doc_idx + 1}. [{created_at_date}] {doc}\n"

    form_data["messages"] = add_or_update_system_message(
        f"User Context:\n{user_context}\n", form_data["messages"], append=True
    )

    return form_data


def _normalize_web_search_planner_mode(raw_mode: Any) -> str:
    mode = (str(raw_mode or "hybrid_rewriter")).strip().lower()
    return mode if mode in PLANNER_MODES else "hybrid_rewriter"


def _extract_completion_message_content(response: Any) -> str:
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                return content
    if hasattr(response, "body"):
        try:
            payload = json.loads(response.body.decode("utf-8"))
            if isinstance(payload, dict):
                return str(payload.get("detail", ""))
        except Exception:
            pass
    return ""


def _resolve_models_for_task(request: Request) -> dict[str, dict]:
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        return {request.state.model["id"]: request.state.model}
    return request.app.state.MODELS


def _normalize_generated_queries(queries_raw: Any) -> list[str]:
    if not isinstance(queries_raw, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in queries_raw:
        if not isinstance(item, str):
            continue
        query = re.sub(r"\s+", " ", item).strip()
        if not query:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(query)
    return normalized


def _parse_generated_queries_output(raw_output: str) -> list[str]:
    payload = _extract_json_payload(raw_output) or {}
    queries = _normalize_generated_queries(payload.get("queries"))
    if queries:
        return queries

    fallback = re.sub(r"\s+", " ", (raw_output or "")).strip()
    return [fallback] if fallback else []


def _build_web_search_conversation_context(
    messages: list[dict],
    *,
    max_turns: int = 4,
    max_chars: int = 1200,
) -> str:
    if not isinstance(messages, list):
        return ""

    max_entries = max(2, int(max_turns) * 2)
    snippets: list[str] = []

    for message in reversed(messages):
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue

        content = get_content_from_message(message)
        if not isinstance(content, str):
            continue

        text = re.sub(r"\s+", " ", content).strip()
        if not text:
            continue
        if len(text) > 320:
            text = text[:320].rstrip()

        snippets.append(f"{role}: {text}")
        if len(snippets) >= max_entries:
            break

    if not snippets:
        return ""

    snippets.reverse()
    context = "\n".join(snippets)
    if len(context) > max_chars:
        context = context[-max_chars:]
    return context


def _build_bounded_specialist_telemetry(
    selection: dict[str, Any],
    *,
    selected_model: str,
    reason: str,
    fallback_used: bool = False,
    duration_ms: Optional[int] = None,
    error_class: Optional[str] = None,
) -> dict[str, Any]:
    telemetry = {
        "task_kind": selection.get("task_kind"),
        "route_source": selection.get("route_source"),
        "selected_model": selected_model,
        "selected_via": (
            "active_model" if fallback_used else selection.get("selected_via")
        ),
        "fallback_used": fallback_used,
        "reason": reason,
    }
    if duration_ms is not None:
        telemetry["duration_ms"] = int(duration_ms)
    if error_class:
        telemetry["error_class"] = error_class
    return telemetry


async def _run_active_model_web_query_generation(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    messages: list[dict],
    chat_id: Optional[str],
    timeout_ms: int,
    max_completion_tokens: int,
    metadata: Optional[dict[str, Any]] = None,
    event_emitter=None,
    active_chat_model_id: Optional[str] = None,
    task_kind: str = BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
    operation: str = "planner_query_generation",
    selected_via: str = "active_model",
    route_source: str = "active_model",
    reason: str = "active_model_query_generation",
    fallback_used: bool = False,
) -> tuple[list[str], dict[str, Any]]:
    models = _resolve_models_for_task(request)
    if active_model_id not in models:
        raise ValueError(
            f"Active model not found for query generation: {active_model_id}"
        )

    started_at = time.monotonic()
    resolved_active_chat_model_id = active_chat_model_id or active_model_id
    await _emit_tool_journey_event(
        metadata,
        event_emitter,
        _build_model_activity_event(
            phase="model_task_start",
            task_kind=task_kind,
            operation=operation,
            model_id=active_model_id,
            active_model_id=resolved_active_chat_model_id,
            selected_via=selected_via,
            route_source=route_source,
            reason=reason,
            fallback_used=fallback_used,
        ),
    )
    template = (
        (request.app.state.config.QUERY_GENERATION_PROMPT_TEMPLATE or "").strip()
        or DEFAULT_QUERY_GENERATION_PROMPT_TEMPLATE
    )
    prompt = query_generation_template(template, messages, user)
    timeout_seconds = max(0.5, float(timeout_ms) / 1000.0)

    payload = {
        "model": active_model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "temperature": 0.0,
        "max_completion_tokens": max(64, int(max_completion_tokens)),
        "think": False,
        "params": {
            "think": False,
            "custom_params": {
                "chat_template_kwargs": {
                    "enable_thinking": False,
                }
            },
        },
        "metadata": {
            **(request.state.metadata if hasattr(request.state, "metadata") else {}),
            "task": "web_search_query_generation_active_model",
            "chat_id": chat_id,
            "task_body": {"type": "web_search_query_generation_active_model"},
        },
    }

    last_error: Optional[Exception] = None
    for retry_count in range(2):
        try:
            response = await asyncio.wait_for(
                generate_chat_completion(
                    request,
                    form_data=payload,
                    user=user,
                    bypass_system_prompt=True,
                ),
                timeout=timeout_seconds,
            )
            raw_output = _extract_completion_message_content(response)
            queries = _parse_generated_queries_output(raw_output)
            if not queries:
                raise ValueError("No web search queries generated by active model")

            duration_ms = int((time.monotonic() - started_at) * 1000)
            await _emit_tool_journey_event(
                metadata,
                event_emitter,
                _build_model_activity_event(
                    phase="model_task_done",
                    task_kind=task_kind,
                    operation=operation,
                    model_id=active_model_id,
                    active_model_id=resolved_active_chat_model_id,
                    selected_via=selected_via,
                    route_source=route_source,
                    reason=reason,
                    fallback_used=fallback_used,
                    duration_ms=duration_ms,
                    retry_count=retry_count,
                    status="ok",
                ),
            )
            return queries, {
                "model_used": active_model_id,
                "selected_model": active_model_id,
                "selected_via": selected_via,
                "route_source": route_source,
                "fallback_used": fallback_used,
                "reason": reason,
                "retry_count": retry_count,
                "raw_output": raw_output,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            last_error = e

    if last_error:
        await _emit_tool_journey_event(
            metadata,
            event_emitter,
            _build_model_activity_event(
                phase="model_task_done",
                task_kind=task_kind,
                operation=operation,
                model_id=active_model_id,
                active_model_id=resolved_active_chat_model_id,
                selected_via=selected_via,
                route_source=route_source,
                reason=reason,
                fallback_used=fallback_used,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                retry_count=1,
                error_class=last_error.__class__.__name__,
                status="error",
            ),
        )
        raise last_error
    raise ValueError("Active model query generation failed without specific error")


async def _run_bounded_specialist_web_query_generation(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    messages: list[dict],
    chat_id: Optional[str],
    timeout_ms: int,
    max_completion_tokens: int,
    metadata: Optional[dict[str, Any]] = None,
    event_emitter=None,
) -> tuple[list[str], dict[str, Any]]:
    models = _resolve_models_for_task(request)
    selection = get_bounded_specialist_model_selection(
        active_model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
        task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
    )
    selected_model_id = selection["model_id"]
    started_at = time.monotonic()

    try:
        queries, meta = await _run_active_model_web_query_generation(
            request,
            user=user,
            active_model_id=selected_model_id,
            messages=messages,
            chat_id=chat_id,
            timeout_ms=timeout_ms,
            max_completion_tokens=max_completion_tokens,
            metadata=metadata,
            event_emitter=event_emitter,
            active_chat_model_id=active_model_id,
            task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
            operation="planner_query_generation",
            selected_via=selection.get("selected_via", "active_model"),
            route_source=selection.get("route_source", "active_model"),
            reason="planner_query_generation",
        )
        meta.update(
            _build_bounded_specialist_telemetry(
                selection,
                selected_model=selected_model_id,
                reason="planner_query_generation",
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        )
        return queries, meta
    except Exception as e:
        if selected_model_id == active_model_id:
            raise

        queries, meta = await _run_active_model_web_query_generation(
            request,
            user=user,
            active_model_id=active_model_id,
            messages=messages,
            chat_id=chat_id,
            timeout_ms=timeout_ms,
            max_completion_tokens=max_completion_tokens,
            metadata=metadata,
            event_emitter=event_emitter,
            active_chat_model_id=active_model_id,
            task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
            operation="planner_query_generation",
            selected_via="active_model",
            route_source=selection.get("route_source", "active_model"),
            reason="planner_query_generation",
            fallback_used=True,
        )
        meta.update(
            _build_bounded_specialist_telemetry(
                selection,
                selected_model=active_model_id,
                reason="planner_query_generation",
                fallback_used=True,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_class=e.__class__.__name__,
            )
        )
        return queries, meta


async def _run_web_search_rewriter(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    user_message: str,
    conversation_context: Optional[str],
    plan,
    max_queries: int,
    timeout_ms: int,
    max_repair_attempts: int,
    max_completion_tokens: int,
    temperature: float,
    chat_id: Optional[str],
    metadata: Optional[dict[str, Any]] = None,
    event_emitter=None,
    active_chat_model_id: Optional[str] = None,
    task_kind: str = BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_REWRITER,
    operation: str = "planner_query_rewriter",
    selected_via: str = "active_model",
    route_source: str = "active_model",
    reason: str = "active_model_query_rewriter",
    fallback_used: bool = False,
) -> tuple[list, dict[str, Any]]:
    models = _resolve_models_for_task(request)
    if active_model_id not in models:
        raise ValueError(f"Active model not found for rewriter: {active_model_id}")

    started_at = time.monotonic()
    resolved_active_chat_model_id = active_chat_model_id or active_model_id
    await _emit_tool_journey_event(
        metadata,
        event_emitter,
        _build_model_activity_event(
            phase="model_task_start",
            task_kind=task_kind,
            operation=operation,
            model_id=active_model_id,
            active_model_id=resolved_active_chat_model_id,
            selected_via=selected_via,
            route_source=route_source,
            reason=reason,
            fallback_used=fallback_used,
        ),
    )
    prompt = build_rewriter_prompt(
        user_message=user_message,
        plan=plan,
        conversation_context=conversation_context,
        max_queries=max_queries,
    )
    timeout_seconds = max(0.5, timeout_ms / 1000.0)
    last_error: Optional[Exception] = None

    for retry_count in range(2):
        payload = {
            "model": active_model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Rewrite web search queries only. Return plain text with one query "
                        "per line. Preferred format: kind|domain|query. "
                        "Do not include markdown or explanations."
                    ),
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "stream": False,
            "temperature": temperature,
            "max_completion_tokens": max_completion_tokens,
            "think": False,
            "params": {
                "think": False,
                "custom_params": {
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                    }
                },
            },
            "metadata": {
                **(
                    request.state.metadata
                    if hasattr(request.state, "metadata")
                    else {}
                ),
                "task": "web_search_query_rewriter",
                "chat_id": chat_id,
                "task_body": {
                    "type": "web_search_query_rewriter",
                    "max_queries": max_queries,
                },
            },
        }

        try:
            response = await asyncio.wait_for(
                generate_chat_completion(
                    request,
                    form_data=payload,
                    user=user,
                    bypass_system_prompt=True,
                ),
                timeout=timeout_seconds,
            )
            raw_output = _extract_completion_message_content(response)
            parsed_queries = parse_rewriter_output(raw_output)
            validated_queries = validate_or_repair_rewriter_queries(
                parsed_queries,
                plan,
                max_queries=max_queries,
                max_repair_attempts=max_repair_attempts,
            )
            duration_ms = int((time.monotonic() - started_at) * 1000)
            await _emit_tool_journey_event(
                metadata,
                event_emitter,
                _build_model_activity_event(
                    phase="model_task_done",
                    task_kind=task_kind,
                    operation=operation,
                    model_id=active_model_id,
                    active_model_id=resolved_active_chat_model_id,
                    selected_via=selected_via,
                    route_source=route_source,
                    reason=reason,
                    fallback_used=fallback_used,
                    duration_ms=duration_ms,
                    retry_count=retry_count,
                    status="ok",
                ),
            )
            return validated_queries, {
                "model_used": active_model_id,
                "selected_model": active_model_id,
                "selected_via": selected_via,
                "route_source": route_source,
                "fallback_used": fallback_used,
                "reason": reason,
                "retry_count": retry_count,
                "raw_output": raw_output,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            last_error = e

    if last_error:
        await _emit_tool_journey_event(
            metadata,
            event_emitter,
            _build_model_activity_event(
                phase="model_task_done",
                task_kind=task_kind,
                operation=operation,
                model_id=active_model_id,
                active_model_id=resolved_active_chat_model_id,
                selected_via=selected_via,
                route_source=route_source,
                reason=reason,
                fallback_used=fallback_used,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                retry_count=1,
                error_class=last_error.__class__.__name__,
                status="error",
            ),
        )
        raise last_error
    raise ValueError("Rewriter failed without specific error")


async def _run_bounded_specialist_web_search_rewriter(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    user_message: str,
    conversation_context: Optional[str],
    plan,
    max_queries: int,
    timeout_ms: int,
    max_repair_attempts: int,
    max_completion_tokens: int,
    temperature: float,
    chat_id: Optional[str],
    metadata: Optional[dict[str, Any]] = None,
    event_emitter=None,
) -> tuple[list, dict[str, Any]]:
    models = _resolve_models_for_task(request)
    selection = get_bounded_specialist_model_selection(
        active_model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
        task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_REWRITER,
    )
    selected_model_id = selection["model_id"]
    started_at = time.monotonic()

    try:
        queries, meta = await _run_web_search_rewriter(
            request,
            user=user,
            active_model_id=selected_model_id,
            user_message=user_message,
            conversation_context=conversation_context,
            plan=plan,
            max_queries=max_queries,
            timeout_ms=timeout_ms,
            max_repair_attempts=max_repair_attempts,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            chat_id=chat_id,
            metadata=metadata,
            event_emitter=event_emitter,
            active_chat_model_id=active_model_id,
            task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_REWRITER,
            operation="planner_query_rewriter",
            selected_via=selection.get("selected_via", "active_model"),
            route_source=selection.get("route_source", "active_model"),
            reason="planner_query_rewriter",
        )
        meta.update(
            _build_bounded_specialist_telemetry(
                selection,
                selected_model=selected_model_id,
                reason="planner_query_rewriter",
                duration_ms=int((time.monotonic() - started_at) * 1000),
            )
        )
        return queries, meta
    except Exception as e:
        if selected_model_id == active_model_id:
            raise

        queries, meta = await _run_web_search_rewriter(
            request,
            user=user,
            active_model_id=active_model_id,
            user_message=user_message,
            conversation_context=conversation_context,
            plan=plan,
            max_queries=max_queries,
            timeout_ms=timeout_ms,
            max_repair_attempts=max_repair_attempts,
            max_completion_tokens=max_completion_tokens,
            temperature=temperature,
            chat_id=chat_id,
            metadata=metadata,
            event_emitter=event_emitter,
            active_chat_model_id=active_model_id,
            task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_REWRITER,
            operation="planner_query_rewriter",
            selected_via="active_model",
            route_source=selection.get("route_source", "active_model"),
            reason="planner_query_rewriter",
            fallback_used=True,
        )
        meta.update(
            _build_bounded_specialist_telemetry(
                selection,
                selected_model=active_model_id,
                reason="planner_query_rewriter",
                fallback_used=True,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                error_class=e.__class__.__name__,
            )
        )
        return queries, meta


def _estimate_tokens_from_text(text: str) -> int:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0
    # Lightweight approximation used only for context budgeting.
    return max(1, math.ceil(len(cleaned) / 4))


def _extract_json_payload(raw_output: str) -> Optional[dict[str, Any]]:
    content = (raw_output or "").strip()
    if not content:
        return None
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    json_start = content.find("{")
    json_end = content.rfind("}")
    if json_start < 0 or json_end < json_start:
        return None
    try:
        payload = json.loads(content[json_start : json_end + 1])
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _build_round_robin_web_chunks(
    sources: list[dict[str, Any]],
    *,
    chunk_chars: int,
    max_chunks_per_source: int,
) -> list[dict[str, Any]]:
    chunk_chars = max(256, int(chunk_chars))
    max_chunks_per_source = max(1, int(max_chunks_per_source))

    cursors: list[dict[str, Any]] = []
    for source_idx, source in enumerate(sources):
        docs = source.get("document") or []
        metadatas = source.get("metadata") or []
        distances = source.get("distances") or []
        for doc_idx, doc in enumerate(docs):
            if not isinstance(doc, str) or not doc.strip():
                continue
            metadata = (
                metadatas[doc_idx]
                if doc_idx < len(metadatas) and isinstance(metadatas[doc_idx], dict)
                else {}
            )
            distance = distances[doc_idx] if doc_idx < len(distances) else None
            cursors.append(
                {
                    "source_idx": source_idx,
                    "source": source.get("source") or {},
                    "metadata": metadata,
                    "distance": distance,
                    "doc_text": doc,
                    "offset": 0,
                    "doc_idx": doc_idx,
                }
            )

    source_chunk_counts: dict[int, int] = {}
    chunks: list[dict[str, Any]] = []

    while True:
        emitted_any = False
        for cursor in cursors:
            source_idx = cursor["source_idx"]
            if source_chunk_counts.get(source_idx, 0) >= max_chunks_per_source:
                continue

            doc_text = cursor["doc_text"]
            start = cursor["offset"]
            if start >= len(doc_text):
                continue

            end = min(start + chunk_chars, len(doc_text))
            cursor["offset"] = end
            chunk_text = doc_text[start:end].strip()
            if not chunk_text:
                continue

            source_chunk_counts[source_idx] = source_chunk_counts.get(source_idx, 0) + 1
            chunks.append(
                {
                    "source_idx": source_idx,
                    "source": cursor["source"],
                    "metadata": cursor["metadata"],
                    "distance": cursor["distance"],
                    "doc_idx": cursor["doc_idx"],
                    "text": chunk_text,
                }
            )
            emitted_any = True

        if not emitted_any:
            break

    return chunks


def _format_evidence_for_judge(
    selected_chunks: list[dict[str, Any]], *, max_input_chars: int
) -> str:
    remaining = max(256, int(max_input_chars))
    parts: list[str] = []

    for idx, chunk in enumerate(selected_chunks, start=1):
        metadata = chunk.get("metadata") or {}
        source = chunk.get("source") or {}
        source_id = metadata.get("source") or source.get("id") or "N/A"
        source_name = metadata.get("title") or source.get("name") or ""

        header = f"[{idx}] source={source_id}"
        if source_name:
            header += f" title={source_name}"
        header += "\n"

        if remaining <= len(header):
            break

        body_budget = remaining - len(header) - 2
        body = (chunk.get("text") or "")[:body_budget].strip()
        if not body:
            continue

        block = f"{header}{body}\n\n"
        parts.append(block)
        remaining -= len(block)
        if remaining <= 0:
            break

    return "".join(parts).strip()


def _parse_evidence_judge_output(raw_output: str) -> dict[str, Any]:
    payload = _extract_json_payload(raw_output) or {}
    confidence = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    missing_facets: list[str] = []
    if isinstance(payload.get("missing_facets"), list):
        missing_facets = [
            str(item).strip()
            for item in payload["missing_facets"]
            if isinstance(item, str) and item.strip()
        ]

    enough = bool(payload.get("enough", False))
    reason = str(payload.get("reason", "")).strip()
    return {
        "enough": enough,
        "confidence": confidence,
        "missing_facets": missing_facets,
        "reason": reason,
    }


async def _run_web_search_evidence_judge(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    user_message: str,
    evidence_text: str,
    timeout_ms: int,
    max_completion_tokens: int,
) -> dict[str, Any]:
    models = _resolve_models_for_task(request)
    if active_model_id not in models:
        return {
            "enough": False,
            "confidence": 0.0,
            "missing_facets": [],
            "reason": "active_model_not_found",
            "model_used": None,
            "fallback_used": False,
            "error": "active_model_not_found",
        }

    fallback_model_id = get_task_model_id(
        active_model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )
    candidate_model_ids = [active_model_id]
    if fallback_model_id and fallback_model_id not in candidate_model_ids:
        candidate_model_ids.append(fallback_model_id)

    timeout_seconds = max(0.5, float(timeout_ms) / 1000.0)
    prompt = (
        "Decide if current web evidence is sufficient to answer the user request.\n"
        "Return strict JSON only with keys: enough(boolean), confidence(number 0..1), "
        "missing_facets(array of strings), reason(string).\n\n"
        f"User request:\n{user_message}\n\n"
        f"Evidence chunks:\n{evidence_text}"
    )

    last_error: Optional[Exception] = None
    for idx, model_id in enumerate(candidate_model_ids):
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a retrieval evidence sufficiency judge. Output strict JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.0,
            "max_completion_tokens": max(32, int(max_completion_tokens)),
            "think": False,
            "params": {
                "think": False,
                "custom_params": {
                    "chat_template_kwargs": {
                        "enable_thinking": False,
                    }
                },
            },
            "metadata": {
                **(
                    request.state.metadata
                    if hasattr(request.state, "metadata")
                    else {}
                ),
                "task": "web_search_evidence_judge",
                "task_body": {"type": "web_search_evidence_judge"},
            },
        }
        try:
            response = await asyncio.wait_for(
                generate_chat_completion(request, form_data=payload, user=user),
                timeout=timeout_seconds,
            )
            parsed = _parse_evidence_judge_output(
                _extract_completion_message_content(response)
            )
            parsed["model_used"] = model_id
            parsed["fallback_used"] = idx > 0
            parsed["error"] = None
            return parsed
        except Exception as e:
            last_error = e

    return {
        "enough": False,
        "confidence": 0.0,
        "missing_facets": [],
        "reason": "judge_failed",
        "model_used": None,
        "fallback_used": False,
        "error": str(last_error) if last_error else "judge_failed",
    }


def _rebuild_sources_from_selected_chunks(
    sources: list[dict[str, Any]], selected_chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    buckets: dict[int, dict[str, Any]] = {}
    for chunk in selected_chunks:
        source_idx = int(chunk["source_idx"])
        if source_idx not in buckets:
            source_item = sources[source_idx]
            bucket = {
                "source": source_item.get("source") or {},
                "document": [],
                "metadata": [],
            }
            if "distances" in source_item:
                bucket["distances"] = []
            buckets[source_idx] = bucket

        bucket = buckets[source_idx]
        bucket["document"].append(chunk["text"])
        bucket["metadata"].append(chunk.get("metadata") or {})
        if "distances" in bucket:
            bucket["distances"].append(chunk.get("distance"))

    return [buckets[idx] for idx in sorted(buckets.keys())]


async def _apply_web_search_evidence_saturation(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    user_message: str,
    sources: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cfg = request.app.state.config

    max_tokens = max(256, int(getattr(cfg, "WEB_SEARCH_EVIDENCE_MAX_TOKENS", 8192) or 8192))
    chunk_tokens = max(
        64, int(getattr(cfg, "WEB_SEARCH_EVIDENCE_CHUNK_TOKENS", 768) or 768)
    )
    max_chunks_per_source = max(
        1,
        int(getattr(cfg, "WEB_SEARCH_EVIDENCE_MAX_CHUNKS_PER_SOURCE", 3) or 3),
    )
    judge_every_chunks = max(
        1, int(getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_EVERY_CHUNKS", 2) or 2)
    )
    judge_min_chunks = max(
        1, int(getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_MIN_CHUNKS", 2) or 2)
    )
    judge_confidence = float(
        getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_CONFIDENCE", 0.7) or 0.7
    )
    judge_timeout_ms = max(
        500, int(getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_TIMEOUT_MS", 2500) or 2500)
    )
    judge_max_completion_tokens = max(
        32,
        int(
            getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_MAX_COMPLETION_TOKENS", 128) or 128
        ),
    )
    judge_max_input_chars = max(
        512,
        int(getattr(cfg, "WEB_SEARCH_EVIDENCE_JUDGE_MAX_INPUT_CHARS", 16000) or 16000),
    )

    candidates = _build_round_robin_web_chunks(
        sources,
        chunk_chars=chunk_tokens * 4,
        max_chunks_per_source=max_chunks_per_source,
    )
    if not candidates:
        return sources, {
            "enabled": True,
            "stop_reason": "no_candidates",
            "chunks_available": 0,
            "chunks_selected": 0,
            "estimated_tokens_selected": 0,
            "max_tokens": max_tokens,
            "judge_checks": 0,
            "judge_last": None,
        }

    selected_chunks: list[dict[str, Any]] = []
    selected_tokens = 0
    seen_chunk_hashes: set[str] = set()
    judge_checks = 0
    last_judge: Optional[dict[str, Any]] = None
    stop_reason = "budget_exhausted"

    for candidate in candidates:
        token_cost = _estimate_tokens_from_text(candidate["text"])
        if token_cost <= 0:
            continue
        if selected_tokens + token_cost > max_tokens:
            stop_reason = "token_budget_reached"
            break

        chunk_hash = hashlib.sha1(candidate["text"].encode("utf-8")).hexdigest()
        if chunk_hash in seen_chunk_hashes:
            continue
        seen_chunk_hashes.add(chunk_hash)

        selected_chunks.append(candidate)
        selected_tokens += token_cost

        if len(selected_chunks) < judge_min_chunks:
            continue
        if len(selected_chunks) % judge_every_chunks != 0:
            continue

        evidence_text = _format_evidence_for_judge(
            selected_chunks, max_input_chars=judge_max_input_chars
        )
        if not evidence_text:
            continue

        judge_result = await _run_web_search_evidence_judge(
            request,
            user=user,
            active_model_id=active_model_id,
            user_message=user_message,
            evidence_text=evidence_text,
            timeout_ms=judge_timeout_ms,
            max_completion_tokens=judge_max_completion_tokens,
        )
        judge_checks += 1
        last_judge = judge_result

        if (
            judge_result.get("enough")
            and float(judge_result.get("confidence", 0.0)) >= judge_confidence
        ):
            stop_reason = "judge_enough"
            break

    if not selected_chunks:
        return sources, {
            "enabled": True,
            "stop_reason": "no_selection",
            "chunks_available": len(candidates),
            "chunks_selected": 0,
            "estimated_tokens_selected": 0,
            "max_tokens": max_tokens,
            "judge_checks": judge_checks,
            "judge_last": last_judge,
        }

    saturated_sources = _rebuild_sources_from_selected_chunks(sources, selected_chunks)
    return saturated_sources, {
        "enabled": True,
        "stop_reason": stop_reason,
        "chunks_available": len(candidates),
        "chunks_selected": len(selected_chunks),
        "estimated_tokens_selected": selected_tokens,
        "max_tokens": max_tokens,
        "judge_checks": judge_checks,
        "judge_last": last_judge,
    }


async def chat_web_search_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    event_emitter = extra_params["__event_emitter__"]
    metadata = extra_params.get("__metadata__")
    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search",
                "description": "Searching the web",
                "done": False,
            },
        }
    )

    messages = form_data["messages"]
    user_message = get_last_user_message(messages)
    conversation_context = _build_web_search_conversation_context(messages)

    queries = []
    plan_payload = None

    if request.app.state.config.ENABLE_WEB_SEARCH_PLANNER:
        try:
            plan = build_web_search_plan(
                user_message,
                conversation_context=conversation_context,
                max_targeted_domains=max(
                    0,
                    int(
                        request.app.state.config.WEB_SEARCH_PLANNER_MAX_TARGETED_DOMAINS_PER_WAVE
                        or 4
                    ),
                ),
                local_first=bool(
                    getattr(request.app.state.config, "WEB_SEARCH_LOCAL_FIRST", True)
                ),
            )
            planner_mode = _normalize_web_search_planner_mode(
                getattr(request.app.state.config, "WEB_SEARCH_PLANNER_MODE", "hybrid_rewriter")
            )
            plan.mode = planner_mode

            planned_queries = build_base_planned_queries(plan, targeted_slots=3)
            rewriter_raw_output = None

            if planner_mode in {"hybrid_rewriter", "model_only"}:
                try:
                    rewriter_kwargs = {
                        "request": request,
                        "user": user,
                        "active_model_id": form_data["model"],
                        "user_message": user_message,
                        "conversation_context": conversation_context,
                        "plan": plan,
                        "max_queries": max(
                            1,
                            int(
                                getattr(
                                    request.app.state.config,
                                    "WEB_SEARCH_PLANNER_REWRITER_MAX_QUERIES",
                                    6,
                                )
                                or 6
                            ),
                        ),
                        "timeout_ms": max(
                            500,
                            int(
                                getattr(
                                    request.app.state.config,
                                    "WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS",
                                    3500,
                                )
                                or 3500
                            ),
                        ),
                        "max_repair_attempts": max(
                            0,
                            int(
                                getattr(
                                    request.app.state.config,
                                    "WEB_SEARCH_PLANNER_REWRITER_MAX_REPAIR_ATTEMPTS",
                                    1,
                                )
                                or 1
                            ),
                        ),
                        "max_completion_tokens": max(
                            64,
                            int(
                                getattr(
                                    request.app.state.config,
                                    "WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS",
                                    384,
                                )
                                or 384
                            ),
                        ),
                        "temperature": float(
                            getattr(
                                request.app.state.config,
                                "WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE",
                                0.0,
                            )
                            or 0.0
                        ),
                        "chat_id": extra_params.get("__chat_id__"),
                        "metadata": metadata,
                        "event_emitter": event_emitter,
                    }
                    if getattr(
                        request.app.state.config,
                        "ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER",
                        False,
                    ):
                        rewriter_queries, rewriter_meta = (
                            await _run_bounded_specialist_web_search_rewriter(
                                **rewriter_kwargs
                            )
                        )
                    else:
                        rewriter_queries, rewriter_meta = await _run_web_search_rewriter(
                            **rewriter_kwargs
                        )
                    planned_queries = build_planned_queries_from_rewriter(
                        plan,
                        rewriter_queries,
                        targeted_slots=3,
                    )
                    plan.rewriter_model_used = rewriter_meta.get("model_used")
                    plan.rewriter_fallback_used = bool(
                        rewriter_meta.get("fallback_used")
                    )
                    plan.rewriter_retry_count = int(
                        rewriter_meta.get("retry_count", 0) or 0
                    )
                    plan.rewriter_selected_via = rewriter_meta.get("selected_via")
                    plan.rewriter_route_source = rewriter_meta.get("route_source")
                    plan.rewriter_duration_ms = rewriter_meta.get("duration_ms")
                    plan.rewriter_reason = rewriter_meta.get("reason")
                    plan.rewriter_error_class = rewriter_meta.get("error_class")
                    rewriter_raw_output = rewriter_meta.get("raw_output")
                except Exception as e:
                    plan.mode = "rules_only"
                    plan.fallback_reason = f"rewriter_failed:{str(e)}"
                    planned_queries = build_base_planned_queries(plan, targeted_slots=3)

            plan.planned_queries = planned_queries
            plan_payload = (
                plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()  # type: ignore[attr-defined]
            )

            queries = [planned.query for planned in planned_queries]
            queries = [query for query in queries if query and query.strip()]

            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search_plan_generated",
                        "plan": {
                            "mode": plan.mode,
                            "intent": plan.intent,
                            "topic": plan.topic,
                            "time_sensitive": plan.time_sensitive,
                            "community_requested": plan.community_requested,
                            "selected_domains": plan.selected_domains,
                            "rewriter_model_used": plan.rewriter_model_used,
                            "rewriter_selected_via": plan.rewriter_selected_via,
                            "rewriter_route_source": plan.rewriter_route_source,
                            "rewriter_duration_ms": plan.rewriter_duration_ms,
                            "rewriter_reason": plan.rewriter_reason,
                            "rewriter_error_class": plan.rewriter_error_class,
                            "rewriter_fallback_used": plan.rewriter_fallback_used,
                            "rewriter_retry_count": plan.rewriter_retry_count,
                            "fallback_reason": plan.fallback_reason,
                            "anchors": plan.anchors,
                            "intent_requirements": plan.intent_requirements,
                            "allowed_domains_ranked": plan.allowed_domains_ranked,
                        },
                        "rewriter_raw_output": rewriter_raw_output,
                        "queries": queries,
                        "done": False,
                    },
                }
            )
        except Exception as e:
            log.exception("Web search planner failed; falling back to legacy queries")
            queries = []
            plan_payload = None

    if not queries:
        try:
            query_generation_kwargs = {
                "request": request,
                "user": user,
                "active_model_id": form_data["model"],
                "messages": messages,
                "chat_id": extra_params.get("__chat_id__"),
                "timeout_ms": max(
                    500,
                    int(
                        getattr(
                            request.app.state.config,
                            "WEB_SEARCH_PLANNER_REWRITER_TIMEOUT_MS",
                            3500,
                        )
                        or 3500
                    ),
                ),
                "max_completion_tokens": max(
                    64,
                    int(
                        getattr(
                            request.app.state.config,
                            "WEB_SEARCH_PLANNER_REWRITER_MAX_COMPLETION_TOKENS",
                            384,
                        )
                        or 384
                    ),
                ),
                "metadata": metadata,
                "event_emitter": event_emitter,
            }
            if getattr(
                request.app.state.config,
                "ENABLE_TASK_MODEL_WEB_SEARCH_PLANNER",
                False,
            ):
                queries, _ = await _run_bounded_specialist_web_query_generation(
                    **query_generation_kwargs
                )
            else:
                queries, _ = await _run_active_model_web_query_generation(
                    **query_generation_kwargs,
                    active_chat_model_id=form_data["model"],
                    task_kind=BOUNDED_SPECIALIST_TASK_KIND_WEB_SEARCH_QUERY_GENERATION,
                    operation="planner_query_generation",
                )
        except Exception as e:
            log.exception(e)
            queries = [user_message]

    if ENABLE_QUERIES_CACHE:
        request.state.cached_queries = queries

    # Check if generated queries are empty
    if len(queries) == 1 and queries[0].strip() == "":
        queries = [user_message]

    # Check if queries are not found
    if len(queries) == 0:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "No search query generated",
                    "done": True,
                },
            }
        )
        return form_data

    await event_emitter(
        {
            "type": "status",
            "data": {
                "action": "web_search_queries_generated",
                "queries": queries,
                "done": False,
            },
        }
    )

    try:
        results = await process_web_search(
            request,
            SearchForm(queries=queries, plan=plan_payload),
            user=user,
        )

        if results:
            result_queries = results.get("queries", queries)
            files = form_data.get("files", [])

            if results.get("collection_names"):
                for col_idx, collection_name in enumerate(
                    results.get("collection_names")
                ):
                    files.append(
                        {
                            "collection_name": collection_name,
                            "name": ", ".join(result_queries),
                            "type": "web_search",
                            "urls": results["filenames"],
                            "queries": result_queries,
                        }
                    )
            elif results.get("docs"):
                # Invoked when bypass embedding and retrieval is set to True
                docs = results["docs"]
                files.append(
                    {
                        "docs": docs,
                        "name": ", ".join(result_queries),
                        "type": "web_search",
                        "urls": results["filenames"],
                        "queries": result_queries,
                    }
                )

            form_data["files"] = files

            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "Searched {{count}} sites",
                        "urls": results["filenames"],
                        "items": results.get("items", []),
                        "queries": result_queries,
                        "plan": plan_payload,
                        "planner": results.get("planner"),
                        "loaded_count": results.get("loaded_count"),
                        "done": True,
                    },
                }
            )
        else:
            await event_emitter(
                {
                    "type": "status",
                    "data": {
                        "action": "web_search",
                        "description": "No search results found",
                        "done": True,
                        "error": True,
                    },
                }
            )

    except Exception as e:
        log.exception(e)
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "web_search",
                    "description": "An error occurred while searching the web",
                    "queries": queries,
                    "done": True,
                    "error": True,
                },
            }
        )

    return form_data


def get_images_from_messages(message_list):
    images = []

    for message in reversed(message_list):

        message_images = []
        for file in message.get("files", []):
            if file.get("type") == "image":
                message_images.append(file.get("url"))
            elif file.get("content_type", "").startswith("image/"):
                message_images.append(file.get("url"))

        if message_images:
            images.append(message_images)

    return images


def get_image_urls(delta_images, request, metadata, user) -> list[str]:
    if not isinstance(delta_images, list):
        return []

    image_urls = []
    for img in delta_images:
        if not isinstance(img, dict) or img.get("type") != "image_url":
            continue

        url = img.get("image_url", {}).get("url")
        if not url:
            continue

        if url.startswith("data:image/png;base64"):
            url = get_image_url_from_base64(request, url, metadata, user)

        image_urls.append(url)

    return image_urls


def add_file_context(messages: list, chat_id: str, user) -> list:
    """
    Add file URLs to messages for native function calling.
    """
    if not chat_id or chat_id.startswith("local:"):
        return messages

    chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
    if not chat:
        return messages

    history = chat.chat.get("history", {})
    stored_messages = get_message_list(
        history.get("messages", {}), history.get("currentId")
    )

    def format_file_tag(file):
        attrs = f'type="{file.get("type", "file")}" url="{file["url"]}"'
        if file.get("content_type"):
            attrs += f' content_type="{file["content_type"]}"'
        if file.get("name"):
            attrs += f' name="{file["name"]}"'
        return f"<file {attrs}/>"

    for message, stored_message in zip(messages, stored_messages):
        files_with_urls = [
            file
            for file in stored_message.get("files", [])
            if file.get("url") and not file.get("url").startswith("data:")
        ]
        if not files_with_urls:
            continue

        file_tags = [format_file_tag(file) for file in files_with_urls]
        file_context = (
            "<attached_files>\n" + "\n".join(file_tags) + "\n</attached_files>\n\n"
        )

        content = message.get("content", "")
        if isinstance(content, list):
            message["content"] = [{"type": "text", "text": file_context}] + content
        else:
            message["content"] = file_context + content

    return messages


async def chat_image_generation_handler(
    request: Request, form_data: dict, extra_params: dict, user
):
    metadata = extra_params.get("__metadata__", {})
    chat_id = metadata.get("chat_id", None)
    __event_emitter__ = extra_params.get("__event_emitter__", None)

    if not chat_id or not isinstance(chat_id, str) or not __event_emitter__:
        return form_data

    if chat_id.startswith("local:"):
        message_list = form_data.get("messages", [])
    else:
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": "Creating image", "done": False},
            }
        )

        messages_map = chat.chat.get("history", {}).get("messages", {})
        message_id = chat.chat.get("history", {}).get("currentId")
        message_list = get_message_list(messages_map, message_id)

    user_message = get_last_user_message(message_list)

    prompt = user_message
    message_images = get_images_from_messages(message_list)

    # Limit to first 2 sets of images
    # We may want to change this in the future to allow more images
    input_images = []
    for idx, images in enumerate(message_images):
        if idx >= 2:
            break
        for image in images:
            input_images.append(image)

    system_message_content = ""

    if len(input_images) > 0 and request.app.state.config.ENABLE_IMAGE_EDIT:
        # Edit image(s)
        try:
            images = await image_edits(
                request=request,
                form_data=EditImageForm(**{"prompt": prompt, "image": input_images}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been edited and created and is now being shown to the user. Let them know that it has been generated.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    else:
        # Create image(s)
        if request.app.state.config.ENABLE_IMAGE_PROMPT_GENERATION:
            try:
                res = await generate_image_prompt(
                    request,
                    {
                        "model": form_data["model"],
                        "messages": form_data["messages"],
                        "chat_id": metadata.get("chat_id"),
                    },
                    user,
                )

                response = res["choices"][0]["message"]["content"]

                try:
                    bracket_start = response.find("{")
                    bracket_end = response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    response = response[bracket_start:bracket_end]
                    response = json.loads(response)
                    prompt = response.get("prompt", [])
                except Exception as e:
                    prompt = user_message

            except Exception as e:
                log.exception(e)
                prompt = user_message

        try:
            images = await image_generations(
                request=request,
                form_data=CreateImageForm(**{"prompt": prompt}),
                metadata={
                    "chat_id": metadata.get("chat_id", None),
                    "message_id": metadata.get("message_id", None),
                },
                user=user,
            )

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {"description": "Image created", "done": True},
                }
            )

            await __event_emitter__(
                {
                    "type": "files",
                    "data": {
                        "files": [
                            {
                                "type": "image",
                                "url": image["url"],
                            }
                            for image in images
                        ]
                    },
                }
            )

            system_message_content = "<context>The requested image has been created by the system successfully and is now being shown to the user. Let the user know that the image they requested has been generated and is now shown in the chat.</context>"
        except Exception as e:
            log.debug(e)

            error_message = ""
            if isinstance(e, HTTPException):
                if e.detail and isinstance(e.detail, dict):
                    error_message = e.detail.get("message", str(e.detail))
                else:
                    error_message = str(e.detail)

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": f"An error occurred while generating an image",
                        "done": True,
                    },
                }
            )

            system_message_content = f"<context>Image generation was attempted but failed because of an error. The system is currently unable to generate the image. Tell the user that the following error occurred: {error_message}</context>"

    if system_message_content:
        form_data["messages"] = add_or_update_system_message(
            system_message_content, form_data["messages"]
        )

    return form_data


async def chat_completion_files_handler(
    request: Request, body: dict, extra_params: dict, user: UserModel
) -> tuple[dict, dict[str, list]]:
    __event_emitter__ = extra_params["__event_emitter__"]
    sources = []
    web_full_context_once_chat_id: Optional[str] = None

    if files := body.get("metadata", {}).get("files", None):
        files_for_retrieval = list(files)
        if RAG_WEB_FULL_CONTEXT_ONCE:
            web_full_context_once_chat_id = _get_chat_id_for_web_full_context_once(body)
            injected_keys_from_messages = _extract_owui_source_keys_from_messages(
                body.get("messages", [])
            )
            _merge_cached_web_keys_for_chat(
                web_full_context_once_chat_id, injected_keys_from_messages
            )
            injected_keys = injected_keys_from_messages.union(
                _get_cached_web_keys_for_chat(web_full_context_once_chat_id)
            )
            files_for_retrieval, _, pending_web_files = (
                _build_effective_files_for_web_full_context_once(
                    files_for_retrieval, injected_keys
                )
            )

            if not files_for_retrieval:
                reused_keys: set[str] = set()
                for item in files:
                    if not _is_web_full_context_once_item(item):
                        continue
                    reused_keys.update(
                        {
                            key
                            for key in _build_web_source_keys_from_item(item)
                            if key in injected_keys
                        }
                    )

                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "action": "sources_reused",
                            "count": len(reused_keys),
                            "done": True,
                        },
                    }
                )
                log.debug(
                    "Skipping retrieval: all web full-context sources already injected "
                    f"(pending_web_files={len(pending_web_files)})"
                )
                return body, {"sources": []}

        # Check if all files are in full context mode
        all_full_context = all(
            isinstance(item, dict) and item.get("context") == "full"
            for item in files_for_retrieval
        )
        has_web_search_files = any(
            (isinstance(item, dict) and item.get("type") == "web_search")
            for item in files_for_retrieval
        )

        queries = []
        if not all_full_context:
            try:
                queries_response = await generate_queries(
                    request,
                    {
                        "model": body["model"],
                        "messages": body["messages"],
                        "type": "retrieval",
                        "chat_id": body.get("metadata", {}).get("chat_id"),
                    },
                    user,
                )
                queries_response = queries_response["choices"][0]["message"]["content"]

                try:
                    bracket_start = queries_response.find("{")
                    bracket_end = queries_response.rfind("}") + 1

                    if bracket_start == -1 or bracket_end == -1:
                        raise Exception("No JSON object found in the response")

                    queries_response = queries_response[bracket_start:bracket_end]
                    queries_response = json.loads(queries_response)
                except Exception as e:
                    queries_response = {"queries": [queries_response]}

                queries = queries_response.get("queries", [])
            except:
                pass

            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "action": "queries_generated",
                        "queries": queries,
                        "done": False,
                    },
                }
            )

        if len(queries) == 0:
            queries = [get_last_user_message(body["messages"])]

        try:
            # Directly await async get_sources_from_items (no thread needed - fully async now)
            sources = await get_sources_from_items(
                request=request,
                items=files_for_retrieval,
                queries=queries,
                embedding_function=lambda query, prefix: request.app.state.EMBEDDING_FUNCTION(
                    query, prefix=prefix, user=user
                ),
                k=request.app.state.config.TOP_K,
                reranking_function=(
                    (
                        lambda query, documents: request.app.state.RERANKING_FUNCTION(
                            query, documents, user=user
                        )
                    )
                    if request.app.state.RERANKING_FUNCTION
                    else None
                ),
                k_reranker=request.app.state.config.TOP_K_RERANKER,
                r=request.app.state.config.RELEVANCE_THRESHOLD,
                hybrid_bm25_weight=request.app.state.config.HYBRID_BM25_WEIGHT,
                hybrid_search=request.app.state.config.ENABLE_RAG_HYBRID_SEARCH,
                full_context=all_full_context
                or request.app.state.config.RAG_FULL_CONTEXT,
                user=user,
            )
        except Exception as e:
            log.exception(e)

        if (
            sources
            and has_web_search_files
            and bool(
                getattr(
                    request.app.state.config,
                    "ENABLE_WEB_SEARCH_EVIDENCE_SATURATION",
                    False,
                )
            )
        ):
            try:
                sources, saturation_meta = await _apply_web_search_evidence_saturation(
                    request,
                    user=user,
                    active_model_id=body["model"],
                    user_message=get_last_user_message(body["messages"]),
                    sources=sources,
                )

                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "action": "web_search_evidence_saturation",
                            "meta": saturation_meta,
                            "done": False,
                            "hidden": True,
                        },
                    }
                )
            except Exception as e:
                log.exception("web_search evidence saturation failed: %s", e)

        log.debug(f"rag_contexts:sources: {sources}")
        if RAG_WEB_FULL_CONTEXT_ONCE:
            _merge_cached_web_keys_for_chat(
                web_full_context_once_chat_id,
                _extract_web_source_keys_from_sources(sources),
            )

        unique_ids = set()
        for source in sources or []:
            if not source or len(source.keys()) == 0:
                continue

            documents = source.get("document") or []
            metadatas = source.get("metadata") or []
            src_info = source.get("source") or {}

            for index, _ in enumerate(documents):
                metadata = metadatas[index] if index < len(metadatas) else None
                _id = (
                    (metadata or {}).get("source")
                    or (src_info or {}).get("id")
                    or "N/A"
                )
                unique_ids.add(_id)

        sources_count = len(unique_ids)
        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "action": "sources_retrieved",
                    "count": sources_count,
                    "done": True,
                },
            }
        )

    return body, {"sources": sources}


def apply_params_to_form_data(form_data, model):
    params = form_data.pop("params", {})
    custom_params = params.pop("custom_params", {})

    open_webui_params = {
        "stream_response": bool,
        "stream_delta_chunk_size": int,
        "function_calling": str,
        "reasoning_tags": list,
        "ledger_mode": str,
        "focused_search_mode": bool,
        "local_corpus_mode": str,
        "system": str,
    }

    for key in list(params.keys()):
        if key in open_webui_params:
            del params[key]

    if custom_params:
        # Attempt to parse custom_params if they are strings
        for key, value in custom_params.items():
            if isinstance(value, str):
                try:
                    # Attempt to parse the string as JSON
                    custom_params[key] = json.loads(value)
                except json.JSONDecodeError:
                    # If it fails, keep the original string
                    pass

        # If custom_params are provided, merge them into params
        params = deep_update(params, custom_params)

    if model.get("owned_by") == "ollama":
        # OpenAI-only qualitative control, never forward to Ollama options.
        params.pop("moe_experts_level", None)

    if model.get("owned_by") == "ollama":
        # Ollama specific parameters
        form_data["options"] = params
    else:
        if isinstance(params, dict):
            for key, value in params.items():
                if value is not None:
                    form_data[key] = value

        if "logit_bias" in params and params["logit_bias"] is not None:
            try:
                logit_bias = convert_logit_bias_input_to_json(params["logit_bias"])

                if logit_bias:
                    form_data["logit_bias"] = json.loads(logit_bias)
            except Exception as e:
                log.exception(f"Error parsing logit_bias: {e}")

    return form_data


def apply_global_cache_prompt(form_data, model, enabled):
    if not enabled:
        return form_data

    if model.get("owned_by") == "ollama":
        options = form_data.get("options")
        if not isinstance(options, dict):
            options = {}

        if "cache_prompt" not in options:
            options["cache_prompt"] = True
        form_data["options"] = options
    elif "cache_prompt" not in form_data:
        form_data["cache_prompt"] = True

    return form_data


async def convert_url_images_to_base64(form_data):
    messages = form_data.get("messages", [])

    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue

        new_content = []

        for item in content:
            if not isinstance(item, dict) or item.get("type") != "image_url":
                new_content.append(item)
                continue

            image_url = item.get("image_url", {}).get("url", "")
            if image_url.startswith("data:image/"):
                new_content.append(item)
                continue

            try:
                base64_data = await asyncio.to_thread(
                    get_image_base64_from_url, image_url
                )
                new_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": base64_data},
                    }
                )
            except Exception as e:
                log.debug(f"Error converting image URL to base64: {e}")
                new_content.append(item)

        message["content"] = new_content

    return form_data


def load_messages_from_db(chat_id: str, message_id: str) -> Optional[list[dict]]:
    """
    Load the message chain from DB up to message_id,
    keeping only LLM-relevant fields (role, content, output).
    """
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    return [
        {k: v for k, v in msg.items() if k in ("role", "content", "output", "files")}
        for msg in db_messages
    ]


def load_raw_messages_from_db(chat_id: str, message_id: str) -> Optional[list[dict]]:
    messages_map = Chats.get_messages_map_by_chat_id(chat_id)
    if not messages_map:
        return None

    db_messages = get_message_list(messages_map, message_id)
    if not db_messages:
        return None

    return [dict(message) for message in db_messages]


def load_request_history_from_db(
    chat_id: str, parent_message: Optional[dict[str, Any]]
) -> Optional[list[dict]]:
    if not chat_id or not parent_message:
        return None

    parent_message_id = parent_message.get("id")
    if parent_message_id:
        direct_history = load_raw_messages_from_db(chat_id, parent_message_id)
        if direct_history:
            return direct_history

    fallback_parent_id = parent_message.get("parentId") or parent_message.get("parent_id")
    if not fallback_parent_id:
        return None

    ancestor_history = load_raw_messages_from_db(chat_id, fallback_parent_id)
    if not ancestor_history:
        return None

    current_message = dict(parent_message)
    if current_message.get("role"):
        ancestor_history = [*ancestor_history, current_message]

    return ancestor_history


def _resolve_user_ui_settings(user: Any) -> dict[str, Any]:
    settings = getattr(user, "settings", None)
    if settings is None and isinstance(user, dict):
        settings = user.get("settings")

    if hasattr(settings, "ui"):
        return dict(getattr(settings, "ui") or {})
    if isinstance(settings, dict):
        return dict(settings.get("ui") or {})
    return {}


def _resolve_context_maintenance_enabled(request, user, tasks: Optional[dict] = None) -> bool:
    if isinstance(tasks, dict) and "context_maintenance" in tasks:
        return bool(tasks["context_maintenance"])

    ui_settings = _resolve_user_ui_settings(user)
    if "contextMaintenance" in ui_settings:
        return bool(ui_settings["contextMaintenance"])

    return bool(getattr(request.app.state.config, "ENABLE_CONTEXT_MAINTENANCE", True))


def _resolve_chat_recall_enabled(request, user) -> bool:
    return resolve_chat_recall_enabled(request, user)


def process_messages_with_output(messages: list[dict]) -> list[dict]:
    """
    Process messages with OR-aligned output items for LLM consumption.

    For assistant messages with 'output' field, produces properly formatted
    OpenAI-style messages (tool_calls + tool results). Strips 'output' before LLM.
    """
    processed = []

    for message in messages:
        if message.get("role") == "assistant" and message.get("output"):
            # Use output items for clean OpenAI-format messages
            output_messages = convert_output_to_messages(message["output"], raw=True)
            if output_messages:
                processed.extend(output_messages)
                continue

        # Strip 'output' field before adding (LLM shouldn't see it)
        clean_message = {k: v for k, v in message.items() if k != "output"}
        processed.append(clean_message)

    return processed


async def process_chat_payload(request, form_data, user, metadata, model):
    # Pipeline Inlet -> Filter Inlet -> Chat Memory -> Chat Web Search -> Chat Image Generation
    # -> Chat Code Interpreter (Form Data Update) -> (Default) Chat Tools Function Calling
    # -> Chat Files

    form_data = apply_params_to_form_data(form_data, model)
    form_data = apply_global_cache_prompt(
        form_data, model, request.app.state.config.ENABLE_CACHE_PROMPT
    )
    log.debug(f"form_data: {form_data}")

    # Load messages from DB when available — DB preserves structured 'output' items
    # which the frontend strips, causing tool calls to be merged into content.
    chat_id = metadata.get("chat_id")
    parent_message_id = metadata.get("parent_message_id")
    parent_message = metadata.get("parent_message") or {}
    system_message = get_system_message(form_data.get("messages", []))
    raw_history_messages = None

    if chat_id and parent_message_id and not chat_id.startswith("local:"):
        raw_history_messages = load_request_history_from_db(chat_id, parent_message)
        if raw_history_messages:
            raw_history_messages = inject_image_files_into_history(raw_history_messages)
            replay_messages = [
                {
                    key: value
                    for key, value in message.items()
                    if key in ("role", "content", "output", "files", "tool_calls", "tool_call_id")
                }
                for message in raw_history_messages
            ]
            form_data["messages"] = (
                [system_message, *replay_messages]
                if system_message
                else replay_messages
            )

    event_emitter = get_event_emitter(metadata)
    event_caller = get_event_call(metadata)
    chat_recall_enabled = _resolve_chat_recall_enabled(request, user)
    memory_telemetry: dict[str, Any] = {}

    if chat_recall_enabled and raw_history_messages:
        try:
            enqueue_branch_backfill(chat_id, raw_history_messages)
        except Exception as exc:
            log.debug("Failed to enqueue chat recall backfill for %s: %s", chat_id, exc)

    if _resolve_context_maintenance_enabled(request, user):
        history_messages = raw_history_messages
        if history_messages is None:
            history_messages = [
                dict(message)
                for message in form_data.get("messages", [])
                if message.get("role") in {"user", "assistant", "tool"}
            ]

        if history_messages:
            form_data["messages"], maintenance_result = await build_inline_maintained_messages(
                request,
                user=user,
                model=model,
                form_data=form_data,
                metadata=metadata,
                system_message=system_message,
                history_messages=history_messages,
                summary_state=get_chat_maintenance_state(chat_id) if chat_id else None,
            )

            if event_emitter and maintenance_result.get("summary_refreshed"):
                await event_emitter(
                    {
                        "type": "status",
                        "data": {
                            "action": "context_maintenance",
                            "description": "Condensing earlier turns...",
                            "done": True,
                        },
                    }
                )
            elif event_emitter and maintenance_result.get("fallback_used"):
                await event_emitter(
                    {
                        "type": "status",
                        "data": {
                            "action": "context_maintenance",
                            "description": "Context maintenance failed; using recent context only",
                            "done": True,
                        },
                    }
                )
            memory_telemetry["working_memory"] = maintenance_result.get("telemetry") or {}

    if chat_recall_enabled:
        branch_message_ids = (
            extract_branch_message_ids(raw_history_messages) if raw_history_messages else None
        )
        form_data["messages"], recall_result = await maybe_apply_chat_recall(
            request=request,
            chat_id=chat_id,
            branch_message_ids=branch_message_ids,
            history_messages=raw_history_messages,
            messages=form_data.get("messages", []),
            event_emitter=event_emitter,
        )
        if recall_result.get("evidence_injected"):
            log.debug(
                "Chat recall injected %s hits for chat %s (%s)",
                recall_result.get("hit_count", 0),
                chat_id,
                recall_result.get("reason"),
            )
        memory_telemetry["recall"] = {
            key: recall_result.get(key)
            for key in [
                "triggered",
                "reason",
                "mode",
                "depth",
                "evidence_injected",
                "timed_out",
                "hit_count",
                "usable_hit_count",
                "evidence_tokens",
                "indexed_message_count",
                "queued_message_count",
                "missing_message_count",
                "fallback_used",
                "fallback_mode",
            ]
        }

    form_data["messages"], ledger_result = await maybe_apply_ledger(
        chat_id=chat_id,
        raw_history_messages=raw_history_messages,
        messages=form_data.get("messages", []),
        original_system_message=system_message,
        working_memory_telemetry=memory_telemetry.get("working_memory") or {},
        metadata=metadata,
    )
    if metadata.get("params", {}).get("debug_memory_telemetry"):
        memory_telemetry["ledger"] = ledger_result

    if memory_telemetry:
        memory_telemetry["chat_id"] = chat_id
        memory_telemetry["message_id"] = metadata.get("message_id")
        metadata["memory_telemetry"] = memory_telemetry
        if runtime_telemetry.is_enabled():
            runtime_telemetry.record(
                kind="memory",
                payload=memory_telemetry,
                chat_id=chat_id,
                message_id=metadata.get("message_id"),
                user_id=metadata.get("user_id"),
                model_id=model.get("id") if isinstance(model, dict) else None,
            )
        if event_emitter:
            try:
                await event_emitter(
                    {
                        "type": "chat:memory:telemetry",
                        "data": memory_telemetry,
                    }
                )
            except Exception:
                pass
        if metadata.get("params", {}).get("debug_memory_telemetry"):
            log.info("Chat memory telemetry: %s", memory_telemetry)
        else:
            log.debug("Chat memory telemetry: %s", memory_telemetry)

    if metadata.get("branch"):
        forced_prefix, token_branch = _prepare_branch_prefill(metadata)
        form_data["messages"] = [
            *form_data.get("messages", []),
            {"role": "assistant", "content": forced_prefix},
        ]
        metadata = {**metadata, "tokenBranch": token_branch}

    # Process messages with OR-aligned output items for clean LLM messages
    form_data["messages"] = process_messages_with_output(form_data.get("messages", []))

    system_message = get_system_message(form_data.get("messages", []))
    if system_message:  # Chat Controls/User Settings
        try:
            form_data = apply_system_prompt_to_body(
                system_message.get("content"), form_data, metadata, user, replace=True
            )  # Required to handle system prompt variables
        except:
            pass

    form_data["messages"] = _inject_runtime_timestamp_once(
        form_data.get("messages", [])
    )

    form_data = await convert_url_images_to_base64(form_data)
    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
        "__chat_id__": metadata.get("chat_id"),
        "__message_id__": metadata.get("message_id"),
    }
    # Initialize events to store additional event to be sent to the client
    # Initialize contexts and citation
    if getattr(request.state, "direct", False) and hasattr(request.state, "model"):
        models = {
            request.state.model["id"]: request.state.model,
        }
    else:
        models = request.app.state.MODELS

    bounded_specialist_selection = get_bounded_specialist_model_selection(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
        task_kind=BOUNDED_SPECIALIST_TASK_KIND_FUNCTION_CALLING,
    )
    task_model_id = bounded_specialist_selection["model_id"]

    events = []
    sources = []

    # Folder "Project" handling
    # Check if the request has chat_id and is inside of a folder
    # Uses lightweight column query — only fetches folder_id, not the full chat JSON blob
    chat_id = metadata.get("chat_id", None)
    if chat_id and user:
        folder_id = Chats.get_chat_folder_id(chat_id, user.id)
        if folder_id:
            folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)

            if folder and folder.data:
                if "system_prompt" in folder.data:
                    form_data = apply_system_prompt_to_body(
                        folder.data["system_prompt"], form_data, metadata, user
                    )
                if "files" in folder.data:
                    if metadata.get("params", {}).get("function_calling") != "native":
                        form_data["files"] = [
                            *folder.data["files"],
                            *form_data.get("files", []),
                        ]
                    else:
                        # Native FC: skip RAG injection, builtin tools
                        # will read folder knowledge from metadata.
                        metadata["folder_knowledge"] = folder.data["files"]

    # Model "Knowledge" handling
    user_message = get_last_user_message(form_data["messages"])
    model_knowledge = model.get("info", {}).get("meta", {}).get("knowledge", False)

    if (
        model_knowledge
        and metadata.get("params", {}).get("function_calling") != "native"
    ):
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": False,
                },
            }
        )

        knowledge_files = []
        for item in model_knowledge:
            if item.get("collection_name"):
                knowledge_files.append(
                    {
                        "id": item.get("collection_name"),
                        "name": item.get("name"),
                        "legacy": True,
                    }
                )
            elif item.get("collection_names"):
                knowledge_files.append(
                    {
                        "name": item.get("name"),
                        "type": "collection",
                        "collection_names": item.get("collection_names"),
                        "legacy": True,
                    }
                )
            else:
                knowledge_files.append(item)

        files = form_data.get("files", [])
        files.extend(knowledge_files)
        form_data["files"] = files

    variables = form_data.pop("variables", None)

    # Process the form_data through the pipeline
    try:
        form_data = await process_pipeline_inlet_filter(
            request, form_data, user, models
        )
    except Exception as e:
        raise e

    try:
        filter_ids = get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
        filter_functions = Functions.get_functions_by_ids(filter_ids)

        form_data, flags = await process_filter_functions(
            request=request,
            filter_functions=filter_functions,
            filter_type="inlet",
            form_data=form_data,
            extra_params=extra_params,
        )
    except Exception as e:
        raise Exception(f"{e}")

    features = form_data.pop("features", None) or {}
    extra_params["__features__"] = features
    if features:
        if "deep_research" in features and features["deep_research"]:
            return await chat_deep_research_handler(
                request, form_data, extra_params, user
            )

        if "voice" in features and features["voice"]:
            if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != None:
                if request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE != "":
                    template = request.app.state.config.VOICE_MODE_PROMPT_TEMPLATE
                else:
                    template = DEFAULT_VOICE_MODE_PROMPT_TEMPLATE

                form_data["messages"] = add_or_update_system_message(
                    template,
                    form_data["messages"],
                )

        if "memory" in features and features["memory"]:
            # Skip forced memory injection when native FC is enabled - model can use memory tools
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_memory_handler(
                    request, form_data, extra_params, user
                )

        if "web_search" in features and features["web_search"]:
            # Skip forced RAG web search when native FC is enabled - model can use web_search tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_web_search_handler(
                    request, form_data, extra_params, user
                )

        if "image_generation" in features and features["image_generation"]:
            # Skip forced image generation when native FC is enabled - model can use generate_image tool
            if metadata.get("params", {}).get("function_calling") != "native":
                form_data = await chat_image_generation_handler(
                    request, form_data, extra_params, user
                )

        if "code_interpreter" in features and features["code_interpreter"]:
            engine = getattr(
                request.app.state.config, "CODE_INTERPRETER_ENGINE", "pyodide"
            )

            # Skip XML-tag prompt injection when native FC is enabled —
            # execute_code will be injected as a builtin tool instead
            if metadata.get("params", {}).get("function_calling") != "native":
                prompt = (
                    request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE
                    if request.app.state.config.CODE_INTERPRETER_PROMPT_TEMPLATE != ""
                    else DEFAULT_CODE_INTERPRETER_PROMPT
                )

                # Append filesystem awareness only for pyodide engine
                if engine != "jupyter":
                    prompt += CODE_INTERPRETER_PYODIDE_PROMPT

                form_data["messages"] = add_or_update_user_message(
                    prompt,
                    form_data["messages"],
                )
            else:
                # Native FC: tool docstring can't be dynamic, so inject
                # filesystem context into messages for pyodide engine
                if engine != "jupyter":
                    form_data["messages"] = add_or_update_user_message(
                        CODE_INTERPRETER_PYODIDE_PROMPT,
                        form_data["messages"],
                    )

    local_corpus_mode = normalize_local_corpus_mode(
        metadata.get("params", {}).get("local_corpus_mode")
    )
    if (
        local_corpus_mode == "prefer"
        and metadata.get("params", {}).get("function_calling") == "native"
        and getattr(request.app.state.config, "ENABLE_LOCAL_CORPUS_TOOLS", False)
        and getattr(request.app.state.config, "LOCAL_CORPUS_ROOT", None)
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if LOCAL_CORPUS_PREFER_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                LOCAL_CORPUS_PREFER_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    if _should_enable_shared_tool_narration(request, metadata, features):
        metadata.setdefault(
            "tool_narration_state",
            _initialize_tool_narration_state(request, metadata, features),
        )
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if TOOL_NARRATION_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                TOOL_NARRATION_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    tool_ids = form_data.pop("tool_ids", None)
    terminal_id = form_data.pop("terminal_id", None)
    files = form_data.pop("files", None)

    # Caller-provided OpenAI-style tools take precedence over server-side
    # tool resolution (tool_ids, MCP servers, builtin tools).
    payload_tools = form_data.get("tools", None)

    # Skills
    user_skill_ids = set(form_data.pop("skill_ids", None) or [])
    model_skill_ids = set(model.get("info", {}).get("meta", {}).get("skillIds", []))

    all_skill_ids = user_skill_ids | model_skill_ids
    available_skills = []
    if all_skill_ids:
        from open_webui.models.skills import Skills as SkillsModel

        accessible_skill_ids = {
            s.id for s in SkillsModel.get_skills_by_user_id(user.id, "read")
        }
        available_skills = [
            s
            for sid in all_skill_ids
            if sid in accessible_skill_ids
            and (s := SkillsModel.get_skill_by_id(sid))
            and s.is_active
        ]

        skill_descriptions = ""
        for skill in available_skills:
            if skill.id in user_skill_ids:
                # User-selected: inject full content
                form_data["messages"] = add_or_update_system_message(
                    f'<skill name="{skill.name}">\n{skill.content}\n</skill>',
                    form_data["messages"],
                    append=True,
                )
            else:
                # Model-attached: name+description only
                skill_descriptions += f"<skill>\n<name>{skill.name}</name>\n<description>{skill.description or ''}</description>\n</skill>\n"

        if skill_descriptions:
            form_data["messages"] = add_or_update_system_message(
                f"<available_skills>\n{skill_descriptions}</available_skills>",
                form_data["messages"],
                append=True,
            )

    prompt = get_last_user_message(form_data["messages"])
    # TODO: re-enable URL extraction from prompt
    # urls = []
    # if prompt and len(prompt or "") < 500 and (not files or len(files) == 0):
    #     urls = extract_urls(prompt)

    if files:
        if not files:
            files = []

        for file_item in files:
            if file_item.get("type", "file") == "folder":
                # Get folder files
                folder_id = file_item.get("id", None)
                if folder_id:
                    folder = Folders.get_folder_by_id_and_user_id(folder_id, user.id)
                    if folder and folder.data and "files" in folder.data:
                        files = [f for f in files if f.get("id", None) != folder_id]
                        files = [*files, *folder.data["files"]]

        # files = [*files, *[{"type": "url", "url": url, "name": url} for url in urls]]
        # Remove duplicate files based on their content
        files = list({json.dumps(f, sort_keys=True): f for f in files}.values())

    metadata = {
        **metadata,
        "tool_ids": tool_ids,
        "terminal_id": terminal_id,
        "files": files,
    }
    form_data["metadata"] = metadata

    # When the caller provides an explicit OpenAI-style `tools` array in the
    # request body, skip all server-side tool resolution and pass the caller's
    # tools through to the model unchanged.
    if not payload_tools:
        # Server side tools
        tool_ids = metadata.get("tool_ids", None)
        # Client side tools
        direct_tool_servers = metadata.get("tool_servers", None)

        log.debug(f"{tool_ids=}")
        log.debug(f"{direct_tool_servers=}")

        tools_dict = {}

        mcp_clients = {}
        mcp_tools_dict = {}

        if tool_ids:
            for tool_id in tool_ids:
                if tool_id.startswith("server:mcp:"):
                    try:
                        server_id = tool_id[len("server:mcp:") :]

                        mcp_server_connection = None
                        for (
                            server_connection
                        ) in request.app.state.config.TOOL_SERVER_CONNECTIONS:
                            if (
                                server_connection.get("type", "") == "mcp"
                                and server_connection.get("info", {}).get("id")
                                == server_id
                            ):
                                mcp_server_connection = server_connection
                                break

                        if not mcp_server_connection:
                            log.error(f"MCP server with id {server_id} not found")
                            continue

                        # Check access control for MCP server
                        if not has_connection_access(user, mcp_server_connection):
                            log.warning(
                                f"Access denied to MCP server {server_id} for user {user.id}"
                            )
                            continue

                        auth_type = mcp_server_connection.get("auth_type", "")
                        headers = {}
                        if auth_type == "bearer":
                            headers["Authorization"] = (
                                f"Bearer {mcp_server_connection.get('key', '')}"
                            )
                        elif auth_type == "none":
                            # No authentication
                            pass
                        elif auth_type == "session":
                            headers["Authorization"] = (
                                f"Bearer {request.state.token.credentials}"
                            )
                        elif auth_type == "system_oauth":
                            oauth_token = extra_params.get("__oauth_token__", None)
                            if oauth_token:
                                headers["Authorization"] = (
                                    f"Bearer {oauth_token.get('access_token', '')}"
                                )
                        elif auth_type == "oauth_2.1":
                            try:
                                splits = server_id.split(":")
                                server_id = splits[-1] if len(splits) > 1 else server_id

                                oauth_token = await request.app.state.oauth_client_manager.get_oauth_token(
                                    user.id, f"mcp:{server_id}"
                                )

                                if oauth_token:
                                    headers["Authorization"] = (
                                        f"Bearer {oauth_token.get('access_token', '')}"
                                    )
                            except Exception as e:
                                log.error(f"Error getting OAuth token: {e}")
                                oauth_token = None

                        connection_headers = mcp_server_connection.get("headers", None)
                        if connection_headers and isinstance(connection_headers, dict):
                            for key, value in connection_headers.items():
                                headers[key] = value

                        # Add user info headers if enabled
                        if ENABLE_FORWARD_USER_INFO_HEADERS and user:
                            headers = include_user_info_headers(headers, user)
                            if metadata and metadata.get("chat_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_CHAT_ID] = (
                                    metadata.get("chat_id")
                                )
                            if metadata and metadata.get("message_id"):
                                headers[FORWARD_SESSION_INFO_HEADER_MESSAGE_ID] = (
                                    metadata.get("message_id")
                                )

                        mcp_clients[server_id] = MCPClient()
                        await mcp_clients[server_id].connect(
                            url=mcp_server_connection.get("url", ""),
                            headers=headers if headers else None,
                        )

                        function_name_filter_list = mcp_server_connection.get(
                            "config", {}
                        ).get("function_name_filter_list", "")

                        if isinstance(function_name_filter_list, str):
                            function_name_filter_list = function_name_filter_list.split(
                                ","
                            )

                        tool_specs = await mcp_clients[server_id].list_tool_specs()
                        for tool_spec in tool_specs:

                            def make_tool_function(client, function_name):
                                async def tool_function(**kwargs):
                                    return await client.call_tool(
                                        function_name,
                                        function_args=kwargs,
                                    )

                                return tool_function

                            if function_name_filter_list:
                                if not is_string_allowed(
                                    tool_spec["name"], function_name_filter_list
                                ):
                                    # Skip this function
                                    continue

                            tool_function = make_tool_function(
                                mcp_clients[server_id], tool_spec["name"]
                            )

                            mcp_tools_dict[f"{server_id}_{tool_spec['name']}"] = {
                                "spec": {
                                    **tool_spec,
                                    "name": f"{server_id}_{tool_spec['name']}",
                                },
                                "callable": tool_function,
                                "type": "mcp",
                                "client": mcp_clients[server_id],
                                "direct": False,
                            }
                    except Exception as e:
                        log.debug(e)
                        if event_emitter:
                            await event_emitter(
                                {
                                    "type": "chat:message:error",
                                    "data": {
                                        "error": {
                                            "content": f"Failed to connect to MCP server '{server_id}'"
                                        }
                                    },
                                }
                            )
                        continue

            tools_dict = await get_tools(
                request,
                tool_ids,
                user,
                {
                    **extra_params,
                    "__model__": models[task_model_id],
                    "__messages__": form_data["messages"],
                    "__files__": metadata.get("files", []),
                },
            )

            if mcp_tools_dict:
                tools_dict = {**tools_dict, **mcp_tools_dict}

        # Resolve terminal tools if terminal_id is set (outside tool_ids check
        # so system terminals work even when no other tools are selected)
        if terminal_id:
            try:
                terminal_tools = await get_terminal_tools(
                    request,
                    terminal_id,
                    user,
                    extra_params,
                )
                if terminal_tools:
                    tools_dict = {**tools_dict, **terminal_tools}
            except Exception as e:
                log.exception(e)

        if direct_tool_servers:
            for tool_server in direct_tool_servers:
                tool_specs = tool_server.pop("specs", [])

                for tool in tool_specs:
                    tools_dict[tool["name"]] = {
                        "spec": tool,
                        "direct": True,
                        "server": tool_server,
                    }

        if mcp_clients:
            metadata["mcp_clients"] = mcp_clients

        # Inject builtin tools for native function calling based on enabled features and model capability
        # Check if builtin_tools capability is enabled for this model (defaults to True if not specified)
        builtin_tools_enabled = (
            model.get("info", {}).get("meta", {}).get("capabilities") or {}
        ).get("builtin_tools", True)
        if (
            metadata.get("params", {}).get("function_calling") == "native"
            and builtin_tools_enabled
        ):
            # Add file context to user messages
            chat_id = metadata.get("chat_id")
            form_data["messages"] = add_file_context(
                form_data.get("messages", []), chat_id, user
            )
            builtin_tools = get_builtin_tools(
                request,
                {
                    **extra_params,
                    "__event_emitter__": event_emitter,
                    "__skill_ids__": [
                        s.id for s in available_skills if s.id not in user_skill_ids
                    ],
                },
                features,
                model,
            )
            for name, tool_dict in builtin_tools.items():
                if name not in tools_dict:
                    tools_dict[name] = tool_dict

        if tools_dict:
            if metadata.get("params", {}).get("function_calling") == "native":
                # If the function calling is native, then call the tools function calling handler
                metadata["tools"] = tools_dict
                form_data["tools"] = [
                    {"type": "function", "function": tool.get("spec", {})}
                    for tool in tools_dict.values()
                ]
            else:
                # If the function calling is not native, then call the tools function calling handler
                try:
                    form_data, flags = await chat_completion_tools_handler(
                        request, form_data, extra_params, user, models, tools_dict
                    )
                    sources.extend(flags.get("sources", []))
                    if isinstance(flags.get("toolJourneyTelemetry"), dict):
                        metadata["tool_journey_telemetry"] = flags.get(
                            "toolJourneyTelemetry"
                        )
                except Exception as e:
                    log.exception(e)

    # Check if file context extraction is enabled for this model (default True)
    file_context_enabled = (
        model.get("info", {}).get("meta", {}).get("capabilities") or {}
    ).get("file_context", True)

    if file_context_enabled:
        try:
            form_data, flags = await chat_completion_files_handler(
                request, form_data, extra_params, user
            )
            sources.extend(flags.get("sources", []))
        except Exception as e:
            log.exception(e)

    # Save the pre-RAG message state so the native tool call loop can
    # restore to the true original (before file-source injection) rather
    # than a snapshot that already has the RAG template baked in.
    system_message = get_system_message(form_data["messages"])
    metadata["system_prompt"] = (
        get_content_from_message(system_message) if system_message else None
    )
    metadata["user_prompt"] = get_last_user_message(form_data["messages"])
    metadata["sources"] = sources[:] if sources else []

    # If context is not empty, insert it into the messages
    if sources and prompt:
        form_data["messages"] = apply_source_context_to_messages(
            request, form_data["messages"], sources, prompt
        )

    # If there are citations, add them to the data_items
    sources = [
        source
        for source in sources
        if source.get("source", {}).get("name", "")
        or source.get("source", {}).get("id", "")
    ]

    if len(sources) > 0:
        events.append({"sources": sources})

    if model_knowledge:
        await event_emitter(
            {
                "type": "status",
                "data": {
                    "action": "knowledge_search",
                    "query": user_message,
                    "done": True,
                    "hidden": True,
                },
            }
        )

    return form_data, metadata, events


def get_event_emitter_and_caller(metadata):
    event_emitter = None
    event_caller = None
    if (
        "session_id" in metadata
        and metadata["session_id"]
        and "chat_id" in metadata
        and metadata["chat_id"]
        and "message_id" in metadata
        and metadata["message_id"]
    ):
        event_emitter = get_event_emitter(metadata)
        event_caller = get_event_call(metadata)
    return event_emitter, event_caller


def build_chat_response_context(
    request, form_data, user, model, metadata, tasks, events
):
    event_emitter, event_caller = get_event_emitter_and_caller(metadata)
    return {
        "request": request,
        "form_data": form_data,
        "user": user,
        "model": model,
        "metadata": metadata,
        "tasks": tasks,
        "events": events,
        "event_emitter": event_emitter,
        "event_caller": event_caller,
    }


def get_response_data(response):
    if isinstance(response, list) and len(response) == 1:
        # If the response is a single-item list, unwrap it #17213
        response = response[0]

    if isinstance(response, JSONResponse):
        if isinstance(response.body, bytes):
            try:
                response_data = json.loads(response.body.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                response_data = {"error": {"detail": "Invalid JSON response"}}
        else:
            response_data = response
    elif isinstance(response, dict):
        response_data = response
    else:
        response_data = None

    return response, response_data


def merge_events_into_response(response_data, events):
    if events and isinstance(events, list):
        extra_response = {}
        for event in events:
            if isinstance(event, dict):
                extra_response.update(event)
            else:
                extra_response[event] = True

        return {
            **extra_response,
            **response_data,
        }
    return response_data


def build_response_object(response, response_data):
    if isinstance(response, dict):
        return response_data
    if isinstance(response, JSONResponse):
        return JSONResponse(
            content=response_data,
            headers=response.headers,
            status_code=response.status_code,
        )
    return response


async def get_system_oauth_token(request, user):
    oauth_token = None
    try:
        if request.cookies.get("oauth_session_id", None):
            oauth_token = await request.app.state.oauth_manager.get_oauth_token(
                user.id,
                request.cookies.get("oauth_session_id", None),
            )
    except Exception as e:
        log.error(f"Error getting OAuth token: {e}")
    return oauth_token


SOURCE_DIARY_SYSTEM_PROMPT = """You write exact-turn research source diaries in markdown.

Rules:
- Write markdown only.
- Stay bounded to the supplied turn packet. Do not reconstruct earlier turns.
- Do not invent sources, URLs, claims, or citations.
- Keep the synopsis compact and factual.
- If something is missing, say so briefly instead of guessing.

Required sections:
## Metadata
## User Question
## Final Answer Synopsis
## Discovery Path
## Helpful Sources
## Weak Or Unhelpful Sources
## Candidate Domains For Manual Curation
"""


def _truncate_source_diary_text(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n...[truncated]"


def _extract_tool_source_entries(sources: list[Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources or []:
        if not isinstance(source, dict):
            continue
        metadata_entries = source.get("metadata") or []
        for item in metadata_entries:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or item.get("source") or "").strip()
            name = str(item.get("name") or item.get("source") or url).strip()
            if not url:
                continue
            key = (url, name)
            if key in seen:
                continue
            seen.add(key)
            entries.append({"url": url, "name": name})
    return entries


def _build_source_diary_context(
    *,
    metadata: dict[str, Any],
    message: dict[str, Any],
    messages: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    state = metadata.get("research_turn_state") or {}
    tool_calls = state.get("tool_calls") or []
    if not tool_calls:
        return None

    web_tool_used = any(
        str((entry or {}).get("tool") or "")
        in {"search_web", "web_research_strong", "fetch_url", "query_web_evidence"}
        for entry in tool_calls
    )
    if not web_tool_used:
        return None

    assistant_message = None
    for candidate in reversed(messages):
        if candidate.get("role") == "assistant":
            assistant_message = candidate
            break

    assistant_text = ""
    if assistant_message:
        assistant_text = get_content_from_message(assistant_message) or ""
        if not assistant_text and isinstance(assistant_message.get("content"), str):
            assistant_text = assistant_message.get("content", "")

    source_entries = _extract_tool_source_entries(metadata.get("tool_call_sources") or [])
    stored_artifacts = state.get("stored_artifacts") or []
    fetched_urls = [str(item.get("url") or "") for item in stored_artifacts if str(item.get("url") or "").strip()]
    fetched_domains = sorted(
        {
            str(item.get("domain") or "").strip()
            for item in stored_artifacts
            if str(item.get("domain") or "").strip()
        }
    )

    return {
        "chat_id": metadata.get("chat_id"),
        "message_id": metadata.get("message_id"),
        "active_model": message.get("model") or metadata.get("model_id"),
        "user_question": _truncate_source_diary_text(
            metadata.get("user_prompt") or get_last_user_message(messages) or "",
            6000,
        ),
        "final_answer_text": _truncate_source_diary_text(assistant_text, 12000),
        "research_discovery_lane": state.get("research_discovery_lane"),
        "strong_hardening_used": bool(state.get("strong_hardening_triggered")),
        "strong_hardening_reason": state.get("strong_hardening_reason"),
        "strong_hardening_improved_bundle": state.get("strong_hardening_improved_bundle"),
        "broad_fallback_after_strong": bool(state.get("broad_fallback_after_strong")),
        "tool_sequence": copy.deepcopy(tool_calls),
        "fetched_urls": fetched_urls,
        "fetched_domains": fetched_domains,
        "stored_artifact_ids": [
            str(item.get("artifact_id") or "")
            for item in stored_artifacts
            if str(item.get("artifact_id") or "").strip()
        ],
        "query_web_evidence_diagnostics": copy.deepcopy(state.get("evidence_queries") or []),
        "citation_sources": source_entries,
    }


def _build_source_diary_prompt(context: dict[str, Any]) -> str:
    packet = json.dumps(context, ensure_ascii=False, indent=2)
    return (
        "Write a markdown source diary for this completed research turn.\n"
        "Focus on which sources materially helped, which ones did not, and which domains "
        "look worth manual curation later.\n\n"
        f"Turn packet:\n```json\n{packet}\n```"
    )


def _write_source_diary_markdown(
    *,
    chat_id: str,
    message_id: str,
    markdown: str,
) -> str:
    chat_dir = _resolve_chat_artifacts_dir(chat_id)
    if chat_dir is None:
        raise ValueError("Unable to resolve chat artifacts directory for source diary")

    diary_dir = chat_dir / "source_diary"
    diary_dir.mkdir(parents=True, exist_ok=True)
    target_path = diary_dir / f"{message_id}.md"
    temp_path = diary_dir / f".{message_id}.{uuid4().hex}.tmp"
    temp_path.write_text(markdown, encoding="utf-8", errors="replace")
    temp_path.replace(target_path)
    return str(target_path)


async def run_background_source_diary_generation(
    *,
    request: Request,
    user,
    active_model_id: str,
    chat_id: str,
    message_id: str,
    context: dict[str, Any],
    metadata: dict[str, Any],
    event_emitter=None,
) -> dict[str, Any]:
    result = {
        "status": "skipped",
        "path": None,
        "selected_model": None,
        "selected_via": None,
    }
    if not chat_id or not message_id or str(chat_id).startswith("local:") or not context:
        return result

    models = _resolve_models_for_task(request)
    selection = get_bounded_specialist_model_selection(
        active_model_id,
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
        task_kind=BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
    )
    if not selection.get("used_bounded_specialist"):
        return result

    selected_model_id = selection["model_id"]
    result["selected_model"] = selected_model_id
    result["selected_via"] = selection.get("selected_via")
    started_at = time.monotonic()

    await _emit_tool_journey_event(
        metadata,
        event_emitter,
        {
            "phase": "source_diary_generation_started",
            "task_kind": BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
            "actor": "bounded_specialist",
            "model_id": selected_model_id,
            "active_model_id": active_model_id,
            "selected_via": selection.get("selected_via"),
            "route_source": selection.get("route_source"),
            "reason": "background_source_diary",
            "source_diary_generation_started": True,
        },
    )

    last_error: Optional[Exception] = None
    for _attempt in range(2):
        try:
            payload = {
                "model": selected_model_id,
                "messages": [
                    {"role": "system", "content": SOURCE_DIARY_SYSTEM_PROMPT},
                    {"role": "user", "content": _build_source_diary_prompt(context)},
                ],
                "stream": False,
                "temperature": 0.0,
                "max_completion_tokens": 900,
                "think": False,
                "params": {
                    "think": False,
                    "custom_params": {
                        "chat_template_kwargs": {
                            "enable_thinking": False,
                        }
                    },
                },
                "metadata": {
                    **(
                        request.state.metadata
                        if hasattr(request.state, "metadata")
                        else {}
                    ),
                    "task": BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
                    "chat_id": chat_id,
                    "task_body": {
                        "type": BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
                        "message_id": message_id,
                    },
                    "bounded_specialist": _build_bounded_specialist_telemetry(
                        selection,
                        selected_model=selected_model_id,
                        reason="background_source_diary",
                    ),
                },
            }

            response = await generate_chat_completion(
                request,
                form_data=payload,
                user=user,
                bypass_system_prompt=True,
            )
            markdown = (_extract_completion_message_content(response) or "").strip()
            if not markdown:
                raise ValueError("Empty source diary output")

            path = _write_source_diary_markdown(
                chat_id=chat_id,
                message_id=message_id,
                markdown=markdown,
            )
            duration_ms = int((time.monotonic() - started_at) * 1000)
            await _emit_tool_journey_event(
                metadata,
                event_emitter,
                {
                    "phase": "source_diary_generation_done",
                    "task_kind": BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
                    "actor": "bounded_specialist",
                    "model_id": selected_model_id,
                    "active_model_id": active_model_id,
                    "selected_via": selection.get("selected_via"),
                    "route_source": selection.get("route_source"),
                    "reason": "background_source_diary",
                    "status": "ok",
                    "duration_ms": duration_ms,
                    "recent_artifact_count": len(context.get("stored_artifact_ids") or []),
                    "source_diary_generation_done": True,
                },
            )
            result["status"] = "written"
            result["path"] = path
            return result
        except Exception as exc:
            last_error = exc

    await _emit_tool_journey_event(
        metadata,
        event_emitter,
        {
            "phase": "source_diary_generation_failed",
            "task_kind": BOUNDED_SPECIALIST_TASK_KIND_SOURCE_DIARY_GENERATION,
            "actor": "bounded_specialist",
            "model_id": selected_model_id,
            "active_model_id": active_model_id,
            "selected_via": selection.get("selected_via"),
            "route_source": selection.get("route_source"),
            "reason": "background_source_diary",
            "status": "error",
            "duration_ms": int((time.monotonic() - started_at) * 1000),
            "error_class": last_error.__class__.__name__ if last_error else "UnknownError",
            "source_diary_generation_failed": True,
        },
    )
    return result


async def background_tasks_handler(ctx):
    request = ctx["request"]
    form_data = ctx["form_data"]
    user = ctx["user"]
    metadata = ctx["metadata"]
    tasks = ctx["tasks"]
    event_emitter = ctx["event_emitter"]
    context_maintenance_enabled = _resolve_context_maintenance_enabled(
        request, user, tasks
    )

    async def emit_background_event(event: dict[str, Any]) -> None:
        if event_emitter:
            await event_emitter(event)

    message = None
    messages = []

    if "chat_id" in metadata and not metadata["chat_id"].startswith("local:"):
        messages_map = Chats.get_messages_map_by_chat_id(metadata["chat_id"])
        message = messages_map.get(metadata["message_id"]) if messages_map else None

        message_list = get_message_list(messages_map, metadata["message_id"])

        # Remove details tags and files from the messages.
        # as get_message_list creates a new list, it does not affect
        # the original messages outside of this handler

        messages = []
        for message in message_list:
            content = message.get("content", "")
            if isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        content = item["text"]
                        break

            if isinstance(content, str):
                content = re.sub(
                    r"<details\b[^>]*>.*?<\/details>|!\[.*?\]\(.*?\)",
                    "",
                    content,
                    flags=re.S | re.I,
                ).strip()

            messages.append(
                {
                    **message,
                    "role": message.get(
                        "role", "assistant"
                    ),  # Safe fallback for missing role
                    "content": content,
                }
            )
    else:
        # Local temp chat, get the model and message from the form_data
        message = get_last_user_message_item(form_data.get("messages", []))
        messages = form_data.get("messages", [])
        if message:
            message["model"] = form_data.get("model")

    if message and "model" in message:
        if messages:
            if (
                tasks
                and
                TASKS.FOLLOW_UP_GENERATION in tasks
                and tasks[TASKS.FOLLOW_UP_GENERATION]
                and not context_maintenance_enabled
            ):
                res = await generate_follow_ups(
                    request,
                    {
                        "model": message["model"],
                        "messages": messages,
                        "message_id": metadata["message_id"],
                        "chat_id": metadata["chat_id"],
                    },
                    user,
                )

                if res and isinstance(res, dict):
                    if len(res.get("choices", [])) == 1:
                        response_message = res.get("choices", [])[0].get("message", {})

                        follow_ups_string = response_message.get(
                            "content"
                        ) or response_message.get("reasoning_content", "")
                    else:
                        follow_ups_string = ""

                    follow_ups_string = follow_ups_string[
                        follow_ups_string.find("{") : follow_ups_string.rfind("}") + 1
                    ]

                    try:
                        follow_ups = json.loads(follow_ups_string).get("follow_ups", [])
                        await emit_background_event(
                            {
                                "type": "chat:message:follow_ups",
                                "data": {
                                    "follow_ups": follow_ups,
                                },
                            }
                        )

                        if not metadata.get("chat_id", "").startswith("local:"):
                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                metadata["chat_id"],
                                metadata["message_id"],
                                {
                                    "followUps": follow_ups,
                                },
                            )

                    except Exception as e:
                        pass

            if (
                context_maintenance_enabled
                and not metadata.get("chat_id", "").startswith("local:")
                and metadata.get("chat_id")
                and metadata.get("message_id")
            ):
                model_id = message.get("model")
                active_model = request.app.state.MODELS.get(model_id) if model_id else None
                if active_model:
                    asyncio.create_task(
                        run_background_context_maintenance(
                            request=request,
                            user=user,
                            model=active_model,
                            chat_id=metadata["chat_id"],
                            message_id=metadata["message_id"],
                            event_emitter=event_emitter,
                        )
                    )

            if (
                not metadata.get("chat_id", "").startswith("local:")
                and metadata.get("chat_id")
                and metadata.get("message_id")
            ):
                asyncio.create_task(
                    run_background_ledger_capture(
                        chat_id=metadata["chat_id"],
                        message_id=metadata["message_id"],
                        metadata=metadata,
                        event_emitter=event_emitter,
                    )
                )

            if (
                not metadata.get("chat_id", "").startswith("local:")
                and metadata.get("chat_id")
                and metadata.get("message_id")
            ):
                diary_context = _build_source_diary_context(
                    metadata=metadata,
                    message=message,
                    messages=messages,
                )
                if diary_context:
                    asyncio.create_task(
                        run_background_source_diary_generation(
                            request=request,
                            user=user,
                            active_model_id=message["model"],
                            chat_id=metadata["chat_id"],
                            message_id=metadata["message_id"],
                            context=diary_context,
                            metadata=metadata,
                            event_emitter=event_emitter,
                        )
                    )

            if not metadata.get("chat_id", "").startswith(
                "local:"
            ):  # Only update titles and tags for non-temp chats
                if tasks and TASKS.TITLE_GENERATION in tasks:
                    user_message = get_last_user_message(messages)
                    if user_message and len(user_message) > 100:
                        user_message = user_message[:100] + "..."

                    title = None
                    if tasks[TASKS.TITLE_GENERATION]:
                        res = await generate_title(
                            request,
                            {
                                "model": message["model"],
                                "messages": messages,
                                "chat_id": metadata["chat_id"],
                            },
                            user,
                        )

                        if res and isinstance(res, dict):
                            if len(res.get("choices", [])) == 1:
                                response_message = res.get("choices", [])[0].get(
                                    "message", {}
                                )

                                title_string = (
                                    response_message.get("content")
                                    or response_message.get(
                                        "reasoning_content",
                                    )
                                    or message.get("content", user_message)
                                )
                            else:
                                title_string = ""

                            title_string = title_string[
                                title_string.find("{") : title_string.rfind("}") + 1
                            ]

                            try:
                                title = json.loads(title_string).get(
                                    "title", user_message
                                )
                            except Exception as e:
                                title = ""

                            if not title:
                                title = messages[0].get("content", user_message)

                            Chats.update_chat_title_by_id(metadata["chat_id"], title)

                            await emit_background_event(
                                {
                                    "type": "chat:title",
                                    "data": title,
                                }
                            )

                    if title == None and len(messages) == 2:
                        title = messages[0].get("content", user_message)

                        Chats.update_chat_title_by_id(metadata["chat_id"], title)

                        await emit_background_event(
                            {
                                "type": "chat:title",
                                "data": message.get("content", user_message),
                            }
                        )

                if (
                    tasks
                    and TASKS.TAGS_GENERATION in tasks
                    and tasks[TASKS.TAGS_GENERATION]
                ):
                    res = await generate_chat_tags(
                        request,
                        {
                            "model": message["model"],
                            "messages": messages,
                            "chat_id": metadata["chat_id"],
                        },
                        user,
                    )

                    if res and isinstance(res, dict):
                        if len(res.get("choices", [])) == 1:
                            response_message = res.get("choices", [])[0].get(
                                "message", {}
                            )

                            tags_string = response_message.get(
                                "content"
                            ) or response_message.get("reasoning_content", "")
                        else:
                            tags_string = ""

                        tags_string = tags_string[
                            tags_string.find("{") : tags_string.rfind("}") + 1
                        ]

                        try:
                            tags = json.loads(tags_string).get("tags", [])
                            Chats.update_chat_tags_by_id(
                                metadata["chat_id"], tags, user
                            )

                            await emit_background_event(
                                {
                                    "type": "chat:tags",
                                    "data": tags,
                                }
                            )
                        except Exception as e:
                            pass


async def non_streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    user = ctx["user"]
    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]

    response, response_data = get_response_data(response)
    if response_data is None:
        return response

    token_telemetry = _extract_non_streaming_token_telemetry(response_data)
    token_branch = metadata.get("tokenBranch")
    memory_telemetry = (
        metadata.get("memory_telemetry")
        if metadata.get("params", {}).get("debug_memory_telemetry")
        else None
    )
    tool_journey_telemetry = (
        metadata.get("tool_journey_telemetry")
        if _is_debug_flag_enabled(metadata.get("params", {}).get("debug_tool_journey"))
        else None
    )
    prompt_telemetry = (
        get_prompt_telemetry(request, metadata)
        if is_prompt_telemetry_enabled(metadata)
        else None
    )
    if isinstance(tool_journey_telemetry, dict):
        tool_journey_telemetry["completed_at"] = int(time.time())

    if token_telemetry:
        response_data["tokenTelemetry"] = token_telemetry
    if token_branch:
        response_data["tokenBranch"] = token_branch
    if memory_telemetry:
        response_data["memoryTelemetry"] = memory_telemetry
    if tool_journey_telemetry:
        response_data["toolJourneyTelemetry"] = tool_journey_telemetry
    if prompt_telemetry:
        response_data["promptTelemetry"] = prompt_telemetry

    try:
        if "error" in response_data:
            error = response_data.get("error")

            if isinstance(error, dict):
                error = error.get("detail", error)
            else:
                error = str(error)

            Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata["chat_id"],
                metadata["message_id"],
                {
                    "error": {"content": error},
                    **({"promptTelemetry": prompt_telemetry} if prompt_telemetry else {}),
                },
            )
            if event_emitter and (isinstance(error, str) or isinstance(error, dict)):
                await event_emitter(
                    {
                        "type": "chat:message:error",
                        "data": {"error": {"content": error}},
                    }
                )

        if "selected_model_id" in response_data:
            Chats.upsert_message_to_chat_by_id_and_message_id(
                metadata["chat_id"],
                metadata["message_id"],
                {
                    "selectedModelId": response_data["selected_model_id"],
                },
            )

        choices = response_data.get("choices", [])
        if choices and choices[0].get("message", {}).get("content"):
            content = response_data["choices"][0]["message"]["content"]

            if content:
                if event_emitter:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": response_data,
                        }
                    )

                title = Chats.get_chat_title_by_id(metadata["chat_id"])

                # Use output from backend if provided (OR-compliant backends),
                # otherwise generate from response content
                response_output = response_data.get("output")
                if not response_output:
                    response_output = [
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "completed",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ]

                if event_emitter:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "done": True,
                                "content": content,
                                "output": response_output,
                                "title": title,
                                **(
                                    {"tokenTelemetry": token_telemetry}
                                    if token_telemetry
                                    else {}
                                ),
                                **({"tokenBranch": token_branch} if token_branch else {}),
                                **(
                                    {"toolJourneyTelemetry": tool_journey_telemetry}
                                    if tool_journey_telemetry
                                    else {}
                                ),
                                **(
                                    {"promptTelemetry": prompt_telemetry}
                                    if prompt_telemetry
                                    else {}
                                ),
                            },
                        }
                    )

                # Save message in the database
                usage = normalize_usage(response_data.get("usage", {}) or {})

                Chats.upsert_message_to_chat_by_id_and_message_id(
                    metadata["chat_id"],
                    metadata["message_id"],
                    {
                        "role": "assistant",
                        "content": content,
                        "output": response_output,
                        **(
                            {"tokenTelemetry": token_telemetry}
                            if token_telemetry
                            else {}
                        ),
                        **({"tokenBranch": token_branch} if token_branch else {}),
                        **(
                            {"toolJourneyTelemetry": tool_journey_telemetry}
                            if tool_journey_telemetry
                            else {}
                        ),
                        **(
                            {"promptTelemetry": prompt_telemetry}
                            if prompt_telemetry
                            else {}
                        ),
                        **({"usage": usage} if usage else {}),
                    },
                )

                # Send a webhook notification if the user is not active
                if not Users.is_user_active(user.id):
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                            {
                                "action": "chat",
                                "message": content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await background_tasks_handler(ctx)

        response = build_response_object(
            response, merge_events_into_response(response_data, events)
        )
    except Exception as e:
        log.debug(f"Error occurred while processing request: {e}")
        pass

    if event_emitter:
        return response

    if isinstance(response, dict):
        response = merge_events_into_response(response_data, events)

    return response


async def streaming_chat_response_handler(response, ctx):
    request = ctx["request"]

    form_data = ctx["form_data"]

    user = ctx["user"]
    model = ctx["model"]

    metadata = ctx["metadata"]
    events = ctx["events"]

    event_emitter = ctx["event_emitter"]
    event_caller = ctx["event_caller"]

    extra_params = {
        "__event_emitter__": event_emitter,
        "__event_call__": event_caller,
        "__user__": user.model_dump() if isinstance(user, UserModel) else {},
        "__metadata__": metadata,
        "__oauth_token__": await get_system_oauth_token(request, user),
        "__request__": request,
        "__model__": model,
    }

    filter_functions = [
        Functions.get_function_by_id(filter_id)
        for filter_id in get_sorted_filter_ids(
            request, model, metadata.get("filter_ids", [])
        )
    ]

    # Standard streaming response handler
    if event_emitter and event_caller:
        task_id = str(uuid4())  # Create a unique task ID.
        model_id = form_data.get("model", "")

        # Handle as a background task
        async def response_handler(response, events):
            def tag_output_handler(content_type, tags, output):
                """
                Detect special tags (reasoning, solution, code_interpreter) in streaming
                content and create corresponding OR-aligned output items directly.
                Operates on output items instead of content_blocks.

                Uses the text from the output items themselves for tag detection,
                eliminating state divergence between accumulated content and items.
                """
                end_flag = False

                def extract_attributes(tag_content):
                    """Extract attributes from a tag if they exist."""
                    attributes = {}
                    if not tag_content:
                        return attributes
                    matches = re.findall(r'(\w+)\s*=\s*"([^"]+)"', tag_content)
                    for key, value in matches:
                        attributes[key] = value
                    return attributes

                def get_last_text(out):
                    """Get text from last message item, or empty string."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            return parts[-1].get("text", "")
                    return ""

                def set_last_text(out, text):
                    """Set text on last message item's output_text."""
                    if out and out[-1].get("type") == "message":
                        parts = out[-1].get("content", [])
                        if parts and parts[-1].get("type") == "output_text":
                            parts[-1]["text"] = text

                # Map content_type to output item type
                output_type_map = {
                    "reasoning": "reasoning",
                    "solution": "message",  # solution tags just produce text
                    "code_interpreter": "open_webui:code_interpreter",
                }
                output_item_type = output_type_map.get(content_type, content_type)

                last_type = output[-1].get("type", "") if output else ""

                if last_type == "message":
                    # Use the output item's own text for tag detection
                    item_text = get_last_text(output)
                    for start_tag, end_tag in tags:

                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )

                        match = re.search(start_tag_pattern, item_text)
                        if match:
                            try:
                                attr_content = match.group(1) if match.group(1) else ""
                            except:
                                attr_content = ""

                            attributes = extract_attributes(attr_content)

                            before_tag = item_text[: match.start()]
                            after_tag = item_text[match.end() :]

                            # Keep only text before the tag in the message
                            set_last_text(output, before_tag)

                            if not before_tag.strip():
                                # Remove empty message item
                                if output and output[-1].get("type") == "message":
                                    output.pop()

                            # Append the new output item
                            if output_item_type == "reasoning":
                                output.append(
                                    {
                                        "type": "reasoning",
                                        "id": output_id("r"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "content": [],
                                        "summary": None,
                                        "started_at": time.time(),
                                    }
                                )
                            elif output_item_type == "open_webui:code_interpreter":
                                output.append(
                                    {
                                        "type": "open_webui:code_interpreter",
                                        "id": output_id("ci"),
                                        "status": "in_progress",
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "lang": attributes.get("lang", "python"),
                                        "code": "",
                                        "output": None,
                                        "started_at": time.time(),
                                    }
                                )
                            else:
                                # solution or other text-producing tag
                                output.append(
                                    {
                                        "type": "message",
                                        "id": output_id("msg"),
                                        "status": "in_progress",
                                        "role": "assistant",
                                        "content": [
                                            {"type": "output_text", "text": ""}
                                        ],
                                        "_tag_type": content_type,
                                        "start_tag": start_tag,
                                        "end_tag": end_tag,
                                        "attributes": attributes,
                                        "started_at": time.time(),
                                    }
                                )

                            if after_tag:
                                # Set the after_tag content on the new item
                                if output_item_type == "reasoning":
                                    output[-1]["content"] = [
                                        {"type": "output_text", "text": after_tag}
                                    ]
                                elif output_item_type == "open_webui:code_interpreter":
                                    output[-1]["code"] = after_tag
                                else:
                                    set_last_text(output, after_tag)

                                _, recursive_end = tag_output_handler(
                                    content_type, tags, output
                                )
                                if recursive_end:
                                    end_flag = True

                            break

                elif (
                    (last_type == "reasoning" and content_type == "reasoning")
                    or (
                        last_type == "open_webui:code_interpreter"
                        and content_type == "code_interpreter"
                    )
                    or (
                        last_type == "message"
                        and output[-1].get("_tag_type") == content_type
                    )
                ):
                    item = output[-1]
                    start_tag = item.get("start_tag", "")
                    end_tag = item.get("end_tag", "")

                    end_tag_pattern = rf"{re.escape(end_tag)}"

                    # Get the block content from the item itself
                    if last_type == "reasoning":
                        parts = item.get("content", [])
                        block_content = ""
                        if parts and parts[-1].get("type") == "output_text":
                            block_content = parts[-1].get("text", "")
                    elif last_type == "open_webui:code_interpreter":
                        block_content = item.get("code", "")
                    else:
                        block_content = get_last_text(output)

                    if re.search(end_tag_pattern, block_content):
                        end_flag = True

                        # Strip start and end tags from content
                        start_tag_pattern = rf"{re.escape(start_tag)}"
                        if start_tag.startswith("<") and start_tag.endswith(">"):
                            start_tag_pattern = (
                                rf"<{re.escape(start_tag[1:-1])}(\s.*?)?>"
                            )
                        block_content = re.sub(
                            start_tag_pattern, "", block_content
                        ).strip()

                        end_tag_regex = re.compile(end_tag_pattern, re.DOTALL)
                        split_content = end_tag_regex.split(block_content, maxsplit=1)

                        block_content = (
                            split_content[0].strip() if split_content else ""
                        )
                        leftover_content = (
                            split_content[1].strip() if len(split_content) > 1 else ""
                        )

                        if block_content:
                            # Update the item with final content
                            if last_type == "reasoning":
                                item["content"] = [
                                    {"type": "output_text", "text": block_content}
                                ]
                                item["ended_at"] = time.time()
                                item["duration"] = int(
                                    item["ended_at"] - item["started_at"]
                                )
                                item["status"] = "completed"
                            elif last_type == "open_webui:code_interpreter":
                                item["code"] = block_content
                                item["ended_at"] = time.time()
                                item["duration"] = int(
                                    item["ended_at"] - item["started_at"]
                                )
                            else:
                                set_last_text(output, block_content)
                                item["ended_at"] = time.time()

                            # Reset by appending a new message item for leftover
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )
                        else:
                            # Remove the block if content is empty
                            output.pop()
                            output.append(
                                {
                                    "type": "message",
                                    "id": output_id("msg"),
                                    "status": "in_progress",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": leftover_content,
                                        }
                                    ],
                                }
                            )

                return output, end_flag

            message = Chats.get_message_by_id_and_message_id(
                metadata["chat_id"], metadata["message_id"]
            )

            tool_calls = []

            last_assistant_message = None
            try:
                if form_data["messages"][-1]["role"] == "assistant":
                    last_assistant_message = get_last_assistant_message(
                        form_data["messages"]
                    )
            except Exception as e:
                pass

            content = (
                message.get("content", "")
                if message
                else last_assistant_message if last_assistant_message else ""
            )

            # Initialize output: use existing from message if continuing, else create new
            existing_output = message.get("output") if message else None
            if existing_output:
                output = existing_output
            else:
                # Only create an initial message item if there is content to initialize with
                if content:
                    output = [
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": content}],
                        }
                    ]
                else:
                    output = []

            usage = None
            token_telemetry_state = {"tokens": [], "capped": False}
            token_branch = metadata.get("tokenBranch")
            termination_cause = None
            debug_tool_journey = _is_debug_flag_enabled(
                metadata.get("params", {}).get("debug_tool_journey")
            )

            if debug_tool_journey:
                metadata["tool_journey_telemetry"] = {
                    "enabled": True,
                    "chat_id": metadata.get("chat_id"),
                    "message_id": metadata.get("message_id"),
                    "task_id": task_id,
                    "model_id": model_id,
                    "events": [],
                    "capped": False,
                    "started_at": int(time.time()),
                }
                init_event = _append_tool_journey_event(
                    metadata,
                    {
                        "phase": "init",
                        "tool_calls_pending": len(tool_calls),
                    },
                )
                if init_event:
                    await event_emitter(
                        {"type": "chat:tool:journey", "data": init_event}
                    )

            reasoning_tags_param = metadata.get("params", {}).get("reasoning_tags")
            DETECT_REASONING_TAGS = reasoning_tags_param is not False
            DETECT_CODE_INTERPRETER = metadata.get("features", {}).get(
                "code_interpreter", False
            )

            reasoning_tags = []
            if DETECT_REASONING_TAGS:
                if (
                    isinstance(reasoning_tags_param, list)
                    and len(reasoning_tags_param) == 2
                ):
                    reasoning_tags = [
                        (reasoning_tags_param[0], reasoning_tags_param[1])
                    ]
                else:
                    reasoning_tags = DEFAULT_REASONING_TAGS

            try:
                for event in events:
                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": event,
                        }
                    )

                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            **event,
                        },
                    )

                async def stream_body_handler(response, form_data):
                    nonlocal content
                    nonlocal usage
                    nonlocal output
                    nonlocal termination_cause

                    response_tool_calls = []

                    delta_count = 0
                    delta_chunk_size = max(
                        CHAT_RESPONSE_STREAM_DELTA_CHUNK_SIZE,
                        int(
                            metadata.get("params", {}).get("stream_delta_chunk_size")
                            or 1
                        ),
                    )
                    last_delta_data = None

                    async def flush_pending_delta_data(threshold: int = 0):
                        nonlocal delta_count
                        nonlocal last_delta_data

                        if delta_count >= threshold and last_delta_data:
                            await event_emitter(
                                {
                                    "type": "chat:completion",
                                    "data": last_delta_data,
                                }
                            )
                            delta_count = 0
                            last_delta_data = None

                    async for line in response.body_iterator:
                        line = (
                            line.decode("utf-8", "replace")
                            if isinstance(line, bytes)
                            else line
                        )
                        data = line

                        # Skip empty lines
                        if not data.strip():
                            continue

                        # "data:" is the prefix for each event
                        if not data.startswith("data:"):
                            continue

                        # Remove the prefix
                        data = data[len("data:") :].strip()

                        try:
                            data = json.loads(data)

                            data, _ = await process_filter_functions(
                                request=request,
                                filter_functions=filter_functions,
                                filter_type="stream",
                                form_data=data,
                                extra_params={"__body__": form_data, **extra_params},
                            )

                            if data:
                                if "event" in data and not getattr(
                                    request.state, "direct", False
                                ):
                                    await event_emitter(data.get("event", {}))

                                if "selected_model_id" in data:
                                    model_id = data["selected_model_id"]
                                    Chats.upsert_message_to_chat_by_id_and_message_id(
                                        metadata["chat_id"],
                                        metadata["message_id"],
                                        {
                                            "selectedModelId": model_id,
                                        },
                                    )
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                                # Check for Responses API events (type field starts with "response.")
                                elif data.get("type", "").startswith("response."):
                                    output, response_metadata = (
                                        handle_responses_streaming_event(data, output)
                                    )

                                    processed_data = {
                                        "output": output,
                                        "content": serialize_output(output),
                                    }

                                    # print(data)
                                    # print(processed_data)

                                    # Merge any metadata (usage, done, etc.)
                                    if response_metadata:
                                        processed_data.update(response_metadata)

                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": processed_data,
                                        }
                                    )
                                    continue
                                else:
                                    choices = data.get("choices", [])

                                    # Normalize usage data to standard format
                                    raw_usage = data.get("usage", {}) or {}
                                    raw_usage.update(
                                        data.get("timings", {})
                                    )  # llama.cpp
                                    if raw_usage:
                                        usage = normalize_usage(raw_usage)
                                        await event_emitter(
                                            {
                                                "type": "chat:completion",
                                                "data": {
                                                    "usage": usage,
                                                },
                                            }
                                        )

                                    if not choices:
                                        error = data.get("error", {})
                                        if error:
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "error": error,
                                                    },
                                                }
                                            )
                                        continue

                                    delta = choices[0].get("delta", {})

                                    # Handle delta annotations
                                    annotations = delta.get("annotations")
                                    if annotations:
                                        for annotation in annotations:
                                            if (
                                                annotation.get("type") == "url_citation"
                                                and "url_citation" in annotation
                                            ):
                                                url_citation = annotation[
                                                    "url_citation"
                                                ]

                                                url = url_citation.get("url", "")
                                                title = url_citation.get("title", url)

                                                await event_emitter(
                                                    {
                                                        "type": "source",
                                                        "data": {
                                                            "source": {
                                                                "name": title,
                                                                "url": url,
                                                            },
                                                            "document": [title],
                                                            "metadata": [
                                                                {
                                                                    "source": url,
                                                                    "name": title,
                                                                }
                                                            ],
                                                        },
                                                    }
                                                )

                                    delta_tool_calls = delta.get("tool_calls", None)
                                    if delta_tool_calls:
                                        for delta_tool_call in delta_tool_calls:
                                            tool_call_index = delta_tool_call.get(
                                                "index"
                                            )

                                            if tool_call_index is not None:
                                                # Check if the tool call already exists
                                                current_response_tool_call = None
                                                for (
                                                    response_tool_call
                                                ) in response_tool_calls:
                                                    if (
                                                        response_tool_call.get("index")
                                                        == tool_call_index
                                                    ):
                                                        current_response_tool_call = (
                                                            response_tool_call
                                                        )
                                                        break

                                                if current_response_tool_call is None:
                                                    # Add the new tool call
                                                    delta_tool_call.setdefault(
                                                        "function", {}
                                                    )
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("name", "")
                                                    delta_tool_call[
                                                        "function"
                                                    ].setdefault("arguments", "")
                                                    response_tool_calls.append(
                                                        delta_tool_call
                                                    )
                                                else:
                                                    # Update the existing tool call
                                                    delta_name = delta_tool_call.get(
                                                        "function", {}
                                                    ).get("name")
                                                    delta_arguments = (
                                                        delta_tool_call.get(
                                                            "function", {}
                                                        ).get("arguments")
                                                    )

                                                    if delta_name:
                                                        current_response_tool_call[
                                                            "function"
                                                        ]["name"] = delta_name

                                                    if delta_arguments:
                                                        current_response_tool_call[
                                                            "function"
                                                        ][
                                                            "arguments"
                                                        ] += delta_arguments

                                        # Emit pending tool calls in real-time
                                        if response_tool_calls:
                                            # Flush any pending text first
                                            await flush_pending_delta_data()

                                            # Build pending function_call output items for display
                                            pending_fc_items = []
                                            for tc in response_tool_calls:
                                                call_id = tc.get("id", "")
                                                func = tc.get("function", {})
                                                pending_fc_items.append(
                                                    {
                                                        "type": "function_call",
                                                        "id": call_id
                                                        or output_id("fc"),
                                                        "call_id": call_id,
                                                        "name": func.get("name", ""),
                                                        "arguments": func.get(
                                                            "arguments", "{}"
                                                        ),
                                                        "status": "in_progress",
                                                    }
                                                )
                                            pending_output = output + pending_fc_items
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "content": serialize_output(
                                                            pending_output
                                                        ),
                                                    },
                                                }
                                            )

                                    image_urls = get_image_urls(
                                        delta.get("images", []), request, metadata, user
                                    )
                                    if image_urls:
                                        image_file_list = [
                                            {"type": "image", "url": url}
                                            for url in image_urls
                                        ]
                                        message_files = Chats.add_message_files_by_id_and_message_id(
                                            metadata["chat_id"],
                                            metadata["message_id"],
                                            image_file_list,
                                        )
                                        if message_files is None:
                                            message_files = image_file_list

                                        await event_emitter(
                                            {
                                                "type": "files",
                                                "data": {"files": message_files},
                                            }
                                        )

                                    value = delta.get("content")
                                    _append_token_telemetry_from_choice(
                                        token_telemetry_state, choices[0], value
                                    )

                                    reasoning_content = (
                                        delta.get("reasoning_content")
                                        or delta.get("reasoning")
                                        or delta.get("thinking")
                                    )
                                    if reasoning_content:
                                        if (
                                            not output
                                            or output[-1].get("type") != "reasoning"
                                        ):
                                            reasoning_item = {
                                                "type": "reasoning",
                                                "id": output_id("r"),
                                                "status": "in_progress",
                                                "start_tag": "<think>",
                                                "end_tag": "</think>",
                                                "attributes": {
                                                    "type": "reasoning_content"
                                                },
                                                "content": [],
                                                "summary": None,
                                                "started_at": time.time(),
                                            }
                                            output.append(reasoning_item)
                                        else:
                                            reasoning_item = output[-1]

                                        # Append to reasoning content
                                        parts = reasoning_item.get("content", [])
                                        if (
                                            parts
                                            and parts[-1].get("type") == "output_text"
                                        ):
                                            parts[-1]["text"] += reasoning_content
                                        else:
                                            reasoning_item["content"] = [
                                                {
                                                    "type": "output_text",
                                                    "text": reasoning_content,
                                                }
                                            ]

                                        data = {"content": serialize_output(output)}

                                    if value:
                                        if (
                                            output
                                            and output[-1].get("type") == "reasoning"
                                            and output[-1]
                                            .get("attributes", {})
                                            .get("type")
                                            == "reasoning_content"
                                        ):
                                            reasoning_item = output[-1]
                                            reasoning_item["ended_at"] = time.time()
                                            reasoning_item["duration"] = int(
                                                reasoning_item["ended_at"]
                                                - reasoning_item["started_at"]
                                            )
                                            reasoning_item["status"] = "completed"

                                            output.append(
                                                {
                                                    "type": "message",
                                                    "id": output_id("msg"),
                                                    "status": "in_progress",
                                                    "role": "assistant",
                                                    "content": [
                                                        {
                                                            "type": "output_text",
                                                            "text": "",
                                                        }
                                                    ],
                                                }
                                            )

                                        if ENABLE_CHAT_RESPONSE_BASE64_IMAGE_URL_CONVERSION:
                                            value = convert_markdown_base64_images(
                                                request,
                                                value,
                                                {
                                                    "chat_id": metadata.get(
                                                        "chat_id", None
                                                    ),
                                                    "message_id": metadata.get(
                                                        "message_id", None
                                                    ),
                                                },
                                                user,
                                            )

                                        content = f"{content}{value}"

                                        # Check if we're inside a tag-based block
                                        # (reasoning, code_interpreter, or solution).
                                        # If so, append to the existing in-progress
                                        # item instead of creating a new message —
                                        # otherwise tag_output_handler re-detects the
                                        # start tag on every chunk and fragments the
                                        # output.
                                        last_item = output[-1] if output else None
                                        last_item_type = (
                                            last_item.get("type", "")
                                            if last_item
                                            else ""
                                        )
                                        inside_tag_block = (
                                            last_item is not None
                                            and last_item.get("status") == "in_progress"
                                            and last_item.get("attributes", {}).get(
                                                "type"
                                            )
                                            != "reasoning_content"
                                            and (
                                                last_item_type == "reasoning"
                                                or last_item_type
                                                == "open_webui:code_interpreter"
                                                or (
                                                    last_item_type == "message"
                                                    and last_item.get("_tag_type")
                                                    is not None
                                                )
                                            )
                                        )

                                        if inside_tag_block:
                                            # Append to the existing tag-based item
                                            if (
                                                last_item_type
                                                == "open_webui:code_interpreter"
                                            ):
                                                last_item["code"] = (
                                                    last_item.get("code", "") + value
                                                )
                                            elif last_item_type == "reasoning":
                                                parts = last_item.get("content", [])
                                                if (
                                                    parts
                                                    and parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                            else:
                                                # solution or other _tag_type message
                                                msg_parts = last_item.get("content", [])
                                                if (
                                                    msg_parts
                                                    and msg_parts[-1].get("type")
                                                    == "output_text"
                                                ):
                                                    msg_parts[-1]["text"] += value
                                                else:
                                                    last_item["content"] = [
                                                        {
                                                            "type": "output_text",
                                                            "text": value,
                                                        }
                                                    ]
                                        else:
                                            if (
                                                not output
                                                or output[-1].get("type") != "message"
                                            ):
                                                output.append(
                                                    {
                                                        "type": "message",
                                                        "id": output_id("msg"),
                                                        "status": "in_progress",
                                                        "role": "assistant",
                                                        "content": [
                                                            {
                                                                "type": "output_text",
                                                                "text": "",
                                                            }
                                                        ],
                                                    }
                                                )

                                            # Append value to last message item's text
                                            msg_parts = output[-1].get("content", [])
                                            if (
                                                msg_parts
                                                and msg_parts[-1].get("type")
                                                == "output_text"
                                            ):
                                                msg_parts[-1]["text"] += value
                                            else:
                                                output[-1]["content"] = [
                                                    {
                                                        "type": "output_text",
                                                        "text": value,
                                                    }
                                                ]

                                        if DETECT_REASONING_TAGS:
                                            output, _ = tag_output_handler(
                                                "reasoning",
                                                reasoning_tags,
                                                output,
                                            )

                                            output, _ = tag_output_handler(
                                                "solution",
                                                DEFAULT_SOLUTION_TAGS,
                                                output,
                                            )

                                        if DETECT_CODE_INTERPRETER:
                                            output, end = tag_output_handler(
                                                "code_interpreter",
                                                DEFAULT_CODE_INTERPRETER_TAGS,
                                                output,
                                            )

                                            if end:
                                                break

                                        if ENABLE_REALTIME_CHAT_SAVE:
                                            # Save message in the database
                                            Chats.upsert_message_to_chat_by_id_and_message_id(
                                                metadata["chat_id"],
                                                metadata["message_id"],
                                                {
                                                    "content": serialize_output(output),
                                                    "output": output,
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_output(output),
                                            }

                                if delta:
                                    delta_count += 1
                                    last_delta_data = data
                                    if delta_count >= delta_chunk_size:
                                        await flush_pending_delta_data(delta_chunk_size)
                                else:
                                    await event_emitter(
                                        {
                                            "type": "chat:completion",
                                            "data": data,
                                        }
                                    )
                        except Exception as e:
                            done = "data: [DONE]" in line
                            if done:
                                pass
                            else:
                                log.debug(f"Error: {e}")
                                continue
                    await flush_pending_delta_data()

                    if output:
                        # Clean up the last message item
                        if output[-1].get("type") == "message":
                            parts = output[-1].get("content", [])
                            if parts and parts[-1].get("type") == "output_text":
                                parts[-1]["text"] = parts[-1]["text"].strip()

                                if not parts[-1]["text"]:
                                    output.pop()

                                    if not output:
                                        output.append(
                                            {
                                                "type": "message",
                                                "id": output_id("msg"),
                                                "status": "in_progress",
                                                "role": "assistant",
                                                "content": [
                                                    {"type": "output_text", "text": ""}
                                                ],
                                            }
                                        )

                        if output[-1].get("type") == "reasoning":
                            reasoning_item = output[-1]
                            if reasoning_item.get("ended_at") is None:
                                reasoning_item["ended_at"] = time.time()
                                reasoning_item["duration"] = int(
                                    reasoning_item["ended_at"]
                                    - reasoning_item["started_at"]
                                )
                                reasoning_item["status"] = "completed"

                    if response_tool_calls:
                        tool_calls.append(_split_tool_calls(response_tool_calls))

                    if response.background:
                        await response.background()

                await stream_body_handler(response, form_data)

                tool_call_retries = 0
                tool_call_sources = []  # Track citation sources from tool results
                all_tool_call_sources = []  # Accumulated sources across all iterations
                user_message = get_last_user_message(form_data["messages"])
                search_notes_empty_streak = 0
                search_notes_guard_active = False

                # Check if citations are enabled for this model
                citations_enabled = (
                    model.get("info", {}).get("meta", {}).get("capabilities") or {}
                ).get("citations", True)

                # Use the pre-RAG system content captured before the
                # initial file-source injection in process_chat_payload.
                # This ensures restore truly undoes the RAG template.
                original_system_content = metadata.get("system_prompt")
                if original_system_content is None:
                    original_system_message = get_system_message(form_data["messages"])
                    original_system_content = (
                        get_content_from_message(original_system_message)
                        if original_system_message
                        else None
                    )

                while (
                    len(tool_calls) > 0
                    and tool_call_retries < CHAT_RESPONSE_MAX_TOOL_CALL_RETRIES
                ):

                    tool_call_retries += 1

                    response_tool_calls = tool_calls.pop(0)

                    # Append function_call items for each tool call
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        func = tc.get("function", {})
                        output.append(
                            {
                                "type": "function_call",
                                "id": call_id or output_id("fc"),
                                "call_id": call_id,
                                "name": func.get("name", ""),
                                "arguments": func.get("arguments", "{}"),
                                "status": "in_progress",
                            }
                        )

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    tools = metadata.get("tools", {})

                    results = []
                    completed_tool_phases = []

                    for tool_call in response_tool_calls:
                        tool_call_id = tool_call.get("id", "")
                        tool_function_name = tool_call.get("function", {}).get(
                            "name", ""
                        )
                        resolved_tool_function_name = TOOL_NAME_ALIASES.get(
                            tool_function_name, tool_function_name
                        )
                        tool_args = tool_call.get("function", {}).get("arguments", "{}")
                        tool_started_at = time.time()

                        tool_function_params = {}
                        if tool_args and tool_args.strip():
                            try:
                                # json.loads cannot be used because some models do not produce valid JSON
                                tool_function_params = ast.literal_eval(tool_args)
                            except Exception as e:
                                log.debug(e)
                                # Fallback to JSON parsing
                                try:
                                    tool_function_params = json.loads(tool_args)
                                except Exception as e:
                                    log.error(
                                        f"Error parsing tool call arguments: {tool_args}"
                                    )
                                    parse_event = _append_tool_journey_event(
                                        metadata,
                                        {
                                            "phase": "tool_args_parse_error",
                                            "call_id": tool_call_id,
                                            "tool": tool_function_name,
                                            "arguments_preview": _truncate_telemetry_value(
                                                tool_args
                                            ),
                                        },
                                    )
                                    if parse_event:
                                        await event_emitter(
                                            {
                                                "type": "chat:tool:journey",
                                                "data": parse_event,
                                            }
                                        )
                                    results.append(
                                        {
                                            "tool_call_id": tool_call_id,
                                            "content": f"Error: Tool call arguments could not be parsed. The model generated malformed or incomplete JSON for `{tool_function_name}`. Please try again.",
                                        }
                                    )
                                    continue

                        # Ensure arguments are valid JSON for downstream LLM integrations
                        log.debug(
                            f"Parsed args from {tool_args} to {tool_function_params}"
                        )
                        tool_call.setdefault("function", {})["arguments"] = json.dumps(
                            tool_function_params
                        )
                        start_event = _append_tool_journey_event(
                            metadata,
                            {
                                "phase": "tool_execute_start",
                                "call_id": tool_call_id,
                                "tool": tool_function_name,
                                "params_preview": _truncate_telemetry_value(
                                    tool_function_params
                                ),
                            },
                        )
                        if start_event:
                            await event_emitter(
                                {
                                    "type": "chat:tool:journey",
                                    "data": start_event,
                                }
                            )

                        tool_result = None
                        tool = None
                        tool_type = None
                        direct_tool = False
                        tool_execution_error = None
                        search_notes_blocked = False
                        next_web_tool = _preferred_web_tool_for_loop_breaker(tools)

                        if (
                            resolved_tool_function_name in NOTES_LOOKUP_TOOL_NAMES
                            and search_notes_guard_active
                        ):
                            search_notes_blocked = True
                            tool_result = _build_search_notes_loop_breaker_result(
                                search_notes_empty_streak,
                                blocked=True,
                                tool_name=tool_function_name,
                                next_tool=next_web_tool,
                            )
                            blocked_event = _append_tool_journey_event(
                                metadata,
                                {
                                    "phase": "tool_loop_breaker_blocked",
                                    "call_id": tool_call_id,
                                    "tool": resolved_tool_function_name,
                                    "empty_streak": search_notes_empty_streak,
                                    "next_tool": next_web_tool,
                                },
                            )
                            if blocked_event:
                                await event_emitter(
                                    {
                                        "type": "chat:tool:journey",
                                        "data": blocked_event,
                                    }
                                )

                        if search_notes_blocked:
                            pass
                        elif resolved_tool_function_name in tools:
                            tool = tools[resolved_tool_function_name]
                            spec = tool.get("spec", {})

                            tool_type = tool.get("type", "")
                            direct_tool = tool.get("direct", False)

                            try:
                                allowed_params = (
                                    spec.get("parameters", {})
                                    .get("properties", {})
                                    .keys()
                                )

                                tool_function_params = {
                                    k: v
                                    for k, v in tool_function_params.items()
                                    if k in allowed_params
                                }

                                if direct_tool:
                                    tool_result = await event_caller(
                                        {
                                            "type": "execute:tool",
                                            "data": {
                                                "id": str(uuid4()),
                                                "name": resolved_tool_function_name,
                                                "params": tool_function_params,
                                                "server": tool.get("server", {}),
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                            },
                                        }
                                    )

                                else:
                                    tool_function = get_updated_tool_function(
                                        function=tool["callable"],
                                        extra_params={
                                            "__messages__": form_data.get(
                                                "messages", []
                                            ),
                                            "__files__": metadata.get("files", []),
                                        },
                                    )

                                    tool_result = await tool_function(
                                        **tool_function_params
                                    )

                            except Exception as e:
                                tool_result = str(e)
                                tool_execution_error = str(e)
                        else:
                            missing_event = _append_tool_journey_event(
                                metadata,
                                {
                                    "phase": "tool_not_found",
                                    "call_id": tool_call_id,
                                    "tool": tool_function_name,
                                },
                            )
                            if missing_event:
                                await event_emitter(
                                    {
                                        "type": "chat:tool:journey",
                                        "data": missing_event,
                                    }
                                )

                        tool_result, tool_result_files, tool_result_embeds = (
                            process_tool_result(
                                request,
                                tool_function_name,
                                tool_result,
                                tool_type,
                                direct_tool,
                                metadata,
                                user,
                            )
                        )

                        if resolved_tool_function_name in NOTES_LOOKUP_TOOL_NAMES:
                            if not search_notes_blocked:
                                if _is_empty_search_notes_result(tool_result):
                                    search_notes_empty_streak += 1
                                else:
                                    search_notes_empty_streak = 0

                            if (
                                not search_notes_blocked
                                and search_notes_empty_streak
                                >= SEARCH_NOTES_EMPTY_STREAK_LIMIT
                            ):
                                search_notes_guard_active = True
                                tool_result = _build_search_notes_loop_breaker_result(
                                    search_notes_empty_streak,
                                    tool_name=tool_function_name,
                                    next_tool=next_web_tool,
                                )
                                breaker_event = _append_tool_journey_event(
                                    metadata,
                                    {
                                        "phase": "tool_loop_breaker_triggered",
                                        "call_id": tool_call_id,
                                        "tool": resolved_tool_function_name,
                                        "empty_streak": search_notes_empty_streak,
                                        "next_tool": next_web_tool,
                                    },
                                )
                                if breaker_event:
                                    await event_emitter(
                                        {
                                            "type": "chat:tool:journey",
                                            "data": breaker_event,
                                        }
                                    )
                        elif resolved_tool_function_name not in NOTES_LOOKUP_TOOL_NAMES:
                            search_notes_empty_streak = 0

                        await terminal_event_handler(
                            tool_function_name,
                            tool_function_params,
                            tool_result,
                            event_emitter,
                        )

                        completion_event = _append_tool_journey_event(
                            metadata,
                            {
                                "phase": "tool_execute_done",
                                "call_id": tool_call_id,
                                "tool": tool_function_name,
                                "duration_ms": int((time.time() - tool_started_at) * 1000),
                                "status": "error" if tool_execution_error else "ok",
                                **(
                                    {"error": _truncate_telemetry_value(tool_execution_error)}
                                    if tool_execution_error
                                    else {}
                                ),
                                "result_summary": _tool_result_summary(
                                    tool_function_name, tool_result
                                ),
                            },
                        )
                        if completion_event:
                            await event_emitter(
                                {
                                    "type": "chat:tool:journey",
                                    "data": completion_event,
                                }
                            )

                        for research_event in _update_research_turn_state(
                            metadata,
                            tool_name=tool_function_name,
                            tool_params=tool_function_params,
                            tool_result=tool_result,
                        ):
                            await _emit_tool_journey_event(
                                metadata, event_emitter, research_event
                            )

                        # Extract citation sources from tool results
                        if (
                            citations_enabled
                            and tool_function_name
                            in [
                                "search_web",
                                "web_research_strong",
                                "search_strong_sources",
                                "query_web_evidence",
                                "fetch_url",
                                "view_knowledge_file",
                                "query_knowledge_files",
                            ]
                            and tool_result
                        ):
                            try:
                                citation_sources = get_citation_source_from_tool_result(
                                    tool_name=tool_function_name,
                                    tool_params=tool_function_params,
                                    tool_result=tool_result,
                                    tool_id=tool.get("tool_id", "") if tool else "",
                                )
                                tool_call_sources.extend(citation_sources)
                            except Exception as e:
                                log.exception(f"Error extracting citation source: {e}")

                        results.append(
                            {
                                "tool_call_id": tool_call_id,
                                "content": str(tool_result) if tool_result else "",
                                **(
                                    {"files": tool_result_files}
                                    if tool_result_files
                                    else {}
                                ),
                                **(
                                    {"embeds": tool_result_embeds}
                                    if tool_result_embeds
                                    else {}
                                ),
                            }
                        )
                        phase = _tool_narration_phase_for_tool(
                            resolved_tool_function_name
                        )
                        if phase:
                            completed_tool_phases.append(phase)

                    # Update function_call statuses and append function_call_output items
                    for tc in response_tool_calls:
                        call_id = tc.get("id", "")
                        # Mark function_call as completed
                        for item in output:
                            if (
                                item.get("type") == "function_call"
                                and item.get("call_id") == call_id
                            ):
                                item["status"] = "completed"
                                # Update arguments with parsed/sanitized version
                                item["arguments"] = tc.get("function", {}).get(
                                    "arguments", "{}"
                                )
                                break

                    for result in results:
                        output.append(
                            {
                                "type": "function_call_output",
                                "id": output_id("fco"),
                                "call_id": result.get("tool_call_id", ""),
                                "output": [
                                    {
                                        "type": "input_text",
                                        "text": result.get("content", ""),
                                    }
                                ],
                                "status": "completed",
                                **(
                                    {"files": result.get("files")}
                                    if result.get("files")
                                    else {}
                                ),
                                **(
                                    {"embeds": result.get("embeds")}
                                    if result.get("embeds")
                                    else {}
                                ),
                            }
                        )

                    # Append a new empty message item for the next response
                    output.append(
                        {
                            "type": "message",
                            "id": output_id("msg"),
                            "status": "in_progress",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": ""}],
                        }
                    )

                    # Emit citation sources to the frontend for display
                    if citations_enabled:
                        for source in tool_call_sources:
                            await event_emitter({"type": "source", "data": source})

                        # Apply tool source context to messages for the model.
                        # Restoring to pre-RAG original prevents duplicating
                        # the RAG template across file and tool sources.
                        all_tool_call_sources.extend(tool_call_sources)
                        if all_tool_call_sources and user_message:
                            metadata["tool_call_sources"] = copy.deepcopy(
                                all_tool_call_sources
                            )
                            # Restore pre-RAG message state before re-applying
                            # to prevent RAG template duplication.
                            original_user_message = (
                                metadata.get("user_prompt") or user_message
                            )
                            set_last_user_message_content(
                                original_user_message,
                                form_data["messages"],
                            )
                            replace_system_message_content(
                                original_system_content or "",
                                form_data["messages"],
                            )

                            # Build context: file sources with content,
                            # tool sources as citation markers only.
                            source_ids = {}
                            source_context = get_source_context(
                                metadata.get("sources", []), source_ids
                            ) + get_source_context(
                                all_tool_call_sources,
                                source_ids,
                                include_content=False,
                            )
                            source_context = source_context.strip()
                            if source_context:
                                rag_content = rag_template(
                                    request.app.state.config.RAG_TEMPLATE,
                                    source_context,
                                    user_message,
                                )
                                if RAG_SYSTEM_CONTEXT:
                                    form_data["messages"] = (
                                        add_or_update_system_message(
                                            rag_content,
                                            form_data["messages"],
                                            append=True,
                                        )
                                    )
                                else:
                                    form_data["messages"] = add_or_update_user_message(
                                        rag_content,
                                        form_data["messages"],
                                        append=False,
                                    )
                        tool_call_sources.clear()

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": output,
                            },
                        }
                    )

                    try:
                        narration_instruction = _register_tool_narration_phase_transition(
                            metadata.get("tool_narration_state", {}),
                            completed_tool_phases,
                        )
                        continuation_messages = _build_tool_continuation_messages(
                            form_data["messages"],
                            output,
                            narration_instruction=narration_instruction,
                        )
                        new_form_data = {
                            **form_data,
                            "model": model_id,
                            "stream": True,
                            "messages": continuation_messages,
                        }

                        res = await generate_chat_completion(
                            request,
                            new_form_data,
                            user,
                            bypass_system_prompt=True,
                        )

                        if isinstance(res, StreamingResponse):
                            await stream_body_handler(res, new_form_data)
                        else:
                            termination_cause = _build_agent_loop_termination_cause(
                                kind="unexpected_non_streaming_response",
                                phase="tool_loop_continuation",
                                detail={"response_type": type(res).__name__},
                            )
                            log.warning(
                                "Agent loop stopped after tool continuation without streaming response: chat_id=%s message_id=%s cause=%s",
                                metadata.get("chat_id"),
                                metadata.get("message_id"),
                                termination_cause,
                            )
                            break
                    except Exception as e:
                        termination_cause = _build_agent_loop_termination_cause(
                            kind="continuation_exception",
                            phase="tool_loop_continuation",
                            exc=e,
                        )
                        log.warning(
                            "Agent loop stopped after tool continuation exception: chat_id=%s message_id=%s cause=%s",
                            metadata.get("chat_id"),
                            metadata.get("message_id"),
                            termination_cause,
                            exc_info=True,
                        )
                        break

                if DETECT_CODE_INTERPRETER:
                    MAX_RETRIES = 5
                    retries = 0

                    while (
                        output
                        and output[-1].get("type") == "open_webui:code_interpreter"
                        and retries < MAX_RETRIES
                    ):

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        retries += 1
                        log.debug(f"Attempt count: {retries}")

                        ci_item = output[-1]
                        ci_output = ""
                        try:
                            if ci_item.get("attributes", {}).get("type") == "code":
                                code = ci_item.get("code", "")
                                # Sanitize code (strips ANSI codes and markdown fences)
                                code = sanitize_code(code)

                                if CODE_INTERPRETER_BLOCKED_MODULES:
                                    blocking_code = textwrap.dedent(f"""
                                        import builtins
    
                                        BLOCKED_MODULES = {CODE_INTERPRETER_BLOCKED_MODULES}
    
                                        _real_import = builtins.__import__
                                        def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
                                            if name.split('.')[0] in BLOCKED_MODULES:
                                                importer_name = globals.get('__name__') if globals else None
                                                if importer_name == '__main__':
                                                    raise ImportError(
                                                        f"Direct import of module {{name}} is restricted."
                                                    )
                                            return _real_import(name, globals, locals, fromlist, level)
    
                                        builtins.__import__ = restricted_import
                                    """)
                                    code = blocking_code + "\n" + code

                                if (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "pyodide"
                                ):
                                    ci_output = await event_caller(
                                        {
                                            "type": "execute:python",
                                            "data": {
                                                "id": str(uuid4()),
                                                "code": code,
                                                "session_id": metadata.get(
                                                    "session_id", None
                                                ),
                                                "files": metadata.get("files", []),
                                            },
                                        }
                                    )
                                elif (
                                    request.app.state.config.CODE_INTERPRETER_ENGINE
                                    == "jupyter"
                                ):
                                    ci_output = await execute_code_jupyter(
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_URL,
                                        code,
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_TOKEN
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "token"
                                            else None
                                        ),
                                        (
                                            request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH_PASSWORD
                                            if request.app.state.config.CODE_INTERPRETER_JUPYTER_AUTH
                                            == "password"
                                            else None
                                        ),
                                        request.app.state.config.CODE_INTERPRETER_JUPYTER_TIMEOUT,
                                    )
                                else:
                                    ci_output = {
                                        "stdout": "Code interpreter engine not configured."
                                    }

                                log.debug(f"Code interpreter output: {ci_output}")

                                if isinstance(ci_output, dict):
                                    stdout = ci_output.get("stdout", "")

                                    if isinstance(stdout, str):
                                        stdoutLines = stdout.split("\n")
                                        for idx, line in enumerate(stdoutLines):

                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                if image_url:
                                                    stdoutLines[idx] = (
                                                        f"![Output Image]({image_url})"
                                                    )

                                        ci_output["stdout"] = "\n".join(stdoutLines)

                                    result = ci_output.get("result", "")

                                    if isinstance(result, str):
                                        resultLines = result.split("\n")
                                        for idx, line in enumerate(resultLines):
                                            if "data:image/png;base64" in line:
                                                image_url = get_image_url_from_base64(
                                                    request,
                                                    line,
                                                    metadata,
                                                    user,
                                                )
                                                resultLines[idx] = (
                                                    f"![Output Image]({image_url})"
                                                )
                                        ci_output["result"] = "\n".join(resultLines)
                        except Exception as e:
                            ci_output = str(e)

                        ci_item["output"] = ci_output
                        ci_item["status"] = "completed"

                        output.append(
                            {
                                "type": "message",
                                "id": output_id("msg"),
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [{"type": "output_text", "text": ""}],
                            }
                        )

                        await event_emitter(
                            {
                                "type": "chat:completion",
                                "data": {
                                    "content": serialize_output(output),
                                    "output": output,
                                },
                            }
                        )

                        try:
                            new_form_data = {
                                **form_data,
                                "model": model_id,
                                "stream": True,
                                "messages": [
                                    *form_data["messages"],
                                    *convert_output_to_messages(output, raw=True),
                                ],
                            }

                            res = await generate_chat_completion(
                                request,
                                new_form_data,
                                user,
                                bypass_system_prompt=True,
                            )

                            if isinstance(res, StreamingResponse):
                                await stream_body_handler(res, new_form_data)
                            else:
                                termination_cause = (
                                    _build_agent_loop_termination_cause(
                                        kind="unexpected_non_streaming_response",
                                        phase="code_interpreter_continuation",
                                        detail={"response_type": type(res).__name__},
                                    )
                                )
                                log.warning(
                                    "Agent loop stopped after code interpreter continuation without streaming response: chat_id=%s message_id=%s cause=%s",
                                    metadata.get("chat_id"),
                                    metadata.get("message_id"),
                                    termination_cause,
                                )
                                break
                        except Exception as e:
                            termination_cause = _build_agent_loop_termination_cause(
                                kind="continuation_exception",
                                phase="code_interpreter_continuation",
                                exc=e,
                            )
                            log.warning(
                                "Agent loop stopped after code interpreter continuation exception: chat_id=%s message_id=%s cause=%s",
                                metadata.get("chat_id"),
                                metadata.get("message_id"),
                                termination_cause,
                                exc_info=True,
                            )
                            break

                # Mark all in-progress items as completed
                for item in output:
                    if item.get("status") == "in_progress":
                        item["status"] = "completed"

                token_telemetry = _build_token_telemetry_payload(token_telemetry_state)
                memory_telemetry = (
                    metadata.get("memory_telemetry")
                    if metadata.get("params", {}).get("debug_memory_telemetry")
                    else None
                )
                tool_journey_telemetry = (
                    metadata.get("tool_journey_telemetry")
                    if _is_debug_flag_enabled(
                        metadata.get("params", {}).get("debug_tool_journey")
                    )
                    else None
                )
                prompt_telemetry = (
                    get_prompt_telemetry(request, metadata)
                    if is_prompt_telemetry_enabled(metadata)
                    else None
                )
                if isinstance(tool_journey_telemetry, dict):
                    tool_journey_telemetry["completed_at"] = int(time.time())
                title = Chats.get_chat_title_by_id(metadata["chat_id"])
                data = {
                    "done": True,
                    "content": serialize_output(output),
                    "output": output,
                    "title": title,
                    **(
                        {"tokenTelemetry": token_telemetry}
                        if token_telemetry
                        else {}
                    ),
                    **({"tokenBranch": token_branch} if token_branch else {}),
                    **(
                        {"memoryTelemetry": memory_telemetry}
                        if memory_telemetry
                        else {}
                    ),
                    **(
                        {"toolJourneyTelemetry": tool_journey_telemetry}
                        if tool_journey_telemetry
                        else {}
                    ),
                    **(
                        {"promptTelemetry": prompt_telemetry}
                        if prompt_telemetry
                        else {}
                    ),
                    **(
                        {"terminationCause": termination_cause}
                        if termination_cause
                        else {}
                    ),
                }

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_output(output),
                            "output": output,
                            **({"usage": usage} if usage else {}),
                            **(
                                {"tokenTelemetry": token_telemetry}
                                if token_telemetry
                                else {}
                            ),
                            **({"tokenBranch": token_branch} if token_branch else {}),
                            **(
                                {"memoryTelemetry": memory_telemetry}
                                if memory_telemetry
                                else {}
                            ),
                            **(
                                {"toolJourneyTelemetry": tool_journey_telemetry}
                                if tool_journey_telemetry
                                else {}
                            ),
                            **(
                                {"promptTelemetry": prompt_telemetry}
                                if prompt_telemetry
                                else {}
                            ),
                            **(
                                {"terminationCause": termination_cause}
                                if termination_cause
                                else {}
                            ),
                        },
                    )
                elif (
                    usage
                    or token_telemetry
                    or token_branch
                    or memory_telemetry
                    or tool_journey_telemetry
                    or prompt_telemetry
                    or termination_cause
                ):
                    update_payload = {}
                    if usage:
                        update_payload["usage"] = usage
                    if token_telemetry:
                        update_payload["tokenTelemetry"] = token_telemetry
                    if token_branch:
                        update_payload["tokenBranch"] = token_branch
                    if memory_telemetry:
                        update_payload["memoryTelemetry"] = memory_telemetry
                    if tool_journey_telemetry:
                        update_payload["toolJourneyTelemetry"] = tool_journey_telemetry
                    if prompt_telemetry:
                        update_payload["promptTelemetry"] = prompt_telemetry
                    if termination_cause:
                        update_payload["terminationCause"] = termination_cause
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        update_payload,
                    )

                # Send a webhook notification if the user is not active
                if not Users.is_user_active(user.id):
                    webhook_url = Users.get_user_webhook_url_by_id(user.id)
                    if webhook_url:
                        await post_webhook(
                            request.app.state.WEBUI_NAME,
                            webhook_url,
                            f"{title} - {request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}\n\n{content}",
                            {
                                "action": "chat",
                                "message": content,
                                "title": title,
                                "url": f"{request.app.state.config.WEBUI_URL}/c/{metadata['chat_id']}",
                            },
                        )

                await event_emitter(
                    {
                        "type": "chat:completion",
                        "data": data,
                    }
                )

                await background_tasks_handler(ctx)
            except asyncio.CancelledError:
                log.warning("Task was cancelled!")
                termination_cause = _build_agent_loop_termination_cause(
                    kind="task_cancelled",
                    phase="streaming_chat_response_handler",
                )
                await event_emitter({"type": "chat:tasks:cancel"})

                if not ENABLE_REALTIME_CHAT_SAVE:
                    # Save message in the database
                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        {
                            "content": serialize_output(output),
                            "output": output,
                            "terminationCause": termination_cause,
                        },
                    )

            if response.background is not None:
                await response.background()

        return await response_handler(response, events)

    else:
        # Fallback to the original response
        async def stream_wrapper(original_generator, events):
            def wrap_item(item):
                return f"data: {item}\n\n"

            for event in events:
                event, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=event,
                    extra_params=extra_params,
                )

                if event:
                    yield wrap_item(json.dumps(event))

            async for data in original_generator:
                data, _ = await process_filter_functions(
                    request=request,
                    filter_functions=filter_functions,
                    filter_type="stream",
                    form_data=data,
                    extra_params=extra_params,
                )

                if data:
                    yield data

        return StreamingResponse(
            stream_wrapper(response.body_iterator, events),
            headers=dict(response.headers),
            background=response.background,
        )


async def process_chat_response(response, ctx):
    # Non-streaming response
    if not isinstance(response, StreamingResponse):
        return await non_streaming_chat_response_handler(response, ctx)

    # Non standard response
    if not any(
        content_type in response.headers["Content-Type"]
        for content_type in ["text/event-stream", "application/x-ndjson"]
    ):
        return response

    # Streaming response
    return await streaming_chat_response_handler(response, ctx)
