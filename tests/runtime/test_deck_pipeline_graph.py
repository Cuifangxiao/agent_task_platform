from core.deck.pipeline.graph import (
    NODE_SEQUENCE,
    PptGraph,
    _route_after_advance,
    _route_after_validation,
)
from core.deck.pipeline.state import DeckGraphState


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


def test_root_graph_exposes_every_html_authoring_and_repair_step(tmp_path):
    module = PptGraph({}, tmp_path, browser_session=object())

    assert _edges(module.graph) == FLAT_EDGES
    graph_nodes = set(module.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert graph_nodes == set(NODE_SEQUENCE)


def test_every_request_gets_fresh_pipeline_dependencies(tmp_path):
    first = PptGraph({}, tmp_path / "first", browser_session=object())
    second = PptGraph({}, tmp_path / "second", browser_session=object())

    assert first.graph is not second.graph
    assert first.planning is not second.planning
    assert first.design is not second.design
    assert first.generation is not second.generation
    assert first.delivery is not second.delivery
    assert first.design.output_dir == tmp_path / "first"
    assert second.design.output_dir == tmp_path / "second"
    assert first.generation.output_dir == tmp_path / "first"
    assert second.delivery.output_dir == (tmp_path / "second").resolve()


def test_validation_router_has_only_bounded_repair_outcomes():
    assert _route_after_validation({"current_render_decision": "valid"}) == "valid"
    assert _route_after_validation({"current_render_decision": "repair"}) == "repair"
    assert _route_after_validation({"current_render_decision": "fallback"}) == "fallback"


def test_advance_router_uses_zero_based_cursor_and_planned_count():
    plan = {"slides": [{"slide_id": "s01"}, {"slide_id": "s02"}]}

    assert _route_after_advance({"deck_plan": plan, "current_slide_index": 1}) == "more"
    assert _route_after_advance({"deck_plan": plan, "current_slide_index": 2}) == "done"


def test_state_contract_contains_only_current_pipeline_concepts():
    fields = set(DeckGraphState.__annotations__)
    assert {
        "brief",
        "deck_plan",
        "design_spec",
        "current_slide_plan",
        "completed_slides",
        "artifact_path",
    } <= fields
    assert fields.isdisjoint(
        {
            "job_id",
            "deck_id",
            "source_images",
            "generation_mode",
            "plan",
            "design_contract",
            "theme",
            "art_direction",
            "slides",
            "raw_slides",
            "final_slides",
            "artifacts",
            "deck_spec",
        }
    )
