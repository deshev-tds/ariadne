from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from open_webui.retrieval.local_corpus import resolve_local_corpus_root
from open_webui.retrieval.local_corpus_reasoning import normalize_local_corpus_mode
from open_webui.retrieval.working_mode import normalize_working_mode


@dataclass(frozen=True)
class CorpusRuntimeSelection:
    working_mode: str
    local_corpus_mode: str
    science_root: Optional[Path]
    offsec_root: Optional[Path]

    @property
    def science_enabled(self) -> bool:
        return self.science_root is not None

    @property
    def offsec_enabled(self) -> bool:
        return self.offsec_root is not None

    @property
    def any_enabled(self) -> bool:
        return self.science_enabled or self.offsec_enabled


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

    resolved = candidate.expanduser().resolve()
    if not resolved.exists():
        return None
    return resolved


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
        return CorpusRuntimeSelection(
            working_mode=working_mode,
            local_corpus_mode=local_corpus_mode,
            science_root=None,
            offsec_root=None,
        )

    science_root = None
    offsec_root = None

    if working_mode == "science":
        science_root = resolve_local_corpus_root(config_or_path)
    elif working_mode == "offsec":
        offsec_root = resolve_offsec_corpus_root(config_or_path)

    return CorpusRuntimeSelection(
        working_mode=working_mode,
        local_corpus_mode=local_corpus_mode,
        science_root=science_root,
        offsec_root=offsec_root,
    )
