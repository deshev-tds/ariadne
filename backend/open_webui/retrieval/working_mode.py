from typing import Any

from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode

WORKING_MODES = {"general", "medical", "general_science", "offsec", "news"}
LEGACY_WORKING_MODE_ALIASES = {"science": "medical"}


def normalize_working_mode(value: Any, *, local_corpus_mode: Any = None) -> str:
    normalized = str(value or "").strip().lower()
    normalized = LEGACY_WORKING_MODE_ALIASES.get(normalized, normalized)
    if normalized in WORKING_MODES:
        return normalized

    # Backward compatibility: older chats only carry local_corpus_mode.
    # If local corpus retrieval is enabled, map them onto the current
    # evidence-first family, exposed product-wise as Medical Mode.
    if normalize_local_corpus_mode(local_corpus_mode) != "off":
        return "medical"

    return "general"
