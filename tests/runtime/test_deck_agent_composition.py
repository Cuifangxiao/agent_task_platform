from langgraph.graph import END, START, StateGraph

import core.deck.agents as agents
from core.deck.agents import Design, Generation, Planning, Postprocessor
from core.deck.instrumentation import run_phase_graph
from core.deck.runner import NODE_SEQUENCE, PptGraph, _route_after_advance, _route_after_validation
from core.deck.state import DeckGraphState


FLAT_EDGES = {
    ("__start__", "normalize_request"),
    ("normalize_request", "plan_deck"),
    ("plan_deck", "select_template"),
    ("select_template", "create_design_spec"),
    ("create_design_spec", "prepare_slide"),
    ("prepare_slide", "generate_slide_content"),
    ("generate_slide_content", "generate_slide_html"),
    ("generate_slide_html", "sanitize_and_assemble_html"),
    ("sanitize_and_assemble_html", "render_html_and_measure_dom"),
    ("render_html_and_measure_dom", "validate_dom"),
    ("validate_dom", "capture_png"),
    ("validate_dom", "repair_slide_html"),
    ("validate_dom", "build_fallback_html"),
    ("repair_slide_html", "sanitize_and_assemble_html"),
    ("build_fallback_html", "sanitize_and_assemble_html"),
    ("capture_png", "persist_slide_artifacts"),
    ("persist_slide_artifacts", "emit_slide_preview"),
    ("emit_slide_preview", "advance_slide"),
    ("advance_slide", "prepare_slide"),
    ("advance_slide", "assemble_image_pptx"),
    ("assemble_image_pptx", "finalize_deck"),
    ("finalize_deck", "__end__"),
}


def _edges(graph) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in graph.get_graph().edges}


def test_agents_keep_legacy_phases_and_expose_v2_node_providers():
    assert {
        "Design",
        "DesignNodes",
        "Generation",
        "GenerationNodes",
        "Planning",
        "PlanningNodes",
        "Postprocessor",
        "PostprocessorNodes",
    }.issubset(agents.__all__)
    assert not hasattr(agents, "AgentNode")
    assert not hasattr(agents, "GraphFragment")
    assert not hasattr(agents, "DeckWorkflow")


def test_legacy_phase_graphs_remain_available_during_v2_migration(tmp_path):
    planning = Planning({})
    design = Design({}, tmp_path)
    generation = Generation({}, tmp_path)
    postprocessor = Postprocessor(tmp_path)

    assert _edges(planning.graph) == {
        ("__start__", "brief"),
        ("brief", "outline"),
        ("outline", "__end__"),
    }
    assert _edges(design.graph) == {
        ("__start__", "template_selection"),
        ("template_selection", "design_lock"),
        ("design_lock", "__end__"),
    }
    assert _edges(generation.graph) == {
        ("__start__", "write_slides"),
        ("write_slides", "__end__"),
    }
    assert _edges(postprocessor.graph) == {
        ("__start__", "export"),
        ("export", "__end__"),
    }


def test_root_runner_exposes_every_html_authoring_and_repair_step(tmp_path):
    module = PptGraph({}, tmp_path, browser_session=object())

    assert _edges(module.graph) == FLAT_EDGES
    graph_nodes = set(module.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert graph_nodes == set(NODE_SEQUENCE)


def test_every_request_gets_fresh_root_and_v2_dependencies(tmp_path):
    first = PptGraph({}, tmp_path / "first", browser_session=object())
    second = PptGraph({}, tmp_path / "second", browser_session=object())

    assert first.graph is not second.graph
    assert first.planning is not second.planning
    assert first.design is not second.design
    assert first.generation is not second.generation
    assert first.postprocessor is not second.postprocessor
    assert first.design.output_dir == tmp_path / "first"
    assert second.design.output_dir == tmp_path / "second"
    assert first.generation.output_dir == tmp_path / "first"
    assert second.postprocessor.output_dir == (tmp_path / "second").resolve()


def test_validation_router_has_only_bounded_repair_outcomes():
    assert _route_after_validation({"current_render_decision": "valid"}) == "valid"
    assert _route_after_validation({"current_render_decision": "repair"}) == "repair"
    assert _route_after_validation({"current_render_decision": "fallback"}) == "fallback"


def test_advance_router_uses_zero_based_cursor_and_planned_count():
    plan = {"slides": [{"slide_id": "s01"}, {"slide_id": "s02"}]}

    assert _route_after_advance({"deck_plan": plan, "current_slide_index": 1}) == "more"
    assert _route_after_advance({"deck_plan": plan, "current_slide_index": 2}) == "done"


class _RecordingPhase:
    """Keep a small compatibility check for nested legacy phase execution."""

    def __init__(self, marker: str) -> None:
        self.marker = marker
        builder = StateGraph(DeckGraphState)
        builder.add_node("record", self.record)
        builder.add_edge(START, "record")
        builder.add_edge("record", END)
        self.graph = builder.compile()

    def record(self, state: DeckGraphState):
        title = str(state.get("deck_title") or "")
        return {"deck_title": f"{title}>{self.marker}"}

    def run(self, state: DeckGraphState):
        return run_phase_graph(self.graph, state)


def test_legacy_phase_runner_still_passes_state_through_child_graph(monkeypatch):
    monkeypatch.setattr("core.deck.instrumentation.get_stream_writer", lambda: lambda _event: None)
    result = _RecordingPhase("legacy").run({"deck_title": "seed"})

    assert result["deck_title"] == "seed>legacy"
