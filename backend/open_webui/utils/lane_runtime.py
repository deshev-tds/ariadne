from __future__ import annotations

import json
from typing import Any


SCIENCE_RESEARCH_MODES = {"light", "deep"}
SCIENCE_ATTACHED_CORPORA_SUPPORTED = ("medicine",)


def normalize_science_research_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SCIENCE_RESEARCH_MODES:
        return normalized
    return "light"


def normalize_science_attached_corpora(value: Any) -> list[str]:
    items: list[Any]
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                items = parsed
            else:
                items = raw.split(",")
        else:
            items = raw.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    supported = set(SCIENCE_ATTACHED_CORPORA_SUPPORTED)
    for item in items:
        corpus_id = str(item or "").strip().lower()
        if not corpus_id or corpus_id in seen or corpus_id not in supported:
            continue
        seen.add(corpus_id)
        normalized.append(corpus_id)
    return normalized


def has_science_attached_corpus(params: dict[str, Any] | None, corpus_id: str) -> bool:
    if not isinstance(params, dict):
        return False
    normalized_corpus_id = str(corpus_id or "").strip().lower()
    if not normalized_corpus_id:
        return False
    return normalized_corpus_id in set(
        normalize_science_attached_corpora(params.get("science_attached_corpora"))
    )
