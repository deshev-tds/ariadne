from __future__ import annotations

from typing import Any

from open_webui.utils.misc import get_content_from_message


def estimate_tokens_from_text(text: str) -> int:
    # Fast deterministic estimate for routing.
    # 4 chars/token is a conservative fallback for English-like text.
    if not text:
        return 0
    return max(1, int(len(text) / 4))


def estimate_tokens_from_message(message: dict[str, Any]) -> int:
    if not isinstance(message, dict):
        return 0

    content = message.get("content")

    if isinstance(content, str):
        return estimate_tokens_from_text(content)

    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += estimate_tokens_from_text(str(part.get("text") or ""))
        return total

    if isinstance(content, dict):
        text = content.get("text") or content.get("content") or ""
        return estimate_tokens_from_text(str(text))

    text = get_content_from_message(message) or ""
    return estimate_tokens_from_text(text)


def estimate_tokens_from_messages(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages or []:
        total += estimate_tokens_from_message(message)
    return total


def clamp_retrieval_lines(
    lines: list[str],
    max_tokens: int,
) -> list[str]:
    if max_tokens <= 0:
        return []

    selected: list[str] = []
    used = 0
    for line in lines:
        needed = estimate_tokens_from_text(line)
        if used + needed > max_tokens:
            break
        selected.append(line)
        used += needed

    return selected
