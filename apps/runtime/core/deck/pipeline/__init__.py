"""Assemble the deck LangGraph topology; node behavior lives in sibling modules."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.config import get_stream_writer

import core.deck.events as events

from core.deck.export import DeckExporter
from core.deck.pipeline.brief import prepare_brief
from core.deck.pipeline.coherence import review_coherence
from core.deck.pipeline.export import export_deck
from core.deck.pipeline.llm import normalize_llm_registry
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics
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
    graph.add_node("brief", _instrumented("brief", lambda state: prepare_brief(state, llm_registry), llm_registry))
    graph.add_node("outline", _instrumented("outline", lambda state: generate_outline(state, llm_registry), llm_registry))
    graph.add_node("outline_review", _instrumented("outline_review", review_outline, llm_registry))
    graph.add_node("style", _instrumented("style", lambda state: prepare_style_options(state, html_renderer, browser, llm_registry), llm_registry))
    graph.add_node("style_review", _instrumented("style_review", lambda state: review_style(state, llm_registry), llm_registry))
    graph.add_node("write_slides", _instrumented("writer", lambda state: write_and_design_slides(state, llm_registry, html_renderer, browser), llm_registry))
    graph.add_node("validate", _instrumented("validate", validate_deck, llm_registry))
    graph.add_node("repair", _instrumented("repair", lambda state: repair_deck(state, llm_registry, html_renderer, browser), llm_registry))
    graph.add_node("review_vlm", _instrumented("review_vlm", lambda state: review_vlm(state, llm_registry, output_dir), llm_registry))
    graph.add_node("coherence", _instrumented("coherence", lambda state: review_coherence(state, llm_registry, html_renderer, browser), llm_registry))
    graph.add_node("export", _instrumented("export", lambda state: export_deck(state, exporter), llm_registry))

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "outline_review")
    graph.add_conditional_edges("outline_review", _route_after_outline_review, {"outline": "outline", "style": "style"})
    graph.add_edge("style", "style_review")
    graph.add_edge("style_review", "write_slides")
    graph.add_edge("write_slides", "validate")
    graph.add_conditional_edges("validate", _route_after_validate, {"repair": "repair", "review_vlm": "review_vlm", "coherence": "coherence", "export": "export"})
    graph.add_edge("repair", "validate")
    graph.add_conditional_edges("review_vlm", _route_after_vlm, {"repair": "repair", "coherence": "coherence"})
    graph.add_edge("coherence", "validate")
    graph.add_edge("export", END)
    return graph.compile(checkpointer=checkpointer)


def _instrumented(stage: str, node: Any, llm_registry: dict[str, Any]):
    def run(state: DeckGraphState):
        started_at = time.perf_counter()
        collector, token = begin_node_metrics()
        result: dict[str, Any] = {}
        caught: Exception | None = None
        try:
            result = node(state) or {}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            caught = exc
        finally:
            end_node_metrics(token)
        metadata_registry = llm_registry.get("__metadata__") if isinstance(llm_registry.get("__metadata__"), dict) else {}
        model_meta = metadata_registry.get(stage) or metadata_registry.get("review" if stage == "review_vlm" else stage) or {}
        measured = collector.snapshot()
        get_stream_writer()(
            events.stage_progress(
                stage,
                f"{stage} completed",
                {
                    "metrics": {
                        "duration_ms": round((time.perf_counter() - started_at) * 1000),
                        "provider": model_meta.get("provider", ""),
                        "model": model_meta.get("model", ""),
                        **measured,
                        "repair_count": int(result.get("repair_round") or state.get("repair_round") or 0),
                        "vlm_issue_count": len(result.get("validation_issues") or []) if stage == "review_vlm" else 0,
                        "export_degraded": bool(result.get("export_degraded")) if stage == "export" else False,
                        "failed": caught is not None,
                    }
                },
            )
        )
        if caught is not None:
            raise caught
        return result

    return run


def _route_after_validate(state: DeckGraphState) -> str:
    if has_fixable_must_fix(state):
        return "repair"
    if not state.get("vlm_reviewed"):
        return "review_vlm"
    if not state.get("coherence_reviewed"):
        return "coherence"
    return "export"


def _route_after_outline_review(state: DeckGraphState) -> str:
    return "outline" if state.get("outline_action") == "regenerate" else "style"


def _route_after_vlm(state: DeckGraphState) -> str:
    return "repair" if has_fixable_must_fix(state) else "coherence"


__all__ = ["build_deck_graph"]
