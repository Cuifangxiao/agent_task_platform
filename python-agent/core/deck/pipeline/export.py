"""Export node that writes the final PPTX artifact."""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.export import DeckExporter
from core.deck.pipeline.state import DeckGraphState
from core.deck.spec import DeckSpec

logger = logging.getLogger(__name__)


def export_deck(state: DeckGraphState, exporter: DeckExporter) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state["request_id"])
    writer(events.stage_started("export", "Exporting PPTX file"))
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    total = len(deck.slides)
    writer(events.export_progress("Preparing PPTX", 0, total))
    logger.info("deck export start: request_id=%s slides=%s", request_id, total)
    started_at = time.perf_counter()
    result = exporter.export(request_id, deck)
    logger.info("deck export done: request_id=%s path=%s warnings=%s elapsed=%.2fs", request_id, result.path, len(result.warnings), time.perf_counter() - started_at)
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
                "request_id": request_id,
                "filename": result.path.name,
                "path": str(result.path),
                "relative_path": "deck.pptx",
                "warnings": result.warnings,
            },
        )
    )
    return {"artifact_path": str(result.path)}
