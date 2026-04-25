import copy
import time
import logging
import sys
import os
import base64
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


from fastapi import Request, HTTPException
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
    convert_output_to_history_messages,
    sanitize_historical_message_for_llm,
)
from open_webui.utils.tools import (
    get_tools,
    get_updated_tool_function,
    get_terminal_tools,
)
from open_webui.utils.access_control import has_connection_access
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
from open_webui.utils.travel_orchestration import (
    maybe_run_travel_orchestration,
    should_activate_travel_orchestration,
)
from open_webui.utils.science_orchestration import maybe_run_science_orchestration
from open_webui.retrieval.corpus_runtime import resolve_corpus_runtime
from open_webui.retrieval.medical_lane import assess_medical_corpus_sufficiency
from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode
from open_webui.retrieval.working_mode import normalize_working_mode
from open_webui.utils.lane_runtime import (
    normalize_science_research_mode,
)
from open_webui.utils.science_lane import build_science_lane_skill_sets


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
MEDICAL_CONSULT_SYSTEM_PROMPT = (
    "This chat is in Medical mode. Start with medical_corpus_sufficiency before committing to a local-corpus-only answer. "
    "Treat its decision object as the routing contract for this turn: use_corpus_only means stay local; use_corpus_plus_web "
    "means use the local corpus as anchors and then enrich with web evidence; skip_corpus means do not force the local medical "
    "corpus. Do not invent certainty beyond the decision object's evidence basis, and say plainly when the local corpus is stale, "
    "thin, or only partially on-topic."
)
GENERAL_SCIENCE_SYSTEM_PROMPT = (
    "This chat is in General Science mode. Prefer attached local corpora first when they are explicitly attached and compatible. "
    "Then prefer source-native scholarly tools over generic web search. Treat bibliographic discovery, abstract-grounded reasoning, "
    "and full-text-grounded reasoning as distinct evidence modes. Do not treat metadata-only records as substantive proof. Use "
    "generic web search only as a fallback for discovery gaps, inaccessible records, or recency checks."
)
OFFSEC_CONSULT_SYSTEM_PROMPT = (
    "This chat is in Offsec mode. Use the Offsec corpus as a sparing consult layer for methodology, "
    "tool choice, examples, and tactical recall. When methodology, tool choice, or target framing is "
    "unclear, call offsec_consult before committing to a path. Re-consult sparingly when the target "
    "picture materially changes during live work. Do not use generic knowledge-base, notes, or prior-chat "
    "tools unless the user explicitly asks for prior work, saved notes, or a specific prior artifact. "
    "When exact syntax, flags, or version-specific behavior becomes the blocker, prefer official or "
    "project/GitHub docs before broad web search."
)
NEWS_CONSULT_SYSTEM_PROMPT = (
    "This chat is in News mode. Use the local News lane first. Start with news_consult to orient around "
    "the relevant local stories. If the user asks for the morning briefing, today's briefing, or everything "
    "important from today, and news_consult returns route=latest_briefing or route=build_from_snapshot, "
    "answer directly from that compiled briefing payload instead of asking follow-up scoping questions. "
    "If news_consult returns route=empty_state, say plainly that no closed local news snapshot exists yet "
    "and do not pivot into broad web search as a substitute. Default broad briefing requests to the full "
    "briefing with paragraph-level detail unless the user explicitly asks for a shorter version. For broad "
    "briefing asks, stay close to the selected item paragraphs, cover every matched story exactly once, and "
    "prefer one block per story rather than compressing multiple items into short thematic bullets. Do not "
    "silently merge or omit items unless they refer to the same story instance. Otherwise retrieve article-grounded "
    "evidence with news_retrieve_articles and use "
    "news_retrieve_timeline only when continuity matters. Treat source text as canonical. Do not silently "
    "reconcile disagreements between sources. If details conflict, attribute them or state that the reporting "
    "diverges."
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
    "news_consult": "orientation",
    "news_retrieve_articles": "evidence_gathering",
    "news_retrieve_timeline": "evidence_gathering",
    "news_view_articles": "evidence_gathering",
    "offsec_consult": "orientation",
    "offsec_retrieve_evidence": "evidence_gathering",
    "medical_corpus_sufficiency": "orientation",
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
    "scholarly_search_pubmed": "evidence_gathering",
    "scholarly_search_openalex": "evidence_gathering",
    "scholarly_search_crossref": "evidence_gathering",
    "scholarly_search_europe_pmc": "evidence_gathering",
    "scholarly_resolve_doi": "evidence_gathering",
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

DEFAULT_SELECTOR_MEDICAL_GUIDANCE = (
    "In Medical mode, start with medical_corpus_sufficiency before deciding whether the local corpus is enough. "
    "Treat use_corpus_only, use_corpus_plus_web, and skip_corpus as a routing contract, not a soft suggestion."
)
DEFAULT_SELECTOR_GENERAL_SCIENCE_GUIDANCE = (
    "In General Science mode, prefer attached local corpora only when they are explicitly attached and compatible. "
    "Then prefer source-native scholarly tools. Use generic web search only as a fallback for discovery gaps, recency, "
    "or inaccessible records."
)
DEFAULT_SELECTOR_OFFSEC_GUIDANCE = (
    "In Offsec mode, when methodology, tool choice, or target framing is unclear, "
    "start with offsec_consult. Re-consult sparingly when the target picture materially "
    "changes during live work. Do not use generic knowledge-base, notes, or prior-chat "
    "tools unless the user explicitly asks for prior work, saved notes, or a specific prior artifact. "
    "When exact syntax, flags, or version-specific behavior becomes the blocker, prefer official or "
    "project/GitHub docs before broad web search."
)
DEFAULT_SELECTOR_OFFSEC_TERMINAL_GUIDANCE = (
    "When terminal tools are available, keep the terminal as the primary execution lane "
    "and use the Offsec corpus as a sparing consult layer."
)
DEFAULT_SELECTOR_NEWS_GUIDANCE = (
    "In News mode, start with news_consult to orient around the relevant local stories. "
    "Prefer article-grounded retrieval with news_retrieve_articles before broader timeline "
    "summaries. Treat source text as canonical, and do not flatten source disagreements into "
    "a single confident claim."
)

DEFAULT_SELECTOR_PRIOR_WORK_FALLBACK_GUIDANCE = (
    "When primary evidence lanes are unavailable, before answering from model knowledge "
    "alone, check user-owned prior work when it is likely to contain relevant leads. "
    "Prefer prior-work sources in this order: knowledge files, notes, prior chats. "
    "Treat them as prior work or leads, not automatically authoritative evidence."
)

DEFAULT_SELECTOR_RETRIEVAL_TOOL_NAMES = {
    "news_consult",
    "news_retrieve_articles",
    "news_retrieve_timeline",
    "offsec_consult",
    "offsec_retrieve_evidence",
    "medical_corpus_sufficiency",
    "local_corpus_frame_problem",
    "local_corpus_plan_axes",
    "local_corpus_collect_axis_evidence",
    "local_corpus_assess_evidence",
    "local_corpus_shortlist_books",
    "local_corpus_retrieve_evidence",
    "scholarly_search_pubmed",
    "scholarly_search_openalex",
    "scholarly_search_crossref",
    "scholarly_search_europe_pmc",
    "scholarly_resolve_doi",
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

DEFAULT_SELECTOR_OFFSEC_TOOL_NAMES = {
    "offsec_consult",
    "offsec_retrieve_evidence",
}

DEFAULT_SELECTOR_NEWS_TOOL_NAMES = {
    "news_consult",
    "news_retrieve_articles",
    "news_retrieve_timeline",
    "news_view_articles",
}

DEFAULT_SELECTOR_MEDICAL_TOOL_NAMES = {
    "medical_corpus_sufficiency",
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

DEFAULT_SELECTOR_SCHOLARLY_TOOL_NAMES = {
    "scholarly_search_pubmed",
    "scholarly_search_openalex",
    "scholarly_search_crossref",
    "scholarly_search_europe_pmc",
    "scholarly_resolve_doi",
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
DEFAULT_SELECTOR_TERMINAL_TOOL_NAMES = {"run_command", "get_process_status"}
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


def _normalized_working_mode(params: Optional[dict[str, Any]]) -> str:
    normalized_params = params or {}
    return normalize_working_mode(
        normalized_params.get("working_mode"),
        local_corpus_mode=normalized_params.get("local_corpus_mode"),
    )


def _tool_names_from_selector_tools(tools: dict[str, Any]) -> set[str]:
    return {str(name) for name in (tools or {}).keys()}


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

    for tool_call in message.get("tool_calls", []) or []:
        function = tool_call.get("function", {}) or {}
        if function.get("name") in DEFAULT_SELECTOR_PRIOR_WORK_TOOL_NAMES:
            return True
    return False


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
    working_mode = _normalized_working_mode(params)

    clauses: list[str] = []

    if _selector_has_any_tool(tools, DEFAULT_SELECTOR_RETRIEVAL_TOOL_NAMES):
        clauses.append(DEFAULT_SELECTOR_TERM_PRESERVATION_GUIDANCE)

    if (
        local_corpus_mode == "prefer"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_LOCAL_CORPUS_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_LOCAL_CORPUS_PREFER_GUIDANCE)

    if (
        working_mode == "medical"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_MEDICAL_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_MEDICAL_GUIDANCE)
    elif (
        working_mode == "general_science"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_SCHOLARLY_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_GENERAL_SCIENCE_GUIDANCE)
    elif (
        working_mode == "offsec"
        and local_corpus_mode != "off"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_OFFSEC_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_OFFSEC_GUIDANCE)
        if _selector_has_any_tool(tools, DEFAULT_SELECTOR_TERMINAL_TOOL_NAMES):
            clauses.append(DEFAULT_SELECTOR_OFFSEC_TERMINAL_GUIDANCE)
    elif (
        working_mode == "news"
        and _selector_has_any_tool(tools, DEFAULT_SELECTOR_NEWS_TOOL_NAMES)
    ):
        clauses.append(DEFAULT_SELECTOR_NEWS_GUIDANCE)

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
    metadata: dict, tools: dict[str, Any], messages: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    params = metadata.get("params", {}) or {}
    if params.get("function_calling") != "default":
        return None

    if _normalized_working_mode(params) == "medical":
        if "medical_corpus_sufficiency" not in _tool_names_from_selector_tools(tools):
            return None
        return {
            "name": "medical_corpus_sufficiency",
            "parameters": {"query": get_last_user_message(messages or []) or ""},
        }

    return None
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
    ENABLE_RESPONSES_API_STATEFUL,
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
    working_mode = _normalized_working_mode(params)
    corpus_runtime = resolve_corpus_runtime(request.app.state.config, params)
    medical_enabled = working_mode == "medical" and corpus_runtime.medical_enabled
    general_local_corpus_enabled = (
        working_mode == "general"
        and local_corpus_mode == "prefer"
        and corpus_runtime.medical_enabled
    )
    general_science_enabled = (
        working_mode == "general_science"
        and (
            corpus_runtime.general_science_enabled
            or bool((params.get("science_attached_corpora") or []))
        )
    )
    offsec_enabled = (
        working_mode == "offsec"
        and local_corpus_mode != "off"
        and corpus_runtime.offsec_enabled
    )
    news_enabled = working_mode == "news" and corpus_runtime.news_enabled
    focused_search_enabled = bool(features.get("focused_search"))
    return bool(
        medical_enabled
        or general_local_corpus_enabled
        or general_science_enabled
        or offsec_enabled
        or news_enabled
        or focused_search_enabled
    )


def _initialize_tool_narration_state(
    request: Request, metadata: dict, features: dict
) -> dict[str, Any]:
    params = metadata.get("params", {}) or {}
    working_mode = _normalized_working_mode(params)
    corpus_runtime = resolve_corpus_runtime(request.app.state.config, params)
    medical_mode = working_mode == "medical" and corpus_runtime.medical_enabled
    general_local_corpus_mode = (
        working_mode == "general"
        and normalize_local_corpus_mode(params.get("local_corpus_mode")) == "prefer"
        and corpus_runtime.medical_enabled
    )
    general_science_mode = working_mode == "general_science"
    offsec_mode = (
        working_mode == "offsec"
        and normalize_local_corpus_mode(params.get("local_corpus_mode")) != "off"
        and corpus_runtime.offsec_enabled
    )
    news_mode = working_mode == "news" and corpus_runtime.news_enabled
    focused_search_enabled = bool(features.get("focused_search"))
    return {
        "enabled": _should_enable_shared_tool_narration(request, metadata, features),
        "last_narrated_phase": (
            "orientation"
            if (
                medical_mode
                or general_local_corpus_mode
                or general_science_mode
                or offsec_mode
                or news_mode
            )
            else None
        ),
        "current_major_phase": None,
        "narration_count": (
            1
            if (
                medical_mode
                or general_local_corpus_mode
                or general_science_mode
                or offsec_mode
                or news_mode
            )
            else 0
        ),
        "max_beats": TOOL_NARRATION_MAX_BEATS,
        "initial_preamble_expected": bool(
            medical_mode
            or general_local_corpus_mode
            or general_science_mode
            or offsec_mode
            or news_mode
            or focused_search_enabled
        ),
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
    request: Optional[Request] = None,
    metadata: Optional[dict[str, Any]] = None,
    latest_tool_call_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    continuation_output = _compact_same_turn_tool_outputs_for_continuation(
        request,
        metadata,
        output,
        latest_tool_call_ids=latest_tool_call_ids,
    )
    messages = [
        *copy.deepcopy(form_data_messages),
        *convert_output_to_messages(continuation_output, raw=True),
    ]
    if narration_instruction:
        messages = add_or_update_system_message(
            narration_instruction,
            messages,
            append=True,
        )
    return messages


DEFAULT_SOLUTION_TAGS = [("<|begin_of_solution|>", "<|end_of_solution|>")]
DEFAULT_CODE_INTERPRETER_TAGS = [("<code_interpreter>", "</code_interpreter>")]

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
_SAME_TURN_TOOL_OUTPUT_COMPACTION_PREFIX = (
    "[same-turn tool output compacted for context budget]"
)


def _transliterate_cyrillic_to_latin(text: str) -> str:
    return "".join(_BG_CYRILLIC_TO_LATIN.get(char, char) for char in str(text or ""))


def _resolve_persona_capability_from_metadata(
    metadata: Optional[dict[str, Any]], capability_name: str
) -> Optional[bool]:
    if not isinstance(metadata, dict) or not capability_name:
        return None

    capability_sources = (
        metadata.get("persona_effective_capabilities"),
        (metadata.get("persona_requested_defaults") or {}).get("capabilities"),
        (metadata.get("persona_snapshot") or {}).get("capabilities"),
    )

    for source in capability_sources:
        if isinstance(source, dict) and capability_name in source:
            return bool(source.get(capability_name))

    return None


def _should_compact_same_turn_tool_outputs(
    request: Optional[Request], metadata: Optional[dict[str, Any]]
) -> bool:
    if request is None:
        return False

    config = getattr(getattr(request, "app", None), "state", None)
    config = getattr(config, "config", None)
    if not bool(
        getattr(config, "ENABLE_SAME_TURN_TOOL_OUTPUT_COMPACTION", False)
    ):
        return False

    capability_enabled = _resolve_persona_capability_from_metadata(
        metadata, "same_turn_tool_output_compaction"
    )
    return bool(capability_enabled)


def _extract_function_call_output_text(item: dict[str, Any]) -> str:
    text_chunks: list[str] = []
    for part in item.get("output") or []:
        if isinstance(part, dict) and part.get("type") == "input_text":
            text_chunks.append(str(part.get("text") or ""))
    return "".join(text_chunks)


def _build_same_turn_tool_output_compaction_text(
    *,
    call_id: str,
    tool_name: str,
    original_text: str,
) -> str:
    summary = _tool_result_summary(tool_name, original_text)
    try:
        summary_text = json.dumps(summary, ensure_ascii=False, sort_keys=True)
    except Exception:
        summary_text = "{}"

    return "\n".join(
        [
            _SAME_TURN_TOOL_OUTPUT_COMPACTION_PREFIX,
            f"tool: {tool_name or 'unknown'}",
            f"call_id: {call_id or 'unknown'}",
            f"original_chars: {len(original_text)}",
            f"summary: {summary_text}",
        ]
    )


def _record_same_turn_tool_output_compaction_telemetry(
    metadata: Optional[dict[str, Any]],
    *,
    compacted_call_count: int,
    kept_raw_call_count: int,
    raw_chars: int,
    compacted_chars: int,
    compacted_by_tool: dict[str, int],
) -> None:
    if not isinstance(metadata, dict) or compacted_call_count <= 0:
        return

    telemetry = metadata.setdefault(
        "same_turn_tool_output_compaction",
        {
            "continuation_calls": 0,
            "compacted_call_count": 0,
            "kept_raw_call_count": 0,
            "raw_chars": 0,
            "compacted_chars": 0,
            "saved_chars": 0,
            "compacted_by_tool": {},
        },
    )
    telemetry["continuation_calls"] = int(telemetry.get("continuation_calls", 0)) + 1
    telemetry["compacted_call_count"] = int(
        telemetry.get("compacted_call_count", 0)
    ) + int(compacted_call_count)
    telemetry["kept_raw_call_count"] = int(
        telemetry.get("kept_raw_call_count", 0)
    ) + int(kept_raw_call_count)
    telemetry["raw_chars"] = int(telemetry.get("raw_chars", 0)) + int(raw_chars)
    telemetry["compacted_chars"] = int(
        telemetry.get("compacted_chars", 0)
    ) + int(compacted_chars)
    telemetry["saved_chars"] = int(telemetry.get("saved_chars", 0)) + max(
        0, int(raw_chars) - int(compacted_chars)
    )

    compacted_by_tool_total = telemetry.setdefault("compacted_by_tool", {})
    for tool_name, count in compacted_by_tool.items():
        compacted_by_tool_total[tool_name] = int(
            compacted_by_tool_total.get(tool_name, 0)
        ) + int(count)


def _compact_same_turn_tool_outputs_for_continuation(
    request: Optional[Request],
    metadata: Optional[dict[str, Any]],
    output: list[dict[str, Any]],
    *,
    latest_tool_call_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    if not _should_compact_same_turn_tool_outputs(request, metadata):
        return output
    if not isinstance(output, list) or not output:
        return output

    latest_ids = {
        str(call_id)
        for call_id in (latest_tool_call_ids or [])
        if isinstance(call_id, str) and call_id
    }
    call_names = {
        str(item.get("call_id") or ""): str(item.get("name") or "")
        for item in output
        if isinstance(item, dict) and item.get("type") == "function_call"
    }

    compacted_output: list[dict[str, Any]] = []
    compacted_call_count = 0
    kept_raw_call_count = 0
    raw_chars = 0
    compacted_chars = 0
    compacted_by_tool: dict[str, int] = {}

    for item in output:
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            compacted_output.append(copy.deepcopy(item))
            continue

        call_id = str(item.get("call_id") or "")
        if call_id and call_id in latest_ids:
            kept_raw_call_count += 1
            compacted_output.append(copy.deepcopy(item))
            continue

        original_text = _extract_function_call_output_text(item)
        if not original_text:
            compacted_output.append(copy.deepcopy(item))
            continue

        tool_name = call_names.get(call_id, "")
        compacted_text = _build_same_turn_tool_output_compaction_text(
            call_id=call_id,
            tool_name=tool_name,
            original_text=original_text,
        )

        compacted_item = copy.deepcopy(item)
        compacted_item["output"] = [{"type": "input_text", "text": compacted_text}]
        compacted_output.append(compacted_item)

        compacted_call_count += 1
        raw_chars += len(original_text)
        compacted_chars += len(compacted_text)
        compacted_by_tool[tool_name or "unknown"] = (
            compacted_by_tool.get(tool_name or "unknown", 0) + 1
        )

    _record_same_turn_tool_output_compaction_telemetry(
        metadata,
        compacted_call_count=compacted_call_count,
        kept_raw_call_count=kept_raw_call_count,
        raw_chars=raw_chars,
        compacted_chars=compacted_chars,
        compacted_by_tool=compacted_by_tool,
    )

    return compacted_output


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


def _coerce_token_branch_text(value) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return str(value)


def _needs_token_branch_display_spacer(prefix: str, continuation: str) -> bool:
    if not prefix or not continuation:
        return False
    if prefix[-1].isspace() or continuation[0].isspace():
        return False
    return prefix[-1].isalnum() and continuation[0].isalnum()


def _join_token_branch_display_prefix(prefix, continuation) -> str:
    prefix_text = _coerce_token_branch_text(prefix)
    continuation_text = _coerce_token_branch_text(continuation)
    if not prefix_text or continuation_text.startswith(prefix_text):
        return continuation_text

    spacer = (
        " "
        if _needs_token_branch_display_spacer(prefix_text, continuation_text)
        else ""
    )
    return f"{prefix_text}{spacer}{continuation_text}"


def _get_token_branch_display_prefix(source_message: dict) -> str:
    prefix = source_message.get("tokenBranchDisplayPrefix")
    if isinstance(prefix, str):
        return prefix

    token_branch = source_message.get("tokenBranch")
    if isinstance(token_branch, dict):
        prefix = token_branch.get("displayPrefix")
        if isinstance(prefix, str):
            return prefix

    return ""


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

    forced_suffix = "".join(
        (
            token.get("text", "")
            if isinstance(token.get("text", ""), str)
            else str(token.get("text", ""))
        )
        for token in tokens[:fork_index]
    )
    forced_suffix = f"{forced_suffix}{chosen_token_text}"
    forced_prefix = _join_token_branch_display_prefix(
        _get_token_branch_display_prefix(source_message),
        forced_suffix,
    )

    token_branch = {
        "version": TOKEN_BRANCH_VERSION,
        "sourceMessageId": source_message_id,
        "forkIndex": fork_index,
        "chosenAltRank": alt_rank,
        "chosenTokenText": chosen_token_text,
        "chosenTokenId": _safe_int(chosen_alt.get("tokenId", chosen_alt.get("token_id"))),
        "forcingStrategy": TOKEN_BRANCH_FORCING_STRATEGY,
        "displayPrefix": forced_prefix,
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
        "news_consult",
        "news_retrieve_articles",
        "news_retrieve_timeline",
        "news_view_articles",
        "search_strong_sources",
        "web_research_strong",
        "query_web_evidence",
        "offsec_consult",
        "offsec_retrieve_evidence",
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
        elif tool_name in {"search_strong_sources", "web_research_strong"}:
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
                title = snippet.get("title", "") or snippet.get("url", "") or "web evidence"
                url = snippet.get("url", "")
                text = snippet.get("text", "")
                documents.append(f"{title}\n{text}".strip())
                metadata.append(
                    {
                        "source": url or "query_web_evidence",
                        "name": title,
                        "url": url,
                        "artifact_id": snippet.get("artifact_id", ""),
                        "domain": snippet.get("domain", ""),
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

        elif tool_name in {"offsec_consult", "news_consult", "news_retrieve_timeline"}:
            payload = tool_result if isinstance(tool_result, dict) else {}
            source_documents = (
                payload.get("source_documents", []) if isinstance(payload, dict) else []
            )
            sources = []
            for item in source_documents:
                if not isinstance(item, dict):
                    continue
                sources.append(
                    {
                        "source": {
                            "id": item.get("id", ""),
                            "name": item.get("name", "offsec consult"),
                            "type": item.get("type", "offsec_selection"),
                        },
                        "document": [item.get("content", "")],
                        "metadata": [
                            {
                                "source": item.get("source_path", item.get("name", "")),
                                "name": item.get("name", tool_name),
                                "book_id": item.get("book_id", ""),
                                "domain": item.get("domain", ""),
                                "page_no": item.get("page_no"),
                                "section_path": item.get("section_path", ""),
                                "article_ids": item.get("article_ids", []),
                                "category_ids": item.get("category_ids", []),
                            }
                        ],
                    }
                )
            return sources

        elif tool_name == "offsec_retrieve_evidence":
            payload = tool_result if isinstance(tool_result, dict) else {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            grouped_sources = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title", "") or "offsec corpus"
                book_id = item.get("book_id", "")
                key = book_id or title
                if key not in grouped_sources:
                    grouped_sources[key] = {
                        "source": {
                            "id": book_id,
                            "name": title,
                            "type": "offsec_book",
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

        elif tool_name == "news_retrieve_articles":
            payload = tool_result if isinstance(tool_result, dict) else {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            documents = []
            metadata = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                title = item.get("title", "") or "news evidence"
                preview = item.get("preview", "")
                documents.append(f"{title}\n{preview}".strip())
                metadata.append(
                    {
                        "source": item.get("story_candidate_id", title),
                        "name": title,
                        "story_candidate_id": item.get("story_candidate_id", ""),
                        "cluster_id": item.get("cluster_id", ""),
                        "article_ids": item.get("article_ids", []),
                        "category_ids": item.get("category_ids", []),
                    }
                )
            return [
                {
                    "source": {
                        "name": "news_retrieve_articles",
                        "id": "news_retrieve_articles",
                    },
                    "document": documents,
                    "metadata": metadata,
                }
            ]

        elif tool_name == "news_view_articles":
            payload = tool_result if isinstance(tool_result, dict) else {}
            items = payload.get("items", []) if isinstance(payload, dict) else []
            sources = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                sources.append(
                    {
                        "source": {
                            "id": item.get("article_id", ""),
                            "name": item.get("title", "news article"),
                            "type": "news_article",
                        },
                        "document": [item.get("raw_text_md", "")],
                        "metadata": [
                            {
                                "source": item.get("url", ""),
                                "name": item.get("title", "news article"),
                                "article_id": item.get("article_id", ""),
                                "source_id": item.get("source_id", ""),
                                "published_at": item.get("published_at", ""),
                            }
                        ],
                    }
                )
            return sources

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
STRONG_WEB_TOOL_NAMES = {"web_research_strong", "search_strong_sources"}
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


def _tool_result_summary(tool_name: str, tool_result: Any) -> dict[str, Any]:
    parsed: Any = tool_result
    if isinstance(tool_result, str):
        try:
            parsed = json.loads(tool_result)
        except Exception:
            parsed = tool_result

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

    if tool_name == "offsec_consult" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "route": parsed.get("route"),
            "matched_id": parsed.get("matched_id"),
            "recommended_book_ids": len(parsed.get("recommended_book_ids") or []),
            "docs_fallback_suggested": bool(parsed.get("docs_fallback_suggested", False)),
        }

    if tool_name == "offsec_retrieve_evidence" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "next_action": parsed.get("next_action"),
            "book_ids": len(parsed.get("book_ids") or []),
            "items": len(parsed.get("items") or []),
            "candidate_count": parsed.get("candidate_count"),
        }

    if tool_name == "news_consult" and isinstance(parsed, dict):
        return {
            "phase": parsed.get("phase"),
            "route": parsed.get("route"),
            "snapshot_id": parsed.get("snapshot_id"),
            "matched_stories": len(parsed.get("matched_stories") or []),
            "selected_item_count": parsed.get("selected_item_count"),
        }

    if tool_name == "news_retrieve_articles" and isinstance(parsed, dict):
        return {
            "snapshot_id": parsed.get("snapshot_id"),
            "items": len(parsed.get("items") or []),
        }

    if tool_name == "news_retrieve_timeline" and isinstance(parsed, dict):
        return {
            "snapshot_id": parsed.get("snapshot_id"),
            "items": len(parsed.get("items") or []),
        }

    if tool_name == "news_view_articles" and isinstance(parsed, dict):
        return {
            "items": len(parsed.get("items") or []),
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
        return {"items": len(parsed)}

    if tool_name == "fetch_url":
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


def _build_agent_loop_termination_cause(
    *,
    kind: str,
    phase: str,
    exc: Exception | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": str(kind or ""),
        "phase": str(phase or ""),
    }
    if exc is None:
        return payload

    payload["exception_type"] = type(exc).__name__
    if isinstance(exc, HTTPException):
        payload["status_code"] = exc.status_code
        detail = exc.detail
        if isinstance(detail, dict):
            payload["error"] = detail.get("message") or str(detail)
        else:
            payload["error"] = str(detail)
    else:
        payload["error"] = str(exc)
    return payload


def _record_skill_usage_telemetry(metadata: dict[str, Any], payload: dict[str, Any]) -> None:
    if not runtime_telemetry.is_enabled():
        return

    runtime_telemetry.record(
        kind="skill_usage",
        payload=payload,
        chat_id=metadata.get("chat_id"),
        message_id=metadata.get("message_id"),
        user_id=metadata.get("user_id"),
        model_id=metadata.get("model_id"),
    )


def _record_medical_lane_telemetry(metadata: dict[str, Any], payload: dict[str, Any]) -> None:
    if not runtime_telemetry.is_enabled():
        return

    runtime_telemetry.record(
        kind="medical_lane",
        payload=payload,
        chat_id=metadata.get("chat_id"),
        message_id=metadata.get("message_id"),
        user_id=metadata.get("user_id"),
        model_id=metadata.get("model_id"),
    )


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


def _build_search_notes_loop_breaker_result(
    streak: int,
    blocked: bool = False,
    tool_name: str = "notes_lookup",
    next_tool: str | None = "web_research_strong",
) -> str:
    payload = {
        "status": "loop_breaker_active" if blocked else "loop_breaker_triggered",
        "tool": tool_name,
        "empty_streak": streak,
        "message": (
            f"{tool_name} returned no matches multiple times. "
            "This tool searches user notes only, not the web."
        ),
        "hint": (
            "Stop using notes tools for web discovery. "
            "Use web_research_strong for focused web evidence."
        ),
        "next_tool": next_tool,
    }
    if next_tool is None:
        payload["next_action"] = "enable_internet_access"
        payload["hint"] = (
            "Internet tools are not available right now. Enable internet access "
            "before retrying web discovery."
        )
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

        return new_output, {
            "usage": response_data.get("usage"),
            "done": True,
            "response_id": response_data.get("id"),
        }

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
    result_context = None
    metadata = metadata or {}

    if (
        isinstance(tool_result, tuple)
        and len(tool_result) == 2
        and isinstance(tool_result[0], HTMLResponse)
    ):
        tool_result, result_context = tool_result

    if isinstance(tool_result, HTMLResponse):
        content_disposition = tool_result.headers.get("Content-Disposition", "")
        if "inline" in content_disposition:
            content = tool_result.body.decode("utf-8", "replace")
            tool_result_embeds.append(content)

            if 200 <= tool_result.status_code < 300:
                if result_context is not None and isinstance(
                    result_context, (str, dict, list)
                ):
                    tool_result = result_context
                else:
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
                    nested_result_context = None
                    html_content = tool_result
                    if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
                        html_content, nested_result_context = tool_result

                    tool_result_embeds.append(html_content)
                    if nested_result_context is not None and isinstance(
                        nested_result_context, (str, dict, list)
                    ):
                        tool_result = nested_result_context
                    else:
                        tool_result = {
                            "status": "success",
                            "code": "ui_component",
                            "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                        }
                elif location:
                    nested_result_context = None
                    if isinstance(tool_result, (tuple, list)) and len(tool_result) == 2:
                        _, nested_result_context = tool_result

                    tool_result_embeds.append(location)
                    if nested_result_context is not None and isinstance(
                        nested_result_context, (str, dict, list)
                    ):
                        tool_result = nested_result_context
                    else:
                        tool_result = {
                            "status": "success",
                            "code": "ui_component",
                            "message": f"{tool_function_name}: Embedded UI result is active and visible to the user.",
                        }

    tool_result_files = []

    if isinstance(tool_result, str) and tool_result.startswith("data:image/"):
        tool_result_files.append({"type": "image", "url": tool_result})
        tool_result = f"{tool_function_name}: Image file read successfully."

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
                    elif item.get("type") == "resource":
                        resource = item.get("resource") or {}
                        text = resource.get("text", "")
                        if isinstance(text, str) and text:
                            try:
                                text = json.loads(text)
                            except json.JSONDecodeError:
                                pass
                            tool_response.append(text)
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

    def get_tools_function_calling_payload(messages, task_model_id, content):
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
            "metadata": {"task": str(TASKS.FUNCTION_CALLING)},
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

    task_model_id = get_task_model_id(
        body["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

    skip_files = False
    sources = []
    forced_tool_call = _build_forced_default_selector_tool_call(
        metadata, tools, body.get("messages")
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

    tools_function_calling_prompt = tools_function_calling_generation_template(
        template, tools_specs
    )
    payload = get_tools_function_calling_payload(
        body["messages"], task_model_id, tools_function_calling_prompt
    )

    try:
        response = await generate_chat_completion(request, form_data=payload, user=user)
        log.debug(f"{response=}")
        content = await get_content_from_response(response)
        log.debug(f"{content=}")

        if not content:
            return body, {}

        try:
            content = content[content.find("{") : content.rfind("}") + 1]
            if not content:
                raise Exception("No JSON object found in the response")

            result = json.loads(content)

            # check if "tool_calls" in result
            if result.get("tool_calls"):
                for tool_call in result.get("tool_calls"):
                    await tool_call_handler(tool_call)
            else:
                await tool_call_handler(result)

        except Exception as e:
            log.debug(f"Error: {e}")
            content = None
    except Exception as e:
        log.debug(f"Error: {e}")
        content = None

    log.debug(f"tool_contexts: {sources}")

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


async def _run_active_model_web_query_generation(
    request: Request,
    *,
    user: Any,
    active_model_id: str,
    messages: list[dict],
    chat_id: Optional[str],
    timeout_ms: int,
    max_completion_tokens: int,
) -> tuple[list[str], dict[str, Any]]:
    models = _resolve_models_for_task(request)
    if active_model_id not in models:
        raise ValueError(
            f"Active model not found for query generation: {active_model_id}"
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

            return queries, {
                "model_used": active_model_id,
                "retry_count": retry_count,
                "raw_output": raw_output,
            }
        except Exception as e:
            last_error = e

    if last_error:
        raise last_error
    raise ValueError("Active model query generation failed without specific error")


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
) -> tuple[list, dict[str, Any]]:
    models = _resolve_models_for_task(request)
    if active_model_id not in models:
        raise ValueError(f"Active model not found for rewriter: {active_model_id}")

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
            return validated_queries, {
                "model_used": active_model_id,
                "fallback_used": False,
                "retry_count": retry_count,
                "raw_output": raw_output,
            }
        except Exception as e:
            last_error = e

    if last_error:
        raise last_error
    raise ValueError("Rewriter failed without specific error")


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
                    rewriter_queries, rewriter_meta = await _run_web_search_rewriter(
                        request,
                        user=user,
                        active_model_id=form_data["model"],
                        user_message=user_message,
                        conversation_context=conversation_context,
                        plan=plan,
                        max_queries=max(
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
                        timeout_ms=max(
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
                        max_repair_attempts=max(
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
                        max_completion_tokens=max(
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
                        temperature=float(
                            getattr(
                                request.app.state.config,
                                "WEB_SEARCH_PLANNER_REWRITER_TEMPERATURE",
                                0.0,
                            )
                            or 0.0
                        ),
                        chat_id=extra_params.get("__chat_id__"),
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
            queries, _ = await _run_active_model_web_query_generation(
                request,
                user=user,
                active_model_id=form_data["model"],
                messages=messages,
                chat_id=extra_params.get("__chat_id__"),
                timeout_ms=max(
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
                max_completion_tokens=max(
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

    # Pair only user-role messages from both lists to avoid misalignment.
    # After process_messages_with_output(), assistant messages with tool calls
    # are expanded into multiple messages (assistant + tool results), making
    # the payload message list longer than the stored message list. A naive
    # positional zip() would pair user messages with wrong stored messages,
    # causing later images to lose their file context (see #21878).
    user_messages = [m for m in messages if m.get("role") == "user"]
    stored_user_messages = [
        m for m in stored_messages if m.get("role") == "user"
    ]

    for message, stored_message in zip(user_messages, stored_user_messages):
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
        "working_mode": str,
        "focused_search_mode": bool,
        "local_corpus_mode": str,
        "science_research_mode": str,
        "science_attached_corpora": list,
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
            output_messages = convert_output_to_history_messages(message["output"])
            if output_messages:
                processed.extend(output_messages)
                continue

        # Strip 'output' field before adding (LLM shouldn't see it)
        clean_message = {k: v for k, v in message.items() if k != "output"}
        processed.append(sanitize_historical_message_for_llm(clean_message))

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
    travel_orchestration_enabled = should_activate_travel_orchestration(model, metadata)
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

    if chat_recall_enabled and not (
        travel_orchestration_enabled and not raw_history_messages
    ):
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
    elif chat_recall_enabled and travel_orchestration_enabled:
        memory_telemetry["recall"] = {
            "triggered": False,
            "reason": "travel_orchestration_skip",
            "mode": None,
            "depth": 0,
            "evidence_injected": False,
            "timed_out": False,
            "hit_count": 0,
            "usable_hit_count": 0,
            "evidence_tokens": 0,
            "indexed_message_count": 0,
            "queued_message_count": 0,
            "missing_message_count": 0,
            "fallback_used": False,
            "fallback_mode": None,
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

    task_model_id = get_task_model_id(
        form_data["model"],
        request.app.state.config.TASK_MODEL,
        request.app.state.config.TASK_MODEL_EXTERNAL,
        models,
    )

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

    params = metadata.get("params", {}) or {}
    local_corpus_mode = normalize_local_corpus_mode(params.get("local_corpus_mode"))
    working_mode = _normalized_working_mode(params)
    corpus_runtime = resolve_corpus_runtime(request.app.state.config, params)
    if (
        working_mode == "medical"
        and metadata.get("params", {}).get("function_calling") == "native"
        and corpus_runtime.medical_enabled
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if MEDICAL_CONSULT_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                MEDICAL_CONSULT_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    if (
        working_mode == "general"
        and local_corpus_mode == "prefer"
        and metadata.get("params", {}).get("function_calling") == "native"
        and corpus_runtime.medical_enabled
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if LOCAL_CORPUS_PREFER_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                LOCAL_CORPUS_PREFER_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    if (
        working_mode == "general_science"
        and metadata.get("params", {}).get("function_calling") == "native"
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if GENERAL_SCIENCE_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                GENERAL_SCIENCE_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    if (
        working_mode == "offsec"
        and local_corpus_mode != "off"
        and metadata.get("params", {}).get("function_calling") == "native"
        and corpus_runtime.offsec_enabled
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if OFFSEC_CONSULT_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                OFFSEC_CONSULT_SYSTEM_PROMPT,
                form_data["messages"],
                append=True,
            )

    if (
        working_mode == "news"
        and metadata.get("params", {}).get("function_calling") == "native"
        and corpus_runtime.news_enabled
    ):
        current_system = get_system_message(form_data.get("messages", []))
        current_content = current_system.get("content", "") if current_system else ""
        if NEWS_CONSULT_SYSTEM_PROMPT not in str(current_content):
            form_data["messages"] = add_or_update_system_message(
                NEWS_CONSULT_SYSTEM_PROMPT,
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
    (
        science_lane_default_skill_ids,
        explicit_skill_ids,
        all_skill_ids,
    ) = build_science_lane_skill_sets(
        working_mode=working_mode,
        configured_skill_ids=None,
        user_skill_ids=user_skill_ids,
        model_skill_ids=model_skill_ids,
    )
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
            if skill.id in explicit_skill_ids:
                # User-selected and lane-default skills: inject full content
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
    if (
        working_mode == "medical"
        and prompt
        and corpus_runtime.medical_enabled
        and metadata.get("medical_corpus_sufficiency") is None
    ):
        try:
            medical_gate = await asyncio.to_thread(
                assess_medical_corpus_sufficiency,
                query=prompt,
                config_or_path=request.app.state.config,
            )
            if isinstance(medical_gate, dict) and medical_gate.get("status") == "ok":
                metadata["medical_corpus_sufficiency"] = medical_gate
                _record_medical_lane_telemetry(metadata, medical_gate)

                if metadata.get("params", {}).get("function_calling") == "native":
                    current_system = get_system_message(form_data.get("messages", []))
                    current_content = (
                        current_system.get("content", "") if current_system else ""
                    )
                    if "<medical_corpus_gate>" not in str(current_content):
                        form_data["messages"] = add_or_update_system_message(
                            "<medical_corpus_gate>\n"
                            + json.dumps(
                                {
                                    "decision": medical_gate.get("decision"),
                                    "fallback_reason": medical_gate.get("fallback_reason"),
                                    "relevance_score": medical_gate.get("relevance_score"),
                                    "freshness_score": medical_gate.get("freshness_score"),
                                    "topical_fit": medical_gate.get("topical_fit"),
                                    "usable_anchor_count": medical_gate.get(
                                        "usable_anchor_count"
                                    ),
                                    "contradiction_flag": medical_gate.get(
                                        "contradiction_flag"
                                    ),
                                },
                                ensure_ascii=False,
                            )
                            + "\n</medical_corpus_gate>",
                            form_data["messages"],
                            append=True,
                        )
        except Exception as exc:
            log.warning("Medical corpus sufficiency precheck failed: %s", exc)

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
        "science_lane_default_skill_ids": sorted(science_lane_default_skill_ids),
    }
    extra_params["__metadata__"] = metadata
    form_data["metadata"] = metadata

    _record_skill_usage_telemetry(
        metadata,
        {
            "operation": "inject",
            "working_mode": working_mode,
            "science_research_mode": normalize_science_research_mode(
                (metadata.get("params", {}) or {}).get("science_research_mode")
            ),
            "lane_default_skill_ids": sorted(science_lane_default_skill_ids),
            "explicit_skill_ids": sorted(explicit_skill_ids),
            "model_skill_ids": sorted(model_skill_ids),
            "available_skill_ids": [skill.id for skill in available_skills],
        },
    )

    science_orchestration_result = await maybe_run_science_orchestration(
        request=request,
        form_data=form_data,
        user=user,
        metadata=metadata,
        model=model,
        task_model=models.get(task_model_id),
        features=features,
        event_emitter=event_emitter,
    )
    if science_orchestration_result:
        system_message = get_system_message(form_data.get("messages", []))
        metadata["system_prompt"] = (
            get_content_from_message(system_message) if system_message else None
        )
        metadata["user_prompt"] = get_last_user_message(form_data.get("messages", []))
        metadata["sources"] = []
        metadata["science_orchestration_response"] = science_orchestration_result.get(
            "response"
        )
        events.extend(science_orchestration_result.get("events") or [])
        return form_data, metadata, events

    travel_orchestration_result = await maybe_run_travel_orchestration(
        request=request,
        form_data=form_data,
        user=user,
        metadata=metadata,
        model=model,
        task_model=models.get(task_model_id),
        features=features,
        event_emitter=event_emitter,
    )
    if travel_orchestration_result:
        system_message = get_system_message(form_data.get("messages", []))
        metadata["system_prompt"] = (
            get_content_from_message(system_message) if system_message else None
        )
        metadata["user_prompt"] = get_last_user_message(form_data.get("messages", []))
        metadata["sources"] = []
        metadata["travel_orchestration_response"] = travel_orchestration_result.get(
            "response"
        )
        events.extend(travel_orchestration_result.get("events") or [])
        return form_data, metadata, events

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
                        s.id for s in available_skills if s.id not in explicit_skill_ids
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
                            files=metadata.get("files") or [],
                            system_message=(
                                {
                                    "role": "system",
                                    "content": str(metadata.get("system_prompt") or "").strip(),
                                }
                                if str(metadata.get("system_prompt") or "").strip()
                                else None
                            ),
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
            prior_output = []
            last_response_id = None
            token_telemetry_state = {"tokens": [], "capped": False}
            token_branch = metadata.get("tokenBranch")
            debug_tool_journey = _is_debug_flag_enabled(
                metadata.get("params", {}).get("debug_tool_journey")
            )

            def full_output():
                return prior_output + output if prior_output else output

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
                    nonlocal prior_output
                    nonlocal last_response_id

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
                                        "output": full_output(),
                                        "content": serialize_output(full_output()),
                                    }

                                    # print(data)
                                    # print(processed_data)

                                    # Merge any metadata (usage, done, etc.)
                                    if response_metadata:
                                        if ENABLE_RESPONSES_API_STATEFUL:
                                            response_id = response_metadata.pop(
                                                "response_id", None
                                            )
                                            if response_id:
                                                last_response_id = response_id
                                        processed_data.update(response_metadata)
                                        processed_data.pop("done", None)

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
                                            await event_emitter(
                                                {
                                                    "type": "chat:completion",
                                                    "data": {
                                                        "content": serialize_output(
                                                            full_output()
                                                            + pending_fc_items
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

                                        data = {
                                            "content": serialize_output(full_output())
                                        }

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
                                                    "content": serialize_output(
                                                        full_output()
                                                    ),
                                                    "output": full_output(),
                                                },
                                            )
                                        else:
                                            data = {
                                                "content": serialize_output(
                                                    full_output()
                                                ),
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

                    # Responses API path: extract function_call items from output
                    if not response_tool_calls and output:
                        handled_call_ids = {
                            item.get("call_id")
                            for item in (prior_output + output)
                            if item.get("type") == "function_call_output"
                        }
                        responses_api_tool_calls = []
                        for item in output:
                            if (
                                item.get("type") == "function_call"
                                and item.get("status") != "completed"
                                and item.get("call_id") not in handled_call_ids
                            ):
                                arguments = item.get("arguments", "{}")
                                responses_api_tool_calls.append(
                                    {
                                        "id": item.get("call_id", ""),
                                        "index": len(responses_api_tool_calls),
                                        "function": {
                                            "name": item.get("name", ""),
                                            "arguments": (
                                                arguments
                                                if isinstance(arguments, str)
                                                else json.dumps(arguments)
                                            ),
                                        },
                                    }
                                )
                        if responses_api_tool_calls:
                            tool_calls.append(_split_tool_calls(responses_api_tool_calls))

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

                        if (
                            resolved_tool_function_name in NOTES_LOOKUP_TOOL_NAMES
                            and search_notes_guard_active
                        ):
                            search_notes_blocked = True
                            tool_result = _build_search_notes_loop_breaker_result(
                                search_notes_empty_streak,
                                blocked=True,
                                tool_name=tool_function_name,
                            )
                            blocked_event = _append_tool_journey_event(
                                metadata,
                                {
                                    "phase": "tool_loop_breaker_blocked",
                                    "call_id": tool_call_id,
                                    "tool": resolved_tool_function_name,
                                    "empty_streak": search_notes_empty_streak,
                                    "next_tool": "web_research_strong",
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
                                )
                                breaker_event = _append_tool_journey_event(
                                    metadata,
                                    {
                                        "phase": "tool_loop_breaker_triggered",
                                        "call_id": tool_call_id,
                                        "tool": resolved_tool_function_name,
                                        "empty_streak": search_notes_empty_streak,
                                        "next_tool": "web_research_strong",
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

                        # Extract citation sources from tool results
                        if (
                            citations_enabled
                            and tool_function_name
                            in [
                                "search_web",
                                "web_research_strong",
                                "search_strong_sources",
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
                        output_parts = [
                            {
                                "type": "input_text",
                                "text": result.get("content", ""),
                            }
                        ]
                        display_files = []

                        for file_item in result.get("files", []):
                            if file_item.get("type") == "image" and str(
                                file_item.get("url", "")
                            ).startswith("data:"):
                                output_parts.append(
                                    {
                                        "type": "input_image",
                                        "image_url": file_item["url"],
                                    }
                                )
                            else:
                                display_files.append(file_item)

                        output.append(
                            {
                                "type": "function_call_output",
                                "id": output_id("fco"),
                                "call_id": result.get("tool_call_id", ""),
                                "output": output_parts,
                                "status": "completed",
                                **(
                                    {"files": display_files}
                                    if display_files
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

                    frontend_output = []
                    for item in output:
                        if item.get("type") == "function_call_output":
                            parts = item.get("output", [])
                            if any(
                                part.get("type") == "input_image" for part in parts
                            ):
                                item = {
                                    **item,
                                    "output": [
                                        part
                                        for part in parts
                                        if part.get("type") != "input_image"
                                    ],
                                }
                        frontend_output.append(item)

                    await event_emitter(
                        {
                            "type": "chat:completion",
                            "data": {
                                "content": serialize_output(output),
                                "output": frontend_output,
                            },
                        }
                    )

                    try:
                        narration_instruction = _register_tool_narration_phase_transition(
                            metadata.get("tool_narration_state", {}),
                            completed_tool_phases,
                        )
                        new_form_data = {
                            **form_data,
                            "model": model_id,
                            "stream": True,
                        }

                        if ENABLE_RESPONSES_API_STATEFUL and last_response_id:
                            system_message = get_system_message(form_data["messages"])
                            new_form_data["messages"] = (
                                [system_message] if system_message else []
                            ) + convert_output_to_messages(output, raw=True)
                            new_form_data["previous_response_id"] = last_response_id
                        else:
                            continuation_messages = _build_tool_continuation_messages(
                                form_data["messages"],
                                output,
                                narration_instruction=narration_instruction,
                                request=request,
                                metadata=metadata,
                                latest_tool_call_ids=[
                                    result.get("tool_call_id", "")
                                    for result in results
                                ],
                            )

                            image_urls = []
                            for message in continuation_messages:
                                if message.get("role") == "tool" and isinstance(
                                    message.get("content"), list
                                ):
                                    text_parts = []
                                    for part in message["content"]:
                                        if part.get("type") == "input_text":
                                            text_parts.append(part.get("text", ""))
                                        elif part.get("type") == "input_image":
                                            image_urls.append(
                                                part.get("image_url", "")
                                            )
                                    message["content"] = "".join(text_parts)

                            new_form_data["messages"] = continuation_messages

                            if image_urls:
                                new_form_data["messages"].append(
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "Here are the images from the tool results above. Please analyze them.",
                                            },
                                            *[
                                                {
                                                    "type": "image_url",
                                                    "image_url": {"url": url},
                                                }
                                                for url in image_urls
                                            ],
                                        ],
                                    }
                                )

                        res = await generate_chat_completion(
                            request,
                            new_form_data,
                            user,
                            bypass_system_prompt=True,
                        )

                        if isinstance(res, StreamingResponse):
                            prior_output = list(output)
                            if (
                                prior_output
                                and prior_output[-1].get("type") == "message"
                                and prior_output[-1].get("status") == "in_progress"
                            ):
                                msg_parts = prior_output[-1].get("content", [])
                                if not msg_parts or (
                                    len(msg_parts) == 1
                                    and not msg_parts[0].get("text", "").strip()
                                ):
                                    prior_output.pop()
                            output = []
                            await stream_body_handler(res, new_form_data)
                            output[:0] = prior_output
                            prior_output = []
                        else:
                            break
                    except Exception as e:
                        log.debug(e)
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

                                            if re.match(r"data:image/\w+;base64", line):
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
                                            if re.match(r"data:image/\w+;base64", line):
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
                                "messages": _build_tool_continuation_messages(
                                    form_data["messages"],
                                    output,
                                    request=request,
                                    metadata=metadata,
                                ),
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
                                break
                        except Exception as e:
                            log.debug(e)
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
                        },
                    )
                elif (
                    usage
                    or token_telemetry
                    or token_branch
                    or memory_telemetry
                    or tool_journey_telemetry
                    or prompt_telemetry
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
                body_iterator = getattr(response, "body_iterator", None)
                aclose = getattr(body_iterator, "aclose", None)
                if callable(aclose):
                    try:
                        await asyncio.shield(aclose())
                    except Exception as e:
                        log.debug("Failed to close cancelled stream body iterator: %s", e)

                async def save_cancelled_state():
                    if event_emitter:
                        await event_emitter({"type": "chat:tasks:cancel"})

                    if not metadata.get("chat_id") or not metadata.get("message_id"):
                        return

                    update_payload = {"done": True}
                    if not ENABLE_REALTIME_CHAT_SAVE:
                        update_payload.update(
                            {
                                "content": serialize_output(output),
                                "output": output,
                            }
                        )

                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        metadata["chat_id"],
                        metadata["message_id"],
                        update_payload,
                    )

                try:
                    await asyncio.shield(save_cancelled_state())
                except Exception as e:
                    log.debug("Failed to persist cancelled chat state: %s", e)

                raise

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
