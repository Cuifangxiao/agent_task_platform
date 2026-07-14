"""Build the linear, one-way PPT generation graph."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

import core.deck.events as events
from core.deck.export import DeckExporter
from core.deck.pipeline.brief import prepare_brief
from core.deck.pipeline.design_lock import freeze_design_contract
from core.deck.pipeline.export import export_deck
from core.deck.pipeline.llm import llm_log_context, normalize_llm_registry
from core.deck.pipeline.outline import generate_outline
from core.deck.pipeline.slides import write_and_design_slides
from core.deck.pipeline.state import DeckGraphState
from core.deck.pipeline.style import prepare_style_options
from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics


logger = logging.getLogger(__name__)
DeckNode = Callable[[DeckGraphState], dict[str, Any]]


@dataclass(frozen=True)
class _DeckNodes:
    brief: DeckNode
    outline: DeckNode
    style: DeckNode
    design_lock: DeckNode
    write_slides: DeckNode
    export: DeckNode


def build_ppt_graph(
    llms: Any,
    output_dir: Path,
    browser_session: BrowserSession | None = None,
):
    """Compile a fresh one-way graph for a task."""

    llm_registry = normalize_llm_registry(llms)
    nodes = _build_nodes(
        llm_registry,
        output_dir,
        HTMLSlideRenderer(),
        BrowserRenderer(output_dir, browser_session),
        DeckExporter(output_dir, browser_session),
    )
    graph = StateGraph(DeckGraphState)
    graph.add_node("brief", nodes.brief)
    graph.add_node("outline", nodes.outline)
    graph.add_node("style", nodes.style)
    graph.add_node("design_lock", nodes.design_lock)
    graph.add_node("write_slides", nodes.write_slides)
    graph.add_node("export", nodes.export)
    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "style")
    graph.add_edge("style", "design_lock")
    graph.add_edge("design_lock", "write_slides")
    graph.add_edge("write_slides", "export")
    graph.add_edge("export", END)
    return graph.compile()


def _build_nodes(
    llm_registry: dict[str, Any],
    output_dir: Path,
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
    exporter: DeckExporter,
) -> _DeckNodes:
    instrument = partial(_instrumented, llm_registry=llm_registry)
    return _DeckNodes(
        brief=instrument("brief", partial(prepare_brief, llm_registry=llm_registry)),
        outline=instrument("outline", partial(generate_outline, llm_registry=llm_registry)),
        style=instrument("style", partial(prepare_style_options, output_root=output_dir)),
        design_lock=instrument(
            "design_lock",
            partial(freeze_design_contract, output_dir=output_dir, llm_registry=llm_registry),
        ),
        write_slides=instrument(
            "writer",
            partial(
                write_and_design_slides,
                llm_registry=llm_registry,
                html_renderer=html_renderer,
                browser=browser,
            ),
        ),
        export=instrument("export", partial(export_deck, exporter=exporter)),
    )


def _instrumented(stage: str, node: DeckNode, llm_registry: dict[str, Any]) -> DeckNode:
    def run(state: DeckGraphState) -> dict[str, Any]:
        started_at = time.perf_counter()
        collector, token = begin_node_metrics()
        task_id = str(state.get("task_id") or state.get("request_id") or "")
        metadata = llm_registry.get("__metadata__") if isinstance(llm_registry.get("__metadata__"), dict) else {}
        model_meta = metadata.get(stage) or (metadata.get("style") if stage == "design_lock" else {}) or {}
        logger.info(
            "event=ppt_step_start task_id=%s stage=%s provider=%s model=%s",
            task_id,
            stage,
            model_meta.get("provider", ""),
            model_meta.get("model", ""),
        )
        try:
            with llm_log_context(request_id=task_id, stage=stage):
                result = node(state) or {}
        except Exception:
            logger.exception("event=ppt_step_failed task_id=%s stage=%s", task_id, stage)
            raise
        finally:
            end_node_metrics(token)

        duration_ms = round((time.perf_counter() - started_at) * 1000)
        measured = collector.snapshot()
        logger.info(
            "event=ppt_step_end task_id=%s stage=%s duration_ms=%s model_call_count=%s token_usage=%s",
            task_id,
            stage,
            duration_ms,
            measured["model_call_count"],
            measured["token_usage"],
        )
        get_stream_writer()(
            events.stage_progress(
                stage,
                f"{stage} completed",
                {"status": "done", "metrics": {"duration_ms": duration_ms, **measured}},
            )
        )
        return result

    return run


__all__ = ["build_ppt_graph"]
