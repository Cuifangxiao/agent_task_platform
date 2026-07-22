"""Map-reduce LangGraph assembly for the HTML-first deck pipeline.

Planning and design remain global.  Once the immutable DesignSpec exists, the
root graph uses ``Send`` to run one explicit authoring subgraph per slide and
merges reducer-backed results before delivery.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.html import HTMLAssembler
from core.deck.html.browser import (
    HTMLBrowserRenderer,
    SecureBrowserSession,
    SecureBrowserSessionPool,
    resolve_browser_concurrency,
)
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.delivery import DeliveryNodes
from core.deck.pipeline.design import DesignNodes
from core.deck.pipeline.generation import GenerationNodes
from core.deck.pipeline.planning import PlanningNodes
from core.deck.pipeline.state import DeckGraphState, SlideGenerationOutput
from core.deck.domain.schemas import DeckPlan


DEFAULT_SLIDE_CONCURRENCY = 6

ROOT_NODE_SEQUENCE = (
    "normalize_request",
    "plan_deck",
    "select_template",
    "create_design_spec",
    "generate_slide",
    "aggregate_slides",
    "assemble_image_pptx",
    "finalize_deck",
)

SLIDE_NODE_SEQUENCE = (
    "prepare_slide",
    "generate_slide_content",
    "generate_slide_html",
    "sanitize_and_assemble_html",
    "render_html_and_measure_dom",
    "validate_dom",
    "repair_slide_html",
    "build_fallback_html",
    "capture_png",
    "persist_slide_artifacts",
    "emit_slide_preview",
)

# Backwards-compatible public name now describes the root graph.  The explicit
# per-slide sequence is available separately as ``SLIDE_NODE_SEQUENCE``.
NODE_SEQUENCE = ROOT_NODE_SEQUENCE


class PptGraph:
    """Request-scoped, parallel HTML authoring graph.

    Browser and LLM clients are dependency objects owned by the request; only
    JSON-serializable dictionaries and artifact paths enter ``DeckGraphState``.
    The optional node-provider arguments keep the graph easy to exercise with
    deterministic fakes while the single-slide subgraph remains explicit.
    """

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: SecureBrowserSession | SecureBrowserSessionPool | None = None,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
        exporter: ImagePptxExporter | None = None,
        planning_nodes: PlanningNodes | None = None,
        design_nodes: DesignNodes | None = None,
        generation_nodes: GenerationNodes | None = None,
        delivery_nodes: DeliveryNodes | None = None,
        max_repairs: int = 2,
        slide_concurrency: int | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.slide_concurrency = resolve_slide_concurrency(slide_concurrency)
        self.llm_registry = llms if isinstance(llms, dict) else {}
        self.browser_session = browser_session or SecureBrowserSessionPool(
            resolve_browser_concurrency(slide_concurrency=self.slide_concurrency)
        )
        self._owns_browser_session = browser_session is None
        self.assembler = assembler or HTMLAssembler()
        self.browser_renderer = browser_renderer or HTMLBrowserRenderer(self.browser_session)
        self.exporter = exporter or ImagePptxExporter(self.output_dir)

        self.planning = planning_nodes or PlanningNodes(llms, self.output_dir)
        self.design = design_nodes or DesignNodes(llms, self.output_dir)
        self.generation = generation_nodes or GenerationNodes(
            llms,
            self.output_dir,
            assembler=self.assembler,
            browser_renderer=self.browser_renderer,
            max_repairs=max_repairs,
        )
        self.delivery = delivery_nodes or DeliveryNodes(
            self.output_dir,
            exporter=self.exporter,
        )
        self.slide_graph = self.compile_slide_graph()
        self.graph = self.compile_graph()

    def compile_slide_graph(self):
        """Compile the isolated single-slide authoring/repair pipeline."""

        builder = StateGraph(DeckGraphState, output_schema=SlideGenerationOutput)
        node_handlers = {
            "prepare_slide": (self.generation.prepare_slide, ""),
            "generate_slide_content": (self.generation.generate_slide_content, "writer"),
            "generate_slide_html": (self.generation.generate_slide_html, "designer"),
            "sanitize_and_assemble_html": (self.generation.sanitize_and_assemble_html, ""),
            "render_html_and_measure_dom": (self.generation.render_html_and_measure_dom, ""),
            "validate_dom": (self.generation.validate_dom, ""),
            "repair_slide_html": (self.generation.repair_slide_html, "designer"),
            "build_fallback_html": (self.generation.build_fallback_html, ""),
            "capture_png": (self.generation.capture_png, ""),
            "persist_slide_artifacts": (self.generation.persist_slide_artifacts, ""),
            "emit_slide_preview": (self.generation.emit_slide_preview, ""),
        }
        for name in SLIDE_NODE_SEQUENCE:
            handler, model_key = node_handlers[name]
            builder.add_node(
                name,
                instrument_node(name, handler, self.llm_registry, model_key=model_key),
            )

        builder.add_edge(START, "prepare_slide")
        builder.add_edge("prepare_slide", "generate_slide_content")
        builder.add_edge("generate_slide_content", "generate_slide_html")
        builder.add_edge("generate_slide_html", "sanitize_and_assemble_html")
        builder.add_edge("sanitize_and_assemble_html", "render_html_and_measure_dom")
        builder.add_edge("render_html_and_measure_dom", "validate_dom")
        builder.add_conditional_edges(
            "validate_dom",
            _route_after_validation,
            {
                "valid": "capture_png",
                "repair": "repair_slide_html",
                "fallback": "build_fallback_html",
            },
        )
        builder.add_edge("repair_slide_html", "sanitize_and_assemble_html")
        builder.add_edge("build_fallback_html", "sanitize_and_assemble_html")
        builder.add_edge("capture_png", "persist_slide_artifacts")
        builder.add_edge("persist_slide_artifacts", "emit_slide_preview")
        builder.add_edge("emit_slide_preview", END)
        return builder.compile()

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        node_handlers = {
            "normalize_request": (self.planning.normalize_request, "brief"),
            "plan_deck": (self.planning.plan_deck, "outline"),
            "select_template": (self.design.select_template, "style"),
            "create_design_spec": (self.design.create_design_spec, "style"),
            "aggregate_slides": (self.generation.aggregate_slides, ""),
            "assemble_image_pptx": (self.delivery.assemble_image_pptx, ""),
            "finalize_deck": (self.delivery.finalize_deck, ""),
        }
        for name in ROOT_NODE_SEQUENCE:
            if name == "generate_slide":
                builder.add_node(name, self.slide_graph)
                continue
            handler, model_key = node_handlers[name]
            builder.add_node(
                name,
                instrument_node(name, handler, self.llm_registry, model_key=model_key),
            )

        builder.add_edge(START, "normalize_request")
        builder.add_edge("normalize_request", "plan_deck")
        builder.add_edge("plan_deck", "select_template")
        builder.add_edge("select_template", "create_design_spec")
        builder.add_conditional_edges(
            "create_design_spec",
            _fan_out_slides,
            ["generate_slide"],
        )
        builder.add_edge("generate_slide", "aggregate_slides")
        builder.add_edge("aggregate_slides", "assemble_image_pptx")
        builder.add_edge("assemble_image_pptx", "finalize_deck")
        builder.add_edge("finalize_deck", END)
        return builder.compile()

    def invoke(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        return self.graph.invoke(
            input_state,
            config=_graph_config(config, self.slide_concurrency),
            **kwargs,
        )

    def stream(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        stream_mode = kwargs.get("stream_mode", "values")
        modes = set(stream_mode) if isinstance(stream_mode, (list, tuple)) else {stream_mode}
        include_subgraphs = "custom" in modes
        if not include_subgraphs:
            yield from self.graph.stream(
                input_state,
                config=_graph_config(config, self.slide_concurrency),
                **kwargs,
            )
            return

        # Native subgraph streaming adds a namespace tuple.  Forward custom
        # page events from every slide branch but keep values snapshots rooted
        # at the deck graph so callers never mistake branch-local state for the
        # final result.
        kwargs["subgraphs"] = True
        for chunk in self.graph.stream(
            input_state,
            config=_graph_config(config, self.slide_concurrency),
            **kwargs,
        ):
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
        """Close resources only when this graph created the browser session."""

        if self._owns_browser_session:
            self.browser_session.close()


def _route_after_validation(state: DeckGraphState) -> Literal["valid", "repair", "fallback"]:
    decision = str(state.get("current_render_decision") or "")
    if decision not in {"valid", "repair", "fallback"}:
        raise ValueError(f"unexpected render decision: {decision or '<empty>'}")
    return decision  # type: ignore[return-value]


def _fan_out_slides(state: DeckGraphState) -> list[Send]:
    """Create one independent slide state after the immutable design is ready."""

    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    preferences = state.get("layout_preferences") or {}
    if not isinstance(preferences, dict):
        preferences = {}
    ordered_preferences = [
        dict(preferences.get(slide.slide_id) or {})
        for slide in plan.slides
    ]
    shared = dict(state)
    sends: list[Send] = []
    for offset, slide in enumerate(plan.slides):
        branch_state = {
            **shared,
            "current_slide_index": offset,
            "current_layout_preference": ordered_preferences[offset],
            "previous_compositions": ordered_preferences[max(0, offset - 4) : offset],
            # Fan-out branches must return only their own reducer contribution.
            "completed_slides": {},
            "layout_history": [],
            "validation_issues": [],
            "status": "generating",
        }
        sends.append(Send("generate_slide", branch_state))
    return sends


def resolve_slide_concurrency(value: int | None = None) -> int:
    """Resolve a bounded Send concurrency, configurable per deployment."""

    raw: Any = value if value is not None else os.getenv(
        "DECK_SLIDE_CONCURRENCY",
        str(DEFAULT_SLIDE_CONCURRENCY),
    )
    try:
        resolved = int(raw)
    except (TypeError, ValueError):
        resolved = DEFAULT_SLIDE_CONCURRENCY
    return max(1, min(30, resolved))


def _graph_config(
    config: dict[str, Any] | None,
    slide_concurrency: int = DEFAULT_SLIDE_CONCURRENCY,
) -> dict[str, Any]:
    """Apply bounded fan-out defaults without weakening caller config."""

    resolved = dict(config or {})
    resolved.setdefault("max_concurrency", resolve_slide_concurrency(slide_concurrency))
    # Repair loops are per branch; retain headroom for the maximum repair path.
    resolved.setdefault("recursion_limit", 1_000)
    return resolved


__all__ = [
    "DEFAULT_SLIDE_CONCURRENCY",
    "NODE_SEQUENCE",
    "PptGraph",
    "ROOT_NODE_SEQUENCE",
    "SLIDE_NODE_SEQUENCE",
    "resolve_slide_concurrency",
]
