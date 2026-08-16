"""Top-level three-stage PPT graph with deterministic render/export infrastructure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.deck.agents.postprocessor import Postprocessor
from core.deck.agents.preprocessor import Preprocessor
from core.deck.agents.processor import Processor
from core.deck.agents.processor.graph import fan_out_slides, processor_graph_config
from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.html import HTMLAssembler
from core.deck.html.browser import HTMLBrowserRenderer, SecureBrowserSession, SecureBrowserSessionPool
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.state import DeckGraphState


ROOT_NODE_SEQUENCE = (
    "generate_deck_outline",
    "fan_out_slides",
    "generate_slide",
    "aggregate_slides",
    "assemble_image_pptx",
    "finalize_deck",
)
NODE_SEQUENCE = ROOT_NODE_SEQUENCE


class PptGraph:
    """Request-scoped outline -> per-page design/HTML -> PPTX graph."""

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
        max_repairs: int = 3,
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
        """Compile the requested three-model topology as the real root graph."""

        llm_registry = self.preprocessor.llm_registry
        builder = StateGraph(DeckGraphState)
        builder.add_node(
            "generate_deck_outline",
            instrument_node(
                "generate_deck_outline",
                self.preprocessor.generate_deck_outline,
                llm_registry,
                model_key="outline",
            ),
        )
        builder.add_node(
            "fan_out_slides",
            instrument_node(
                "fan_out_slides",
                self.processor.prepare_fan_out,
                llm_registry,
                model_key="",
            ),
        )
        builder.add_node("generate_slide", self.processor.slide_graph)
        builder.add_node(
            "aggregate_slides",
            instrument_node(
                "aggregate_slides",
                self.processor.generation.aggregate_slides,
                llm_registry,
                model_key="",
            ),
        )
        builder.add_node(
            "assemble_image_pptx",
            instrument_node(
                "assemble_image_pptx",
                self.postprocessor.export.assemble_image_pptx,
                {},
                model_key="",
            ),
        )
        builder.add_node(
            "finalize_deck",
            instrument_node(
                "finalize_deck",
                self.postprocessor.export.finalize_deck,
                {},
                model_key="",
            ),
        )
        builder.add_edge(START, "generate_deck_outline")
        builder.add_edge("generate_deck_outline", "fan_out_slides")
        builder.add_conditional_edges("fan_out_slides", fan_out_slides, ["generate_slide"])
        builder.add_edge("generate_slide", "aggregate_slides")
        builder.add_edge("aggregate_slides", "assemble_image_pptx")
        builder.add_edge("assemble_image_pptx", "finalize_deck")
        builder.add_edge("finalize_deck", END)
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
