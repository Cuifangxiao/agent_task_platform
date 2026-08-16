"""Parallel per-slide visual planning, HTML authoring, QA, and capture."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from core.deck.agents.processor.generation import GenerationNodes
from core.deck.domain.schemas import DeckOutlineSpec, DeckPlan
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


DEFAULT_SLIDE_CONCURRENCY = 4

PROCESSOR_NODE_SEQUENCE = (
    "fan_out_slides",
    "generate_slide",
    "aggregate_slides",
)

SLIDE_NODE_SEQUENCE = (
    "prepare_slide",
    "generate_slide_design",
    "generate_slide_html",
    "sanitize_and_assemble_html",
    "render_html_and_measure_dom",
    "validate_dom",
    "repair_slide_html",
    "capture_png",
    "persist_slide_artifacts",
    "emit_slide_preview",
)


class Processor:
    """Own the parallel page phase from a validated outline to page images."""

    def __init__(
        self,
        llms: Any,
        output_dir: Path,
        browser_session: SecureBrowserSession | SecureBrowserSessionPool | None = None,
        *,
        assembler: HTMLAssembler | None = None,
        browser_renderer: HTMLBrowserRenderer | None = None,
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
        self.generation = generation_nodes or GenerationNodes(
            llms,
            self.output_dir,
            assembler=self.assembler,
            browser_renderer=self.browser_renderer,
            max_repairs=max_repairs,
        )
        self.slide_graph = self.compile_slide_graph()
        self.graph = self.compile_graph()

    def prepare_fan_out(self, state: DeckGraphState) -> dict[str, Any]:
        DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
        DeckPlan.model_validate(state.get("deck_plan") or {})
        return {"status": "designing"}

    def compile_slide_graph(self):
        """Compile the isolated visual-plan -> HTML -> repair page graph."""

        builder = StateGraph(DeckGraphState, output_schema=SlideGenerationOutput)
        handlers = {
            "prepare_slide": (self.generation.prepare_slide, ""),
            "generate_slide_design": (self.generation.generate_slide_design, "style"),
            "generate_slide_html": (self.generation.generate_slide_html, "designer"),
            "sanitize_and_assemble_html": (self.generation.sanitize_and_assemble_html, ""),
            "render_html_and_measure_dom": (self.generation.render_html_and_measure_dom, ""),
            "validate_dom": (self.generation.validate_dom, ""),
            "repair_slide_html": (self.generation.repair_slide_html, "designer"),
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
        builder.add_edge("prepare_slide", "generate_slide_design")
        builder.add_edge("generate_slide_design", "generate_slide_html")
        builder.add_edge("generate_slide_html", "sanitize_and_assemble_html")
        builder.add_edge("sanitize_and_assemble_html", "render_html_and_measure_dom")
        builder.add_edge("render_html_and_measure_dom", "validate_dom")
        builder.add_conditional_edges(
            "validate_dom",
            route_after_validation,
            {
                "valid": "capture_png",
                "repair": "repair_slide_html",
            },
        )
        builder.add_edge("repair_slide_html", "sanitize_and_assemble_html")
        builder.add_edge("capture_png", "persist_slide_artifacts")
        builder.add_edge("persist_slide_artifacts", "emit_slide_preview")
        builder.add_edge("emit_slide_preview", END)
        return builder.compile()

    def compile_graph(self):
        """Compile the fan-out/fan-in middle phase."""

        builder = StateGraph(DeckGraphState)
        builder.add_node(
            "fan_out_slides",
            instrument_node("fan_out_slides", self.prepare_fan_out, self.llm_registry, model_key=""),
        )
        builder.add_node("generate_slide", self.slide_graph)
        builder.add_node(
            "aggregate_slides",
            instrument_node(
                "aggregate_slides",
                self.generation.aggregate_slides,
                self.llm_registry,
                model_key="",
            ),
        )
        builder.add_edge(START, "fan_out_slides")
        builder.add_conditional_edges("fan_out_slides", fan_out_slides, ["generate_slide"])
        builder.add_edge("generate_slide", "aggregate_slides")
        builder.add_edge("aggregate_slides", END)
        return builder.compile()

    def run(self, state: DeckGraphState) -> dict[str, Any]:
        return self.graph.invoke(state, config=processor_graph_config(None, self.slide_concurrency))

    def close(self) -> None:
        if self._owns_browser_session:
            self.browser_session.close()


def route_after_validation(state: DeckGraphState) -> Literal["valid", "repair"]:
    decision = str(state.get("current_render_decision") or "")
    if decision not in {"valid", "repair"}:
        raise ValueError(f"unexpected render decision: {decision or '<empty>'}")
    return decision  # type: ignore[return-value]


def fan_out_slides(state: DeckGraphState) -> list[Send]:
    """Create one independent page state after the global outline is complete."""

    outline = DeckOutlineSpec.model_validate(state.get("deck_outline") or {})
    plan = DeckPlan.model_validate(state.get("deck_plan") or {})
    if len(outline.slides) != len(plan.slides):
        raise ValueError("deck outline and renderer plan have different page counts")
    preferences = state.get("layout_preferences") or {}
    if not isinstance(preferences, dict):
        preferences = {}
    ordered_preferences = [dict(preferences.get(slide.slide_id) or {}) for slide in plan.slides]
    shared = dict(state)
    sends: list[Send] = []
    for offset, slide in enumerate(plan.slides):
        if outline.slides[offset].id != slide.slide_id:
            raise ValueError("deck outline and renderer plan slide ids are not aligned")
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
