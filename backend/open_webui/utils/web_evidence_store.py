import asyncio
import hashlib
import json
import logging
import math
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

WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY = "legacy_store_retrieval"
WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED = "segmented_confidence_gated"
WEB_EVIDENCE_RETRIEVAL_MODE_VALUES = {
    WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY,
    WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
}

LARGE_ARTIFACT_CHARS_THRESHOLD = 8000
LARGE_ARTIFACT_CHUNK_CHARS = 1600
LARGE_ARTIFACT_CHUNK_OVERLAP = 240
MAX_CHUNK_CANDIDATES_PER_ARTIFACT = 4
SEGMENT_INDEX_VERSION = 1
STRUCTURE_CONFIDENCE_STRONG_FLOOR = 0.65
STRUCTURE_CONFIDENCE_WEAK_FLOOR = 0.4
FOCUS_WINNER_SCORE_FLOOR = 0.45
FOCUS_FILL_SCORE_FLOOR = 0.35
MAX_FOCUS_CLAUSES = 4
MAX_SEGMENT_CHARS = 1600
SEGMENT_DEDUPE_MAX_SPAN_CHARS = 2200
SEGMENT_DEDUPE_MAX_GAP_CHARS = 80
CONCEPT_ALIGNMENT_TABLE_NEIGHBORHOOD_CHARS = 2500
CONCEPT_ALIGNMENT_TABLE_BLOCK_RADIUS = 3
CONCEPT_ALIGNMENT_SEMANTIC_SHORTLIST = 8
CONCEPT_ALIGNMENT_AMBIGUOUS_SCORE_GAP = 0.08
CONCEPT_ALIGNMENT_ARTIFACT_SCOPE_MAX = 3

QUERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "in",
    "into",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "recent",
    "recently",
    "that",
    "the",
    "their",
    "there",
    "these",
    "this",
    "those",
    "to",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}
NUMERIC_VALUE_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?(?:%|/[0-9]+)?")
STAT_OPERATOR_RE = re.compile(r"(?:=|<=|>=|<|>|±|to)\s*[-+]?\d")
PARENTHETICAL_ALIAS_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9/\-, ]{3,120}?)\s*\(([A-Z][A-Z0-9-]{1,12})\)"
)
REVERSE_ALIAS_RE = re.compile(
    r"\b([A-Z][A-Z0-9-]{1,12})\s*\(([A-Za-z][A-Za-z0-9/\-, ]{3,120}?)\)"
)
SHORT_ALIAS_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,12}\b")
TABLE_LIKE_ROW_RE = re.compile(r"(?:\||\t| {2,})")
CAPTION_LINE_RE = re.compile(r"^\s*(?:table|figure|fig\.?)\b", re.IGNORECASE)
FOOTNOTE_LINE_RE = re.compile(r"^\s*(?:note|notes|abbreviations?)\b", re.IGNORECASE)
ALIGNMENT_STRENGTH_ORDER = {
    "none": 0,
    "weak": 1,
    "strong": 2,
    "exact": 3,
}

SINGLE_ARTIFACT_LOW_SIGNAL_TOKENS = {
    "adult",
    "adults",
    "article",
    "evidence",
    "glasses",
    "human",
    "meta",
    "meta-analysis",
    "paper",
    "review",
    "reviews",
    "source",
    "sources",
    "study",
    "studies",
    "systematic",
    "trial",
    "trials",
}
SINGLE_ARTIFACT_RESULT_INTENT_TOKENS = {
    "effect",
    "effects",
    "estimate",
    "estimates",
    "confidence",
    "interval",
    "intervals",
    "result",
    "results",
    "significant",
    "significance",
    "statistically",
    "pooled",
    "conclusion",
    "conclusions",
    "mean",
    "difference",
    "differences",
    "non-significant",
    "nonsignificant",
}
SINGLE_ARTIFACT_RESULT_EXPANSION_TERMS = [
    "results",
    "mean",
    "difference",
    "confidence",
    "interval",
    "significant",
    "conclusion",
]
RESULT_SECTION_POSITIVE_CUES = (
    "results",
    "pooled",
    "mean difference",
    "confidence interval",
    "not statistically significant",
    "statistically significant",
    "conclusion",
    "forest plot",
)
RESULT_SECTION_NEGATIVE_CUES = (
    "background",
    "objective",
    "materials and methods",
    "methods",
    "study design",
    "data sources",
    "searches were performed",
    "introduction",
    "keywords",
    "copyright notice",
)
RESULT_CLAUSE_COMPLETE_CUES = (
    "confidence interval",
    "mean difference",
    "odds ratio",
    "hazard ratio",
    "risk ratio",
    "effect size",
    "not statistically significant",
    "statistically significant",
    "low certainty",
    "limited evidence",
    "inconclusive",
    "conclusion",
)
RESULT_NUMERIC_SIGNAL_RE = re.compile(
    r"\b(?:md|rr|or|hr|ci|p)\b|\b\d+(?:\.\d+)?\b",
    re.IGNORECASE,
)
RESULT_PVALUE_RE = re.compile(
    r"\bp\s*(?:=|>=|>|≤|<=|<)\s*([0-9]*\.?[0-9]+)\b",
    re.IGNORECASE,
)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
EXPANDED_CONTEXT_TARGET_CHARS = 5800
EXPANDED_CONTEXT_BEFORE_CHARS = 2600
EXPANDED_CONTEXT_AFTER_CHARS = 3200
EXPANDED_CONTEXT_MAX_MERGE_GAP = 160


def normalize_web_evidence_retrieval_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in WEB_EVIDENCE_RETRIEVAL_MODE_VALUES:
        return normalized
    return WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY


def resolve_web_evidence_retrieval_mode(
    *, config_or_path: Any = None, metadata: Optional[dict[str, Any]] = None
) -> tuple[str, str]:
    default_mode = normalize_web_evidence_retrieval_mode(
        getattr(config_or_path, "WEB_EVIDENCE_RETRIEVAL_MODE", None)
        if config_or_path is not None
        else None
    )
    params = {}
    if isinstance(metadata, dict):
        params = metadata.get("params", {}) or {}
    override = params.get("web_evidence_retrieval_mode")
    if override is None and isinstance(params.get("custom_params"), dict):
        override = (params.get("custom_params") or {}).get("web_evidence_retrieval_mode")
    override_mode = normalize_web_evidence_retrieval_mode(override)
    if override in WEB_EVIDENCE_RETRIEVAL_MODE_VALUES:
        return override_mode, "chat_override"
    return default_mode, "global_default"


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _artifact_ids_look_like_search_source_keys(values: list[str]) -> bool:
    if not values:
        return False
    return all(str(value or "").strip().startswith("web:") for value in values)


def _not_found_scope_message(normalized_artifact_ids: list[str]) -> tuple[str, str]:
    if _artifact_ids_look_like_search_source_keys(normalized_artifact_ids):
        return (
            "Provided artifact_ids look like search/source owui keys, not stored web artifacts. "
            "Search snippets are excerpts; call fetch_url(url, mode=\"store\") first, then call "
            "query_web_evidence with the returned artifact_id or omit artifact_ids for the current turn.",
            "fetch_store_then_query",
        )
    return "No local web evidence index found.", "fetch_more"


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifact_segment_meta (
            artifact_id TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            segment_index_version INTEGER NOT NULL,
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
        CREATE INDEX IF NOT EXISTS idx_web_artifact_segment_meta_chat_message
        ON web_artifact_segment_meta(chat_id, message_id, indexed_at ASC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_artifact_segments (
            segment_id TEXT PRIMARY KEY,
            artifact_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            message_id TEXT,
            segment_index_version INTEGER NOT NULL,
            segmentation_mode TEXT NOT NULL,
            structure_confidence REAL NOT NULL,
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
        ON web_artifact_segments(artifact_id, start_offset ASC, end_offset ASC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_web_artifact_segments_chat_message
        ON web_artifact_segments(chat_id, message_id, artifact_id, start_offset ASC)
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
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS web_artifact_segments_fts
            USING fts5(
                segment_id UNINDEXED,
                label,
                path_text,
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


def _read_artifact_text(path: str) -> str:
    try:
        return Path(str(path or "")).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _normalize_numeric_text(value: Any) -> str:
    return (
        str(value or "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace(" ", " ")
    )


def _normalize_text_span(value: Any) -> str:
    return re.sub(r"\s+", " ", _normalize_numeric_text(value)).strip().lower()


def _content_query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9-]{2,}", str(query or ""))
    filtered: list[str] = []
    for token in tokens:
        normalized = token.strip().lower()
        if not normalized or normalized in QUERY_STOPWORDS:
            continue
        filtered.append(normalized)
    return filtered


def _build_query_concepts(query: str) -> tuple[list[str], list[str], list[str]]:
    tokens = _content_query_tokens(query)
    if not tokens:
        return [], [], []

    modifiers: list[str] = []
    single_tokens = _dedupe_terms_preserve_order(tokens, limit=16)
    concepts: list[str] = []

    max_span = min(4, len(tokens))
    for span_len in range(max_span, 1, -1):
        for idx in range(0, len(tokens) - span_len + 1):
            concepts.append(" ".join(tokens[idx : idx + span_len]))

    raw_abbreviations = [
        match.group(0)
        for match in re.finditer(r"\b[A-Z][A-Z0-9-]{1,12}\b", str(query or ""))
    ]
    concepts.extend(raw_abbreviations)
    target_concepts = _dedupe_terms_preserve_order([_normalize_text_span(item) for item in concepts], limit=8)

    if not target_concepts:
        target_concepts = single_tokens[:4]

    if len(single_tokens) > len(target_concepts):
        concept_tokens = {
            token
            for concept in target_concepts
            for token in re.findall(r"[a-z0-9-]{2,}", concept)
        }
        modifiers = [token for token in single_tokens if token not in concept_tokens][:6]

    return target_concepts, modifiers, single_tokens


def _build_concept_first_query_profile(
    *,
    query: str,
    scope_rows: list[Any],
) -> dict[str, Any]:
    target_concepts, query_modifiers, single_tokens = _build_query_concepts(query)
    normalized_terms = single_tokens[:]
    if not normalized_terms and target_concepts:
        normalized_terms = _dedupe_terms_preserve_order(
            [
                token
                for concept in target_concepts
                for token in re.findall(r"[a-z0-9-]{2,}", concept)
            ],
            limit=16,
        )
    return {
        "normalized_query": str(query or "").strip(),
        "normalized_terms": normalized_terms,
        "target_concepts": target_concepts,
        "query_modifiers": query_modifiers,
        "single_artifact_scope": len(scope_rows) == 1,
        "title_overlap_dropped": 0,
        "compaction_applied": False,
        "serving_logic": "concept",
    }


def _token_overlap_ratio(left: str, right: str) -> float:
    left_tokens = {
        token
        for token in re.findall(r"[a-z0-9-]{2,}", _normalize_text_span(left))
        if token not in QUERY_STOPWORDS
    }
    right_tokens = {
        token
        for token in re.findall(r"[a-z0-9-]{2,}", _normalize_text_span(right))
        if token not in QUERY_STOPWORDS
    }
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens)))


def _concept_matches_target(canonical_concept: str, target_concepts: list[str]) -> bool:
    normalized = _normalize_text_span(canonical_concept)
    if not normalized or not target_concepts:
        return False
    for target in target_concepts:
        normalized_target = _normalize_text_span(target)
        if not normalized_target:
            continue
        if normalized == normalized_target:
            return True
        if normalized in normalized_target or normalized_target in normalized:
            return True
        if _token_overlap_ratio(normalized, normalized_target) >= 0.67:
            return True
    return False


def _extract_alias_registry(text: str) -> dict[str, dict[str, Any]]:
    normalized_text = _normalize_numeric_text(text)
    alias_to_canonicals: dict[str, set[str]] = {}
    alias_sources: dict[tuple[str, str], str] = {}

    for match in PARENTHETICAL_ALIAS_RE.finditer(normalized_text):
        canonical = _normalize_text_span(match.group(1))
        alias = _normalize_text_span(match.group(2))
        if not canonical or not alias:
            continue
        alias_to_canonicals.setdefault(alias, set()).add(canonical)
        alias_sources[(alias, canonical)] = "explicit_definition"

    for match in REVERSE_ALIAS_RE.finditer(normalized_text):
        alias = _normalize_text_span(match.group(1))
        canonical = _normalize_text_span(match.group(2))
        if not canonical or not alias:
            continue
        alias_to_canonicals.setdefault(alias, set()).add(canonical)
        alias_sources[(alias, canonical)] = "explicit_definition"

    registry: dict[str, dict[str, Any]] = {}
    for alias, canonicals in alias_to_canonicals.items():
        candidates = sorted(canonical for canonical in canonicals if canonical)
        ambiguous = len(candidates) != 1
        canonical = candidates[0] if candidates else ""
        registry[alias] = {
            "alias_text": alias,
            "canonical_concept": canonical,
            "evidence_type": "explicit_definition",
            "confidence_tier": "high" if not ambiguous else "low",
            "ambiguous": ambiguous,
            "candidate_canonicals": candidates,
        }
    return registry


def _extract_medium_confidence_aliases(
    *,
    text: str,
    target_concepts: list[str],
    existing_registry: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    registry = dict(existing_registry)
    if not target_concepts:
        return registry

    lowered = _normalize_numeric_text(text)
    for alias_match in SHORT_ALIAS_TOKEN_RE.finditer(lowered):
        alias = _normalize_text_span(alias_match.group(0))
        if not alias or alias in registry:
            continue
        concept_hits: list[str] = []
        left = max(0, alias_match.start() - 160)
        right = min(len(lowered), alias_match.end() + 160)
        window = lowered[left:right]
        for concept in target_concepts:
            if concept and concept in _normalize_text_span(window):
                concept_hits.append(concept)
        concept_hits = _dedupe_terms_preserve_order(concept_hits, limit=4)
        if len(concept_hits) != 1:
            continue
        registry[alias] = {
            "alias_text": alias,
            "canonical_concept": concept_hits[0],
            "evidence_type": "repeated_local_cooccurrence",
            "confidence_tier": "medium",
            "ambiguous": False,
            "candidate_canonicals": concept_hits,
        }
    return registry


def _alias_confidence_summary(alias_registry: dict[str, dict[str, Any]]) -> dict[str, int]:
    summary = {"high": 0, "medium": 0, "low": 0, "ambiguous": 0}
    for alias in alias_registry.values():
        if alias.get("ambiguous"):
            summary["ambiguous"] += 1
        tier = str(alias.get("confidence_tier") or "").lower()
        if tier in summary:
            summary[tier] += 1
    return summary


def _looks_result_like(text: str) -> bool:
    lowered = _normalize_numeric_text(text).lower()
    if not lowered:
        return False
    if any(cue in lowered for cue in RESULT_CLAUSE_COMPLETE_CUES):
        return True
    return bool(RESULT_PVALUE_RE.search(lowered))


def _result_clause_complete(text: str) -> bool:
    normalized = _normalize_numeric_text(text)
    if not normalized:
        return False
    for sentence in SENTENCE_SPLIT_RE.split(normalized):
        stripped = sentence.strip()
        lowered = stripped.lower()
        if len(stripped) < 24:
            continue
        if not RESULT_NUMERIC_SIGNAL_RE.search(stripped):
            continue
        if not any(cue in lowered for cue in RESULT_CLAUSE_COMPLETE_CUES) and not RESULT_PVALUE_RE.search(
            lowered
        ):
            continue
        if stripped.endswith((".", ";", "!", "?")):
            return True
        if "confidence interval" in lowered or RESULT_PVALUE_RE.search(lowered):
            return True
    return False


def _split_sentences_with_offsets(text: str) -> list[dict[str, Any]]:
    normalized = _normalize_numeric_text(text)
    if not normalized:
        return []
    spans: list[dict[str, Any]] = []
    start = 0
    for match in SENTENCE_SPLIT_RE.finditer(normalized):
        end = match.start()
        sentence = normalized[start:end].strip()
        if sentence:
            spans.append({"text": sentence, "start": start, "end": end})
        start = match.end()
    tail = normalized[start:].strip()
    if tail:
        spans.append({"text": tail, "start": start, "end": len(normalized)})
    return spans


def _has_numeric_clause(text: str) -> bool:
    normalized = _normalize_numeric_text(text)
    if not normalized:
        return False
    numeric_hits = len(NUMERIC_VALUE_RE.findall(normalized))
    if numeric_hits >= 2:
        return True
    return bool(STAT_OPERATOR_RE.search(normalized))


def _concept_exact_in_text(text: str, target_concepts: list[str]) -> Optional[str]:
    normalized = _normalize_text_span(text)
    if not normalized:
        return None
    for concept in target_concepts:
        normalized_concept = _normalize_text_span(concept)
        if normalized_concept and normalized_concept in normalized:
            return normalized_concept
    return None


def _alias_hits_in_text(
    text: str,
    alias_registry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    lowered = _normalize_text_span(text)
    if not lowered or not alias_registry:
        return []
    hits: list[dict[str, Any]] = []
    for alias, meta in alias_registry.items():
        if alias and re.search(rf"\b{re.escape(alias)}\b", lowered):
            hits.append(meta)
    return hits


def _table_like_lines(text: str) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    cursor = 0
    for raw_line in _normalize_numeric_text(text).splitlines(keepends=True):
        end = cursor + len(raw_line)
        stripped = raw_line.strip()
        if stripped:
            lines.append(
                {
                    "text": stripped,
                    "start": cursor,
                    "end": end,
                    "table_like": bool(TABLE_LIKE_ROW_RE.search(raw_line) or len(NUMERIC_VALUE_RE.findall(raw_line)) >= 2),
                    "caption_like": bool(CAPTION_LINE_RE.match(stripped)),
                    "footnote_like": bool(FOOTNOTE_LINE_RE.match(stripped)),
                    "numeric": _has_numeric_clause(stripped),
                }
            )
        cursor = end
    return lines


def _line_alignment_from_neighborhood(
    *,
    line_index: int,
    lines: list[dict[str, Any]],
    target_concepts: list[str],
    alias_registry: dict[str, dict[str, Any]],
) -> tuple[str, str, str, list[str], bool]:
    line = lines[line_index]
    reasons: list[str] = []
    exact_concept = _concept_exact_in_text(line["text"], target_concepts)
    if exact_concept:
        reasons.append("query_term_same_line")
        return "exact", "same_sentence", "query_exact_term", reasons, False

    alias_hits = _alias_hits_in_text(line["text"], alias_registry)
    for meta in alias_hits:
        if meta.get("ambiguous"):
            reasons.append("alias_ambiguous_same_line")
            continue
        if _concept_matches_target(str(meta.get("canonical_concept") or ""), target_concepts):
            tier = str(meta.get("confidence_tier") or "")
            reasons.append(f"{tier}_alias_same_line")
            if tier == "high":
                return "exact", "same_sentence", "high_confidence_alias", reasons, False
            if tier == "medium":
                return "weak", "same_segment", "medium_confidence_alias", reasons, False

    for radius in range(1, 3):
        for neighbor_idx in (line_index - radius, line_index + radius):
            if neighbor_idx < 0 or neighbor_idx >= len(lines):
                continue
            neighbor = lines[neighbor_idx]
            exact_concept = _concept_exact_in_text(neighbor["text"], target_concepts)
            if exact_concept:
                reasons.append("query_term_adjacent_line")
                if neighbor.get("caption_like"):
                    return "strong", "table_caption_row", "query_exact_term", reasons, False
                if neighbor.get("footnote_like"):
                    return "weak", "table_footnote_row", "query_exact_term", reasons, False
                return "strong", "adjacent_sentence", "query_exact_term", reasons, False
            alias_hits = _alias_hits_in_text(neighbor["text"], alias_registry)
            for meta in alias_hits:
                if meta.get("ambiguous"):
                    reasons.append("alias_ambiguous_adjacent_line")
                    continue
                if _concept_matches_target(str(meta.get("canonical_concept") or ""), target_concepts):
                    tier = str(meta.get("confidence_tier") or "")
                    reasons.append(f"{tier}_alias_adjacent_line")
                    if tier == "high":
                        evidence = "table_caption_row" if neighbor.get("caption_like") else "adjacent_sentence"
                        return "strong", evidence, "high_confidence_alias", reasons, False
                    if tier == "medium":
                        return "weak", "same_segment", "medium_confidence_alias", reasons, False

    sibling_conflict = False
    for meta in alias_hits:
        canonical = str(meta.get("canonical_concept") or "")
        if meta.get("ambiguous"):
            sibling_conflict = True
            reasons.append("alias_ambiguous")
            continue
        if not _concept_matches_target(canonical, target_concepts):
            sibling_conflict = True
            reasons.append("sibling_alias_same_line")
    return "none", "none", "none", reasons, sibling_conflict


def _same_segment_alignment(
    *,
    text: str,
    target_concepts: list[str],
    alias_registry: dict[str, dict[str, Any]],
) -> tuple[str, str, str, list[str], bool]:
    reasons: list[str] = []
    if _concept_exact_in_text(text, target_concepts):
        reasons.append("query_term_same_segment")
        return "weak", "same_segment", "query_exact_term", reasons, False
    alias_hits = _alias_hits_in_text(text, alias_registry)
    sibling_conflict = False
    for meta in alias_hits:
        canonical = str(meta.get("canonical_concept") or "")
        tier = str(meta.get("confidence_tier") or "")
        if meta.get("ambiguous"):
            sibling_conflict = True
            reasons.append("alias_ambiguous_same_segment")
            continue
        if _concept_matches_target(canonical, target_concepts):
            reasons.append(f"{tier}_alias_same_segment")
            if tier == "medium":
                return "weak", "same_segment", "medium_confidence_alias", reasons, False
        else:
            sibling_conflict = True
            reasons.append("sibling_alias_same_segment")
    return "none", "none", "none", reasons, sibling_conflict


def _concept_alignment_for_text(
    *,
    text: str,
    target_concepts: list[str],
    alias_registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    lines = _table_like_lines(text)
    best = {
        "concept_aligned": False,
        "alignment_strength": "none",
        "alignment_evidence": "none",
        "alignment_concept_source": "none",
        "alignment_reason_codes": [],
        "adjacent_outcome_conflict": False,
        "has_numeric_clause": False,
    }

    for idx, line in enumerate(lines):
        if not line.get("numeric"):
            continue
        strength, evidence, source, reasons, sibling_conflict = _line_alignment_from_neighborhood(
            line_index=idx,
            lines=lines,
            target_concepts=target_concepts,
            alias_registry=alias_registry,
        )
        candidate = {
            "concept_aligned": strength in {"exact", "strong", "weak"},
            "alignment_strength": strength,
            "alignment_evidence": evidence,
            "alignment_concept_source": source,
            "alignment_reason_codes": reasons[:6],
            "adjacent_outcome_conflict": sibling_conflict,
            "has_numeric_clause": True,
        }
        if ALIGNMENT_STRENGTH_ORDER.get(candidate["alignment_strength"], 0) > ALIGNMENT_STRENGTH_ORDER.get(
            best["alignment_strength"], 0
        ):
            best = candidate

    if best["alignment_strength"] != "none":
        return best

    strength, evidence, source, reasons, sibling_conflict = _same_segment_alignment(
        text=text,
        target_concepts=target_concepts,
        alias_registry=alias_registry,
    )
    return {
        "concept_aligned": strength in {"exact", "strong", "weak"},
        "alignment_strength": strength,
        "alignment_evidence": evidence,
        "alignment_concept_source": source,
        "alignment_reason_codes": reasons[:6],
        "adjacent_outcome_conflict": sibling_conflict,
        "has_numeric_clause": any(line.get("numeric") for line in lines),
    }


def _concept_result_clause_complete(text: str) -> bool:
    for sentence in _split_sentences_with_offsets(text):
        normalized = _normalize_numeric_text(sentence.get("text") or "")
        if not normalized or len(normalized) < 18:
            continue
        if not _has_numeric_clause(normalized):
            continue
        if normalized.rstrip().endswith((".", ";", "!", "?")):
            return True
    for line in _table_like_lines(text):
        if line.get("numeric") and line.get("table_like"):
            return True
    return False


def _align_to_sentence_boundary(
    text: str,
    index: int,
    *,
    direction: str,
    max_adjust_chars: int = 160,
) -> int:
    bounded = max(0, min(len(text), int(index or 0)))
    if direction == "backward":
        cursor = bounded
        remaining = max_adjust_chars
        while cursor > 0 and remaining > 0 and text[cursor - 1] not in {".", "!", "?", "\n"}:
            cursor -= 1
            remaining -= 1
        return cursor
    cursor = bounded
    remaining = max_adjust_chars
    while (
        cursor < len(text)
        and remaining > 0
        and text[cursor - 1 if cursor > 0 else 0] not in {".", "!", "?", "\n"}
    ):
        cursor += 1
        remaining -= 1
    return min(len(text), cursor)


def _expand_context_window(
    content: str,
    *,
    start: int,
    end: int,
    anchor_index: Optional[int] = None,
    target_chars: int = EXPANDED_CONTEXT_TARGET_CHARS,
    before_chars: int = EXPANDED_CONTEXT_BEFORE_CHARS,
    after_chars: int = EXPANDED_CONTEXT_AFTER_CHARS,
) -> tuple[int, int, str]:
    text = str(content or "")
    if not text:
        return 0, 0, ""
    bounded_start = max(0, min(len(text), int(start or 0)))
    bounded_end = max(bounded_start, min(len(text), int(end or 0)))
    anchor = max(
        bounded_start,
        min(len(text), int(anchor_index if anchor_index is not None else bounded_start)),
    )

    anchor_start = max(0, anchor - before_chars)
    anchor_end = min(len(text), max(bounded_end, anchor) + after_chars)
    span = anchor_end - anchor_start
    if span < target_chars:
        remaining = target_chars - span
        grow_before = min(anchor_start, min(remaining // 6, 80))
        grow_after = min(len(text) - anchor_end, remaining - grow_before)
        anchor_start -= grow_before
        anchor_end += grow_after

    aligned_start = _align_to_sentence_boundary(text, anchor_start, direction="backward")
    aligned_end = _align_to_sentence_boundary(text, anchor_end, direction="forward")
    if aligned_end <= aligned_start:
        aligned_start = anchor_start
        aligned_end = anchor_end
    return aligned_start, aligned_end, text[aligned_start:aligned_end].strip()


def _snippet_should_expand(
    snippet: dict[str, Any],
    *,
    query_profile: Optional[dict[str, Any]] = None,
) -> bool:
    text = str(snippet.get("text") or "")
    return bool(text)


def _merge_expanded_snippets(
    snippets: list[dict[str, Any]],
    *,
    text_cache: dict[str, str],
) -> list[dict[str, Any]]:
    if len(snippets) <= 1:
        return snippets

    merged: list[dict[str, Any]] = []
    snippets_sorted = sorted(
        snippets,
        key=lambda item: (
            str(item.get("artifact_id") or ""),
            int(item.get("start") or 0),
            int(item.get("end") or 0),
        ),
    )
    current: Optional[dict[str, Any]] = None
    for snippet in snippets_sorted:
        if current is None:
            current = dict(snippet)
            continue
        same_artifact = str(current.get("artifact_id") or "") == str(snippet.get("artifact_id") or "")
        overlaps = int(snippet.get("start") or 0) <= int(current.get("end") or 0) + EXPANDED_CONTEXT_MAX_MERGE_GAP
        if (
            same_artifact
            and overlaps
            and current.get("expanded_context_applied")
            and snippet.get("expanded_context_applied")
        ):
            path = str(current.get("path") or snippet.get("path") or "")
            content = text_cache.get(path) if path else ""
            if content:
                start = min(int(current.get("start") or 0), int(snippet.get("start") or 0))
                end = max(int(current.get("end") or 0), int(snippet.get("end") or 0))
                current["start"] = start
                current["end"] = end
                current["text"] = content[start:end].strip()
                current["effective_window_chars"] = end - start
            current["score"] = max(
                float(current.get("score", 0.0) or 0.0),
                float(snippet.get("score", 0.0) or 0.0),
            )
            current["expanded_context_applied"] = bool(
                current.get("expanded_context_applied") or snippet.get("expanded_context_applied")
            )
            current["snippet_truncated"] = bool(current.get("snippet_truncated") or snippet.get("snippet_truncated"))
            current["result_clause_complete"] = bool(
                current.get("result_clause_complete") or snippet.get("result_clause_complete")
            )
            current["truncation_trust_hint"] = bool(
                current.get("truncation_trust_hint") or snippet.get("truncation_trust_hint")
            )
            continue
        merged.append(current)
        current = dict(snippet)

    if current is not None:
        merged.append(current)
    merged.sort(
        key=lambda item: (
            -float(item.get("score", 0.0) or 0.0),
            str(item.get("artifact_id") or ""),
            int(item.get("start", 0) or 0),
        )
    )
    return merged


def _annotate_and_expand_snippets(
    snippets: list[dict[str, Any]],
    *,
    scope_artifact_count: int,
    query_profile: Optional[dict[str, Any]] = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not snippets:
        return [], {"expanded_context_count": 0, "truncation_trust_hits": 0}

    tight_scope = int(scope_artifact_count or 0) > 0 and int(scope_artifact_count or 0) <= 2
    text_cache: dict[str, str] = {}
    expanded_context_count = 0
    truncation_trust_hits = 0
    expansions_by_artifact: dict[str, int] = {}
    annotated: list[dict[str, Any]] = []

    for snippet in snippets:
        updated = dict(snippet)
        path = str(updated.get("path") or "")
        artifact_id = str(updated.get("artifact_id") or "")
        content = text_cache.get(path)
        if content is None and path:
            content = _read_artifact_text(path)
            text_cache[path] = content
        content = content or str(updated.get("text") or "")

        raw_start = int(updated.get("start") or 0)
        raw_end = int(updated.get("end") or 0)
        raw_text = str(updated.get("text") or "")
        updated["snippet_truncated"] = bool(raw_start > 0 or raw_end < len(content))
        updated["result_clause_complete"] = _result_clause_complete(raw_text)
        updated["truncation_trust_hint"] = bool(
            updated["result_clause_complete"] and updated["snippet_truncated"]
        )
        updated["expanded_context_applied"] = False
        updated["effective_window_chars"] = max(0, raw_end - raw_start)
        updated["expansion_anchor_start"] = int(updated.get("hit_index") or raw_start)

        should_expand = (
            tight_scope
            and expansions_by_artifact.get(artifact_id, 0) <= 0
            and _snippet_should_expand(updated, query_profile=query_profile)
        )
        if content and should_expand:
            new_start, new_end, expanded_text = _expand_context_window(
                content,
                start=raw_start,
                end=raw_end,
                anchor_index=int(updated.get("hit_index") or raw_start),
            )
            if expanded_text and (new_start != raw_start or new_end != raw_end):
                updated["start"] = new_start
                updated["end"] = new_end
                updated["text"] = expanded_text
                updated["expanded_context_applied"] = True
                updated["effective_window_chars"] = new_end - new_start
                updated["snippet_truncated"] = bool(new_start > 0 or new_end < len(content))
                updated["result_clause_complete"] = _result_clause_complete(expanded_text)
                updated["truncation_trust_hint"] = bool(
                    updated["result_clause_complete"] and updated["snippet_truncated"]
                )
                expanded_context_count += 1
                expansions_by_artifact[artifact_id] = expansions_by_artifact.get(artifact_id, 0) + 1

        if updated["truncation_trust_hint"]:
            truncation_trust_hits += 1
        annotated.append(updated)

    merged = _merge_expanded_snippets(annotated, text_cache=text_cache)
    return merged, {
        "expanded_context_count": expanded_context_count,
        "truncation_trust_hits": truncation_trust_hits,
    }


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, sqlite3.Row):
        return row[key] if key in row.keys() else default
    if isinstance(row, dict):
        return row.get(key, default)
    return default


def _build_artifact_alignment_contexts(
    *,
    scope_rows: list[Any],
    query_profile: Optional[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    target_concepts = list((query_profile or {}).get("target_concepts") or [])
    contexts: dict[str, dict[str, Any]] = {}
    for row in scope_rows:
        artifact_id = str(_row_value(row, "artifact_id", "") or "")
        if not artifact_id:
            continue
        path = str(_row_value(row, "path", "") or "")
        content = _read_artifact_text(path)
        alias_registry = _extract_alias_registry(content)
        alias_registry = _extract_medium_confidence_aliases(
            text=content,
            target_concepts=target_concepts,
            existing_registry=alias_registry,
        )
        contexts[artifact_id] = {
            "path": path,
            "content": content,
            "alias_registry": alias_registry,
            "alias_confidence_summary": _alias_confidence_summary(alias_registry),
        }
    return contexts


def _concept_snippet_sort_key(snippet: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -ALIGNMENT_STRENGTH_ORDER.get(str(snippet.get("alignment_strength") or "none"), 0),
        -int(_normalize_bool(snippet.get("result_clause_complete"))),
        -int(not _normalize_bool(snippet.get("adjacent_outcome_conflict"))),
        -float(snippet.get("semantic_score", 0.0) or 0.0),
        -float(snippet.get("score", 0.0) or 0.0),
        -float(snippet.get("structure_confidence", 0.0) or 0.0),
        str(snippet.get("artifact_id", "") or ""),
        int(snippet.get("start", 0) or 0),
    )


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _run_async_sync(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
        return None
    except RuntimeError:
        return asyncio.run(coro)


def _semantic_rerank_concept_snippets(
    *,
    snippets: list[dict[str, Any]],
    query_profile: Optional[dict[str, Any]],
    embedding_function: Any = None,
) -> tuple[list[dict[str, Any]], bool, int]:
    if not embedding_function or len(snippets) < 2:
        return snippets, False, 0

    shortlist = snippets[: min(CONCEPT_ALIGNMENT_SEMANTIC_SHORTLIST, len(snippets))]
    top_gap = abs(float(shortlist[0].get("score", 0.0) or 0.0) - float(shortlist[1].get("score", 0.0) or 0.0))
    if (
        ALIGNMENT_STRENGTH_ORDER.get(str(shortlist[0].get("alignment_strength") or "none"), 0) >= ALIGNMENT_STRENGTH_ORDER["strong"]
        or top_gap > CONCEPT_ALIGNMENT_AMBIGUOUS_SCORE_GAP
    ):
        return snippets, False, 0

    query_text = " ".join((query_profile or {}).get("target_concepts") or []) or str(
        (query_profile or {}).get("normalized_query") or ""
    )
    embeddings = _run_async_sync(
        embedding_function([query_text, *[str(snippet.get("text") or "") for snippet in shortlist]])
    )
    if not isinstance(embeddings, list) or len(embeddings) != len(shortlist) + 1:
        return snippets, False, 0

    query_embedding = embeddings[0]
    reranked: list[dict[str, Any]] = []
    for snippet, embedding in zip(shortlist, embeddings[1:]):
        updated = dict(snippet)
        try:
            updated["semantic_score"] = round(
                _cosine_similarity(list(query_embedding), list(embedding)),
                4,
            )
        except Exception:
            updated["semantic_score"] = 0.0
        reranked.append(updated)
    reranked.sort(key=_concept_snippet_sort_key)
    return [*reranked, *snippets[len(shortlist) :]], True, len(shortlist)


def _concept_should_expand(
    snippet: dict[str, Any],
) -> bool:
    return bool(str(snippet.get("text") or ""))


def _annotate_and_expand_snippets_concept(
    snippets: list[dict[str, Any]],
    *,
    scope_artifact_count: int,
    query_profile: Optional[dict[str, Any]],
    artifact_contexts: dict[str, dict[str, Any]],
    embedding_function: Any = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not snippets:
        return [], {
            "expanded_context_count": 0,
            "truncation_trust_hits": 0,
            "concept_aligned_trust_hits": 0,
            "adjacent_outcome_conflict_count": 0,
            "semantic_rerank_used": False,
            "semantic_rerank_candidate_count": 0,
            "alias_confidence_summary": {"high": 0, "medium": 0, "low": 0, "ambiguous": 0},
        }

    tight_scope = int(scope_artifact_count or 0) > 0 and int(scope_artifact_count or 0) <= CONCEPT_ALIGNMENT_ARTIFACT_SCOPE_MAX
    text_cache: dict[str, str] = {
        str(context.get("path") or ""): str(context.get("content") or "")
        for context in artifact_contexts.values()
        if str(context.get("path") or "")
    }
    expansions_by_artifact: dict[str, int] = {}
    expanded_context_count = 0
    annotated: list[dict[str, Any]] = []
    alias_summary = {"high": 0, "medium": 0, "low": 0, "ambiguous": 0}
    for context in artifact_contexts.values():
        for key, value in dict(context.get("alias_confidence_summary") or {}).items():
            if key in alias_summary:
                alias_summary[key] += int(value or 0)

    for snippet in snippets:
        updated = dict(snippet)
        artifact_id = str(updated.get("artifact_id") or "")
        context = artifact_contexts.get(artifact_id) or {}
        alias_registry = dict(context.get("alias_registry") or {})
        path = str(updated.get("path") or context.get("path") or "")
        content = text_cache.get(path)
        if content is None and path:
            content = _read_artifact_text(path)
            text_cache[path] = content
        content = content or str(updated.get("text") or "")

        raw_start = int(updated.get("start") or 0)
        raw_end = int(updated.get("end") or 0)
        raw_text = str(updated.get("text") or "")
        updated["snippet_truncated"] = bool(raw_start > 0 or raw_end < len(content))
        alignment = _concept_alignment_for_text(
            text=raw_text,
            target_concepts=list((query_profile or {}).get("target_concepts") or []),
            alias_registry=alias_registry,
        )
        updated.update(alignment)
        updated["result_clause_complete"] = _concept_result_clause_complete(raw_text)
        updated["expanded_context_applied"] = False
        updated["effective_window_chars"] = max(0, raw_end - raw_start)
        updated["expansion_anchor_start"] = int(updated.get("hit_index") or raw_start)
        updated["truncation_trust_hint"] = bool(
            updated["snippet_truncated"]
            and updated["result_clause_complete"]
            and str(updated.get("alignment_strength") or "none") in {"exact", "strong"}
        )

        should_expand = (
            tight_scope
            and expansions_by_artifact.get(artifact_id, 0) <= 0
            and _concept_should_expand(updated)
        )
        if content and should_expand:
            new_start, new_end, expanded_text = _expand_context_window(
                content,
                start=raw_start,
                end=raw_end,
                anchor_index=int(updated.get("hit_index") or raw_start),
            )
            if expanded_text and (new_start != raw_start or new_end != raw_end):
                updated["start"] = new_start
                updated["end"] = new_end
                updated["text"] = expanded_text
                updated["expanded_context_applied"] = True
                updated["effective_window_chars"] = new_end - new_start
                updated["snippet_truncated"] = bool(new_start > 0 or new_end < len(content))
                updated.update(
                    _concept_alignment_for_text(
                        text=expanded_text,
                        target_concepts=list((query_profile or {}).get("target_concepts") or []),
                        alias_registry=alias_registry,
                    )
                )
                updated["result_clause_complete"] = _concept_result_clause_complete(expanded_text)
                updated["truncation_trust_hint"] = bool(
                    updated["snippet_truncated"]
                    and updated["result_clause_complete"]
                    and str(updated.get("alignment_strength") or "none") in {"exact", "strong"}
                )
                expanded_context_count += 1
                expansions_by_artifact[artifact_id] = expansions_by_artifact.get(artifact_id, 0) + 1

        annotated.append(updated)

    merged = _merge_expanded_snippets(annotated, text_cache=text_cache)
    reranked, semantic_rerank_used, semantic_rerank_candidate_count = _semantic_rerank_concept_snippets(
        snippets=merged,
        query_profile=query_profile,
        embedding_function=embedding_function,
    )
    reranked.sort(key=_concept_snippet_sort_key)
    truncation_trust_hits = sum(
        1 for snippet in reranked if _normalize_bool(snippet.get("truncation_trust_hint"))
    )
    concept_aligned_trust_hits = sum(
        1
        for snippet in reranked
        if str(snippet.get("alignment_strength") or "none") in {"exact", "strong"}
        and _normalize_bool(snippet.get("result_clause_complete"))
    )
    adjacent_outcome_conflict_count = sum(
        1 for snippet in reranked if _normalize_bool(snippet.get("adjacent_outcome_conflict"))
    )
    return reranked, {
        "expanded_context_count": expanded_context_count,
        "truncation_trust_hits": truncation_trust_hits,
        "concept_aligned_trust_hits": concept_aligned_trust_hits,
        "adjacent_outcome_conflict_count": adjacent_outcome_conflict_count,
        "semantic_rerank_used": semantic_rerank_used,
        "semantic_rerank_candidate_count": semantic_rerank_candidate_count,
        "alias_confidence_summary": alias_summary,
    }


def _classify_evidence_strength_concept(snippets: list[dict[str, Any]]) -> str:
    aligned = [
        snippet
        for snippet in snippets
        if str(snippet.get("alignment_strength") or "none") in {"exact", "strong"}
        and _normalize_bool(snippet.get("result_clause_complete"))
    ]
    if not aligned:
        return "weak"
    unique_domains = {
        str(snippet.get("domain") or "").strip().lower()
        for snippet in aligned
        if str(snippet.get("domain") or "").strip()
    }
    if len(aligned) >= 2 and len(unique_domains) >= 2:
        return "strong"
    return "adequate"


def _suggest_next_action_concept(
    *,
    searched_artifact_count: int,
    snippets: list[dict[str, Any]],
) -> str:
    if searched_artifact_count <= 0:
        return "fetch_more"
    if any(
        str(snippet.get("alignment_strength") or "none") in {"exact", "strong"}
        and _normalize_bool(snippet.get("result_clause_complete"))
        for snippet in snippets
    ):
        return "answer_with_current_evidence"
    return "refine_within_same_source"


def _build_concept_alignment_shadow_diff(
    *,
    serving_payload: dict[str, Any],
    shadow_payload: Optional[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(shadow_payload, dict):
        return {"ran": False}

    serving_top = ((serving_payload.get("snippets") or [])[:1] or [{}])[0]
    shadow_top = ((shadow_payload.get("snippets") or [])[:1] or [{}])[0]
    serving_identity = (
        str(serving_top.get("artifact_id") or ""),
        int(serving_top.get("start", 0) or 0),
        int(serving_top.get("end", 0) or 0),
    )
    shadow_identity = (
        str(shadow_top.get("artifact_id") or ""),
        int(shadow_top.get("start", 0) or 0),
        int(shadow_top.get("end", 0) or 0),
    )
    return {
        "ran": True,
        "top_hit_changed": serving_identity != shadow_identity,
        "serving_top_hit": serving_identity,
        "shadow_top_hit": shadow_identity,
        "serving_next_action": serving_payload.get("suggested_next_action"),
        "shadow_next_action": shadow_payload.get("suggested_next_action"),
        "serving_trust_hits": int(serving_payload.get("concept_aligned_trust_hits") or serving_payload.get("truncation_trust_hits") or 0),
        "shadow_trust_hits": int(shadow_payload.get("concept_aligned_trust_hits") or shadow_payload.get("truncation_trust_hits") or 0),
        "serving_adjacent_outcome_conflicts": int(serving_payload.get("adjacent_outcome_conflict_count") or 0),
        "shadow_adjacent_outcome_conflicts": int(shadow_payload.get("adjacent_outcome_conflict_count") or 0),
    }


def _apply_agent_guidance_overlay(
    *,
    serving_payload: dict[str, Any],
    shadow_payload: Optional[dict[str, Any]],
    query_profile: Optional[dict[str, Any]],
) -> dict[str, Any]:
    payload = dict(serving_payload or {})
    target_outcomes = list((query_profile or {}).get("target_concepts") or [])
    serving_logic = str((query_profile or {}).get("serving_logic") or "")
    shadow_ran = isinstance(shadow_payload, dict)
    serving_has_alignment = any(
        isinstance(snippet, dict) and "alignment_strength" in snippet
        for snippet in (payload.get("snippets") or [])
    )
    if not shadow_ran and not serving_has_alignment:
        payload["target_outcomes"] = target_outcomes
        payload["exact_target_match_found"] = None
        payload["best_hit_is_adjacent_outcome"] = False
        payload["agent_guidance"] = (
            "use_current_evidence"
            if str(payload.get("suggested_next_action") or "") == "answer_with_current_evidence"
            else str(payload.get("suggested_next_action") or "refine_within_same_source")
        )
        payload["agent_guidance_reason_codes"] = []
        payload["serving_confidence_downgraded"] = False
        payload["serving_confidence_reason"] = []
        payload.setdefault("concept_alignment_shadow", {"ran": False})
        return payload
    diagnostic_payload = (
        payload
        if serving_logic == "concept" or serving_has_alignment
        else (shadow_payload if shadow_ran else payload)
    )
    diagnostic_snippets = list((diagnostic_payload or {}).get("snippets") or [])
    shadow_diff = (
        _build_concept_alignment_shadow_diff(
            serving_payload=serving_payload,
            shadow_payload=shadow_payload,
        )
        if shadow_ran
        else {"ran": False}
    )

    exact_target_match_found = any(
        str(snippet.get("alignment_strength") or "none") in {"exact", "strong"}
        and _normalize_bool(snippet.get("result_clause_complete"))
        for snippet in diagnostic_snippets
    )
    best_hit_is_adjacent_outcome = False
    if diagnostic_snippets:
        best_hit_is_adjacent_outcome = _normalize_bool(
            diagnostic_snippets[0].get("adjacent_outcome_conflict")
        )
    if not best_hit_is_adjacent_outcome and diagnostic_payload is not payload:
        best_hit_is_adjacent_outcome = bool(
            int((diagnostic_payload or {}).get("adjacent_outcome_conflict_count") or 0) > 0
        )

    guidance_reason_codes: list[str] = []
    if not exact_target_match_found:
        guidance_reason_codes.append("exact_target_result_not_found")
    if best_hit_is_adjacent_outcome:
        guidance_reason_codes.append("best_hit_adjacent_outcome_conflict")
    if serving_logic != "concept" and shadow_diff.get("top_hit_changed"):
        guidance_reason_codes.append("shadow_top_hit_disagreement")
    if (
        serving_logic != "concept"
        and shadow_diff.get("serving_next_action") != shadow_diff.get("shadow_next_action")
    ):
        guidance_reason_codes.append("shadow_action_disagreement")

    guidance = "use_current_evidence" if exact_target_match_found else "refine_within_same_source"
    downgraded = guidance != "use_current_evidence"

    payload["target_outcomes"] = target_outcomes
    payload["exact_target_match_found"] = exact_target_match_found
    payload["best_hit_is_adjacent_outcome"] = best_hit_is_adjacent_outcome
    payload["agent_guidance"] = guidance
    payload["agent_guidance_reason_codes"] = guidance_reason_codes[:6]
    payload["concept_alignment_shadow"] = shadow_diff

    if downgraded:
        payload["suggested_next_action"] = "refine_within_same_source"
        payload["truncation_trust_hits"] = 0
        payload["serving_confidence_downgraded"] = True
        payload["serving_confidence_reason"] = guidance_reason_codes[:6]
        downgraded_snippets: list[dict[str, Any]] = []
        for snippet in payload.get("snippets") or []:
            updated = dict(snippet)
            updated["truncation_trust_hint"] = False
            downgraded_snippets.append(updated)
        payload["snippets"] = downgraded_snippets
    else:
        payload["serving_confidence_downgraded"] = False
        payload["serving_confidence_reason"] = []
    return payload


def _iter_line_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    for raw_line in str(text or "").splitlines(keepends=True):
        line_end = offset + len(raw_line)
        records.append(
            {
                "raw": raw_line,
                "text": raw_line.rstrip("\r\n"),
                "stripped": raw_line.strip(),
                "start": offset,
                "end": line_end,
            }
        )
        offset = line_end
    if not records and text:
        records.append({"raw": text, "text": text, "stripped": text.strip(), "start": 0, "end": len(text)})
    return records


_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
_GENERIC_HEADING_RE = re.compile(
    r"^(chapter|part|section|article|appendix|glava|razdel|глава|раздел|чл\.|ал\.|т\.|§)\b",
    re.IGNORECASE,
)
_NUMBERED_HEADING_RE = re.compile(
    r"^(\d+(?:[.)]\d+)*[.)]?|[IVXLCM]+[.)])\s+\S+",
    re.IGNORECASE,
)


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
    if lowered.startswith(("section", "appendix", "раздел")):
        return 2
    if lowered.startswith(("article", "чл.", "§")):
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
    if not stripped:
        return False
    if len(stripped) > 160:
        return False
    if _MARKDOWN_HEADING_RE.match(stripped):
        return True
    if _GENERIC_HEADING_RE.match(stripped):
        return True
    if _NUMBERED_HEADING_RE.match(stripped):
        return True

    alpha_chars = re.sub(r"[^A-Za-zА-Яа-я]", "", stripped)
    word_count = len(stripped.split())
    if (
        len(alpha_chars) >= 4
        and word_count <= 12
        and stripped == stripped.upper()
        and any(char.isalpha() for char in stripped)
    ):
        return True
    return False


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
    for idx, chunk in enumerate(
        _build_large_artifact_chunks(
            text,
            chunk_chars=MAX_SEGMENT_CHARS,
            overlap_chars=LARGE_ARTIFACT_CHUNK_OVERLAP,
        )
    ):
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

    # Preserve a preamble block if the document starts with meaningful content.
    first_heading_start = int(heading_records[0]["start"])
    if first_heading_start > 240:
        preamble_text = text[:first_heading_start].strip()
        if preamble_text:
            for idx, chunk in enumerate(
                _build_large_artifact_chunks(
                    preamble_text,
                    chunk_chars=MAX_SEGMENT_CHARS,
                    overlap_chars=LARGE_ARTIFACT_CHUNK_OVERLAP,
                )
            ):
                segments.append(
                    {
                        "segment_id": f"{artifact_id}:preamble:{idx}",
                        "artifact_id": artifact_id,
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "label": "Preamble",
                        "path_text": "Preamble",
                        "start_offset": int(chunk.get("start") or 0),
                        "end_offset": int(chunk.get("end") or 0),
                        "segment_chars": len(str(chunk.get("text") or "")),
                        "content": str(chunk.get("text") or ""),
                    }
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
        for chunk_idx, chunk in enumerate(
            _build_large_artifact_chunks(
                section_text,
                chunk_chars=MAX_SEGMENT_CHARS,
                overlap_chars=LARGE_ARTIFACT_CHUNK_OVERLAP,
            )
        ):
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


def _rebuild_segment_index_for_artifact(
    conn: sqlite3.Connection,
    *,
    artifact_row: dict[str, Any],
    content: str,
    fts_enabled: bool,
) -> dict[str, Any]:
    artifact_id = str(artifact_row.get("artifact_id") or "")
    chat_id = str(artifact_row.get("chat_id") or "")
    message_id = str(artifact_row.get("message_id") or "")
    segmentation_mode, structure_confidence, segments, stats = _build_segment_index_for_content(
        artifact_id=artifact_id,
        chat_id=chat_id,
        message_id=message_id,
        content=content,
    )
    indexed_at = int(time.time())
    existing_segment_ids = [
        str(row["segment_id"] or "")
        for row in conn.execute(
            "SELECT segment_id FROM web_artifact_segments WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchall()
        if str(row["segment_id"] or "")
    ]
    conn.execute("DELETE FROM web_artifact_segment_meta WHERE artifact_id = ?", (artifact_id,))
    if fts_enabled:
        for segment_id in existing_segment_ids:
            conn.execute(
                "DELETE FROM web_artifact_segments_fts WHERE segment_id = ?",
                (segment_id,),
            )
    conn.execute("DELETE FROM web_artifact_segments WHERE artifact_id = ?", (artifact_id,))
    conn.execute(
        """
        INSERT OR REPLACE INTO web_artifact_segment_meta (
            artifact_id, chat_id, message_id, segment_index_version, segmentation_mode,
            structure_confidence, segment_count, segment_stats_json, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            artifact_id,
            chat_id,
            message_id,
            SEGMENT_INDEX_VERSION,
            segmentation_mode,
            float(structure_confidence),
            len(segments),
            json.dumps(stats, ensure_ascii=False),
            indexed_at,
        ),
    )
    for segment in segments:
        conn.execute(
            """
            INSERT OR REPLACE INTO web_artifact_segments (
                segment_id, artifact_id, chat_id, message_id, segment_index_version,
                segmentation_mode, structure_confidence, label, path_text,
                start_offset, end_offset, segment_chars, content
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                segment["segment_id"],
                artifact_id,
                chat_id,
                message_id,
                SEGMENT_INDEX_VERSION,
                segmentation_mode,
                float(structure_confidence),
                segment.get("label") or "",
                segment.get("path_text") or "",
                int(segment.get("start_offset") or 0),
                int(segment.get("end_offset") or 0),
                int(segment.get("segment_chars") or 0),
                segment.get("content") or "",
            ),
        )
        if fts_enabled:
            conn.execute(
                """
                INSERT OR REPLACE INTO web_artifact_segments_fts (
                    segment_id, label, path_text, content
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    segment["segment_id"],
                    segment.get("label") or "",
                    segment.get("path_text") or "",
                    segment.get("content") or "",
                ),
            )
    return {
        "artifact_id": artifact_id,
        "segmentation_mode": segmentation_mode,
        "structure_confidence": float(structure_confidence),
        "segment_count": len(segments),
        "segment_stats": stats,
    }


def _ensure_segment_indexes(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    artifact_rows: list[sqlite3.Row],
) -> list[dict[str, Any]]:
    fts_enabled = _init_db(conn)
    ensured: list[dict[str, Any]] = []
    for row in artifact_rows:
        artifact_id = str(row["artifact_id"] or "")
        meta_row = conn.execute(
            """
            SELECT artifact_id, segmentation_mode, structure_confidence, segment_count,
                   segment_stats_json, segment_index_version
            FROM web_artifact_segment_meta
            WHERE artifact_id = ?
            """,
            (artifact_id,),
        ).fetchone()
        if meta_row and int(meta_row["segment_index_version"] or 0) == SEGMENT_INDEX_VERSION:
            ensured.append(
                {
                    "artifact_id": artifact_id,
                    "segmentation_mode": str(meta_row["segmentation_mode"] or "chunk"),
                    "structure_confidence": float(meta_row["structure_confidence"] or 0.0),
                    "segment_count": int(meta_row["segment_count"] or 0),
                    "segment_stats": json.loads(meta_row["segment_stats_json"] or "{}"),
                }
            )
            continue

        content = _read_artifact_text(str(row["path"] or ""))
        ensured.append(
            _rebuild_segment_index_for_artifact(
                conn,
                artifact_row={
                    "artifact_id": artifact_id,
                    "chat_id": chat_id,
                    "message_id": str(row["message_id"] or ""),
                },
                content=content,
                fts_enabled=fts_enabled,
            )
        )
    conn.commit()
    return ensured


def store_web_page(
    *,
    chat_id: str,
    message_id: Optional[str],
    url: str,
    content: str,
    title: Optional[str] = None,
    retrieval_mode: str = WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY,
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
        segment_meta = None
        if normalize_web_evidence_retrieval_mode(retrieval_mode) == WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED:
            segment_meta = _rebuild_segment_index_for_artifact(
                conn,
                artifact_row={
                    "artifact_id": artifact_id,
                    "chat_id": normalized_chat_id,
                    "message_id": str(message_id or ""),
                },
                content=text,
                fts_enabled=fts_enabled,
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
        "segment_indexed": bool(segment_meta),
        "segmentation_mode": (segment_meta or {}).get("segmentation_mode"),
        "structure_confidence": (segment_meta or {}).get("structure_confidence"),
    }


def _fts_query_terms(query: str) -> list[str]:
    return [token for token in re.findall(r"[\w-]{2,}", (query or "").lower()) if token]


def _dedupe_terms_preserve_order(terms: list[str], *, limit: int = 12) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        normalized = str(term or "").strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def _single_artifact_query_profile(
    *,
    query: str,
    scope_rows: list[Any],
) -> dict[str, Any]:
    raw_terms = _fts_query_terms(query)
    profile = {
        "normalized_query": str(query or "").strip(),
        "normalized_terms": raw_terms,
        "result_intent": False,
        "single_artifact_scope": len(scope_rows) == 1,
        "title_overlap_dropped": 0,
        "compaction_applied": False,
    }
    if len(scope_rows) != 1 or len(raw_terms) < 4:
        return profile

    row = scope_rows[0]
    title = ""
    if isinstance(row, sqlite3.Row):
        title = str(row["title"] or "")
    elif isinstance(row, dict):
        title = str(row.get("title") or "")

    title_terms = set(_fts_query_terms(title))
    result_intent = any(term in SINGLE_ARTIFACT_RESULT_INTENT_TOKENS for term in raw_terms)

    compact_terms: list[str] = []
    dropped_overlap = 0
    for term in raw_terms:
        if term in SINGLE_ARTIFACT_LOW_SIGNAL_TOKENS:
            continue
        if term in title_terms and term not in SINGLE_ARTIFACT_RESULT_INTENT_TOKENS:
            dropped_overlap += 1
            continue
        compact_terms.append(term)

    if result_intent:
        compact_terms = [
            term
            for term in compact_terms
            if term not in SINGLE_ARTIFACT_RESULT_INTENT_TOKENS
        ]
        compact_terms.extend(SINGLE_ARTIFACT_RESULT_EXPANSION_TERMS)

    normalized_terms = _dedupe_terms_preserve_order(compact_terms)
    if len(normalized_terms) < 3:
        normalized_terms = raw_terms
        dropped_overlap = 0

    normalized_query = " ".join(normalized_terms).strip() or str(query or "").strip()
    profile["normalized_query"] = normalized_query
    profile["normalized_terms"] = normalized_terms
    profile["result_intent"] = bool(result_intent)
    profile["title_overlap_dropped"] = dropped_overlap
    profile["compaction_applied"] = normalized_query != str(query or "").strip()
    return profile


def _result_intent_section_bonus(text: str) -> float:
    lowered = str(text or "").lower()
    if not lowered:
        return 0.0

    bonus = 0.0
    positive_hits = sum(1 for cue in RESULT_SECTION_POSITIVE_CUES if cue in lowered)
    negative_hits = sum(1 for cue in RESULT_SECTION_NEGATIVE_CUES if cue in lowered)

    if positive_hits:
        bonus += min(0.22, 0.05 * positive_hits)
    if negative_hits:
        bonus -= min(0.18, 0.04 * negative_hits)
    return bonus


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


def _best_hit_index(
    content: str,
    terms: list[str],
    query_profile: Optional[dict[str, Any]] = None,
) -> int:
    normalized = _normalize_numeric_text(content).lower()
    if not normalized:
        return 0
    if str((query_profile or {}).get("serving_logic") or "") == "concept":
        for concept in (query_profile or {}).get("target_concepts") or []:
            idx = normalized.find(str(concept or "").lower())
            if idx >= 0:
                return idx
        numeric_match = NUMERIC_VALUE_RE.search(normalized)
        if numeric_match:
            return numeric_match.start()
        for term in terms:
            idx = normalized.find(str(term or "").lower())
            if idx >= 0:
                return idx
        return 0
    cue_positions: list[int] = []
    for cue in RESULT_CLAUSE_COMPLETE_CUES:
        idx = normalized.find(cue)
        if idx >= 0:
            cue_positions.append(idx)
    if cue_positions:
        return min(cue_positions)
    p_match = RESULT_PVALUE_RE.search(normalized)
    if p_match:
        return p_match.start()
    for term in terms:
        idx = normalized.find(str(term or "").lower())
        if idx >= 0:
            return idx
    return 0


def _align_chunk_end(content: str, end: int, max_forward: int = 120) -> int:
    bounded_end = min(len(content), end)
    remaining = max_forward
    while (
        bounded_end < len(content)
        and remaining > 0
        and content[bounded_end - 1] not in {"\n", " ", ".", "!", "?", ":", ";"}
    ):
        bounded_end += 1
        remaining -= 1
    return min(len(content), bounded_end)


def _align_chunk_start(content: str, start: int, min_start: int, max_backward: int = 80) -> int:
    bounded_start = max(min_start, start)
    remaining = max_backward
    while (
        bounded_start > min_start
        and remaining > 0
        and content[bounded_start - 1] not in {"\n", " "}
    ):
        bounded_start -= 1
        remaining -= 1
    return max(min_start, bounded_start)


def _build_large_artifact_chunks(
    content: str,
    *,
    chunk_chars: int = LARGE_ARTIFACT_CHUNK_CHARS,
    overlap_chars: int = LARGE_ARTIFACT_CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    text = str(content or "")
    if not text:
        return []

    if len(text) <= chunk_chars:
        return [
            {
                "chunk_index": 0,
                "chunk_count": 1,
                "start": 0,
                "end": len(text),
                "text": text,
            }
        ]

    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_chars)
        end = _align_chunk_end(text, end)
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    "chunk_index": len(chunks),
                    "start": start,
                    "end": end,
                    "text": chunk_text,
                }
            )
        if end >= len(text):
            break
        next_start = max(0, end - overlap_chars)
        next_start = _align_chunk_start(text, next_start, start + 1)
        if next_start <= start:
            next_start = end
        start = next_start

    chunk_count = len(chunks)
    for chunk in chunks:
        chunk["chunk_count"] = chunk_count
    return chunks


def _score_text_block(
    text: str,
    *,
    terms: list[str],
    rank_score: float,
    query_profile: Optional[dict[str, Any]] = None,
) -> tuple[int, int, float]:
    normalized = str(text or "").lower()
    if not normalized:
        return 0, 0, 0.0

    if not terms:
        return 0, 0, max(0.0, min(1.0, rank_score))

    unique_hits = 0
    total_hits = 0
    for term in terms:
        count = normalized.count(term.lower())
        if count > 0:
            unique_hits += 1
            total_hits += count

    if unique_hits <= 0:
        return 0, 0, 0.0

    coverage = unique_hits / max(1, len(terms))
    density = min(1.0, total_hits / max(1, len(terms) * 2))
    lexical_score = (0.75 * coverage) + (0.25 * density)
    blended = max(0.0, min(1.0, (0.8 * lexical_score) + (0.2 * rank_score)))
    if (
        isinstance(query_profile, dict)
        and query_profile.get("result_intent")
        and str(query_profile.get("serving_logic") or "legacy") == "legacy"
    ):
        bonus = _result_intent_section_bonus(text)
        if _result_clause_complete(text):
            bonus += 0.28
        blended = max(0.0, min(1.0, blended + bonus))
    return unique_hits, total_hits, round(blended, 4)


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


def _query_segment_rows(
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
        if terms:
            quoted_terms = [
                f'"{str(term).replace("\"", "").strip()}"'
                for term in terms
                if str(term).strip()
            ]
            # Segment retrieval is recall-oriented. Using OR here avoids
            # compound questions collapsing to zero hits because no single
            # segment contains every lexical term.
            match_query = " OR ".join(quoted_terms) if quoted_terms else "*"
        else:
            match_query = "*"
        params: list[Any] = [match_query, chat_id]
        sql_parts = [
            """
            SELECT
                s.segment_id,
                s.artifact_id,
                s.label,
                s.path_text,
                s.start_offset,
                s.end_offset,
                s.segment_chars,
                s.content,
                s.segmentation_mode,
                s.structure_confidence,
                a.url,
                a.domain,
                a.title,
                a.path,
                bm25(web_artifact_segments_fts, 0.0, 0.15, 0.10, 0.75) AS rank
            FROM web_artifact_segments_fts
            JOIN web_artifact_segments s ON s.segment_id = web_artifact_segments_fts.segment_id
            JOIN web_artifacts a ON a.artifact_id = s.artifact_id
            WHERE web_artifact_segments_fts MATCH ? AND s.chat_id = ?
            """
        ]
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            sql_parts.append(f"AND s.artifact_id IN ({placeholders})")
            params.extend(artifact_ids)
        sql_parts.append("ORDER BY rank ASC LIMIT ?")
        params.append(limit)
        rows = conn.execute("\n".join(sql_parts), params).fetchall()
        if rows:
            return rows, fts_enabled
    except Exception:
        fts_enabled = False

    sql = """
        SELECT
            s.segment_id,
            s.artifact_id,
            s.label,
            s.path_text,
            s.start_offset,
            s.end_offset,
            s.segment_chars,
            s.content,
            s.segmentation_mode,
            s.structure_confidence,
            a.url,
            a.domain,
            a.title,
            a.path
        FROM web_artifact_segments s
        JOIN web_artifacts a ON a.artifact_id = s.artifact_id
        WHERE s.chat_id = ?
    """
    params = [chat_id]
    if artifact_ids:
        placeholders = ",".join("?" for _ in artifact_ids)
        sql += f" AND s.artifact_id IN ({placeholders})"
        params.extend(artifact_ids)
    sql += " ORDER BY s.artifact_id ASC, s.start_offset ASC"
    rows = conn.execute(sql, params).fetchall()

    scored_rows: list[dict[str, Any]] = []
    for row in rows:
        combined = "\n".join(
            [
                str(row["label"] or ""),
                str(row["path_text"] or ""),
                str(row["content"] or ""),
            ]
        )
        unique_hits, total_hits, score = _score_text_block(
            combined,
            terms=terms,
            rank_score=0.0,
        )
        if terms and unique_hits <= 0:
            continue
        scored_rows.append(
            {
                **dict(row),
                "rank": -float(score or 0.0),
                "lexical_hits": unique_hits,
                "lexical_total_hits": total_hits,
                "score": score,
            }
        )

    scored_rows.sort(
        key=lambda item: (
            -float(item.get("score", 0.0) or 0.0),
            str(item.get("artifact_id", "") or ""),
            int(item.get("start_offset", 0) or 0),
        )
    )
    return scored_rows[:limit], fts_enabled


def _extract_focus_clauses(query: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(query or "")).strip()
    if len(normalized) < 12:
        return []

    clauses: list[str] = []
    primary_parts = [part.strip() for part in re.split(r"[;\n]+", normalized) if part.strip()]
    if not primary_parts:
        primary_parts = [normalized]

    for primary in primary_parts:
        comma_parts = [part.strip(" ,") for part in primary.split(",") if part.strip(" ,")]
        if len(comma_parts) > 1 and len(comma_parts[0].split()) <= 5:
            comma_parts = comma_parts[1:]
        if not comma_parts:
            comma_parts = [primary]

        working_parts: list[str] = []
        for part in comma_parts:
            if "," in primary and re.search(r"\band\b", part, flags=re.IGNORECASE):
                maybe_parts = [item.strip(" .,:;?!") for item in re.split(r"\band\b", part, flags=re.IGNORECASE)]
                maybe_parts = [item for item in maybe_parts if len(item.split()) >= 3]
                if len(maybe_parts) >= 2:
                    working_parts.extend(maybe_parts)
                    continue
            working_parts.append(part.strip(" .,:;?!"))

        for part in working_parts:
            cleaned = part.strip(" .,:;?!")
            if len(cleaned.split()) < 3 or not any(char.isalpha() for char in cleaned):
                continue
            clauses.append(cleaned)

    deduped: list[str] = []
    seen: set[str] = set()
    for clause in clauses:
        key = clause.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clause)
    if len(deduped) < 2:
        return []
    return deduped[:MAX_FOCUS_CLAUSES]


def _focus_query_terms(original_query: str, clause: str) -> list[str]:
    clause_terms = _fts_query_terms(clause)
    content_terms = [term for term in clause_terms if len(str(term or "")) >= 4]
    if len(content_terms) >= 2:
        return content_terms
    return _fts_query_terms(f"{original_query} {clause}")


def _resolve_focus_targets(
    conn: sqlite3.Connection,
    *,
    chat_id: str,
    original_query: str,
    focus_clauses: list[str],
    artifact_ids: list[str],
) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for clause in focus_clauses:
        composite_terms = _focus_query_terms(original_query, clause)
        rows, _ = _query_segment_rows(
            conn,
            chat_id=chat_id,
            terms=composite_terms,
            artifact_ids=artifact_ids,
            limit=2,
        )
        segment_ids = {
            str(row["segment_id"] or "")
            for row in rows
            if isinstance(row, sqlite3.Row) and str(row["segment_id"] or "")
        }
        if not segment_ids:
            for row in rows:
                if isinstance(row, dict):
                    segment_id = str(row.get("segment_id") or "")
                    if segment_id:
                        segment_ids.add(segment_id)
        targets[clause] = segment_ids
    return targets


def _snippet_segment_ids(snippet: dict[str, Any]) -> set[str]:
    covered = snippet.get("covered_segment_ids")
    if isinstance(covered, list):
        values = {str(item or "") for item in covered if str(item or "")}
        if values:
            return values
    segment_id = str(snippet.get("segment_id") or "")
    return {segment_id} if segment_id else set()


def _snippet_identity(snippet: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(snippet.get("artifact_id") or ""),
        int(snippet.get("start", 0) or 0),
        int(snippet.get("end", 0) or 0),
    )


def _count_focus_coverage(
    snippets: list[dict[str, Any]],
    *,
    focus_targets: dict[str, set[str]],
) -> int:
    if not snippets or not focus_targets:
        return 0

    seen_segment_ids: set[str] = set()
    for snippet in snippets:
        seen_segment_ids.update(_snippet_segment_ids(snippet))
    covered = 0
    for clause, target_ids in focus_targets.items():
        if clause and target_ids and seen_segment_ids.intersection(target_ids):
            covered += 1
    return covered


def _merge_segment_cluster(
    cluster: list[dict[str, Any]], *, max_span_chars: int = SEGMENT_DEDUPE_MAX_SPAN_CHARS
) -> tuple[dict[str, Any], int]:
    covered_segment_ids = sorted(
        {
            str(item.get("segment_id") or "")
            for item in cluster
            if str(item.get("segment_id") or "")
        }
    )
    covered_focus_clauses = sorted(
        {
            str(item.get("focus_clause") or "")
            for item in cluster
            if str(item.get("focus_clause") or "")
        }
    )
    if len(cluster) <= 1:
        single = dict(cluster[0])
        if covered_segment_ids:
            single["covered_segment_ids"] = covered_segment_ids
        if covered_focus_clauses:
            single["covered_focus_clauses"] = covered_focus_clauses
        return single, 0

    best = max(cluster, key=lambda item: float(item.get("score", 0.0) or 0.0))
    start = min(int(item.get("start", 0) or 0) for item in cluster)
    end = max(int(item.get("end", 0) or 0) for item in cluster)
    if end - start > max_span_chars:
        kept = dict(best)
        if covered_segment_ids:
            kept["covered_segment_ids"] = covered_segment_ids
        if covered_focus_clauses:
            kept["covered_focus_clauses"] = covered_focus_clauses
        return kept, len(cluster) - 1

    artifact_text = _read_artifact_text(str(best.get("path") or ""))
    if not artifact_text:
        kept = dict(best)
        if covered_segment_ids:
            kept["covered_segment_ids"] = covered_segment_ids
        if covered_focus_clauses:
            kept["covered_focus_clauses"] = covered_focus_clauses
        return kept, len(cluster) - 1

    merged = dict(best)
    merged["start"] = start
    merged["end"] = end
    merged["text"] = artifact_text[start:end].strip()
    merged["score"] = max(float(item.get("score", 0.0) or 0.0) for item in cluster)
    if covered_segment_ids:
        merged["covered_segment_ids"] = covered_segment_ids
    if covered_focus_clauses:
        merged["covered_focus_clauses"] = covered_focus_clauses
    return merged, len(cluster) - 1


def _dedupe_segment_snippets(
    snippets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not snippets:
        return [], {
            "dedupe_cluster_count": 0,
            "snippets_dropped_overlap": 0,
        }

    deduped: list[dict[str, Any]] = []
    cluster_count = 0
    dropped_overlap = 0

    snippets_sorted = sorted(
        snippets,
        key=lambda item: (
            str(item.get("artifact_id", "") or ""),
            int(item.get("start", 0) or 0),
            int(item.get("end", 0) or 0),
            -float(item.get("score", 0.0) or 0.0),
        ),
    )
    current_cluster: list[dict[str, Any]] = []
    current_artifact = None
    current_end = -1

    def flush_cluster() -> None:
        nonlocal cluster_count, dropped_overlap
        if not current_cluster:
            return
        cluster_count += 1
        merged, dropped = _merge_segment_cluster(current_cluster)
        deduped.append(merged)
        dropped_overlap += dropped

    for snippet in snippets_sorted:
        artifact_id = str(snippet.get("artifact_id", "") or "")
        start = int(snippet.get("start", 0) or 0)
        end = int(snippet.get("end", 0) or 0)
        if (
            current_cluster
            and artifact_id == current_artifact
            and start <= current_end + SEGMENT_DEDUPE_MAX_GAP_CHARS
        ):
            current_cluster.append(snippet)
            current_end = max(current_end, end)
            continue

        flush_cluster()
        current_cluster = [snippet]
        current_artifact = artifact_id
        current_end = end

    flush_cluster()
    deduped.sort(
        key=lambda item: (
            -float(item.get("score", 0.0) or 0.0),
            str(item.get("artifact_id", "") or ""),
            int(item.get("start", 0) or 0),
        )
    )
    return deduped, {
        "dedupe_cluster_count": cluster_count,
        "snippets_dropped_overlap": dropped_overlap,
    }


def _select_final_segment_snippets(
    snippets: list[dict[str, Any]],
    *,
    focus_targets: dict[str, set[str]],
    limit: int,
) -> list[dict[str, Any]]:
    if not snippets:
        return []

    if not focus_targets:
        return snippets[:limit]

    selected: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, int, int]] = set()

    for clause, target_ids in focus_targets.items():
        if not clause or not target_ids:
            continue
        clause_candidates = [
            snippet
            for snippet in snippets
            if _snippet_segment_ids(snippet).intersection(target_ids)
        ]
        if not clause_candidates:
            continue
        winner = max(clause_candidates, key=lambda item: float(item.get("score", 0.0) or 0.0))
        identity = _snippet_identity(winner)
        if identity in seen_identities:
            continue
        selected.append(winner)
        seen_identities.add(identity)
        if len(selected) >= limit:
            return selected[:limit]

    for snippet in snippets:
        if len(selected) >= limit:
            break
        identity = _snippet_identity(snippet)
        if identity in seen_identities:
            continue
        if float(snippet.get("score", 0.0) or 0.0) < FOCUS_FILL_SCORE_FLOOR:
            continue
        selected.append(snippet)
        seen_identities.add(identity)

    return selected[:limit] if selected else snippets[:limit]


def _select_final_segment_snippets_concept(
    snippets: list[dict[str, Any]],
    *,
    focus_targets: dict[str, set[str]],
    limit: int,
) -> list[dict[str, Any]]:
    if not snippets:
        return []
    if not focus_targets:
        return snippets[:limit]

    selected: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, int, int]] = set()
    for clause, target_ids in focus_targets.items():
        if not clause or not target_ids:
            continue
        winner = next(
            (
                snippet
                for snippet in snippets
                if _snippet_segment_ids(snippet).intersection(target_ids)
            ),
            None,
        )
        if winner is None:
            continue
        identity = _snippet_identity(winner)
        if identity in seen_identities:
            continue
        selected.append(winner)
        seen_identities.add(identity)
        if len(selected) >= limit:
            return selected[:limit]

    for snippet in snippets:
        if len(selected) >= limit:
            break
        identity = _snippet_identity(snippet)
        if identity in seen_identities:
            continue
        selected.append(snippet)
        seen_identities.add(identity)

    return selected[:limit]


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
            SELECT artifact_id, url, domain, title, path, fetched_at, message_id, content_chars
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
        SELECT artifact_id, url, domain, title, path, fetched_at, message_id, content_chars
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


def _build_segment_snippets(
    rows: list[Any],
    *,
    terms: list[str],
    limit: int,
    query_profile: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            content = str(row["content"] or "")
            label = str(row["label"] or "")
            path_text = str(row["path_text"] or "")
            rank = row["rank"] if "rank" in row.keys() else 0.0
        else:
            content = str(row.get("content") or "")
            label = str(row.get("label") or "")
            path_text = str(row.get("path_text") or "")
            rank = row.get("rank", 0.0)
        try:
            rank_f = float(rank)
        except Exception:
            rank_f = 0.0

        rank_score = 1.0 if rank_f <= 0 else 1.0 / (1.0 + rank_f)
        lexical_hits, lexical_total_hits, blended = _score_text_block(
            "\n".join([label, path_text, content]),
            terms=terms,
            rank_score=rank_score,
            query_profile=query_profile,
        )
        if terms and lexical_hits <= 0:
            continue
        snippets.append(
            {
                "segment_id": row["segment_id"] if isinstance(row, sqlite3.Row) else row.get("segment_id"),
                "artifact_id": row["artifact_id"] if isinstance(row, sqlite3.Row) else row.get("artifact_id"),
                "url": row["url"] if isinstance(row, sqlite3.Row) else row.get("url"),
                "domain": row["domain"] if isinstance(row, sqlite3.Row) else row.get("domain"),
                "title": row["title"] if isinstance(row, sqlite3.Row) else row.get("title"),
                "path": row["path"] if isinstance(row, sqlite3.Row) else row.get("path"),
                "start": int(row["start_offset"] if isinstance(row, sqlite3.Row) else row.get("start_offset") or 0),
                "end": int(row["end_offset"] if isinstance(row, sqlite3.Row) else row.get("end_offset") or 0),
                "hit_index": int(
                    row["start_offset"] if isinstance(row, sqlite3.Row) else row.get("start_offset") or 0
                )
                + _best_hit_index(content, terms, query_profile=query_profile),
                "score": blended,
                "text": content,
                "label": label,
                "path_text": path_text,
                "chunked": str(row["segmentation_mode"] if isinstance(row, sqlite3.Row) else row.get("segmentation_mode") or "chunk") == "chunk",
                "segmentation_mode": str(row["segmentation_mode"] if isinstance(row, sqlite3.Row) else row.get("segmentation_mode") or "chunk"),
                "structure_confidence": float(row["structure_confidence"] if isinstance(row, sqlite3.Row) else row.get("structure_confidence") or 0.0),
                "source_level": "segment",
                "lexical_hits": lexical_hits,
                "lexical_total_hits": lexical_total_hits,
            }
        )

    snippets.sort(
        key=lambda item: (
            -float(item.get("score", 0.0) or 0.0),
            str(item.get("artifact_id", "") or ""),
            int(item.get("start", 0) or 0),
        )
    )
    return snippets[:limit]


def _query_web_evidence_store_segmented_legacy(
    *,
    db_path: Path,
    chat_id: str,
    message_id: Optional[str],
    query: str,
    effective_query: Optional[str],
    query_profile: Optional[dict[str, Any]],
    terms: list[str],
    scope_rows: list[sqlite3.Row],
    missing_artifact_ids: list[str],
    searched_artifact_ids: list[str],
    searched_domains: list[str],
    top_k: int,
) -> dict[str, Any]:
    focus_clauses = []
    focus_targets: dict[str, set[str]] = {}
    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        segment_meta = _ensure_segment_indexes(
            conn,
            chat_id=chat_id,
            artifact_rows=scope_rows,
        )
        segment_rows, fts_enabled = _query_segment_rows(
            conn,
            chat_id=chat_id,
            terms=terms,
            artifact_ids=searched_artifact_ids,
            limit=max(top_k * 3, 12),
        )
        large_artifact_in_scope = any(
            int(row["content_chars"] or 0) >= LARGE_ARTIFACT_CHARS_THRESHOLD for row in scope_rows
        )
        focus_clauses = _extract_focus_clauses(query) if large_artifact_in_scope else []
        if focus_clauses:
            focus_targets = _resolve_focus_targets(
                conn,
                chat_id=chat_id,
                original_query=query,
                focus_clauses=focus_clauses,
                artifact_ids=searched_artifact_ids,
            )

    snippets = _build_segment_snippets(
        segment_rows,
        terms=terms,
        limit=max(top_k, 1),
        query_profile=query_profile,
    )
    one_shot_quality = _classify_evidence_strength(snippets)
    coverage_before_merge = _count_focus_coverage(
        snippets,
        focus_targets=focus_targets,
    )

    focus_retrieval_used = False
    focus_candidates = 0
    focus_admitted = 0
    focus_dropped_low_score = 0
    focus_candidate_snippets: list[dict[str, Any]] = []

    if focus_clauses and coverage_before_merge < len(focus_clauses):
        focus_retrieval_used = True
        with _sqlite_conn(db_path) as conn:
            _init_db(conn)
            for clause in focus_clauses:
                composite_terms = _focus_query_terms(query, clause)
                rows, _ = _query_segment_rows(
                    conn,
                    chat_id=chat_id,
                    terms=composite_terms,
                    artifact_ids=searched_artifact_ids,
                    limit=6,
                )
                clause_snippets = _build_segment_snippets(
                    rows,
                    terms=composite_terms,
                    limit=2,
                    query_profile=query_profile,
                )
                focus_candidates += len(clause_snippets)
                admitted_for_clause = False
                for snippet in clause_snippets:
                    snippet["focus_clause"] = clause
                    if float(snippet.get("score", 0.0) or 0.0) >= FOCUS_WINNER_SCORE_FLOOR and not admitted_for_clause:
                        focus_candidate_snippets.append(snippet)
                        focus_admitted += 1
                        admitted_for_clause = True
                    else:
                        focus_dropped_low_score += 1

    merged_candidates = [*snippets, *focus_candidate_snippets]
    deduped_snippets, dedupe_meta = _dedupe_segment_snippets(merged_candidates)
    final_limit = min(12, max(top_k, len(focus_clauses) * 2 if focus_clauses else top_k))
    final_snippets = (
        _select_final_segment_snippets(
            deduped_snippets,
            focus_targets=focus_targets,
            limit=final_limit,
        )
        if deduped_snippets
        else snippets[:final_limit]
    )
    final_snippets, expansion_meta = _annotate_and_expand_snippets(
        final_snippets,
        scope_artifact_count=len(searched_artifact_ids),
        query_profile=query_profile,
    )
    coverage_after_merge = _count_focus_coverage(
        final_snippets,
        focus_targets=focus_targets,
    )
    evidence_strength = _classify_evidence_strength(final_snippets)
    suggested_next_action = _suggest_next_action(
        searched_artifact_count=len(searched_artifact_ids),
        searched_domains=searched_domains,
        snippets=final_snippets,
        evidence_strength=evidence_strength,
    )
    structured_index_used = any(
        item.get("segmentation_mode") in {"structured", "weak_structured"}
        for item in segment_meta
    )
    fallback_chunk_mode = any(
        item.get("segmentation_mode") == "chunk" for item in segment_meta
    )

    return {
        "status": "ok",
        "query": query,
        "normalized_query": effective_query or query,
        "chat_id": chat_id,
        "message_id": str(message_id or ""),
        "scope_mode": "implicit_current_message",
        "artifact_ids": searched_artifact_ids,
        "searched_artifact_count": len(searched_artifact_ids),
        "searched_artifact_ids": searched_artifact_ids,
        "searched_domains": searched_domains,
        "missing_artifact_ids": missing_artifact_ids,
        "snippets": final_snippets,
        "narrow_count": len(snippets),
        "wide_count": 0,
        "wide_pass_used": False,
        "fts_enabled": bool(fts_enabled),
        "top_k": top_k,
        "window_chars": MAX_SEGMENT_CHARS,
        "widen_if_weak": False,
        "weak_narrow_evidence": one_shot_quality == "weak",
        "evidence_strength": evidence_strength,
        "suggested_next_action": suggested_next_action,
        "retrieval_mode_effective": WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        "structured_index_used": structured_index_used,
        "fallback_chunk_mode": fallback_chunk_mode,
        "focus_retrieval_used": focus_retrieval_used,
        "focus_count": len(focus_clauses),
        "focus_clauses": focus_clauses,
        "coverage_before_merge": coverage_before_merge,
        "coverage_after_merge": coverage_after_merge,
        "focus_candidates": focus_candidates,
        "focus_admitted": focus_admitted,
        "focus_dropped_low_score": focus_dropped_low_score,
        "query_compaction_applied": bool((query_profile or {}).get("compaction_applied")),
        "title_overlap_dropped": int((query_profile or {}).get("title_overlap_dropped") or 0),
        **expansion_meta,
        **dedupe_meta,
    }


def _query_web_evidence_store_segmented_concept(
    *,
    db_path: Path,
    chat_id: str,
    message_id: Optional[str],
    query: str,
    effective_query: Optional[str],
    query_profile: Optional[dict[str, Any]],
    terms: list[str],
    scope_rows: list[sqlite3.Row],
    missing_artifact_ids: list[str],
    searched_artifact_ids: list[str],
    searched_domains: list[str],
    top_k: int,
    embedding_function: Any = None,
) -> dict[str, Any]:
    focus_clauses = []
    focus_targets: dict[str, set[str]] = {}
    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        segment_meta = _ensure_segment_indexes(
            conn,
            chat_id=chat_id,
            artifact_rows=scope_rows,
        )
        segment_rows, fts_enabled = _query_segment_rows(
            conn,
            chat_id=chat_id,
            terms=terms,
            artifact_ids=searched_artifact_ids,
            limit=max(top_k * 3, 12),
        )
        large_artifact_in_scope = any(
            int(row["content_chars"] or 0) >= LARGE_ARTIFACT_CHARS_THRESHOLD for row in scope_rows
        )
        focus_clauses = _extract_focus_clauses(query) if large_artifact_in_scope else []
        if focus_clauses:
            focus_targets = _resolve_focus_targets(
                conn,
                chat_id=chat_id,
                original_query=query,
                focus_clauses=focus_clauses,
                artifact_ids=searched_artifact_ids,
            )

    snippets = _build_segment_snippets(
        segment_rows,
        terms=terms,
        limit=max(top_k, 1),
        query_profile=query_profile,
    )
    coverage_before_merge = _count_focus_coverage(
        snippets,
        focus_targets=focus_targets,
    )

    focus_retrieval_used = False
    focus_candidates = 0
    focus_admitted = 0
    focus_dropped_low_score = 0
    focus_candidate_snippets: list[dict[str, Any]] = []

    if focus_clauses and coverage_before_merge < len(focus_clauses):
        focus_retrieval_used = True
        with _sqlite_conn(db_path) as conn:
            _init_db(conn)
            for clause in focus_clauses:
                composite_terms = _focus_query_terms(query, clause)
                rows, _ = _query_segment_rows(
                    conn,
                    chat_id=chat_id,
                    terms=composite_terms,
                    artifact_ids=searched_artifact_ids,
                    limit=6,
                )
                clause_profile = dict(query_profile or {})
                clause_profile["normalized_query"] = clause
                clause_profile["normalized_terms"] = composite_terms
                clause_profile["target_concepts"], clause_profile["query_modifiers"], _ = _build_query_concepts(
                    clause
                )
                clause_snippets = _build_segment_snippets(
                    rows,
                    terms=composite_terms,
                    limit=2,
                    query_profile=clause_profile,
                )
                focus_candidates += len(clause_snippets)
                admitted_for_clause = False
                for snippet in clause_snippets:
                    snippet["focus_clause"] = clause
                    if float(snippet.get("score", 0.0) or 0.0) >= FOCUS_WINNER_SCORE_FLOOR and not admitted_for_clause:
                        focus_candidate_snippets.append(snippet)
                        focus_admitted += 1
                        admitted_for_clause = True
                    else:
                        focus_dropped_low_score += 1

    merged_candidates = [*snippets, *focus_candidate_snippets]
    deduped_snippets, dedupe_meta = _dedupe_segment_snippets(merged_candidates)
    artifact_contexts = _build_artifact_alignment_contexts(
        scope_rows=scope_rows,
        query_profile=query_profile,
    )
    aligned_snippets, concept_meta = _annotate_and_expand_snippets_concept(
        deduped_snippets,
        scope_artifact_count=len(searched_artifact_ids),
        query_profile=query_profile,
        artifact_contexts=artifact_contexts,
        embedding_function=embedding_function,
    )
    final_limit = min(12, max(top_k, len(focus_clauses) * 2 if focus_clauses else top_k))
    final_snippets = _select_final_segment_snippets_concept(
        aligned_snippets,
        focus_targets=focus_targets,
        limit=final_limit,
    )
    coverage_after_merge = _count_focus_coverage(
        final_snippets,
        focus_targets=focus_targets,
    )
    evidence_strength = _classify_evidence_strength_concept(final_snippets)
    suggested_next_action = _suggest_next_action_concept(
        searched_artifact_count=len(searched_artifact_ids),
        snippets=final_snippets,
    )
    structured_index_used = any(
        item.get("segmentation_mode") in {"structured", "weak_structured"}
        for item in segment_meta
    )
    fallback_chunk_mode = any(
        item.get("segmentation_mode") == "chunk" for item in segment_meta
    )

    return {
        "status": "ok",
        "query": query,
        "normalized_query": effective_query or query,
        "chat_id": chat_id,
        "message_id": str(message_id or ""),
        "scope_mode": "implicit_current_message",
        "artifact_ids": searched_artifact_ids,
        "searched_artifact_count": len(searched_artifact_ids),
        "searched_artifact_ids": searched_artifact_ids,
        "searched_domains": searched_domains,
        "missing_artifact_ids": missing_artifact_ids,
        "snippets": final_snippets,
        "narrow_count": len(snippets),
        "wide_count": 0,
        "wide_pass_used": False,
        "fts_enabled": bool(fts_enabled),
        "top_k": top_k,
        "window_chars": MAX_SEGMENT_CHARS,
        "widen_if_weak": False,
        "weak_narrow_evidence": evidence_strength == "weak",
        "evidence_strength": evidence_strength,
        "suggested_next_action": suggested_next_action,
        "retrieval_mode_effective": WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED,
        "structured_index_used": structured_index_used,
        "fallback_chunk_mode": fallback_chunk_mode,
        "focus_retrieval_used": focus_retrieval_used,
        "focus_count": len(focus_clauses),
        "focus_clauses": focus_clauses,
        "coverage_before_merge": coverage_before_merge,
        "coverage_after_merge": coverage_after_merge,
        "focus_candidates": focus_candidates,
        "focus_admitted": focus_admitted,
        "focus_dropped_low_score": focus_dropped_low_score,
        "query_compaction_applied": False,
        "title_overlap_dropped": 0,
        "target_concepts": list((query_profile or {}).get("target_concepts") or []),
        "query_modifiers": list((query_profile or {}).get("query_modifiers") or []),
        **concept_meta,
        **dedupe_meta,
    }


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
    retrieval_mode: str = WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY,
    concept_alignment_enabled: bool = False,
    embedding_function: Any = None,
    reranking_function: Any = None,
) -> dict[str, Any]:
    normalized_chat_id = str(chat_id or "").strip()
    if not normalized_chat_id:
        raise ValueError("chat_id is required for evidence queries")
    normalized_artifact_ids = _normalize_artifact_ids(artifact_ids)
    normalized_retrieval_mode = normalize_web_evidence_retrieval_mode(retrieval_mode)

    chat_dir = resolve_chat_artifacts_dir(normalized_chat_id)
    if chat_dir is None:
        missing_message, next_action = _not_found_scope_message(normalized_artifact_ids)
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
            "suggested_next_action": next_action,
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": False,
            "artifact_ids": [],
            "message": missing_message if normalized_artifact_ids else "No chat artifact directory found.",
            "retrieval_mode_effective": normalized_retrieval_mode,
            "structured_index_used": False,
            "fallback_chunk_mode": False,
            "focus_retrieval_used": False,
            "focus_count": 0,
            "focus_clauses": [],
            "coverage_before_merge": 0,
            "coverage_after_merge": 0,
            "focus_candidates": 0,
            "focus_admitted": 0,
            "focus_dropped_low_score": 0,
            "dedupe_cluster_count": 0,
            "snippets_dropped_overlap": 0,
            "expanded_context_count": 0,
            "truncation_trust_hits": 0,
            "concept_alignment_enabled": bool(concept_alignment_enabled),
            "concept_alignment_shadow": {"ran": False},
        }

    db_path = chat_dir / "web_evidence.sqlite"
    if not db_path.exists():
        missing_message, next_action = _not_found_scope_message(normalized_artifact_ids)
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
            "suggested_next_action": next_action,
            "snippets": [],
            "narrow_count": 0,
            "wide_count": 0,
            "wide_pass_used": False,
            "fts_enabled": False,
            "artifact_ids": [],
            "message": missing_message,
            "retrieval_mode_effective": normalized_retrieval_mode,
            "structured_index_used": False,
            "fallback_chunk_mode": False,
            "focus_retrieval_used": False,
            "focus_count": 0,
            "focus_clauses": [],
            "coverage_before_merge": 0,
            "coverage_after_merge": 0,
            "focus_candidates": 0,
            "focus_admitted": 0,
            "focus_dropped_low_score": 0,
            "dedupe_cluster_count": 0,
            "snippets_dropped_overlap": 0,
            "expanded_context_count": 0,
            "truncation_trust_hits": 0,
            "concept_alignment_enabled": bool(concept_alignment_enabled),
            "concept_alignment_shadow": {"ran": False},
        }

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
        for row in rows:
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
            rank = row_get(row, "rank", 0.0)
            try:
                rank_f = float(rank)
            except Exception:
                rank_f = 0.0

            rank_score = 1.0 if rank_f <= 0 else 1.0 / (1.0 + rank_f)
            artifact_id = row_get(row, "artifact_id", "")
            url = row_get(row, "url", "")
            domain = row_get(row, "domain", "")
            title = row_get(row, "title", "")
            path = row_get(row, "path", "")

            if len(content) >= LARGE_ARTIFACT_CHARS_THRESHOLD:
                chunk_candidates: list[dict[str, Any]] = []
                for chunk in _build_large_artifact_chunks(content):
                    chunk_unique_hits, chunk_total_hits, chunk_score = _score_text_block(
                        chunk.get("text", ""),
                        terms=terms,
                        rank_score=rank_score,
                        query_profile=query_profile,
                    )
                    if terms and chunk_unique_hits <= 0:
                        continue
                    chunk_candidates.append(
                        {
                            "artifact_id": artifact_id,
                            "url": url,
                            "domain": domain,
                            "title": title,
                            "path": path,
                            "start": int(chunk.get("start") or 0),
                            "end": int(chunk.get("end") or 0),
                            "hit_index": int(chunk.get("start") or 0)
                            + _best_hit_index(
                                str(chunk.get("text") or ""),
                                terms,
                                query_profile=query_profile,
                            ),
                            "score": chunk_score,
                            "text": str(chunk.get("text") or ""),
                            "chunked": True,
                            "chunk_index": int(chunk.get("chunk_index") or 0),
                            "chunk_count": int(chunk.get("chunk_count") or 0),
                            "lexical_hits": chunk_unique_hits,
                            "lexical_total_hits": chunk_total_hits,
                        }
                    )

                if not chunk_candidates and not terms:
                    for chunk in _build_large_artifact_chunks(content)[:1]:
                        chunk_candidates.append(
                            {
                                "artifact_id": artifact_id,
                                "url": url,
                                "domain": domain,
                                "title": title,
                                "path": path,
                                "start": int(chunk.get("start") or 0),
                                "end": int(chunk.get("end") or 0),
                                "hit_index": int(chunk.get("start") or 0)
                                + _best_hit_index(
                                    str(chunk.get("text") or ""),
                                    terms,
                                    query_profile=query_profile,
                                ),
                                "score": round(rank_score, 4),
                                "text": str(chunk.get("text") or ""),
                                "chunked": True,
                                "chunk_index": int(chunk.get("chunk_index") or 0),
                                "chunk_count": int(chunk.get("chunk_count") or 0),
                                "lexical_hits": 0,
                                "lexical_total_hits": 0,
                            }
                        )

                chunk_candidates.sort(
                    key=lambda item: (
                        -float(item.get("score", 0.0) or 0.0),
                        int(item.get("start", 0) or 0),
                        str(item.get("artifact_id", "") or ""),
                    )
                )
                snippets.extend(
                    chunk_candidates[: max(1, min(MAX_CHUNK_CANDIDATES_PER_ARTIFACT, limit))]
                )
                continue

            start, end, snippet_text, hit_idx = _extract_window(content, terms, window)
            lexical_hits, lexical_total_hits, blended = _score_text_block(
                content,
                terms=terms,
                rank_score=rank_score,
                query_profile=query_profile,
            )
            snippets.append(
                {
                    "artifact_id": artifact_id,
                    "url": url,
                    "domain": domain,
                    "title": title,
                    "path": path,
                    "start": start,
                    "end": end,
                    "hit_index": hit_idx,
                    "score": blended,
                    "text": snippet_text,
                    "chunked": False,
                    "chunk_index": 0,
                    "chunk_count": 1,
                    "lexical_hits": lexical_hits,
                    "lexical_total_hits": lexical_total_hits,
                }
            )

        snippets.sort(
            key=lambda item: (
                -float(item.get("score", 0.0) or 0.0),
                str(item.get("artifact_id", "") or ""),
                int(item.get("start", 0) or 0),
            )
        )
        return snippets[:limit]

    with _sqlite_conn(db_path) as conn:
        _init_db(conn)
        scope_rows, missing_artifact_ids, scope_mode = _load_scope_rows(
            conn,
            chat_id=normalized_chat_id,
            message_id=message_id,
            artifact_ids=normalized_artifact_ids,
        )
        legacy_query_profile = _single_artifact_query_profile(
            query=query,
            scope_rows=scope_rows,
        )
        legacy_query_profile["serving_logic"] = "legacy"
        concept_query_profile = _build_concept_first_query_profile(
            query=query,
            scope_rows=scope_rows,
        )
        active_query_profile = (
            concept_query_profile if _normalize_bool(concept_alignment_enabled) else legacy_query_profile
        )
        query_profile = active_query_profile
        effective_query = str(active_query_profile.get("normalized_query") or query or "").strip()
        terms = list(active_query_profile.get("normalized_terms") or _fts_query_terms(effective_query))
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
        single_artifact_direct_scan = bool(active_query_profile.get("single_artifact_scope"))
        if normalized_retrieval_mode == WEB_EVIDENCE_RETRIEVAL_MODE_SEGMENTED:
            legacy_effective_query = str(legacy_query_profile.get("normalized_query") or query or "").strip()
            legacy_terms = list(
                legacy_query_profile.get("normalized_terms") or _fts_query_terms(legacy_effective_query)
            )
            concept_effective_query = str(concept_query_profile.get("normalized_query") or query or "").strip()
            concept_terms = list(
                concept_query_profile.get("normalized_terms") or _fts_query_terms(concept_effective_query)
            )
            legacy_payload = _query_web_evidence_store_segmented_legacy(
                db_path=db_path,
                chat_id=normalized_chat_id,
                message_id=message_id,
                query=query,
                effective_query=legacy_effective_query,
                query_profile=legacy_query_profile,
                terms=legacy_terms,
                scope_rows=scope_rows,
                missing_artifact_ids=missing_artifact_ids,
                searched_artifact_ids=searched_artifact_ids,
                searched_domains=searched_domains,
                top_k=bounded_top_k,
            )
            concept_payload = _query_web_evidence_store_segmented_concept(
                db_path=db_path,
                chat_id=normalized_chat_id,
                message_id=message_id,
                query=query,
                effective_query=concept_effective_query,
                query_profile=concept_query_profile,
                terms=concept_terms,
                scope_rows=scope_rows,
                missing_artifact_ids=missing_artifact_ids,
                searched_artifact_ids=searched_artifact_ids,
                searched_domains=searched_domains,
                top_k=bounded_top_k,
                embedding_function=embedding_function,
            )
            serving_payload = concept_payload if _normalize_bool(concept_alignment_enabled) else legacy_payload
            shadow_payload = legacy_payload if _normalize_bool(concept_alignment_enabled) else concept_payload
            serving_payload = _apply_agent_guidance_overlay(
                serving_payload=serving_payload,
                shadow_payload=shadow_payload,
                query_profile=active_query_profile,
            )
            return serving_payload | {
                "scope_mode": scope_mode,
                "concept_alignment_enabled": bool(concept_alignment_enabled),
                "concept_alignment_serving_path": (
                    "concept" if _normalize_bool(concept_alignment_enabled) else "legacy"
                ),
            }
        if single_artifact_direct_scan:
            narrow_rows = scope_rows[:1]
            fts_enabled = True
        else:
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
        if single_artifact_direct_scan:
            rows = scope_rows[:1]
        else:
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
    if _normalize_bool(concept_alignment_enabled):
        artifact_contexts = _build_artifact_alignment_contexts(
            scope_rows=scope_rows,
            query_profile=active_query_profile,
        )
        snippets, expansion_meta = _annotate_and_expand_snippets_concept(
            snippets,
            scope_artifact_count=len(searched_artifact_ids),
            query_profile=active_query_profile,
            artifact_contexts=artifact_contexts,
            embedding_function=embedding_function,
        )
        snippets = snippets[:bounded_top_k]
        evidence_strength = _classify_evidence_strength_concept(snippets)
        suggested_next_action = _suggest_next_action_concept(
            searched_artifact_count=len(searched_artifact_ids),
            snippets=snippets,
        )
    else:
        snippets, expansion_meta = _annotate_and_expand_snippets(
            snippets,
            scope_artifact_count=len(searched_artifact_ids),
            query_profile=active_query_profile,
        )
        evidence_strength = _classify_evidence_strength(snippets)
        suggested_next_action = _suggest_next_action(
            searched_artifact_count=len(searched_artifact_ids),
            searched_domains=searched_domains,
            snippets=snippets,
            evidence_strength=evidence_strength,
        )

    return _apply_agent_guidance_overlay(
        serving_payload={
        "status": "ok",
        "query": query,
        "normalized_query": effective_query,
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
        "retrieval_mode_effective": WEB_EVIDENCE_RETRIEVAL_MODE_LEGACY,
        "structured_index_used": False,
        "fallback_chunk_mode": False,
        "focus_retrieval_used": False,
        "focus_count": 0,
        "focus_clauses": [],
        "coverage_before_merge": 0,
        "coverage_after_merge": 0,
        "focus_candidates": 0,
        "focus_admitted": 0,
        "focus_dropped_low_score": 0,
        "dedupe_cluster_count": 0,
        "snippets_dropped_overlap": 0,
        "query_compaction_applied": bool(active_query_profile.get("compaction_applied")),
        "title_overlap_dropped": int(active_query_profile.get("title_overlap_dropped") or 0),
        "target_concepts": list(active_query_profile.get("target_concepts") or []),
        "query_modifiers": list(active_query_profile.get("query_modifiers") or []),
        "concept_alignment_enabled": bool(concept_alignment_enabled),
        "concept_alignment_serving_path": (
            "concept" if _normalize_bool(concept_alignment_enabled) else "legacy"
        ),
        "concept_alignment_shadow": {"ran": False},
        **expansion_meta,
    },
        shadow_payload=None,
        query_profile=active_query_profile,
    )
