"""Small deterministic helpers shared by concrete agent graphs."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

import core.deck.events as events
from core.deck.state import DeckGraphState


def emit_agent(writer: Any, stage: str, agent: str, message: str) -> None:
    writer(events.agent_message(stage, agent, message))


def fallback_slide_title(index: int, state: DeckGraphState) -> str:
    deck_title = str(state.get("deck_title") or "Untitled presentation")
    if index == 1:
        return deck_title
    return f"{deck_title} - Part {index}"


def component_from_layout(value: Any, index: int) -> str:
    layout = str(value or "").strip().lower()
    if index == 0 or layout == "title":
        return "cover"
    if layout == "section":
        return "section"
    if layout == "comparison":
        return "comparison"
    if layout == "summary":
        return "summary"
    return "content-basic"


def clamp_slide_count(value: Any) -> int:
    try:
        page_count = int(value)
    except (TypeError, ValueError):
        page_count = 8
    return max(1, min(page_count, 30))


def truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


def extract_number_tokens(value: str) -> list[str]:
    """Extract human-visible numbers without missing CJK-adjacent values.

    ASCII identifiers such as ``s01`` and ``v2`` are ignored, while ordinary
    prose such as ``增长35%`` or ``第3阶段`` is treated as numeric evidence.
    """

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    result: list[str] = []
    for match in re.finditer(r"\d+(?:[.,]\d+)*(?:%|％)?", normalized):
        # Match the whole candidate first, then discard it when it belongs to
        # an ASCII identifier. A lookbehind-only regex can restart after the
        # decimal point and misread ``v2.0`` as the independent number ``0``.
        previous = normalized[match.start() - 1] if match.start() else ""
        if previous and (previous.isascii() and (previous.isalnum() or previous == "_")):
            continue
        result.append(match.group(0).replace(",", "").replace("％", "%"))
    return result
