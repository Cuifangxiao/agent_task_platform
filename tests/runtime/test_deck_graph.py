from core.deck.contract import GeneratePptInput, InputResource
from core.deck.agents.design.graph import build_style_options, select_style_option
from core.deck.agents.planning.graph import outline_prompt
from core.deck.sources import parse_uploaded_files_with_provenance


def test_style_builds_only_first_recommendation_and_selects_it(tmp_path):
    state = {
        "request_id": "task-1",
        "user_description": "Technical architecture deck",
        "template_id": "auto",
        "brief": {"audience": "Engineers", "goal": "Explain the system"},
        "plan": {"title": "Architecture", "narrative": "A clear flow", "slides": [{"index": 1}]},
    }

    options = build_style_options(state, tmp_path)
    selected, exact = select_style_option(options, "")

    assert len(options) == 1
    assert selected is options[0]
    assert exact is False
    assert selected["template_id"]


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
