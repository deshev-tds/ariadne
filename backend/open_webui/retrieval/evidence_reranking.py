import logging
import threading
from pathlib import Path
from typing import Any, Callable, Optional


log = logging.getLogger(__name__)

_RERANKER_CACHE: dict[str, Any] = {}
_RERANKER_LOCK = threading.Lock()


def _is_runtime_config(config_or_path: Any) -> bool:
    return config_or_path is not None and not isinstance(config_or_path, (str, Path))


def get_evidence_reranking_settings(config_or_path: Any) -> tuple[bool, str]:
    if not _is_runtime_config(config_or_path):
        return (False, "")

    enabled = bool(
        getattr(config_or_path, "ENABLE_CORPUS_EVIDENCE_RERANKING", False) or False
    )
    model = str(getattr(config_or_path, "CORPUS_EVIDENCE_RERANKING_MODEL", "") or "")
    return (enabled, model.strip())


def _load_reranker(model: str):
    from open_webui.routers.retrieval import get_rf

    return get_rf("", model)


def get_evidence_reranker(config_or_path: Any) -> tuple[Optional[Any], str]:
    enabled, model = get_evidence_reranking_settings(config_or_path)
    if not enabled or not model:
        return (None, "")

    with _RERANKER_LOCK:
        reranker = _RERANKER_CACHE.get(model)
        if reranker is None:
            reranker = _load_reranker(model)
            _RERANKER_CACHE[model] = reranker
        return (reranker, model)


def rerank_items(
    *,
    query: str,
    items: list[dict[str, Any]],
    config_or_path: Any,
    text_getter: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], str]:
    reranker, model = get_evidence_reranker(config_or_path)
    if reranker is None or len(items) <= 1:
        return (items, "")

    try:
        scores = reranker.predict([(query, text_getter(item)) for item in items])
        score_values = scores.tolist() if hasattr(scores, "tolist") else list(scores)
    except Exception as exc:
        log.warning("Evidence reranking failed for model %s: %s", model, exc)
        return (items, "")

    reranked_items: list[dict[str, Any]] = []
    for item, rerank_score in zip(items, score_values):
        reranked_item = dict(item)
        reranked_item["rerank_score"] = round(float(rerank_score), 6)
        reranked_items.append(reranked_item)

    reranked_items.sort(
        key=lambda item: (
            -float(item.get("rerank_score") or 0.0),
            -float(item.get("score") or 0.0),
            int(item.get("page_no") or 0),
            str(item.get("title") or "").lower(),
        )
    )
    return (reranked_items, model)
