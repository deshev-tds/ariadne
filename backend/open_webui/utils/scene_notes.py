from __future__ import annotations

from typing import Any, Optional


def normalize_scene_note(scene_note: Any) -> Optional[dict[str, Any]]:
    if not isinstance(scene_note, dict):
        return None

    enabled = bool(scene_note.get("enabled"))
    if not enabled:
        return None

    preset_id = scene_note.get("preset_id")
    preset_id = preset_id.strip() if isinstance(preset_id, str) else None
    preset_id = preset_id or None

    title = scene_note.get("title")
    title = title.strip() if isinstance(title, str) else None
    title = title or None

    note = scene_note.get("note")
    note = note.strip() if isinstance(note, str) else ""

    resolved_note = scene_note.get("resolved_note")
    resolved_note = resolved_note.strip() if isinstance(resolved_note, str) else None
    resolved_note = resolved_note or None

    thumbnail_url = scene_note.get("thumbnail_url")
    thumbnail_url = thumbnail_url.strip() if isinstance(thumbnail_url, str) else None
    thumbnail_url = thumbnail_url or None

    thumbnail_prompt = scene_note.get("thumbnail_prompt")
    thumbnail_prompt = (
        thumbnail_prompt.strip() if isinstance(thumbnail_prompt, str) else None
    )
    thumbnail_prompt = thumbnail_prompt or None

    # The backend should trust the resolved note provided by the UI, but it
    # still needs a safe fallback when older chats only persisted the freeform
    # note itself.
    if resolved_note is None and note:
        resolved_note = note

    if not resolved_note:
        return None

    return {
        "enabled": True,
        "preset_id": preset_id,
        "title": title,
        "note": note,
        "resolved_note": resolved_note,
        "thumbnail_url": thumbnail_url,
        "thumbnail_prompt": thumbnail_prompt,
        "updated_at": scene_note.get("updated_at"),
    }


def render_scene_note_block(scene_note: Optional[dict[str, Any]]) -> Optional[str]:
    if not scene_note:
        return None

    lines = [
        "[Scene Note - Active Scene Framing]",
        "The user deliberately set or updated the current scene for this chat.",
        "Treat the following as the active scene from this point onward.",
        "Do not rewrite earlier messages.",
        "If the existing chat history implies a transition, make a reasonable attempt to let the shift feel natural.",
        "If no explicit transition is needed, simply assume this is the current setting and behavioral frame now.",
        "Do not overwrite the user's actions, thoughts, choices, or consent.",
    ]

    if scene_note.get("title"):
        lines.append(f"Title: {scene_note['title']}")

    lines.extend(["Active Scene:", scene_note["resolved_note"]])
    return "\n".join(lines)
