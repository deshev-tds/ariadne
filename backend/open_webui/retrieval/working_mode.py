from typing import Any

from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode

WORKING_MODES = {"general", "science", "offsec", "news"}


def normalize_working_mode(value: Any, *, local_corpus_mode: Any = None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in WORKING_MODES:
        return normalized

    # Backward compatibility: older chats only carry local_corpus_mode.
    # If local corpus retrieval is enabled, map them onto the current
    # evidence-first family, exposed product-wise as Science Mode.
    if normalize_local_corpus_mode(local_corpus_mode) != "off":
        return "science"

    return "general"
