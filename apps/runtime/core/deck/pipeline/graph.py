"""Flat LangGraph assembly for the current HTML-first deck pipeline.

The graph deliberately keeps every meaningful authoring and rendering step at
the root level.  This makes repair/fallback routing, per-slide persistence
boundaries, and event ordering observable instead of hiding them inside four
nested graphs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph

from core.deck.export.image_pptx import ImagePptxExporter
from core.deck.html import HTMLAssembler
from core.deck.html.browser import HTMLBrowserRenderer, SecureBrowserSession
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.delivery import DeliveryNodes
from core.deck.pipeline.design import DesignNodes
from core.deck.pipeline.generation import GenerationNodes
from core.deck.pipeline.planning import PlanningNodes
from core.deck.pipeline.state import DeckGraphState


NODE_SEQUENCE = (
    "normalize_request",
    "plan_deck",
    "select_template",
    "create_design_spec",
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
    "advance_slide",
    "assemble_image_pptx",
    "finalize_deck",
)


class PptGraph:
    """Request-scoped, sequential HTML authoring graph.

    Browser and LLM clients are dependency objects owned by the request; only
    JSON-serializable dictionaries and artifact paths enter ``DeckGraphState``.
    The optional node-provider arguments keep the graph easy to exercise with
    deterministic fakes without reintroducing nested phase graphs.
    """

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: SecureBrowserSession | None = None,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
        exporter: ImagePptxExporter | None = None,
        planning_nodes: PlanningNodes | None = None,
        design_nodes: DesignNodes | None = None,
        generation_nodes: GenerationNodes | None = None,
        delivery_nodes: DeliveryNodes | None = None,
        max_repairs: int = 2,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.llm_registry = llms if isinstance(llms, dict) else {}
        self.browser_session = browser_session or SecureBrowserSession()
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
        self.graph = self.compile_graph()

    def compile_graph(self):
        builder = StateGraph(DeckGraphState)
        node_handlers = {
            "normalize_request": (self.planning.normalize_request, "brief"),
            "plan_deck": (self.planning.plan_deck, "outline"),
            "select_template": (self.design.select_template, "style"),
            "create_design_spec": (self.design.create_design_spec, "style"),
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
            "advance_slide": (self.generation.advance_slide, ""),
            "assemble_image_pptx": (self.delivery.assemble_image_pptx, ""),
            "finalize_deck": (self.delivery.finalize_deck, ""),
        }
        for name in NODE_SEQUENCE:
            handler, model_key = node_handlers[name]
            builder.add_node(
                name,
                instrument_node(name, handler, self.llm_registry, model_key=model_key),
            )

        builder.add_edge(START, "normalize_request")
        builder.add_edge("normalize_request", "plan_deck")
        builder.add_edge("plan_deck", "select_template")
        builder.add_edge("select_template", "create_design_spec")
        builder.add_edge("create_design_spec", "prepare_slide")
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
        builder.add_edge("emit_slide_preview", "advance_slide")
        builder.add_conditional_edges(
            "advance_slide",
            _route_after_advance,
            {"more": "prepare_slide", "done": "assemble_image_pptx"},
        )
        builder.add_edge("assemble_image_pptx", "finalize_deck")
        builder.add_edge("finalize_deck", END)
        return builder.compile()

    def invoke(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        return self.graph.invoke(input_state, config=_graph_config(config), **kwargs)

    def stream(self, input_state: Any, config: dict[str, Any] | None = None, **kwargs: Any):
        return self.graph.stream(input_state, config=_graph_config(config), **kwargs)

    def close(self) -> None:
        """Close resources only when this graph created the browser session."""

        if self._owns_browser_session:
            self.browser_session.close()


def _route_after_validation(state: DeckGraphState) -> Literal["valid", "repair", "fallback"]:
    decision = str(state.get("current_render_decision") or "")
    if decision not in {"valid", "repair", "fallback"}:
        raise ValueError(f"unexpected render decision: {decision or '<empty>'}")
    return decision  # type: ignore[return-value]


def _route_after_advance(state: DeckGraphState) -> Literal["more", "done"]:
    plan = state.get("deck_plan") or {}
    slides = plan.get("slides") if isinstance(plan, dict) else None
    total = len(slides) if isinstance(slides, list) else 0
    if total <= 0:
        raise ValueError("DeckPlan has no slides")
    return "done" if int(state.get("current_slide_index") or 0) >= total else "more"


def _graph_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Allow the bounded 30-page loop without weakening caller config."""

    resolved = dict(config or {})
    # 30 pages × (10 normal nodes + up to 2 repair laps) stays below 1,000.
    resolved.setdefault("recursion_limit", 1_000)
    return resolved


__all__ = ["NODE_SEQUENCE", "PptGraph"]
