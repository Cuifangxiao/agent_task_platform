"""Export node that writes the final PPTX artifact."""

from __future__ import annotations

from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.export import DeckExporter
from core.deck.pipeline.state import DeckGraphState
from core.deck.spec import DeckSpec


def export_deck(state: DeckGraphState, exporter: DeckExporter) -> dict[str, Any]:
    writer = get_stream_writer()
    writer(events.stage_started("export", "Exporting PPTX file"))
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    total = len(deck.slides)
    writer(events.export_progress("Preparing PPTX", 0, total))
    result = exporter.export(str(state["request_id"]), deck)
    for warning in result.warnings:
        writer(
            events.stage_progress("export", warning, {"status": "warning", "warning": warning})
        )
    writer(events.export_progress("PPTX export complete", total, total))
    writer(
        events.artifact(
            "export",
            "PPTX file ready",
            str(result.path),
            {
                "request_id": state["request_id"],
                "filename": result.path.name,
                "path": str(result.path),
                "relative_path": "deck.pptx",
                "warnings": result.warnings,
            },
        )
    )
    return {"artifact_path": str(result.path)}
