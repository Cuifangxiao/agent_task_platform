"""Top-level PPT graph assembly.

Each phase owns its internal LangGraph.  This runner only connects phases in
the same explicit style as the soulmate ``GraphModule``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.deck.agents import Design, Generation, Planning, Postprocessor
from core.deck.export import DeckExporter
from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.state import DeckGraphState


class PptGraph:
    """Request-scoped root graph composed from four concrete phases."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: BrowserSession | None = None,
        *,
        html_renderer: HTMLSlideRenderer | None = None,
        browser_renderer: BrowserRenderer | None = None,
        exporter: DeckExporter | None = None,
        planning: Any | None = None,
        design: Any | None = None,
        generation: Any | None = None,
        postprocessor: Any | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.planning = planning if planning is not None else Planning(llms)
        self.design = design if design is not None else Design(llms, self.output_dir)
        self.generation = (
            generation
            if generation is not None
            else Generation(
                llms,
                self.output_dir,
                browser_session,
                html_renderer=html_renderer,
                browser_renderer=browser_renderer,
            )
        )
        self.postprocessor = (
            postprocessor
            if postprocessor is not None
            else Postprocessor(
                self.output_dir,
                browser_session,
                exporter=exporter,
            )
        )
        self.graph = self.compile_graph()

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        builder.add_node("planning_phase", self.run_planning_phase)
        builder.add_node("design_phase", self.run_design_phase)
        builder.add_node("generation_phase", self.run_generation_phase)
        builder.add_node("postprocessor_phase", self.run_postprocessor_phase)

        builder.add_edge(START, "planning_phase")
        builder.add_edge("planning_phase", "design_phase")
        builder.add_edge("design_phase", "generation_phase")
        builder.add_edge("generation_phase", "postprocessor_phase")
        builder.add_edge("postprocessor_phase", END)
        return builder.compile()

    def run_planning_phase(self, state: DeckGraphState) -> dict[str, Any]:
        return self.planning.run(state)

    def run_design_phase(self, state: DeckGraphState) -> dict[str, Any]:
        return self.design.run(state)

    def run_generation_phase(self, state: DeckGraphState) -> dict[str, Any]:
        return self.generation.run(state)

    def run_postprocessor_phase(self, state: DeckGraphState) -> dict[str, Any]:
        return self.postprocessor.run(state)

    def invoke(self, *args: Any, **kwargs: Any):
        return self.graph.invoke(*args, **kwargs)

    def stream(self, *args: Any, **kwargs: Any):
        return self.graph.stream(*args, **kwargs)


__all__ = ["PptGraph"]
