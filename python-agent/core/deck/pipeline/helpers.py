"""Small deterministic helpers shared by pipeline nodes."""

from __future__ import annotations

from typing import Any

import core.deck.events as events
from core.deck.pipeline.state import DeckGraphState


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
