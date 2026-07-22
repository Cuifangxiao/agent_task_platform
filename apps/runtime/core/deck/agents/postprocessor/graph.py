"""Postprocessor graph responsible only for final export and verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.deck.agents.postprocessor.export import ExportNodes
from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.state import DeckGraphState


POSTPROCESSOR_NODE_SEQUENCE = (
    "assemble_image_pptx",
    "finalize_deck",
)


class Postprocessor:
    """Own the terminal phase from validated page images to a PPTX artifact."""

    def __init__(
        self,
        output_dir: Path,
        *,
        exporter: ImagePptxExporter | None = None,
        export_nodes: ExportNodes | None = None,
    ) -> None:
        self.output_dir = Path(output_dir).resolve()
        self.export = export_nodes or ExportNodes(self.output_dir, exporter=exporter)
        self.graph = self.compile_graph()

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        handlers = {
            "assemble_image_pptx": self.export.assemble_image_pptx,
            "finalize_deck": self.export.finalize_deck,
        }
        for name in POSTPROCESSOR_NODE_SEQUENCE:
            builder.add_node(name, instrument_node(name, handlers[name], {}, model_key=""))
        builder.add_edge(START, "assemble_image_pptx")
        builder.add_edge("assemble_image_pptx", "finalize_deck")
        builder.add_edge("finalize_deck", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return self.graph.invoke(state)


__all__ = ["POSTPROCESSOR_NODE_SEQUENCE", "Postprocessor"]
