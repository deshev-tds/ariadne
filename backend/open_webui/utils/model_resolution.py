from __future__ import annotations

from typing import Any, Mapping, Optional


def build_model_id_candidates(*raw_ids: Optional[str]) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    for raw_id in raw_ids:
        if raw_id is None:
            continue

        candidate = str(raw_id).strip()
        if not candidate:
            continue

        for value in (candidate, candidate.split(":")[0]):
            if value and value not in seen:
                seen.add(value)
                candidates.append(value)

    return candidates


def resolve_model_base_model_id(
    *,
    model: Optional[Mapping[str, Any]] = None,
    model_info: Any = None,
    base_model_id_override: Optional[str] = None,
) -> Optional[str]:
    if base_model_id_override is not None:
        candidate = str(base_model_id_override).strip()
        return candidate or None

    info = model.get("info") if isinstance(model, Mapping) else None
    if not isinstance(info, Mapping):
        info = {}

    base_model_id = getattr(model_info, "base_model_id", None)
    if not base_model_id and isinstance(model, Mapping):
        base_model_id = model.get("base_model_id") or info.get("base_model_id")

    if base_model_id is None:
        return None

    candidate = str(base_model_id).strip()
    return candidate or None


def resolve_runtime_model_reference(
    models_map: Mapping[str, Mapping[str, Any]],
    *,
    model_id: Optional[str] = None,
    model: Optional[Mapping[str, Any]] = None,
    model_info: Any = None,
    base_model_id_override: Optional[str] = None,
) -> dict[str, Any]:
    selected_model_id = model_id or (
        str(model.get("id")).strip() if isinstance(model, Mapping) and model.get("id") else None
    )

    display_model = model if isinstance(model, Mapping) else None
    if display_model is None:
        for candidate_id in build_model_id_candidates(selected_model_id):
            candidate = models_map.get(candidate_id)
            if candidate is not None:
                display_model = candidate
                break

    base_model_id = resolve_model_base_model_id(
        model=display_model or model,
        model_info=model_info,
        base_model_id_override=base_model_id_override,
    )

    runtime_model = None
    resolved_model_id = None
    base_model_resolved = False

    for candidate_id in build_model_id_candidates(base_model_id):
        candidate = models_map.get(candidate_id)
        if candidate is not None:
            runtime_model = candidate
            resolved_model_id = candidate.get("id") or candidate_id
            base_model_resolved = True
            break

    if base_model_id and resolved_model_id is None:
        resolved_model_id = base_model_id

    if runtime_model is None:
        for candidate_id in build_model_id_candidates(selected_model_id):
            candidate = models_map.get(candidate_id)
            if candidate is not None:
                runtime_model = candidate
                if resolved_model_id is None:
                    resolved_model_id = candidate.get("id") or candidate_id
                break

    if runtime_model is None and isinstance(display_model, Mapping):
        runtime_model = display_model
        if resolved_model_id is None:
            resolved_model_id = display_model.get("id") or selected_model_id

    if display_model is None and runtime_model is not None:
        display_model = runtime_model

    if resolved_model_id is None:
        resolved_model_id = base_model_id or selected_model_id

    return {
        "display_model": display_model,
        "runtime_model": runtime_model,
        "resolved_model_id": resolved_model_id,
        "selected_model_id": selected_model_id,
        "base_model_id": base_model_id,
        "base_model_resolved": base_model_resolved,
    }
