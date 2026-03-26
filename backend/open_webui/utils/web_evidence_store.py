from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from open_webui.env import AGENTIC_ARTIFACTS_DIR
from open_webui.extensions.simon_engine.token_budget import estimate_tokens_from_text
from open_webui.models.chats import Chats

try:
    import tiktoken
except Exception:
    tiktoken = None


READ_WEB_PAGE_DEFAULT_MAX_TOKENS = 12_000
READ_WEB_PAGE_TOKEN_OVERLAP = 512
READ_WEB_PAGE_CURSOR_VERSION = 1
READ_WEB_PAGE_CURSOR_KIND = "read_web_page"

MAX_SEGMENT_CHARS = 1600
SEGMENT_CHAR_OVERLAP = 200
STRUCTURE_CONFIDENCE_STRONG_FLOOR = 0.55
STRUCTURE_CONFIDENCE_WEAK_FLOOR = 0.25

_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_GENERIC_HEADING_RE = re.compile(
    r"^(chapter|part|section|article|appendix|glava|razdel|глава|раздел|чл\.|ал\.|т\.|§|abstract|introduction|methods|results|discussion|conclusion|references|table)\b",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(\d+(?:[.)]\d+)*[.)]?|[IVXLCM]+[.)])\s+\S+",
    re.IGNORECASE,
)
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_path_component(value: Any, fallback: str, max_len: int = 80) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", str(value or "")).strip(".-_")
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


def _canonicalize_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    lowered = raw.split("#", 1)[0].strip().rstrip("/")
    return lowered


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
    except Exception:
        pass

    return resolved


def _sqlite_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifacts (
            artifact_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_web_artifacts_chat_message_fetched
        ON web_artifacts(chat_id, message_id, fetched_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifacts_chat_canonical_url
        ON web_artifacts(chat_id, canonical_url, fetched_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifact_segment_meta (
            artifact_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            segmentation_mode TEXT NOT NULL,
            structure_confidence REAL NOT NULL,
            segment_count INTEGER NOT NULL,
            segment_stats_json TEXT,
            indexed_at INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifact_segments (
            segment_id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            label TEXT,
            path_text TEXT,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            segment_chars INTEGER NOT NULL,
            content TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifact_segments_artifact
        ON web_artifact_segments(artifact_id, start_offset ASC)
        """
    )
    conn.commit()


def _domain_from_url(url: str) -> str:
    match = re.match(r"^[a-z]+://([^/]+)", str(url or "").strip(), flags=re.IGNORECASE)
    return str((match.group(1) if match else "") or "").lower()


def _read_artifact_text(path: str) -> str:
    try:
        return Path(str(path or "")).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _build_large_artifact_chunks(
    text: str,
    *,
    chunk_chars: int = MAX_SEGMENT_CHARS,
    overlap_chars: int = SEGMENT_CHAR_OVERLAP,
) -> list[dict[str, Any]]:
    value = str(text or "")
    if not value:
        return []
    if len(value) <= chunk_chars:
        return [{"start": 0, "end": len(value), "text": value}]

    chunks: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(value):
        end = min(len(value), cursor + chunk_chars)
        chunk = value[cursor:end]
        chunks.append({"start": cursor, "end": end, "text": chunk})
        if end >= len(value):
            break
        cursor = max(cursor + 1, end - overlap_chars)
    return chunks


def _iter_line_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    cursor = 0
    for line in str(text or "").splitlines(keepends=True):
        end = cursor + len(line)
        records.append(
            {
                "raw": line,
                "text": line.rstrip("\n"),
                "stripped": line.strip(),
                "start": cursor,
                "end": end,
            }
        )
        cursor = end
    if not records and text:
        records.append({"raw": text, "text": text, "stripped": text.strip(), "start": 0, "end": len(text)})
    return records


def _normalize_heading_label(value: str) -> str:
    stripped = str(value or "").strip()
    match = _MARKDOWN_HEADING_RE.match(stripped)
    if match:
        stripped = match.group(2)
    stripped = re.sub(r"\s+", " ", stripped).strip(" -:\t")
    return stripped[:200]


def _heading_level(raw_heading: str) -> int:
    stripped = str(raw_heading or "").strip()
    markdown_match = _MARKDOWN_HEADING_RE.match(stripped)
    if markdown_match:
        return min(4, len(markdown_match.group(1)))

    lowered = stripped.lower()
    if lowered.startswith(("chapter", "part", "глава")):
        return 1
    if lowered.startswith(("section", "appendix", "abstract", "introduction", "methods", "results", "discussion", "conclusion", "references", "раздел")):
        return 2
    if lowered.startswith(("article", "чл.", "§", "table")):
        return 3
    if lowered.startswith(("ал.", "т.")):
        return 4

    numbered_match = _NUMBERED_HEADING_RE.match(stripped)
    if numbered_match:
        prefix = numbered_match.group(1)
        if prefix and prefix[0].isdigit():
            return min(4, prefix.count(".") + 1)
        return 2

    return 2


def _looks_like_heading_line(value: str) -> bool:
    stripped = str(value or "").strip()
    if not stripped or len(stripped) > 160:
        return False
    if _MARKDOWN_HEADING_RE.match(stripped):
        return True
    if _GENERIC_HEADING_RE.match(stripped):
        return True
    if _NUMBERED_HEADING_RE.match(stripped):
        return True
    alpha_chars = re.sub(r"[^A-Za-zА-Яа-я]", "", stripped)
    word_count = len(stripped.split())
    return (
        len(alpha_chars) >= 4
        and word_count <= 12
        and stripped == stripped.upper()
        and any(char.isalpha() for char in stripped)
    )


def _estimate_structure_confidence(
    *,
    text: str,
    heading_labels: list[str],
    segment_count: int,
    avg_segment_chars: float,
) -> tuple[float, dict[str, Any]]:
    line_records = _iter_line_records(text)
    total_lines = max(1, len(line_records))
    heading_count = len(heading_labels)
    coverage_ratio = min(
        1.0,
        (segment_count * max(1.0, avg_segment_chars)) / max(1.0, float(len(text) or 1)),
    )
    heading_density = heading_count / total_lines
    normalized_labels = [label.lower() for label in heading_labels if label]
    unique_headings = len(set(normalized_labels))
    duplicate_heading_ratio = 0.0
    if normalized_labels:
        duplicate_heading_ratio = 1.0 - (
            unique_headings / max(1.0, float(len(normalized_labels)))
        )
    density_score = min(1.0, heading_density / 0.03) if heading_count >= 2 else 0.0
    coverage_score = coverage_ratio
    duplicate_score = max(0.0, 1.0 - duplicate_heading_ratio)
    avg_score = 1.0 if 240 <= avg_segment_chars <= MAX_SEGMENT_CHARS else 0.6
    confidence = (
        (0.35 * density_score)
        + (0.25 * coverage_score)
        + (0.20 * duplicate_score)
        + (0.20 * avg_score)
    )
    if heading_count >= 3 and segment_count >= 3:
        confidence += 0.1
    confidence = round(max(0.0, min(1.0, confidence)), 4)
    stats = {
        "heading_count": heading_count,
        "heading_density": round(heading_density, 4),
        "duplicate_heading_ratio": round(duplicate_heading_ratio, 4),
        "segment_coverage_ratio": round(coverage_ratio, 4),
        "avg_segment_chars": round(avg_segment_chars, 2),
    }
    return confidence, stats


def _build_chunk_segments(
    text: str,
    *,
    artifact_id: str,
    chat_id: str,
    message_id: str,
    label: str = "",
    path_text: str = "",
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for idx, chunk in enumerate(_build_large_artifact_chunks(text)):
        segments.append(
            {
                "segment_id": f"{artifact_id}:chunk:{idx}",
                "artifact_id": artifact_id,
                "chat_id": chat_id,
                "message_id": message_id,
                "label": label,
                "path_text": path_text,
                "start_offset": int(chunk.get("start") or 0),
                "end_offset": int(chunk.get("end") or 0),
                "segment_chars": len(str(chunk.get("text") or "")),
                "content": str(chunk.get("text") or ""),
            }
        )
    return segments


def _build_structured_segments(
    text: str,
    *,
    artifact_id: str,
    chat_id: str,
    message_id: str,
) -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
    records = _iter_line_records(text)
    heading_records: list[dict[str, Any]] = []
    for record in records:
        stripped = record.get("stripped") or ""
        if not _looks_like_heading_line(stripped):
            continue
        label = _normalize_heading_label(stripped)
        if not label:
            continue
        heading_records.append(
            {
                "start": int(record.get("start") or 0),
                "end": int(record.get("end") or 0),
                "label": label,
                "level": _heading_level(stripped),
            }
        )

    if not heading_records:
        chunk_segments = _build_chunk_segments(
            text,
            artifact_id=artifact_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        return chunk_segments, 0.0, {
            "heading_count": 0,
            "heading_density": 0.0,
            "duplicate_heading_ratio": 0.0,
            "segment_coverage_ratio": 1.0 if text else 0.0,
            "avg_segment_chars": round(
                sum(segment["segment_chars"] for segment in chunk_segments) / max(1, len(chunk_segments)),
                2,
            )
            if chunk_segments
            else 0.0,
        }

    segments: list[dict[str, Any]] = []
    heading_stack: list[str] = []

    first_heading_start = int(heading_records[0]["start"])
    if first_heading_start > 240:
        preamble_text = text[:first_heading_start].strip()
        if preamble_text:
            segments.extend(
                _build_chunk_segments(
                    preamble_text,
                    artifact_id=artifact_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    label="Preamble",
                    path_text="Preamble",
                )
            )

    for idx, heading in enumerate(heading_records):
        next_start = (
            int(heading_records[idx + 1]["start"]) if idx + 1 < len(heading_records) else len(text)
        )
        section_start = int(heading.get("start") or 0)
        section_end = max(section_start, next_start)
        section_text = text[section_start:section_end].strip()
        if not section_text:
            continue

        level = int(heading.get("level") or 2)
        heading_stack = heading_stack[: max(0, level - 1)]
        heading_stack.append(str(heading.get("label") or ""))
        path_text = " > ".join(item for item in heading_stack if item)

        for chunk_idx, chunk in enumerate(_build_large_artifact_chunks(section_text)):
            segments.append(
                {
                    "segment_id": f"{artifact_id}:seg:{idx}:{chunk_idx}",
                    "artifact_id": artifact_id,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "label": str(heading.get("label") or ""),
                    "path_text": path_text,
                    "start_offset": section_start + int(chunk.get("start") or 0),
                    "end_offset": section_start + int(chunk.get("end") or 0),
                    "segment_chars": len(str(chunk.get("text") or "")),
                    "content": str(chunk.get("text") or ""),
                }
            )

    avg_segment_chars = (
        sum(segment["segment_chars"] for segment in segments) / max(1, len(segments))
        if segments
        else 0.0
    )
    confidence, stats = _estimate_structure_confidence(
        text=text,
        heading_labels=[str(item.get("label") or "") for item in heading_records],
        segment_count=len(segments),
        avg_segment_chars=avg_segment_chars,
    )
    return segments, confidence, stats


def _build_segment_index_for_content(
    *,
    artifact_id: str,
    chat_id: str,
    message_id: str,
    content: str,
) -> tuple[str, float, list[dict[str, Any]], dict[str, Any]]:
    text = str(content or "")
    structured_segments, structure_confidence, stats = _build_structured_segments(
        text,
        artifact_id=artifact_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    segmentation_mode = "chunk"
    segments = structured_segments
    if structure_confidence >= STRUCTURE_CONFIDENCE_STRONG_FLOOR:
        segmentation_mode = "structured"
    elif structure_confidence >= STRUCTURE_CONFIDENCE_WEAK_FLOOR:
        segmentation_mode = "weak_structured"
    else:
        segments = _build_chunk_segments(
            text,
            artifact_id=artifact_id,
            chat_id=chat_id,
            message_id=message_id,
        )
        stats = {
            **stats,
            "chunk_fallback": True,
            "avg_segment_chars": round(
                sum(segment["segment_chars"] for segment in segments) / max(1, len(segments)),
                2,
            )
            if segments
            else 0.0,
        }
        structure_confidence = round(structure_confidence, 4)
    return segmentation_mode, structure_confidence, segments, stats


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
    canonical_url = _canonicalize_url(url)
    db_path = chat_dir / "web_pages.sqlite"

    segmentation_mode, structure_confidence, segments, stats = _build_segment_index_for_content(
        artifact_id=artifact_id,
        chat_id=normalized_chat_id,
        message_id=str(message_id or ""),
        content=text,
    )

    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO web_artifacts (
                artifact_id, chat_id, message_id, url, canonical_url, domain, title,
                path, fetched_at, content_chars, sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                normalized_chat_id,
                str(message_id or ""),
                url,
                canonical_url,
                domain,
                str(title or ""),
                str(artifact_path),
                fetched_at,
                len(text),
                sha256,
            ),
        )
        conn.execute("DELETE FROM web_artifact_segment_meta WHERE artifact_id = ?", (artifact_id,))
        conn.execute("DELETE FROM web_artifact_segments WHERE artifact_id = ?", (artifact_id,))
        conn.execute(
            """
            INSERT OR REPLACE INTO web_artifact_segment_meta (
                artifact_id, chat_id, message_id, segmentation_mode,
                structure_confidence, segment_count, segment_stats_json, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                normalized_chat_id,
                str(message_id or ""),
                segmentation_mode,
                float(structure_confidence),
                len(segments),
                json.dumps(stats, ensure_ascii=False),
                fetched_at,
            ),
        )
        for segment in segments:
            conn.execute(
                """
                INSERT OR REPLACE INTO web_artifact_segments (
                    segment_id, artifact_id, chat_id, message_id, label, path_text,
                    start_offset, end_offset, segment_chars, content
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment["segment_id"],
                    artifact_id,
                    normalized_chat_id,
                    str(message_id or ""),
                    segment.get("label") or "",
                    segment.get("path_text") or "",
                    int(segment.get("start_offset") or 0),
                    int(segment.get("end_offset") or 0),
                    int(segment.get("segment_chars") or 0),
                    segment.get("content") or "",
                ),
            )
        conn.commit()

    _append_jsonl(
        chat_dir / "web_pages.index.jsonl",
        {
            "ts": fetched_at,
            "kind": "web_page_artifact",
            "chat_id": normalized_chat_id,
            "message_id": message_id,
            "artifact_id": artifact_id,
            "url": url,
            "canonical_url": canonical_url,
            "domain": domain,
            "title": str(title or ""),
            "path": str(artifact_path),
            "chars": len(text),
            "sha256": sha256,
        },
    )

    return {
        "status": "stored",
        "artifact_id": artifact_id,
        "chat_id": normalized_chat_id,
        "message_id": message_id,
        "url": url,
        "canonical_url": canonical_url,
        "domain": domain,
        "title": str(title or ""),
        "path": str(artifact_path),
        "fetched_at": fetched_at,
        "content_chars": len(text),
        "sha256": sha256,
        "fts_indexed": False,
        "segment_indexed": True,
        "segmentation_mode": segmentation_mode,
        "structure_confidence": float(structure_confidence),
        "segment_count": len(segments),
        "segment_stats": stats,
    }


def _artifact_row_to_dict(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def find_stored_web_artifact(
    *,
    chat_id: str,
    artifact_id: Optional[str] = None,
    url: Optional[str] = None,
    message_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        return None
    chat_dir = resolve_chat_artifacts_dir(normalized_chat_id)
    if chat_dir is None:
        return None
    db_path = chat_dir / "web_pages.sqlite"
    if not db_path.exists():
        return None

    target_artifact = str(artifact_id or "").strip()
    target_url = _canonicalize_url(url or "")
    message_value = str(message_id or "").strip()

    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        if target_artifact:
            row = conn.execute(
                """
                SELECT artifact_id, chat_id, message_id, url, canonical_url, domain, title,
                       path, fetched_at, content_chars, sha256
                FROM web_artifacts
                WHERE chat_id = ? AND artifact_id = ?
                LIMIT 1
                """,
                (normalized_chat_id, target_artifact),
            ).fetchone()
            return _artifact_row_to_dict(row) if row else None

        if target_url:
            rows = conn.execute(
                """
                SELECT artifact_id, chat_id, message_id, url, canonical_url, domain, title,
                       path, fetched_at, content_chars, sha256
                FROM web_artifacts
                WHERE chat_id = ?
                ORDER BY CASE WHEN message_id = ? THEN 0 ELSE 1 END, fetched_at DESC
                """,
                (normalized_chat_id, message_value),
            ).fetchall()
            for row in rows:
                row_dict = _artifact_row_to_dict(row)
                if _canonicalize_url(row_dict.get("canonical_url") or row_dict.get("url") or "") == target_url:
                    return row_dict
    return None


@lru_cache(maxsize=8)
def _get_tiktoken_encoding(encoding_name: str):
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(encoding_name)
    except Exception:
        return None


def _tokenize_text(text: str, encoding_name: str) -> list[int] | None:
    encoding = _get_tiktoken_encoding(encoding_name)
    if encoding is None:
        return None
    try:
        return list(encoding.encode(text or ""))
    except Exception:
        return None


def _decode_tokens(tokens: list[int], encoding_name: str) -> str:
    encoding = _get_tiktoken_encoding(encoding_name)
    if encoding is None:
        return ""
    try:
        return encoding.decode(tokens)
    except Exception:
        return ""


def _align_left(text: str, index: int) -> int:
    bounded = max(0, min(len(text), index))
    while bounded > 0 and not text[bounded - 1].isspace():
        bounded -= 1
    return bounded


def _align_right(text: str, index: int) -> int:
    bounded = max(0, min(len(text), index))
    while bounded < len(text) and not text[bounded].isspace():
        bounded += 1
    return bounded


def _approximate_token_slice(
    text: str,
    *,
    start_token: int,
    max_tokens: int,
) -> tuple[str, int]:
    if not text:
        return "", 0
    total_tokens = estimate_tokens_from_text(text)
    if total_tokens <= 0:
        return text, 0
    chars_per_token = max(1.0, len(text) / max(1, total_tokens))
    start_char = int(start_token * chars_per_token)
    end_char = int(min(total_tokens, start_token + max_tokens) * chars_per_token)
    start_char = _align_left(text, start_char)
    end_char = _align_right(text, end_char)
    return text[start_char:end_char], total_tokens


def _encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(str(cursor or "").encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid cursor payload")
    return payload


def read_stored_web_page(
    *,
    chat_id: str,
    artifact_id: Optional[str] = None,
    url: Optional[str] = None,
    message_id: Optional[str] = None,
    cursor: Optional[str] = None,
    max_tokens: Optional[int] = None,
    overlap_tokens: int = READ_WEB_PAGE_TOKEN_OVERLAP,
    encoding_name: str = "cl100k_base",
) -> dict[str, Any]:
    cursor_payload: dict[str, Any] = {}
    if cursor:
        cursor_payload = _decode_cursor(cursor)
        if str(cursor_payload.get("kind") or "") != READ_WEB_PAGE_CURSOR_KIND:
            raise ValueError("invalid read_web_page cursor kind")
        if int(cursor_payload.get("v") or 0) != READ_WEB_PAGE_CURSOR_VERSION:
            raise ValueError("unsupported read_web_page cursor version")
        artifact_id = artifact_id or str(cursor_payload.get("artifact_id") or "")

    row = find_stored_web_artifact(
        chat_id=chat_id,
        artifact_id=artifact_id,
        url=url,
        message_id=message_id,
    )
    if not row:
        return {
            "status": "not_found",
            "message": "No stored web page artifact found for the requested URL or artifact_id.",
        }

    text = _read_artifact_text(str(row.get("path") or ""))
    if not text:
        return {
            "status": "not_found",
            "artifact_id": row.get("artifact_id"),
            "url": row.get("url"),
            "message": "Stored web page artifact has no readable text.",
        }

    cursor_max_tokens = int(cursor_payload.get("max_tokens") or 0)
    effective_max_tokens = (
        int(max_tokens)
        if max_tokens is not None
        else (cursor_max_tokens or READ_WEB_PAGE_DEFAULT_MAX_TOKENS)
    )
    bounded_max_tokens = max(1, effective_max_tokens)
    bounded_overlap = max(0, min(int(overlap_tokens or 0), bounded_max_tokens - 1))
    start_token = max(0, int(cursor_payload.get("start_token") or 0))

    token_ids = _tokenize_text(text, encoding_name)
    if token_ids is not None:
        total_tokens = len(token_ids)
        end_token = min(total_tokens, start_token + bounded_max_tokens)
        slab_text = _decode_tokens(token_ids[start_token:end_token], encoding_name)
    else:
        slab_text, total_tokens = _approximate_token_slice(
            text,
            start_token=start_token,
            max_tokens=bounded_max_tokens,
        )
        end_token = min(total_tokens, start_token + bounded_max_tokens)

    whole_document_returned = start_token == 0 and end_token >= total_tokens
    done = end_token >= total_tokens
    next_cursor = ""
    if not done:
        next_start = max(0, end_token - bounded_overlap)
        next_cursor = _encode_cursor(
            {
                "kind": READ_WEB_PAGE_CURSOR_KIND,
                "v": READ_WEB_PAGE_CURSOR_VERSION,
                "artifact_id": str(row.get("artifact_id") or ""),
                "start_token": next_start,
                "max_tokens": bounded_max_tokens,
            }
        )

    return {
        "status": "ok",
        "artifact_id": row.get("artifact_id"),
        "chat_id": chat_id,
        "message_id": row.get("message_id"),
        "url": row.get("url"),
        "domain": row.get("domain"),
        "title": row.get("title"),
        "text": slab_text,
        "estimated_tokens": estimate_tokens_from_text(slab_text),
        "range_start_token": start_token,
        "range_end_token": end_token,
        "document_estimated_tokens": total_tokens,
        "document_token_count": total_tokens,
        "whole_document_returned": whole_document_returned,
        "done": done,
        "next_cursor": next_cursor or None,
        "cursor_present": bool(cursor),
        "token_count_source": "tiktoken" if token_ids is not None else "character_estimate",
        "token_count_confidence": "fallback" if token_ids is not None else "approximate",
    }
