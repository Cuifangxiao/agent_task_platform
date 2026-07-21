from types import SimpleNamespace

import core.deck.agents.planning.graph as planning_graph
import core.deck.agents.design.graph as design_graph
from core.deck.contract import GeneratePptInput, InputResource
from core.deck.agents.design.graph import build_art_direction, select_template_pack
from core.deck.agents.planning.graph import brief_prompt, outline_prompt
from core.deck.contract import EventType, PptStage
from core.deck.sources import parse_uploaded_files_with_provenance


def test_template_selection_returns_one_resolved_catalog_pack():
    state = {
        "request_id": "task-1",
        "user_description": "Technical architecture deck",
        "requested_template_id": "auto",
        "brief": {"audience": "Engineers", "goal": "Explain the system"},
        "plan": {"title": "Architecture", "narrative": "A clear flow", "slides": [{"index": 1}]},
    }

    selected = select_template_pack(state)
    direction = build_art_direction(selected, state)

    assert selected.template_id == "technical_blueprint"
    assert direction["visual_style"] == selected.visual_style
    assert "template_id" not in direction
    assert "theme" not in direction


def test_template_selection_separates_request_preference_from_selected_result(monkeypatch):
    emitted = []
    monkeypatch.setattr(design_graph, "get_stream_writer", lambda: emitted.append)
    state = {
        "request_id": "task-explicit-template",
        "requested_template_id": "consulting_signal",
        "user_description": "Technical architecture deck",
        "brief": {"audience": "Engineers", "goal": "Explain the system"},
        "plan": {"title": "Architecture", "narrative": "A clear flow", "slides": [{"index": 1}]},
    }

    result = design_graph.prepare_template_selection(state)

    assert result["selected_template_id"] == "consulting_signal"
    assert result["theme"]
    assert result["art_direction"]
    assert "style_options" not in result
    assert emitted[-1].message == "Template selected: Consulting Signal"


def test_outline_prompt_adds_uploaded_sources_only_when_present():
    state = {
        "deck_title": "Market report",
        "user_description": "Create a market report deck",
        "uploaded_file_name": "",
        "uploaded_file_text": "",
    }
    assert "Uploaded documents:" not in outline_prompt(state)

    prompt = outline_prompt(
        {
            **state,
            "uploaded_file_name": "report.md",
            "uploaded_file_text": "### report.md\nRevenue grew 35%.",
        }
    )
    assert "Uploaded documents:" in prompt
    assert "Revenue grew 35%." in prompt


def test_brief_prompt_leaves_source_facts_to_the_deterministic_ledger():
    prompt = brief_prompt("Market report", "Prepare a board update", "Revenue grew 35%.")

    assert '"audience"' in prompt
    assert '"goal"' in prompt
    assert '"occasion"' in prompt
    assert '"facts"' not in prompt
    assert "Revenue grew 35%." in prompt


def test_outline_streaming_emits_only_boundary_events_not_one_event_per_chunk(monkeypatch):
    class ChunkedOutlineLLM:
        @staticmethod
        def stream(_messages):
            for content in (
                "# Architecture\n",
                "> Explain the flow.\n",
                "## 1. Context\n",
                "- Key message: Why the system exists.\n",
            ):
                yield SimpleNamespace(content=content)

    emitted = []
    monkeypatch.setattr(planning_graph, "get_stream_writer", lambda: emitted.append)

    result = planning_graph.generate_outline(
        {
            "deck_title": "Architecture",
            "user_description": "Explain the system",
            "brief": {"audience": "Engineers", "goal": "Explain"},
            "uploaded_file_text": "",
        },
        {"outline": ChunkedOutlineLLM()},
    )

    assert result["plan"]["title"] == "Architecture"
    assert [event.type for event in emitted] == [
        EventType.STEP_STARTED,
        EventType.STEP_COMPLETED,
    ]
    assert all(event.stage is PptStage.PLANNING for event in emitted)
    assert emitted[-1].message == "Outline ready"


def test_uploaded_resources_keep_page_and_paragraph_provenance():
    names, text, chunks = parse_uploaded_files_with_provenance(
        GeneratePptInput(
            task_id="task-2",
            description="Create a deck",
            attachments=[
                InputResource(
                    resource_id="report",
                    name="report.txt",
                    content=b"[Page 2]\nRevenue reached 120 million.\n\nMargin was 42%.",
                )
            ],
        )
    )

    assert names == "report.txt"
    assert "Revenue reached 120 million." in text
    assert [chunk["page"] for chunk in chunks] == [2, 2]
    assert all(chunk["source_file"] == "report.txt" for chunk in chunks)
