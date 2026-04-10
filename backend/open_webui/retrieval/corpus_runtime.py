from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from open_webui.retrieval.local_corpus import (
    resolve_local_corpus_root,
    resolve_repo_relative_corpus_root,
)
from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode
from open_webui.retrieval.news_lane import resolve_news_corpus_root
from open_webui.retrieval.working_mode import normalize_working_mode

DEFAULT_OFFSEC_CORPUS_ROOT_SETTING = Path("offsec_corpus")


@dataclass(frozen=True)
class CorpusRuntimeSelection:
    working_mode: str
    local_corpus_mode: str
    science_root: Optional[Path]
    offsec_root: Optional[Path]
    news_root: Optional[Path]

    @property
    def science_enabled(self) -> bool:
        return self.science_root is not None

    @property
    def offsec_enabled(self) -> bool:
        return self.offsec_root is not None

    @property
    def news_enabled(self) -> bool:
        return self.news_root is not None

    @property
    def any_enabled(self) -> bool:
        return self.science_enabled or self.offsec_enabled or self.news_enabled


def resolve_offsec_corpus_root(config_or_path: Any = None) -> Optional[Path]:
    if config_or_path is None:
        return None

    if isinstance(config_or_path, (str, Path)):
        candidate = Path(config_or_path)
    else:
        raw = getattr(config_or_path, "OFFSEC_CORPUS_ROOT", None)
        candidate = Path(str(raw)) if raw else None

    if candidate is None:
        return None

    return resolve_repo_relative_corpus_root(
        candidate,
        DEFAULT_OFFSEC_CORPUS_ROOT_SETTING,
    )


def resolve_corpus_runtime(
    config_or_path: Any = None,
    params: Optional[dict[str, Any]] = None,
) -> CorpusRuntimeSelection:
    normalized_params = params or {}
    local_corpus_mode = normalize_local_corpus_mode(
        normalized_params.get("local_corpus_mode")
    )
    working_mode = normalize_working_mode(
        normalized_params.get("working_mode"),
        local_corpus_mode=local_corpus_mode,
    )

    tools_enabled = bool(
        getattr(config_or_path, "ENABLE_LOCAL_CORPUS_TOOLS", True)
        if config_or_path is not None
        else True
    )
    if local_corpus_mode == "off" or not tools_enabled:
        news_root = None
        if working_mode == "news" and bool(
            getattr(config_or_path, "NEWS_ENABLED", True) if config_or_path is not None else True
        ):
            news_root = resolve_news_corpus_root(config_or_path)
        return CorpusRuntimeSelection(
            working_mode=working_mode,
            local_corpus_mode=local_corpus_mode,
            science_root=None,
            offsec_root=None,
            news_root=news_root,
        )

    science_root = None
    offsec_root = None
    news_root = None

    if working_mode == "science":
        science_root = resolve_local_corpus_root(config_or_path)
    elif working_mode == "offsec":
        offsec_root = resolve_offsec_corpus_root(config_or_path)
    elif working_mode == "news" and bool(
        getattr(config_or_path, "NEWS_ENABLED", True) if config_or_path is not None else True
    ):
        news_root = resolve_news_corpus_root(config_or_path)

    return CorpusRuntimeSelection(
        working_mode=working_mode,
        local_corpus_mode=local_corpus_mode,
        science_root=science_root,
        offsec_root=offsec_root,
        news_root=news_root,
    )
