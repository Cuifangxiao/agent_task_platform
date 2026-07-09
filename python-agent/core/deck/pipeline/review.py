"""Deterministic validation node for deck specs and rendered artifacts."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import ValidationIssue, dedupe_issue_dicts, issues_to_dicts, validate_deck_spec, validate_theme_contrast
from core.deck.spec import DeckSpec


def validate_deck(state: DeckGraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    issues = validate_theme_contrast(deck) + validate_deck_spec(deck) + validate_fact_sources(deck, state.get("brief") or {})
    current = list(state.get("validation_issues") or [])
    if issues:
        current.extend(issues_to_dicts(issues))
    current = dedupe_issue_dicts(current)
    writer(events.validation_report(current))
    return {"validation_issues": current}


def validate_fact_sources(deck: DeckSpec, brief: dict[str, Any]) -> list[ValidationIssue]:
    fact_ids = {str(item.get("id") or "").strip() for item in brief.get("facts") or [] if isinstance(item, dict)}
    issues: list[ValidationIssue] = []
    for slide in deck.slides:
        missing = [source for source in slide.sources if source.startswith("fact#") and source not in fact_ids]
        if missing:
            issues.append(
                ValidationIssue(
                    page=slide.index,
                    severity="nice_to_have",
                    kind="missing_fact_source",
                    description=f"Slide cites missing fact ids: {', '.join(missing)}.",
                    suggestion="Use only fact ids from the brief or remove unsupported citations.",
                )
            )
    return issues
