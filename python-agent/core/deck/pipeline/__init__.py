"""Assemble the deck LangGraph topology; node behavior lives in sibling modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langgraph.graph import END, START, StateGraph

from core.deck.export import DeckExporter
from core.deck.pipeline.brief import prepare_brief
from core.deck.pipeline.export import export_deck
from core.deck.pipeline.llm import normalize_llm_registry
from core.deck.pipeline.outline import generate_outline, review_outline
from core.deck.pipeline.repair import has_fixable_must_fix, repair_deck
from core.deck.pipeline.review import validate_deck
from core.deck.pipeline.slides import write_and_design_slides
from core.deck.pipeline.state import DeckGraphState
from core.deck.pipeline.style import prepare_style_options, review_style
from core.deck.pipeline.vlm_review import review_vlm
from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.rendering.html import HTMLSlideRenderer


def build_deck_graph(
    llms: Any,
    output_dir: Path,
    checkpointer: Any = None,
    browser_session: BrowserSession | None = None,
):
    llm_registry = normalize_llm_registry(llms)
    html_renderer = HTMLSlideRenderer()
    browser = BrowserRenderer(output_dir, browser_session)
    exporter = DeckExporter(output_dir, browser_session)

    graph = StateGraph(DeckGraphState)
    graph.add_node("brief", lambda state: prepare_brief(state, llm_registry))
    graph.add_node("outline", lambda state: generate_outline(state, llm_registry))
    graph.add_node("outline_review", review_outline)
    graph.add_node("style", lambda state: prepare_style_options(state, html_renderer, browser, llm_registry))
    graph.add_node("style_review", lambda state: review_style(state, llm_registry))
    graph.add_node("write_slides", lambda state: write_and_design_slides(state, llm_registry, html_renderer, browser))
    graph.add_node("validate", validate_deck)
    graph.add_node("repair", lambda state: repair_deck(state, llm_registry, html_renderer, browser))
    graph.add_node("review_vlm", lambda state: review_vlm(state, llm_registry, output_dir))
    graph.add_node("export", lambda state: export_deck(state, exporter))

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "outline_review")
    graph.add_edge("outline_review", "style")
    graph.add_edge("style", "style_review")
    graph.add_edge("style_review", "write_slides")
    graph.add_edge("write_slides", "validate")
    graph.add_conditional_edges("validate", _route_after_validate, {"repair": "repair", "review_vlm": "review_vlm", "export": "export"})
    graph.add_edge("repair", "validate")
    graph.add_conditional_edges("review_vlm", _route_after_vlm, {"repair": "repair", "export": "export"})
    graph.add_edge("export", END)
    return graph.compile(checkpointer=checkpointer)


def _route_after_validate(state: DeckGraphState) -> str:
    if has_fixable_must_fix(state):
        return "repair"
    if not state.get("vlm_reviewed"):
        return "review_vlm"
    return "export"


def _route_after_vlm(state: DeckGraphState) -> str:
    return "repair" if has_fixable_must_fix(state) else "export"


__all__ = ["build_deck_graph"]
