"""Cross-cutting logging, metrics, and progress events for graph nodes."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp

from core.deck.llm import llm_log_context
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics
from core.deck.state import DeckGraphState


logger = logging.getLogger(__name__)
DeckNodeHandler = Callable[[DeckGraphState], dict[str, Any]]


def instrument_node(
    stage: str,
    node: DeckNodeHandler,
    llm_registry: dict[str, Any],
    *,
    model_key: str = "",
) -> DeckNodeHandler:
    """Wrap one node without coupling instrumentation to graph assembly."""

    stage = str(stage or "").strip()
    metadata_key = str(model_key or stage).strip()

    def run(state: DeckGraphState) -> dict[str, Any]:
        started_at = time.perf_counter()
        collector, token = begin_node_metrics()
        task_id = str(state.get("task_id") or state.get("request_id") or "")
        metadata = (
            llm_registry.get("__metadata__")
            if isinstance(llm_registry.get("__metadata__"), dict)
            else {}
        )
        model_meta = metadata.get(metadata_key) if isinstance(metadata.get(metadata_key), dict) else {}
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
        except GraphBubbleUp:
            # Interrupts and other LangGraph control-flow signals are not node failures.
            raise
        except Exception:
            logger.exception("event=ppt_step_failed task_id=%s stage=%s", task_id, stage)
            raise
        finally:
            end_node_metrics(token)

        duration_ms = round((time.perf_counter() - started_at) * 1000)
        measured = collector.snapshot()
        logger.info(
            "event=ppt_step_end task_id=%s stage=%s duration_ms=%s model_call_count=%s token_usage=%s "
            "retry_count=%s fallback_count=%s",
            task_id,
            stage,
            duration_ms,
            measured["model_call_count"],
            measured["token_usage"],
            measured["retry_count"],
            measured["fallback_count"],
        )
        return result

    return run


def run_phase_graph(graph: Any, state: DeckGraphState) -> dict[str, Any]:
    """Run one child graph and forward its custom events to the parent graph."""

    writer = get_stream_writer()
    final_state: dict[str, Any] = dict(state)
    for mode, data in graph.stream(state, stream_mode=["custom", "values"]):
        if mode == "custom":
            writer(data)
        elif mode == "values" and isinstance(data, dict):
            final_state = data
    return final_state


__all__ = ["instrument_node", "run_phase_graph"]
