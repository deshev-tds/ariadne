import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import uuid4

from open_webui.env import AGENTIC_ARTIFACTS_DIR
from open_webui.models.chats import Chats

log = logging.getLogger(__name__)


def _safe_path_component(value: Any, fallback: str, max_len: int = 80) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "")).strip(".-_")
    if not normalized:
        normalized = fallback
    return normalized[:max_len]


def _slugify_title(title: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", str(title or "")).strip("-")
    return cleaned.lower()[:64] or "chat"


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def resolve_chat_artifacts_dir(chat_id: str) -> Optional[Path]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id or normalized_chat_id.startswith("local:"):
        return None

    root = Path(AGENTIC_ARTIFACTS_DIR)
    root.mkdir(parents=True, exist_ok=True)

    existing_dirs = sorted(root.glob(f"{normalized_chat_id}__*"))
    if existing_dirs:
        return existing_dirs[0]

    chat_title = Chats.get_chat_title_by_id(normalized_chat_id) or "chat"
    slug = _slugify_title(chat_title)
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

    return resolved


def _sqlite_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> bool:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifacts (
            artifact_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            url TEXT NOT NULL,
            domain TEXT NOT NULL,
            title TEXT,
            path TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            content_chars INTEGER NOT NULL,
            sha256 TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifacts_chat_fetched
        ON web_artifacts(chat_id, fetched_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifacts_chat_domain
        ON web_artifacts(chat_id, domain)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifacts_chat_message_fetched
        ON web_artifacts(chat_id, message_id, fetched_at ASC, artifact_id ASC)
        """
    )

    fts_enabled = True
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS web_artifacts_fts
            USING fts5(
                artifact_id,
                url,
                domain,
                title,
                content
            )
            """
        )
    except Exception as exc:
        log.warning("FTS5 is unavailable, falling back to LIKE search: %s", exc)
        fts_enabled = False

    conn.commit()
    return fts_enabled


def _domain_from_url(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower()
    except Exception:
        return ""


def store_web_page(
    *,
    chat_id: str,
    message_id: Optional[str],
    url: str,
    content: str,
    title: Optional[str] = None,
) -> dict[str, Any]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required for store mode")

    chat_dir = resolve_chat_artifacts_dir(normalized_chat_id)
    if chat_dir is None:
        raise ValueError("Unable to resolve chat artifacts directory")

    web_pages_dir = chat_dir / "web_pages"
    web_pages_dir.mkdir(parents=True, exist_ok=True)

    text = str(content or "")
    domain = _domain_from_url(url)
    fetched_at = int(time.time())
    artifact_id = f"wp_{uuid4().hex[:24]}"

    filename = (
        f"{int(time.time() * 1000)}"
        f"_{_safe_path_component(message_id, 'message')}"
        f"_{_safe_path_component(domain, 'domain')}"
        f"_{artifact_id}.txt"
    )
    artifact_path = web_pages_dir / filename
    artifact_path.write_text(text, encoding="utf-8", errors="replace")

    encoded = text.encode("utf-8", "replace")
    sha256 = hashlib.sha256(encoded).hexdigest()

    db_path = chat_dir / "web_evidence.sqlite"
    with _sqlite_conn(db_path) as conn:
        fts_enabled = _init_db(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO web_artifacts (
                artifact_id, chat_id, message_id, url, domain, title,
                path, fetched_at, content_chars, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                normalized_chat_id,
                str(message_id or ""),
                url,
                domain,
                str(title or ""),
                str(artifact_path),
                fetched_at,
                len(text),
                sha256,
            ),
        )
        if fts_enabled:
            conn.execute(
                "DELETE FROM web_artifacts_fts WHERE artifact_id = ?",
                (artifact_id,),
            )
            conn.execute(
                """
                INSERT INTO web_artifacts_fts (
                    artifact_id, url, domain, title, content
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (artifact_id, url, domain, str(title or ""), text),
            )
        conn.commit()

    index_payload = {
        "ts": fetched_at,
        "kind": "web_page_artifact",
        "chat_id": normalized_chat_id,
        "message_id": message_id,
        "artifact_id": artifact_id,
        "url": url,
        "domain": domain,
        "title": str(title or ""),
        "path": str(artifact_path),
        "chars": len(text),
        "sha256": sha256,
    }
    _append_jsonl(chat_dir / "web_pages.index.jsonl", index_payload)

    return {
        "status": "stored",
        "artifact_id": artifact_id,
        "chat_id": normalized_chat_id,
        "message_id": message_id,
        "url": url,
        "domain": domain,
        "title": str(title or ""),
        "path": str(artifact_path),
        "fetched_at": fetched_at,
        "content_chars": len(text),
        "sha256": sha256,
        "fts_indexed": bool(fts_enabled),
    }


def _fts_query_terms(query: str) -> list[str]:
    return [token for token in re.findall(r"[\w-]{2,}", (query or "").lower()) if token]


def _normalize_artifact_ids(values: Optional[list[str]]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        artifact_id = str(value or "").strip()
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        normalized.append(artifact_id)
    return normalized


def _extract_window(content: str, terms: list[str], window_chars: int) -> tuple[int, int, str, int]:
    if not content:
        return 0, 0, "", 0

    lower = content.lower()
    best_idx = -1
    best_len = 0
    for term in terms:
        idx = lower.find(term.lower())
        if idx >= 0 and (best_idx < 0 or idx < best_idx):
            best_idx = idx
            best_len = len(term)

    if best_idx < 0:
        best_idx = 0
        best_len = min(32, len(content))

    half = max(80, int(window_chars) // 2)
    start = max(0, best_idx - half)
    end = min(len(content), best_idx + best_len + half)
    snippet = content[start:end]
    return start, end, snippet, best_idx


def _query_fts_rows(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    terms: list[str],
    artifact_ids: list[str],
    limit: int,
) -> tuple[list[Any], bool]:
    if not artifact_ids:
        return [], True

    fts_enabled = True
    try:
        q_parts = []
        params: list[Any] = []

        match_query = " ".join(terms).strip() or "*"
        q_parts.append(
            """
            SELECT
                a.artifact_id,
                a.url,
                a.domain,
                a.title,
                a.path,
                f.content,
                bm25(web_artifacts_fts) AS rank
            FROM web_artifacts_fts f
            JOIN web_artifacts a ON a.artifact_id = f.artifact_id
            WHERE web_artifacts_fts MATCH ? AND a.chat_id = ?
            """
        )
        params.extend([match_query, chat_id])

        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            q_parts.append(f"AND a.artifact_id IN ({placeholders})")
            params.extend(artifact_ids)

        q_parts.append("ORDER BY rank ASC LIMIT ?")
        params.append(limit)
        sql = "\n".join(q_parts)
        rows = conn.execute(sql, params).fetchall()
        return rows, fts_enabled
    except Exception:
        fts_enabled = False

    sql = (
        "SELECT artifact_id, url, domain, title, path, fetched_at "
        "FROM web_artifacts WHERE chat_id = ?"
    )
    params = [chat_id]
    if artifact_ids:
        placeholders = ",".join("?" for _ in artifact_ids)
        sql += f" AND artifact_id IN ({placeholders})"
        params.extend(artifact_ids)
    sql += " ORDER BY fetched_at ASC, artifact_id ASC"
    rows = conn.execute(sql, params).fetchall()

    scored_rows: list[dict[str, Any]] = []
    lowered_terms = [term.lower() for term in terms if term]
    for row in rows:
        path = str(row["path"] or "")
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lowered_content = content.lower()
        if lowered_terms:
            lexical_hits = sum(1 for term in lowered_terms if term in lowered_content)
            if lexical_hits == 0:
                continue
            rank = -float(lexical_hits)
        else:
            lexical_hits = 0
            rank = 0.0

        scored_rows.append(
            {
                "artifact_id": row["artifact_id"],
                "url": row["url"],
                "domain": row["domain"],
                "title": row["title"],
                "path": path,
                "content": content,
                "rank": rank,
                "fetched_at": int(row["fetched_at"] or 0),
                "lexical_hits": lexical_hits,
            }
        )

    scored_rows.sort(
        key=lambda item: (
            -int(item.get("lexical_hits", 0) or 0),
            int(item.get("fetched_at", 0) or 0),
            str(item.get("artifact_id", "") or ""),
        )
    )
    return scored_rows[:limit], fts_enabled


def _load_scope_rows(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    message_id: Optional[str],
    artifact_ids: list[str],
) -> tuple[list[sqlite3.Row], list[str], str]:
    scope_mode = "explicit" if artifact_ids else "implicit_current_message"
    if artifact_ids:
        placeholders = ",".join("?" for _ in artifact_ids)
        rows = conn.execute(
            f"""
            SELECT artifact_id, url, domain, title, path, fetched_at, message_id
            FROM web_artifacts
            WHERE chat_id = ? AND artifact_id IN ({placeholders})
            ORDER BY fetched_at ASC, artifact_id ASC
            """,
            [chat_id, *artifact_ids],
        ).fetchall()
        found_ids = {str(row["artifact_id"] or "") for row in rows}
        missing = [artifact_id for artifact_id in artifact_ids if artifact_id not in found_ids]
        return rows, missing, scope_mode

    normalized_message_id = str(message_id or "").strip()
    if not normalized_message_id:
        return [], [], scope_mode

    rows = conn.execute(
        """
        SELECT artifact_id, url, domain, title, path, fetched_at, message_id
        FROM web_artifacts
        WHERE chat_id = ? AND message_id = ?
        ORDER BY fetched_at ASC, artifact_id ASC
        """,
        (chat_id, normalized_message_id),
    ).fetchall()
    return rows, [], scope_mode


def _classify_evidence_strength(snippets: list[dict[str, Any]]) -> str:
    if not snippets:
        return "weak"

    max_score = max(float(snippet.get("score", 0.0) or 0.0) for snippet in snippets)
    unique_domains = {
        str(snippet.get("domain") or "").strip().lower()
        for snippet in snippets
        if str(snippet.get("domain") or "").strip()
    }

    if len(snippets) >= 3 and len(unique_domains) >= 2 and max_score >= 0.55:
        return "strong"
    if len(snippets) >= 1 and max_score >= 0.35:
        return "adequate"
    return "weak"


def _suggest_next_action(
    *,
    searched_artifact_count: int,
    searched_domains: list[str],
    snippets: list[dict[str, Any]],
    evidence_strength: str,
) -> str:
    if searched_artifact_count <= 0:
        return "fetch_more"
    if evidence_strength in {"adequate", "strong"} and snippets:
        return "answer_with_current_evidence"
    if searched_artifact_count <= 1 or len(searched_domains) <= 1:
        return "broaden_discovery"
    return "refine_query"


def query_web_evidence_store(
    *,
    chat_id: str,
    message_id: Optional[str],
    query: str,
    artifact_ids: Optional[list[str]] = None,
    top_k: int = 6,
    window_chars: int = 320,
    widen_if_weak: bool = True,
    wide_top_k: int = 10,
    wide_window_chars: int = 640,
) -> dict[str, Any]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required for evidence queries")
    normalized_artifact_ids = _normalize_artifact_ids(artifact_ids)

    chat_dir = resolve_chat_artifacts_dir(normalized_chat_id)
    if chat_dir is None:
        return {
            "status": "not_found",
            "query": query,
            "chat_id": normalized_chat_id,
            "message_id": str(message_id or ""),
            "scope_mode": "explicit" if normalized_artifact_ids else "implicit_current_message",
            "searched_artifact_count": 0,
            "searched_artifact_ids": [],
            "searched_domains": [],
            "missing_artifact_ids": normalized_artifact_ids,
            "evidence_strength": "weak",
            "suggested_next_action": "fetch_more",
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": False,
            "artifact_ids": [],
            "message": "No chat artifact directory found.",
        }

    db_path = chat_dir / "web_evidence.sqlite"
    if not db_path.exists():
        return {
            "status": "not_found",
            "query": query,
            "chat_id": normalized_chat_id,
            "message_id": str(message_id or ""),
            "scope_mode": "explicit" if normalized_artifact_ids else "implicit_current_message",
            "searched_artifact_count": 0,
            "searched_artifact_ids": [],
            "searched_domains": [],
            "missing_artifact_ids": normalized_artifact_ids,
            "evidence_strength": "weak",
            "suggested_next_action": "fetch_more",
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": False,
            "artifact_ids": [],
            "message": "No local web evidence index found.",
        }

    terms = _fts_query_terms(query)
    bounded_top_k = max(1, min(20, int(top_k or 6)))
    bounded_window_chars = max(120, min(2000, int(window_chars or 320)))
    bounded_wide_top_k = max(bounded_top_k, min(30, int(wide_top_k or 10)))
    bounded_wide_window_chars = max(
        bounded_window_chars, min(4000, int(wide_window_chars or 640))
    )

    def row_get(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, sqlite3.Row):
            return row[key] if key in row.keys() else default
        if isinstance(row, dict):
            return row.get(key, default)
        return default

    def build_snippets(rows: list[Any], *, limit: int, window: int) -> list[dict[str, Any]]:
        snippets: list[dict[str, Any]] = []
        for idx, row in enumerate(rows[:limit]):
            content = str(row_get(row, "content", "") or "")
            if not content:
                path = str(row_get(row, "path", "") or "")
                if path:
                    try:
                        content = Path(path).read_text(
                            encoding="utf-8", errors="replace"
                        )
                    except Exception:
                        content = ""
            start, end, snippet_text, hit_idx = _extract_window(content, terms, window)
            rank = row_get(row, "rank", 0.0)
            try:
                rank_f = float(rank)
            except Exception:
                rank_f = 0.0

            lexical_hits = sum(1 for term in terms if term in content.lower())
            lexical_score = lexical_hits / max(1, len(terms)) if terms else 0.0
            rank_score = 1.0 if rank_f <= 0 else 1.0 / (1.0 + rank_f)
            blended = max(0.0, min(1.0, (0.65 * lexical_score) + (0.35 * rank_score)))

            snippets.append(
                {
                    "artifact_id": row_get(row, "artifact_id", ""),
                    "url": row_get(row, "url", ""),
                    "domain": row_get(row, "domain", ""),
                    "title": row_get(row, "title", ""),
                    "path": row_get(row, "path", ""),
                    "start": start,
                    "end": end,
                    "hit_index": hit_idx,
                    "score": round(blended, 4),
                    "text": snippet_text,
                }
            )
        return snippets

    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        scope_rows, missing_artifact_ids, scope_mode = _load_scope_rows(
            conn,
            chat_id=normalized_chat_id,
            message_id=message_id,
            artifact_ids=normalized_artifact_ids,
        )
        searched_artifact_ids = [
            str(row["artifact_id"] or "") for row in scope_rows if str(row["artifact_id"] or "")
        ]
        searched_domains = sorted(
            {
                str(row["domain"] or "").strip().lower()
                for row in scope_rows
                if str(row["domain"] or "").strip()
            }
        )
        narrow_rows, fts_enabled = _query_fts_rows(
            conn,
            chat_id=normalized_chat_id,
            terms=terms,
            artifact_ids=searched_artifact_ids,
            limit=max(bounded_top_k * 2, bounded_top_k),
        )

    narrow = build_snippets(narrow_rows, limit=bounded_top_k, window=bounded_window_chars)
    weak = len(narrow) < min(2, bounded_top_k) or max(
        [snippet.get("score", 0.0) for snippet in narrow] + [0.0]
    ) < 0.35

    wide: list[dict[str, Any]] = []
    wide_used = False
    if widen_if_weak and weak:
        with _sqlite_conn(db_path) as conn:
            rows, _ = _query_fts_rows(
                conn,
                chat_id=normalized_chat_id,
                terms=terms,
                artifact_ids=searched_artifact_ids,
                limit=max(bounded_wide_top_k * 2, bounded_wide_top_k),
            )
        wide = build_snippets(rows, limit=bounded_wide_top_k, window=bounded_wide_window_chars)
        wide_used = True

    snippets = wide if wide_used and wide else narrow
    evidence_strength = _classify_evidence_strength(snippets)
    suggested_next_action = _suggest_next_action(
        searched_artifact_count=len(searched_artifact_ids),
        searched_domains=searched_domains,
        snippets=snippets,
        evidence_strength=evidence_strength,
    )

    return {
        "status": "ok",
        "query": query,
        "chat_id": normalized_chat_id,
        "message_id": str(message_id or ""),
        "scope_mode": scope_mode,
        "artifact_ids": searched_artifact_ids,
        "searched_artifact_count": len(searched_artifact_ids),
        "searched_artifact_ids": searched_artifact_ids,
        "searched_domains": searched_domains,
        "missing_artifact_ids": missing_artifact_ids,
        "snippets": snippets,
        "narrow_count": len(narrow),
        "wide_count": len(wide),
        "wide_pass_used": wide_used,
        "fts_enabled": bool(fts_enabled),
        "top_k": bounded_top_k,
        "window_chars": bounded_window_chars,
        "widen_if_weak": bool(widen_if_weak),
        "weak_narrow_evidence": bool(weak),
        "evidence_strength": evidence_strength,
        "suggested_next_action": suggested_next_action,
    }
