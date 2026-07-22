"""Top-level PPT graph that composes the three phase agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.deck.agents.postprocessor import Postprocessor
from core.deck.agents.preprocessor import Preprocessor
from core.deck.agents.processor import Processor
from core.deck.agents.processor.graph import processor_graph_config
from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.html import HTMLAssembler
from core.deck.html.browser import HTMLBrowserRenderer, SecureBrowserSession, SecureBrowserSessionPool
from core.deck.pipeline.state import DeckGraphState


ROOT_NODE_SEQUENCE = (
    "preprocessor_phase",
    "processor_phase",
    "postprocessor_phase",
)
NODE_SEQUENCE = ROOT_NODE_SEQUENCE


class PptGraph:
    """Request-scoped composition of preprocessor, processor, and postprocessor."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: SecureBrowserSession | SecureBrowserSessionPool | None = None,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
        exporter: ImagePptxExporter | None = None,
        preprocessor: Preprocessor | None = None,
        processor: Processor | None = None,
        postprocessor: Postprocessor | None = None,
        max_repairs: int = 2,
        slide_concurrency: int | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.preprocessor = preprocessor or Preprocessor(llms, self.output_dir)
        self.processor = processor or Processor(
            llms,
            self.output_dir,
            browser_session,
            assembler=assembler,
            browser_renderer=browser_renderer,
            max_repairs=max_repairs,
            slide_concurrency=slide_concurrency,
        )
        self.postprocessor = postprocessor or Postprocessor(
            self.output_dir,
            exporter=exporter,
        )
        self.slide_concurrency = self.processor.slide_concurrency
        self.graph = self.compile_graph()

    def compile_graph(self):
        """Compile the same three-phase topology used by the reference project."""

        builder = StateGraph(DeckGraphState)
        builder.add_node("preprocessor_phase", self.preprocessor.graph)
        builder.add_node("processor_phase", self.processor.graph)
        builder.add_node("postprocessor_phase", self.postprocessor.graph)
        builder.add_edge(START, "preprocessor_phase")
        builder.add_edge("preprocessor_phase", "processor_phase")
        builder.add_edge("processor_phase", "postprocessor_phase")
        builder.add_edge("postprocessor_phase", END)
        return builder.compile()

    def invoke(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        return self.graph.invoke(
            input_state,
            config=processor_graph_config(config, self.slide_concurrency),
            **kwargs,
        )

    def stream(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        stream_mode = kwargs.get("stream_mode", "values")
        modes = set(stream_mode) if isinstance(stream_mode, (list, tuple)) else {stream_mode}
        include_subgraphs = "custom" in modes
        resolved_config = processor_graph_config(config, self.slide_concurrency)
        if not include_subgraphs:
            yield from self.graph.stream(
                input_state,
                config=resolved_config,
                **kwargs,
            )
            return

        # Forward custom events from the processor's per-page subgraphs while
        # exposing only root state snapshots to the service.
        kwargs["subgraphs"] = True
        for chunk in self.graph.stream(input_state, config=resolved_config, **kwargs):
            if isinstance(stream_mode, (list, tuple)):
                namespace, mode, data = chunk
                if mode == "values" and namespace:
                    continue
                yield mode, data
                continue
            namespace, data = chunk
            if stream_mode == "values" and namespace:
                continue
            yield data

    def close(self) -> None:
        self.processor.close()


__all__ = ["NODE_SEQUENCE", "PptGraph", "ROOT_NODE_SEQUENCE"]
