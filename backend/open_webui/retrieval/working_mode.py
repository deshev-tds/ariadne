from typing import Any

WORKING_MODES = {"general", "medical", "general_science", "offsec", "news"}
LEGACY_WORKING_MODE_ALIASES = {"science": "medical"}


def normalize_working_mode(value: Any, *, local_corpus_mode: Any = None) -> str:
    normalized = str(value or "").strip().lower()
    normalized = LEGACY_WORKING_MODE_ALIASES.get(normalized, normalized)
    if normalized in WORKING_MODES:
        return normalized

    # Missing or unknown working modes now fall back to General. Local corpus
    # routing remains an independent setting and must not implicitly flip the
    # chat into Medical mode.
    return "general"
