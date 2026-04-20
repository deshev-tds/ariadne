from __future__ import annotations

import json
from typing import Any, Iterable


def normalize_configured_skill_ids(value: Any) -> list[str]:
    if value is None:
        return []

    items: list[Any]
    if isinstance(value, str):
        raw_value = value.strip()
        if not raw_value:
            return []

        if raw_value.startswith("["):
            try:
                parsed = json.loads(raw_value)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                items = parsed
            else:
                items = raw_value.split(",")
        else:
            items = raw_value.split(",")
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        skill_id = str(item or "").strip()
        if skill_id and skill_id not in seen:
            seen.add(skill_id)
            normalized.append(skill_id)
    return normalized


def resolve_science_lane_default_skill_ids(
    working_mode: Any, configured_skill_ids: Any
) -> set[str]:
    normalized_working_mode = str(working_mode or "").strip().lower()
    if normalized_working_mode != "science":
        return set()

    return set(normalize_configured_skill_ids(configured_skill_ids))


def build_science_lane_skill_sets(
    *,
    working_mode: Any,
    configured_skill_ids: Any,
    user_skill_ids: Iterable[str] | None = None,
    model_skill_ids: Iterable[str] | None = None,
) -> tuple[set[str], set[str], set[str]]:
    science_lane_default_skill_ids = resolve_science_lane_default_skill_ids(
        working_mode, configured_skill_ids
    )
    explicit_skill_ids = set(user_skill_ids or []) | science_lane_default_skill_ids
    all_skill_ids = explicit_skill_ids | set(model_skill_ids or [])
    return science_lane_default_skill_ids, explicit_skill_ids, all_skill_ids
