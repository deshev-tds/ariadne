from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import time
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from open_webui.internal.db import engine, get_db_context
log = logging.getLogger(__name__)

EXTRACTOR_VERSION = 1
MAX_QUEUE_ATTEMPTS = 8
DEFAULT_QUEUE_BATCH_SIZE = 20
DEFAULT_QUEUE_POLL_MS = 1200

_STOP_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
    "you",
    "your",
    "we",
    "i",
    "me",
    "my",
    "our",
    "this",
    "that",
    "these",
    "those",
    "what",
    "when",
    "where",
    "who",
    "why",
    "which",
    "how",
    "и",
    "в",
    "на",
    "за",
    "с",
    "по",
    "от",
    "какво",
    "как",
    "ти",
    "ние",
}

_WORD_RE = re.compile(r"\w+")

_WORKER_SETTINGS_LOCK = threading.Lock()
_WORKER_BATCH_SIZE = DEFAULT_QUEUE_BATCH_SIZE
_WORKER_POLL_MS = DEFAULT_QUEUE_POLL_MS


def update_worker_settings(*, batch_size: int | None = None, poll_ms: int | None = None) -> None:
    global _WORKER_BATCH_SIZE, _WORKER_POLL_MS

    with _WORKER_SETTINGS_LOCK:
        if batch_size is not None:
            try:
                _WORKER_BATCH_SIZE = max(1, int(batch_size))
            except Exception:
                pass
        if poll_ms is not None:
            try:
                _WORKER_POLL_MS = max(100, int(poll_ms))
            except Exception:
                pass


def get_worker_settings() -> tuple[int, int]:
    with _WORKER_SETTINGS_LOCK:
        return int(_WORKER_BATCH_SIZE), int(_WORKER_POLL_MS)


def is_supported_database() -> bool:
    return engine.dialect.name == "sqlite"


def ensure_schema(db: Session) -> None:
    if not is_supported_database():
        return

    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS simon_chat_lex (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                parent_id TEXT,
                role TEXT,
                content_text TEXT,
                content_hash TEXT,
                extractor_version INTEGER NOT NULL DEFAULT 1,
                created_at BIGINT,
                updated_at BIGINT,
                UNIQUE(chat_id, message_id)
            )
            """
        )
    )

    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS simon_chat_lex_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                available_at BIGINT NOT NULL,
                created_at BIGINT NOT NULL,
                updated_at BIGINT NOT NULL,
                UNIQUE(chat_id, message_id)
            )
            """
        )
    )

    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_simon_chat_lex_chat_updated
            ON simon_chat_lex(chat_id, updated_at DESC)
            """
        )
    )

    db.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS idx_simon_chat_lex_queue_available
            ON simon_chat_lex_queue(available_at, priority, updated_at)
            """
        )
    )

    db.execute(
        text(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS simon_chat_lex_fts USING fts5(
                content_text,
                message_id UNINDEXED,
                chat_id UNINDEXED,
                parent_id UNINDEXED,
                role UNINDEXED
            )
            """
        )
    )

    db.commit()


def _as_epoch_seconds(value: Any) -> int:
    try:
        ts = int(value)
    except Exception:
        return int(time.time())

    if ts > 10_000_000_000:
        ts = int(ts / 1000)
    return ts


def _normalize_whitespace(text_value: str) -> str:
    return re.sub(r"\s+", " ", text_value or "").strip()


def flatten_content(content: Any) -> str:
    if content is None:
        return ""

    if isinstance(content, str):
        return _normalize_whitespace(content)

    if isinstance(content, dict):
        text_value = content.get("text") or content.get("content") or ""
        return _normalize_whitespace(str(text_value))

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text_value = item.get("text")
                    if isinstance(text_value, str) and text_value.strip():
                        parts.append(text_value.strip())
                else:
                    text_value = item.get("text") or item.get("content")
                    if isinstance(text_value, str) and text_value.strip():
                        parts.append(text_value.strip())
        return _normalize_whitespace("\n".join(parts))

    return _normalize_whitespace(str(content))


def extract_text_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    return flatten_content(content)


def _hash_content(text_value: str) -> str:
    return hashlib.sha256((text_value or "").encode("utf-8")).hexdigest()


def enqueue_message(
    chat_id: str,
    message_id: str,
    *,
    priority: int = 0,
    db: Optional[Session] = None,
) -> None:
    if not is_supported_database():
        return

    if not chat_id or not message_id:
        return

    now = int(time.time())
    with get_db_context(db) as db:
        ensure_schema(db)
        db.execute(
            text(
                """
                INSERT INTO simon_chat_lex_queue(
                    chat_id, message_id, priority, attempts, last_error, available_at, created_at, updated_at
                ) VALUES(
                    :chat_id, :message_id, :priority, 0, NULL, :available_at, :created_at, :updated_at
                )
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    priority = MAX(simon_chat_lex_queue.priority, excluded.priority),
                    attempts = 0,
                    last_error = NULL,
                    available_at = MIN(simon_chat_lex_queue.available_at, excluded.available_at),
                    updated_at = excluded.updated_at
                """
            ),
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "priority": int(priority),
                "available_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        db.commit()


def get_indexed_message_ids(
    chat_id: str,
    message_ids: list[str],
    *,
    include_queue: bool = True,
    db: Optional[Session] = None,
) -> set[str]:
    if not is_supported_database():
        return set()

    normalized_ids = [str(message_id).strip() for message_id in message_ids if str(message_id).strip()]
    if not chat_id or not normalized_ids:
        return set()

    with get_db_context(db) as db:
        ensure_schema(db)

        params: dict[str, Any] = {"chat_id": chat_id}
        placeholders: list[str] = []
        for idx, message_id in enumerate(normalized_ids):
            key = f"message_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = message_id

        in_clause = ", ".join(placeholders)
        indexed = db.execute(
            text(
                f"""
                SELECT message_id
                FROM simon_chat_lex
                WHERE chat_id = :chat_id
                  AND message_id IN ({in_clause})
                """
            ),
            params,
        ).fetchall()

        out = {str(row[0]) for row in indexed}
        if not include_queue:
            return out

        queued = db.execute(
            text(
                f"""
                SELECT message_id
                FROM simon_chat_lex_queue
                WHERE chat_id = :chat_id
                  AND message_id IN ({in_clause})
                """
            ),
            params,
        ).fetchall()
        out.update(str(row[0]) for row in queued)
        return out


def enqueue_missing_messages(
    chat_id: str,
    message_ids: list[str],
    *,
    priority: int = 0,
    db: Optional[Session] = None,
) -> int:
    if not is_supported_database():
        return 0

    normalized_ids = [str(message_id).strip() for message_id in message_ids if str(message_id).strip()]
    if not chat_id or not normalized_ids:
        return 0

    with get_db_context(db) as db:
        existing = get_indexed_message_ids(chat_id, normalized_ids, db=db)
        enqueued = 0
        for message_id in normalized_ids:
            if message_id in existing:
                continue
            enqueue_message(chat_id, message_id, priority=priority, db=db)
            enqueued += 1
        return enqueued


def delete_entries_for_chat_id(chat_id: str, db: Optional[Session] = None) -> None:
    if not is_supported_database() or not chat_id:
        return

    with get_db_context(db) as db:
        db.execute(
            text(
                """
                DELETE FROM simon_chat_lex_fts
                WHERE rowid IN (
                    SELECT id FROM simon_chat_lex WHERE chat_id = :chat_id
                )
                """
            ),
            {"chat_id": chat_id},
        )
        db.execute(
            text("DELETE FROM simon_chat_lex WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        )
        db.execute(
            text("DELETE FROM simon_chat_lex_queue WHERE chat_id = :chat_id"),
            {"chat_id": chat_id},
        )
        db.commit()


def delete_entries_for_chat_ids(chat_ids: list[str], db: Optional[Session] = None) -> None:
    normalized_ids = [str(chat_id).strip() for chat_id in chat_ids if str(chat_id).strip()]
    if not is_supported_database() or not normalized_ids:
        return

    with get_db_context(db) as db:
        ensure_schema(db)

        params: dict[str, Any] = {}
        placeholders: list[str] = []
        for idx, chat_id in enumerate(normalized_ids):
            key = f"chat_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = chat_id

        in_clause = ", ".join(placeholders)
        db.execute(
            text(
                f"""
                DELETE FROM simon_chat_lex_fts
                WHERE rowid IN (
                    SELECT id FROM simon_chat_lex WHERE chat_id IN ({in_clause})
                )
                """
            ),
            params,
        )
        db.execute(
            text(f"DELETE FROM simon_chat_lex WHERE chat_id IN ({in_clause})"),
            params,
        )
        db.execute(
            text(f"DELETE FROM simon_chat_lex_queue WHERE chat_id IN ({in_clause})"),
            params,
        )
        db.commit()


def _upsert_lex_entry(
    db: Session,
    *,
    chat_id: str,
    message_id: str,
    parent_id: str | None,
    role: str,
    content_text: str,
    created_at: int,
) -> None:
    now = int(time.time())
    content_hash = _hash_content(content_text)

    db.execute(
        text(
            """
            INSERT INTO simon_chat_lex(
                chat_id, message_id, parent_id, role, content_text, content_hash,
                extractor_version, created_at, updated_at
            ) VALUES(
                :chat_id, :message_id, :parent_id, :role, :content_text, :content_hash,
                :extractor_version, :created_at, :updated_at
            )
            ON CONFLICT(chat_id, message_id) DO UPDATE SET
                parent_id = excluded.parent_id,
                role = excluded.role,
                content_text = excluded.content_text,
                content_hash = excluded.content_hash,
                extractor_version = excluded.extractor_version,
                updated_at = excluded.updated_at
            """
        ),
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "parent_id": parent_id,
            "role": role,
            "content_text": content_text,
            "content_hash": content_hash,
            "extractor_version": EXTRACTOR_VERSION,
            "created_at": created_at,
            "updated_at": now,
        },
    )

    row_id = db.execute(
        text(
            """
            SELECT id
            FROM simon_chat_lex
            WHERE chat_id = :chat_id AND message_id = :message_id
            """
        ),
        {"chat_id": chat_id, "message_id": message_id},
    ).scalar_one()

    db.execute(
        text("DELETE FROM simon_chat_lex_fts WHERE rowid = :row_id"),
        {"row_id": row_id},
    )

    db.execute(
        text(
            """
            INSERT INTO simon_chat_lex_fts(
                rowid, content_text, message_id, chat_id, parent_id, role
            ) VALUES(
                :row_id, :content_text, :message_id, :chat_id, :parent_id, :role
            )
            """
        ),
        {
            "row_id": row_id,
            "content_text": content_text,
            "message_id": message_id,
            "chat_id": chat_id,
            "parent_id": parent_id,
            "role": role,
        },
    )


def _delete_queue_row(db: Session, queue_id: int) -> None:
    db.execute(
        text("DELETE FROM simon_chat_lex_queue WHERE id = :id"),
        {"id": int(queue_id)},
    )


def _mark_queue_failure(db: Session, queue_id: int, attempts: int, error_text: str) -> None:
    now = int(time.time())
    backoff = min(120, 2 ** min(attempts, 6))

    if attempts >= MAX_QUEUE_ATTEMPTS:
        # Stop retrying but keep evidence of failure for observability.
        db.execute(
            text(
                """
                UPDATE simon_chat_lex_queue
                SET attempts = :attempts,
                    last_error = :last_error,
                    available_at = :available_at,
                    updated_at = :updated_at
                WHERE id = :id
                """
            ),
            {
                "id": int(queue_id),
                "attempts": int(attempts),
                "last_error": error_text[:1000],
                "available_at": now + 3600,
                "updated_at": now,
            },
        )
        return

    db.execute(
        text(
            """
            UPDATE simon_chat_lex_queue
            SET attempts = :attempts,
                last_error = :last_error,
                available_at = :available_at,
                updated_at = :updated_at
            WHERE id = :id
            """
        ),
        {
            "id": int(queue_id),
            "attempts": int(attempts),
            "last_error": error_text[:1000],
            "available_at": now + backoff,
            "updated_at": now,
        },
    )


def process_queue_batch(batch_size: int = 20) -> int:
    if not is_supported_database():
        return 0

    now = int(time.time())
    processed = 0

    with get_db_context() as db:
        ensure_schema(db)

        rows = db.execute(
            text(
                """
                SELECT id, chat_id, message_id, attempts
                FROM simon_chat_lex_queue
                WHERE available_at <= :available_at
                ORDER BY priority DESC, updated_at ASC
                LIMIT :batch_size
                """
            ),
            {"available_at": now, "batch_size": int(max(1, batch_size))},
        ).fetchall()

        for row in rows:
            queue_id = int(row[0])
            chat_id = str(row[1])
            message_id = str(row[2])
            attempts = int(row[3] or 0)

            try:
                from open_webui.models.chats import Chats

                message = Chats.get_message_by_id_and_message_id(chat_id, message_id)
                if not message:
                    _delete_queue_row(db, queue_id)
                    db.commit()
                    processed += 1
                    continue

                role = str(message.get("role") or "")
                parent_id = message.get("parentId")
                content_text = extract_text_from_message(message)
                created_at = _as_epoch_seconds(message.get("timestamp"))

                if role not in {"user", "assistant", "system"}:
                    _delete_queue_row(db, queue_id)
                    db.commit()
                    processed += 1
                    continue

                if not content_text:
                    db.execute(
                        text(
                            """
                            DELETE FROM simon_chat_lex_fts
                            WHERE rowid IN (
                                SELECT id FROM simon_chat_lex
                                WHERE chat_id = :chat_id AND message_id = :message_id
                            )
                            """
                        ),
                        {"chat_id": chat_id, "message_id": message_id},
                    )
                    db.execute(
                        text(
                            """
                            DELETE FROM simon_chat_lex
                            WHERE chat_id = :chat_id AND message_id = :message_id
                            """
                        ),
                        {"chat_id": chat_id, "message_id": message_id},
                    )
                else:
                    _upsert_lex_entry(
                        db,
                        chat_id=chat_id,
                        message_id=message_id,
                        parent_id=parent_id,
                        role=role,
                        content_text=content_text,
                        created_at=created_at,
                    )

                _delete_queue_row(db, queue_id)
                db.commit()
                processed += 1
            except Exception as exc:
                _mark_queue_failure(db, queue_id, attempts + 1, str(exc))
                db.commit()
                log.exception("Simon lexical queue processing failed: %s", exc)

    return processed


def _fts_tokenize(text_value: str) -> list[str]:
    if not text_value:
        return []

    tokens = [token.lower() for token in _WORD_RE.findall(text_value)]
    tokens = [token for token in tokens if len(token) >= 3]

    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)

    return deduped


def _filter_stop_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if token not in _STOP_TOKENS]


def _build_subqueries(tokens: list[str], max_branches: int) -> list[str]:
    if not tokens:
        return []

    max_branches = max(1, int(max_branches))
    candidates: list[str] = []

    if len(tokens) >= 2:
        mid = len(tokens) // 2
        left = " ".join(tokens[:mid])
        right = " ".join(tokens[mid:])
        if left:
            candidates.append(left)
        if right and right != left:
            candidates.append(right)
        for idx in range(len(tokens) - 1):
            candidates.append(f"{tokens[idx]} {tokens[idx + 1]}")

    candidates.extend(tokens)

    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
        if len(out) >= max_branches:
            break

    return out


def _count_token_matches(text_value: str, tokens: list[str]) -> int:
    if not text_value or not tokens:
        return 0

    content_tokens = {token.lower() for token in _WORD_RE.findall(text_value)}
    return sum(1 for token in tokens if token in content_tokens)


def _query_fts(
    db: Session,
    *,
    chat_id: str,
    match_query: str,
    limit: int,
    branch_message_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if not match_query.strip():
        return []

    params: dict[str, Any] = {
        "match_query": match_query,
        "chat_id": chat_id,
        "limit": int(max(1, min(limit, 100))),
    }

    message_filter_sql = ""
    if branch_message_ids:
        placeholders: list[str] = []
        for idx, message_id in enumerate(branch_message_ids):
            key = f"message_id_{idx}"
            placeholders.append(f":{key}")
            params[key] = message_id
        message_filter_sql = f"AND lex.message_id IN ({', '.join(placeholders)})"

    try:
        rows = db.execute(
            text(
                f"""
                SELECT
                    lex.message_id,
                    lex.parent_id,
                    lex.role,
                    lex.content_text,
                    lex.created_at,
                    bm25(simon_chat_lex_fts) AS score
                FROM simon_chat_lex_fts
                JOIN simon_chat_lex AS lex ON lex.id = simon_chat_lex_fts.rowid
                WHERE simon_chat_lex_fts MATCH :match_query
                  AND lex.chat_id = :chat_id
                  {message_filter_sql}
                ORDER BY score ASC
                LIMIT :limit
                """
            ),
            params,
        ).fetchall()
    except Exception as exc:
        # FTS5 MATCH can fail on malformed query syntax.
        # Keep Simon path resilient and let higher tiers answer.
        log.debug("Simon FTS query failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "message_id": row[0],
                "parent_id": row[1],
                "role": row[2],
                "content": row[3],
                "created_at": row[4],
                "score": float(row[5]) if row[5] is not None else None,
            }
        )

    return out


def recursive_search(
    chat_id: str,
    query_text: str,
    *,
    branch_message_ids: list[str] | None = None,
    limit: int = 6,
    depth: int = 2,
    max_queries: int = 12,
    max_branches: int = 4,
    oversample: int = 3,
    min_match_tokens: int = 1,
    db: Optional[Session] = None,
) -> list[dict[str, Any]]:
    if not is_supported_database():
        return []

    tokens = _fts_tokenize(query_text)
    strong_tokens = _filter_stop_tokens(tokens) or tokens
    if not strong_tokens:
        return []

    query_budget = {"count": 0}
    max_queries = max(1, int(max_queries))
    oversample = max(1, int(oversample))

    def to_match_query(query_value: str, *, use_or: bool) -> str:
        query_tokens = _fts_tokenize(query_value)
        if not query_tokens:
            return ""

        joiner = " OR " if use_or else " AND "
        # Quote tokens to avoid FTS parser issues with punctuation/operators.
        return joiner.join(f'"{token}"' for token in query_tokens)

    def run_query(target_db: Session, query_value: str, allow_or: bool) -> list[dict[str, Any]]:
        if query_budget["count"] >= max_queries:
            return []

        query_budget["count"] += 1
        and_query = to_match_query(query_value, use_or=False)
        if not and_query:
            return []

        results = _query_fts(
            target_db,
            chat_id=chat_id,
            match_query=and_query,
            limit=limit * oversample,
            branch_message_ids=branch_message_ids,
        )
        if results or not allow_or:
            return results

        or_query = to_match_query(query_value, use_or=True)
        if or_query and or_query != and_query and query_budget["count"] < max_queries:
            query_budget["count"] += 1
            return _query_fts(
                target_db,
                chat_id=chat_id,
                match_query=or_query,
                limit=limit * oversample,
                branch_message_ids=branch_message_ids,
            )

        return []

    def search_recursive(target_db: Session, query_value: str, depth_left: int) -> list[dict[str, Any]]:
        rows = run_query(target_db, query_value, allow_or=False)
        if rows:
            return rows

        if depth_left <= 0:
            return run_query(target_db, query_value, allow_or=True)

        sub_tokens = _filter_stop_tokens(_fts_tokenize(query_value))
        subqueries = _build_subqueries(sub_tokens or strong_tokens, max_branches)
        acc: list[dict[str, Any]] = []

        for subquery in subqueries:
            if query_budget["count"] >= max_queries:
                break
            acc.extend(search_recursive(target_db, subquery, depth_left - 1))
            if len(acc) >= limit * oversample:
                break

        return acc

    with get_db_context(db) as target_db:
        ensure_schema(target_db)
        rows = search_recursive(target_db, query_text, max(0, int(depth)))

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[row["message_id"]] = row

    filtered = list(deduped.values())
    min_match = max(1, min(int(min_match_tokens), len(strong_tokens)))
    token_filtered = [
        row
        for row in filtered
        if _count_token_matches(str(row.get("content") or ""), strong_tokens) >= min_match
    ]
    if token_filtered:
        filtered = token_filtered

    filtered.sort(key=lambda item: (item.get("score") is None, item.get("score") or 0.0))
    return filtered[: max(1, int(limit))]


async def run_lex_index_worker(
    *,
    stop_event: asyncio.Event,
    batch_size: int = DEFAULT_QUEUE_BATCH_SIZE,
    poll_ms: int = DEFAULT_QUEUE_POLL_MS,
) -> None:
    if not is_supported_database():
        return

    update_worker_settings(batch_size=batch_size, poll_ms=poll_ms)

    while not stop_event.is_set():
        processed = 0
        safe_batch, safe_poll = get_worker_settings()
        try:
            processed = await asyncio.to_thread(process_queue_batch, safe_batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Simon lexical worker loop failed: %s", exc)

        if processed <= 0:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=safe_poll / 1000.0)
            except asyncio.TimeoutError:
                continue


def get_queue_depth(db: Optional[Session] = None) -> int:
    if not is_supported_database():
        return 0

    with get_db_context(db) as db:
        value = db.execute(text("SELECT COUNT(1) FROM simon_chat_lex_queue")).scalar()
        return int(value or 0)
