from __future__ import annotations

import time
from typing import Any

from open_webui.extensions.simon_engine.types import RetrievalProbe, SimonRuntimeContext
from open_webui.models.chats import Chats
from open_webui.models.simon_lex_index import flatten_content, recursive_search
from open_webui.models.users import UserModel
from open_webui.routers.memories import QueryMemoryForm, query_memory

_DEFAULT_MEMORY_K = 3
_DEFAULT_LEXICAL_LIMIT = 6
_BRANCH_SCOPE_LIMIT = 500


def _result_get(obj: Any, key: str, default: Any):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_first_list(value: Any) -> list[Any]:
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, list):
            return first
        return value
    return []


def _distance_to_score(distance: Any) -> float:
    try:
        distance_value = float(distance)
    except Exception:
        return 0.0

    if distance_value < 0:
        distance_value = 0.0

    return 1.0 / (1.0 + distance_value)


def _format_date(timestamp_value: Any) -> str:
    try:
        ts = int(timestamp_value)
        if ts > 10_000_000_000:
            ts = int(ts / 1000)
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except Exception:
        return "unknown"


async def query_vector_memories(
    *,
    request,
    user_model: UserModel | None,
    query_text: str,
    k: int = _DEFAULT_MEMORY_K,
) -> tuple[list[str], list[float], list[dict[str, Any]]]:
    if not request or not user_model:
        return [], [], []

    query_text = (query_text or "").strip()
    if not query_text:
        return [], [], []

    try:
        result = await query_memory(
            request,
            QueryMemoryForm(content=query_text, k=max(1, int(k))),
            user_model,
        )
    except Exception:
        return [], [], []

    documents = _as_first_list(_result_get(result, "documents", []))
    distances = _as_first_list(_result_get(result, "distances", []))
    metadatas = _as_first_list(_result_get(result, "metadatas", []))

    lines: list[str] = []
    scores: list[float] = []
    payloads: list[dict[str, Any]] = []

    for idx, text_value in enumerate(documents):
        if text_value is None:
            continue

        content = str(text_value).strip()
        if not content:
            continue

        distance = distances[idx] if idx < len(distances) else None
        score = _distance_to_score(distance)
        scores.append(score)

        metadata = metadatas[idx] if idx < len(metadatas) else {}
        created_at = _format_date(
            metadata.get("created_at") if isinstance(metadata, dict) else None
        )

        line = f"[memory:{created_at}] {content}"
        lines.append(line)
        payloads.append(
            {
                "source": "memory",
                "content": content,
                "score": round(score, 4),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )

    return lines, scores, payloads


def rehydrate_lexical_candidates(
    *,
    runtime: SimonRuntimeContext,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    hydrated: list[dict[str, Any]] = []
    lines: list[str] = []

    seen_ids: set[str] = set()
    seen_lines: set[str] = set()

    for candidate in candidates:
        message_id = str(candidate.get("message_id") or "").strip()
        if not message_id or message_id in seen_ids:
            continue

        seen_ids.add(message_id)

        canonical = runtime.messages_map.get(message_id)
        if not canonical and runtime.chat_id:
            canonical = Chats.get_message_by_id_and_message_id(runtime.chat_id, message_id)

        if not canonical:
            continue

        role = str(canonical.get("role") or candidate.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system"}:
            continue

        content = flatten_content(canonical.get("content"))
        if not content:
            continue

        score = candidate.get("score")
        try:
            normalized_score = float(score) if score is not None else None
        except Exception:
            normalized_score = None

        payload = {
            "source": "lexical",
            "message_ref": message_id,
            "role": role,
            "content": content,
            "score": normalized_score,
        }
        hydrated.append(payload)

        score_suffix = ""
        if normalized_score is not None:
            score_suffix = f" score={normalized_score:.3f}"

        line = f"[lexical:{message_id}:{role}{score_suffix}] {content}"
        if line in seen_lines:
            continue

        seen_lines.add(line)
        lines.append(line)

    return hydrated, lines


async def query_lexical_anchors(
    *,
    runtime: SimonRuntimeContext,
    query_text: str,
    deep: bool,
    limit: int = _DEFAULT_LEXICAL_LIMIT,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not runtime.chat_id or runtime.chat_id.startswith("local:"):
        return [], []

    query_text = (query_text or "").strip()
    if not query_text:
        return [], []

    search_depth = 3 if deep else 2
    max_queries = 24 if deep else 12
    max_branches = 6 if deep else 4
    result_limit = max(limit, 10 if deep else 6)

    branch_scope = runtime.branch_message_ids[-_BRANCH_SCOPE_LIMIT:]

    candidates = recursive_search(
        runtime.chat_id,
        query_text,
        branch_message_ids=branch_scope,
        limit=result_limit,
        depth=search_depth,
        max_queries=max_queries,
        max_branches=max_branches,
    )

    hydrated, lines = rehydrate_lexical_candidates(runtime=runtime, candidates=candidates)
    return hydrated, lines


async def probe_retrieval(
    *,
    request,
    user_model: UserModel | None,
    runtime: SimonRuntimeContext,
    query_text: str,
    deep: bool,
    memory_k: int = _DEFAULT_MEMORY_K,
    lexical_limit: int = _DEFAULT_LEXICAL_LIMIT,
) -> RetrievalProbe:
    memory_lines, vector_scores, memory_payloads = await query_vector_memories(
        request=request,
        user_model=user_model,
        query_text=query_text,
        k=memory_k,
    )

    lexical_hits, lexical_lines = await query_lexical_anchors(
        runtime=runtime,
        query_text=query_text,
        deep=deep,
        limit=lexical_limit,
    )

    merged_lines: list[str] = []
    seen_lines: set[str] = set()

    for line in [*lexical_lines, *memory_lines]:
        normalized = line.strip()
        if not normalized or normalized in seen_lines:
            continue
        seen_lines.add(normalized)
        merged_lines.append(normalized)

    return RetrievalProbe(
        vector_memories=memory_lines,
        vector_scores=vector_scores,
        lexical_hits=lexical_hits,
        lexical_lines=merged_lines,
        metrics={
            "memory_hits": len(memory_payloads),
            "lexical_hits": len(lexical_hits),
            "source_mix": {
                "memory": len(memory_payloads),
                "lexical": len(lexical_hits),
            },
        },
    )
