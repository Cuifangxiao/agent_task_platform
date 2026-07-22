import inspect
from pathlib import Path

import core.deck.agents.postprocessor.graph as postprocessor_graph
import core.deck.agents.processor.graph as processor_graph
from core.deck.agents.postprocessor.graph import POSTPROCESSOR_NODE_SEQUENCE
from core.deck.agents.preprocessor.graph import PREPROCESSOR_NODE_SEQUENCE
from core.deck.agents.processor.graph import (
    PROCESSOR_NODE_SEQUENCE,
    SLIDE_NODE_SEQUENCE,
    fan_out_slides,
    route_after_validation,
)
from core.deck.agents.processor.layout import allocate_layout_preferences
from core.deck.pipeline.graph import NODE_SEQUENCE, PptGraph
from core.deck.pipeline.state import DeckGraphState
from core.deck.domain.schemas import DeckPlan


ROOT_EDGES = {
    ("__start__", "preprocessor_phase"),
    ("preprocessor_phase", "processor_phase"),
    ("processor_phase", "postprocessor_phase"),
    ("postprocessor_phase", "__end__"),
}

PROCESSOR_EDGES = {
    ("__start__", "prepare_processor_input"),
    ("prepare_processor_input", "select_template"),
    ("select_template", "create_design_spec"),
    ("create_design_spec", "generate_slide"),
    ("generate_slide", "aggregate_slides"),
    ("aggregate_slides", "__end__"),
}

POSTPROCESSOR_EDGES = {
    ("__start__", "assemble_image_pptx"),
    ("assemble_image_pptx", "finalize_deck"),
    ("finalize_deck", "__end__"),
}

PREPROCESSOR_EDGES = {
    ("__start__", "parse_requirements"),
    ("parse_requirements", "create_logical_outline"),
    ("create_logical_outline", "map_outline_to_pages"),
    ("map_outline_to_pages", "__end__"),
}

SLIDE_EDGES = {
    ("__start__", "prepare_slide"),
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
    ("emit_slide_preview", "__end__"),
}


def _edges(graph) -> set[tuple[str, str]]:
    return {(edge.source, edge.target) for edge in graph.get_graph().edges}


def test_root_graph_cleanly_composes_three_phase_agents(tmp_path):
    module = PptGraph({}, tmp_path, browser_session=object())

    assert _edges(module.graph) == ROOT_EDGES
    graph_nodes = set(module.graph.get_graph().nodes) - {"__start__", "__end__"}
    assert graph_nodes == set(NODE_SEQUENCE)
    assert _edges(module.processor.graph) == PROCESSOR_EDGES
    processor_nodes = set(module.processor.graph.get_graph().nodes) - {
        "__start__",
        "__end__",
    }
    assert processor_nodes == set(PROCESSOR_NODE_SEQUENCE)
    assert _edges(module.processor.slide_graph) == SLIDE_EDGES
    slide_nodes = set(module.processor.slide_graph.get_graph().nodes) - {
        "__start__",
        "__end__",
    }
    assert slide_nodes == set(SLIDE_NODE_SEQUENCE)
    assert _edges(module.preprocessor.graph) == PREPROCESSOR_EDGES
    preprocessor_nodes = set(module.preprocessor.graph.get_graph().nodes) - {
        "__start__",
        "__end__",
    }
    assert preprocessor_nodes == set(PREPROCESSOR_NODE_SEQUENCE)
    assert _edges(module.postprocessor.graph) == POSTPROCESSOR_EDGES
    postprocessor_nodes = set(module.postprocessor.graph.get_graph().nodes) - {
        "__start__",
        "__end__",
    }
    assert postprocessor_nodes == set(POSTPROCESSOR_NODE_SEQUENCE)


def test_phase_directories_and_dependency_boundaries_are_explicit():
    deck_root = Path(__file__).resolve().parents[2] / "apps" / "runtime" / "core" / "deck"
    pipeline_files = {
        path.name for path in (deck_root / "pipeline").glob("*.py") if path.is_file()
    }
    assert pipeline_files == {
        "__init__.py",
        "events.py",
        "graph.py",
        "instrumentation.py",
        "state.py",
    }
    assert {
        path.name for path in (deck_root / "agents" / "processor").glob("*.py")
    } == {"__init__.py", "design.py", "generation.py", "graph.py", "layout.py"}
    assert {
        path.name for path in (deck_root / "agents" / "postprocessor").glob("*.py")
    } == {"__init__.py", "export.py", "graph.py"}

    processor_source = inspect.getsource(processor_graph)
    postprocessor_source = inspect.getsource(postprocessor_graph)
    assert "generate_slide_html" in processor_source
    assert "repair_slide_html" in processor_source
    assert "ImagePptxExporter" not in processor_source
    assert "HTMLAssembler" not in postprocessor_source
    assert "GenerationNodes" not in postprocessor_source


def test_every_request_gets_fresh_pipeline_dependencies(tmp_path):
    first = PptGraph({}, tmp_path / "first", browser_session=object())
    second = PptGraph({}, tmp_path / "second", browser_session=object())

    assert first.graph is not second.graph
    assert first.preprocessor is not second.preprocessor
    assert first.processor is not second.processor
    assert first.postprocessor is not second.postprocessor
    assert first.processor.design.output_dir == tmp_path / "first"
    assert second.processor.design.output_dir == tmp_path / "second"
    assert first.processor.generation.output_dir == tmp_path / "first"
    assert second.postprocessor.output_dir == (tmp_path / "second").resolve()


def test_validation_router_has_only_bounded_repair_outcomes():
    assert route_after_validation({"current_render_decision": "valid"}) == "valid"
    assert route_after_validation({"current_render_decision": "repair"}) == "repair"
    assert route_after_validation({"current_render_decision": "fallback"}) == "fallback"


def test_send_fanout_assigns_independent_state_and_planned_composition_history():
    slides = [
        {
            "slide_id": "s01",
            "index": 1,
            "role": "cover",
            "assertion_title": "Opening",
            "takeaway": "Open the argument.",
            "content_intent": "Set up the promise.",
            "layout_family": "cover_minimal",
            "rhythm": "anchor",
            "transition_from_previous": "",
        },
        {
            "slide_id": "s02",
            "index": 2,
            "role": "closing",
            "assertion_title": "Closing",
            "takeaway": "Resolve the argument.",
            "content_intent": "Close with an action.",
            "layout_family": "closing_resolution",
            "rhythm": "breathing",
            "transition_from_previous": "Resolve the opening.",
        },
    ]
    preferences = {
        "s01": {"slide_id": "s01", "layout_variant": "offset_orbit"},
        "s02": {"slide_id": "s02", "layout_variant": "answer_field"},
    }

    sends = fan_out_slides(
        {
            "deck_plan": {
                "deck_title": "Demo",
                "core_message": "One argument",
                "narrative_arc": ["Open", "Close"],
                "slides": slides,
            },
            "layout_preferences": preferences,
            "completed_slides": {"stale": {}},
            "layout_history": [{"slide_id": "stale"}],
            "validation_issues": [{"slide_id": "stale"}],
        }
    )

    assert [send.node for send in sends] == ["generate_slide", "generate_slide"]
    assert [send.arg["current_slide_index"] for send in sends] == [0, 1]
    assert sends[0].arg["previous_compositions"] == []
    assert sends[1].arg["previous_compositions"] == [preferences["s01"]]
    assert sends[1].arg["current_layout_preference"] == preferences["s02"]
    assert all(send.arg["completed_slides"] == {} for send in sends)


def test_layout_preferences_rotate_variants_within_a_repeated_family():
    slides = []
    for index in range(1, 6):
        slides.append(
            {
                "slide_id": f"s{index:02d}",
                "index": index,
                "role": "cover" if index == 1 else "closing" if index == 5 else "insight",
                "assertion_title": f"Assertion {index}",
                "takeaway": f"Takeaway {index}",
                "content_intent": f"Explain point {index}",
                "layout_family": (
                    "cover_minimal"
                    if index == 1
                    else "closing_resolution"
                    if index == 5
                    else "statement_focus"
                ),
                "rhythm": "breathing",
                "transition_from_previous": "" if index == 1 else "Continue.",
            }
        )
    plan = DeckPlan.model_validate(
        {
            "deck_title": "Variant rotation",
            "core_message": "Avoid repeated geometry",
            "narrative_arc": ["Open", "Develop", "Close"],
            "slides": slides,
        }
    )

    preferences = allocate_layout_preferences(plan)

    assert [preferences[f"s{index:02d}"]["layout_variant"] for index in range(2, 5)] == [
        "centered",
        "left_weighted",
        "signal_bar",
    ]


def test_state_contract_contains_only_current_pipeline_concepts():
    fields = set(DeckGraphState.__annotations__)
    assert {
        "global_meta",
        "logical_outline",
        "page_plan",
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
