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
from open_webui.utils.lane_runtime import normalize_science_attached_corpora

DEFAULT_OFFSEC_CORPUS_ROOT_SETTING = Path("offsec_corpus")


@dataclass(frozen=True)
class CorpusRuntimeSelection:
    working_mode: str
    local_corpus_mode: str
    medical_root: Optional[Path]
    attached_corpora: tuple[str, ...]
    attached_roots: dict[str, Path]
    offsec_root: Optional[Path]
    news_root: Optional[Path]

    @property
    def medical_enabled(self) -> bool:
        return self.medical_root is not None

    @property
    def science_enabled(self) -> bool:
        # Backward-compatible alias for the original science lane local corpus.
        return self.medical_enabled

    @property
    def general_science_enabled(self) -> bool:
        return bool(self.attached_roots)

    @property
    def offsec_enabled(self) -> bool:
        return self.offsec_root is not None

    @property
    def news_enabled(self) -> bool:
        return self.news_root is not None

    @property
    def any_enabled(self) -> bool:
        return (
            self.medical_enabled
            or self.general_science_enabled
            or self.offsec_enabled
            or self.news_enabled
        )

    def has_attached_corpus(self, corpus_id: str) -> bool:
        normalized = str(corpus_id or "").strip().lower()
        return normalized in self.attached_roots

    def get_attached_root(self, corpus_id: str) -> Optional[Path]:
        normalized = str(corpus_id or "").strip().lower()
        return self.attached_roots.get(normalized)


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
    attached_corpora = tuple(
        normalize_science_attached_corpora(
            normalized_params.get("science_attached_corpora")
        )
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
            medical_root=None,
            attached_corpora=attached_corpora,
            attached_roots={},
            offsec_root=None,
            news_root=news_root,
        )

    medical_root = None
    attached_roots: dict[str, Path] = {}
    offsec_root = None
    news_root = None

    if working_mode == "medical":
        medical_root = resolve_local_corpus_root(config_or_path)
    elif working_mode == "general_science":
        if "medicine" in attached_corpora:
            medicine_root = resolve_local_corpus_root(config_or_path)
            if medicine_root is not None:
                attached_roots["medicine"] = medicine_root
                medical_root = medicine_root
    elif working_mode == "offsec":
        offsec_root = resolve_offsec_corpus_root(config_or_path)
    elif working_mode == "news" and bool(
        getattr(config_or_path, "NEWS_ENABLED", True) if config_or_path is not None else True
    ):
        news_root = resolve_news_corpus_root(config_or_path)

    return CorpusRuntimeSelection(
        working_mode=working_mode,
        local_corpus_mode=local_corpus_mode,
        medical_root=medical_root,
        attached_corpora=attached_corpora,
        attached_roots=attached_roots,
        offsec_root=offsec_root,
        news_root=news_root,
    )
