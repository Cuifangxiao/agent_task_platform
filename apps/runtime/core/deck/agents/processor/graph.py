"""Processor graph for layout allocation, HTML authoring, QA, and repair."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from core.deck.agents.processor.design import DesignNodes
from core.deck.agents.processor.generation import GenerationNodes
from core.deck.agents.processor.layout import LayoutPlanningNodes
from core.deck.domain.schemas import DeckPlan
from core.deck.html import HTMLAssembler
from core.deck.html.browser import (
    HTMLBrowserRenderer,
    SecureBrowserSession,
    SecureBrowserSessionPool,
    resolve_browser_concurrency,
)
from core.deck.pipeline.instrumentation import instrument_node
from core.deck.pipeline.state import DeckGraphState, SlideGenerationOutput
from core.deck.runtime.llm import normalize_llm_registry


DEFAULT_SLIDE_CONCURRENCY = 6

PROCESSOR_NODE_SEQUENCE = (
    "prepare_processor_input",
    "select_template",
    "create_design_spec",
    "generate_slide",
    "aggregate_slides",
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


class Processor:
    """Own the complete middle phase from PagePlan to validated page images."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: SecureBrowserSession | SecureBrowserSessionPool | None = None,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
        layout_nodes: LayoutPlanningNodes | None = None,
        design_nodes: DesignNodes | None = None,
        generation_nodes: GenerationNodes | None = None,
        max_repairs: int = 2,
        slide_concurrency: int | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.slide_concurrency = resolve_slide_concurrency(slide_concurrency)
        self.llm_registry = normalize_llm_registry(llms)
        self.browser_session = browser_session or SecureBrowserSessionPool(
            resolve_browser_concurrency(slide_concurrency=self.slide_concurrency)
        )
        self._owns_browser_session = browser_session is None
        self.assembler = assembler or HTMLAssembler()
        self.browser_renderer = browser_renderer or HTMLBrowserRenderer(self.browser_session)

        self.layout = layout_nodes or LayoutPlanningNodes(llms, self.output_dir)
        self.design = design_nodes or DesignNodes(llms, self.output_dir)
        self.generation = generation_nodes or GenerationNodes(
            llms,
            self.output_dir,
            assembler=self.assembler,
            browser_renderer=self.browser_renderer,
            max_repairs=max_repairs,
        )
        self.slide_graph = self.compile_slide_graph()
        self.graph = self.compile_graph()

    def compile_slide_graph(self):
        """Compile the isolated single-page HTML authoring and repair graph."""

        builder = StateGraph(DeckGraphState, output_schema=SlideGenerationOutput)
        handlers = {
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
            handler, model_key = handlers[name]
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
            route_after_validation,
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
        """Compile the middle phase around the parallel page subgraph."""

        builder = StateGraph(DeckGraphState)
        handlers = {
            "prepare_processor_input": (self.layout.prepare_processor_input, ""),
            "select_template": (self.design.select_template, "style"),
            "create_design_spec": (self.design.create_design_spec, "style"),
            "aggregate_slides": (self.generation.aggregate_slides, ""),
        }
        for name in PROCESSOR_NODE_SEQUENCE:
            if name == "generate_slide":
                builder.add_node(name, self.slide_graph)
                continue
            handler, model_key = handlers[name]
            builder.add_node(
                name,
                instrument_node(name, handler, self.llm_registry, model_key=model_key),
            )

        builder.add_edge(START, "prepare_processor_input")
        builder.add_edge("prepare_processor_input", "select_template")
        builder.add_edge("select_template", "create_design_spec")
        builder.add_conditional_edges("create_design_spec", fan_out_slides, ["generate_slide"])
        builder.add_edge("generate_slide", "aggregate_slides")
        builder.add_edge("aggregate_slides", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return self.graph.invoke(state, config=processor_graph_config(None, self.slide_concurrency))

    def close(self) -> None:
        if self._owns_browser_session:
            self.browser_session.close()


def route_after_validation(state: DeckGraphState) -> Literal["valid", "repair", "fallback"]:
    decision = str(state.get("current_render_decision") or "")
    if decision not in {"valid", "repair", "fallback"}:
        raise ValueError(f"unexpected render decision: {decision or '<empty>'}")
    return decision  # type: ignore[return-value]


def fan_out_slides(state: DeckGraphState) -> list[Send]:
    """Create one independent page state after processor planning and design."""

    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    preferences = state.get("layout_preferences") or {}
    if not isinstance(preferences, dict):
        preferences = {}
    ordered_preferences = [dict(preferences.get(slide.slide_id) or {}) for slide in plan.slides]
    shared = dict(state)
    sends: list[Send] = []
    for offset, _slide in enumerate(plan.slides):
        branch_state = {
            **shared,
            "current_slide_index": offset,
            "current_layout_preference": ordered_preferences[offset],
            "previous_compositions": ordered_preferences[max(0, offset - 4) : offset],
            "completed_slides": {},
            "layout_history": [],
            "validation_issues": [],
            "status": "generating",
        }
        sends.append(Send("generate_slide", branch_state))
    return sends


def resolve_slide_concurrency(value: int | None = None) -> int:
    raw: Any = value if value is not None else os.getenv(
        "DECK_SLIDE_CONCURRENCY",
        str(DEFAULT_SLIDE_CONCURRENCY),
    )
    try:
        resolved = int(raw)
    except (TypeError, ValueError):
        resolved = DEFAULT_SLIDE_CONCURRENCY
    return max(1, min(30, resolved))


def processor_graph_config(
    config: dict[str, Any] | None,
    slide_concurrency: int = DEFAULT_SLIDE_CONCURRENCY,
) -> dict[str, Any]:
    resolved = dict(config or {})
    resolved.setdefault("max_concurrency", resolve_slide_concurrency(slide_concurrency))
    resolved.setdefault("recursion_limit", 1_000)
    return resolved


__all__ = [
    "DEFAULT_SLIDE_CONCURRENCY",
    "PROCESSOR_NODE_SEQUENCE",
    "SLIDE_NODE_SEQUENCE",
    "Processor",
    "fan_out_slides",
    "processor_graph_config",
    "resolve_slide_concurrency",
    "route_after_validation",
]
