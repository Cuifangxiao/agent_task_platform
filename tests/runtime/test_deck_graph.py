import hashlib
import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.deck.contract import DeckSourceFile, GenerateDeckInput
from core.deck.pipeline import build_deck_graph
from core.deck.pipeline.outline import outline_prompt
from core.deck.pipeline.slides import slide_prompt
from core.deck.sources import parse_uploaded_files, parse_uploaded_files_with_provenance


OUTLINE_MARKDOWN = """# PPTAgent MVP
> Move from model plumbing to deck generation.

## 1. PPTAgent MVP
- 核心信息: A focused deck generator is now possible.
- 版式: title
- 视觉建议: Architecture diagram

## 2. Architecture
- 核心信息: Go owns HTTP, Python owns agent workflow.
- 版式: content
- 视觉建议: Layered system
"""


class FakeLLM:
    def __init__(self):
        self.invoke_count = 0

    def stream(self, messages):
        del messages
        midpoint = len(OUTLINE_MARKDOWN) // 2
        yield SimpleNamespace(content=OUTLINE_MARKDOWN[:midpoint])
        yield SimpleNamespace(content=OUTLINE_MARKDOWN[midpoint:])

    def invoke(self, messages):
        del messages
        self.invoke_count += 1
        slides = [
            {
                "index": 1,
                "title": "PPTAgent MVP",
                "key_message": "A focused deck generator is now possible.",
                "layout": "title",
                "bullets": [],
                "visual_suggestion": "Architecture diagram",
                "speaker_notes": "Open with the product focus.",
            },
            {
                "index": 2,
                "title": "Architecture",
                "key_message": "Go owns HTTP, Python owns agent workflow.",
                "layout": "content",
                "bullets": ["Go streams SSE", "Python runs LangGraph", "Renderer writes PPTX"],
                "visual_suggestion": "Layered system",
                "speaker_notes": "Explain the boundary.",
            },
        ]
        slide = slides[min(self.invoke_count - 1, len(slides) - 1)]
        return SimpleNamespace(
            content=json.dumps(slide)
        )


def _collect_graph_stream(graph, stream_input, config):
    events = []
    interrupts = []
    for part in graph.stream(
        stream_input,
        config=config,
        stream_mode=["custom", "values"],
        version="v2",
        output_keys=[],
    ):
        if part["type"] == "custom":
            events.append(part["data"])
        elif part["type"] == "values":
            interrupts.extend(part["interrupts"])
    return events, interrupts


def test_deck_graph_streams_markdown_outline_and_renders_pptx(tmp_path):
    graph = build_deck_graph(FakeLLM(), tmp_path, InMemorySaver())
    config = {"configurable": {"thread_id": "11111111-1111-1111-1111-111111111111"}}

    events, interrupts = _collect_graph_stream(
        graph,
        {
            "request_id": "11111111-1111-1111-1111-111111111111",
            "deck_title": "PPTAgent MVP",
            "user_description": "Show the first usable PPTAgent flow in 2 slides.",
            "uploaded_file_name": "",
            "uploaded_file_text": "",
        },
        config,
    )

    text_delta_events = [event for event in events if event.get("event_type") == "text_delta"]
    assert text_delta_events
    assert all(event.get("channel") == "outline_markdown" for event in text_delta_events)
    assert "".join(event.get("text_delta", "") for event in text_delta_events) == OUTLINE_MARKDOWN

    outline_events = [event for event in events if event.get("event_type") == "outline"]
    assert outline_events
    assert outline_events[0]["outline_markdown"] == OUTLINE_MARKDOWN.strip()

    assert interrupts
    interrupt_payload = interrupts[0].value
    assert interrupt_payload["outline_markdown"] == OUTLINE_MARKDOWN.strip()

    style_events, style_interrupts = _collect_graph_stream(
        graph,
        Command(resume={"action": "approve_outline", "outline_markdown": outline_events[0]["outline_markdown"]}),
        config,
    )
    events.extend(style_events)

    style_option_events = [event for event in events if event.get("event_type") == "style_options"]
    assert style_option_events
    assert style_interrupts
    style_payload = style_interrupts[0].value
    assert style_payload["stage"] == "style_review"
    assert len(style_payload["style_options"]) == 6
    assert all(option.get("template_id") for option in style_payload["style_options"])
    assert all(option.get("confirmations") for option in style_payload["style_options"])
    assert all(option.get("sample_render_mode") == "static_template_cover" for option in style_payload["style_options"])
    assert all(option.get("cover_png") and not option.get("cover_html") for option in style_payload["style_options"])

    slide_events, _ = _collect_graph_stream(
        graph,
        Command(resume={"action": "choose_style", "style_id": style_payload["style_options"][0]["style_id"]}),
        config,
    )
    events.extend(slide_events)

    slide_started_events = [event for event in events if event.get("event_type") == "slide_started"]
    assert [event["payload"]["index"] for event in slide_started_events] == [1, 2]
    assert slide_started_events[0]["payload"]["status"] == "running"

    slide_spec_events = [event for event in events if event.get("event_type") == "slide_spec_ready"]
    assert [event["payload"]["component"] for event in slide_spec_events] == ["cover", "content-basic"]

    slide_preview_events = [event for event in events if event.get("event_type") == "slide_preview"]
    assert [event["payload"]["index"] for event in slide_preview_events[:2]] == [1, 2]
    assert {event["payload"]["index"] for event in slide_preview_events} == {1, 2}
    assert all(event["payload"]["png"] for event in slide_preview_events)

    slide_ready_events = [event for event in events if event.get("event_type") == "slide_ready"]
    assert [event["payload"]["title"] for event in slide_ready_events] == ["PPTAgent MVP", "Architecture"]
    assert slide_ready_events[1]["payload"]["bullets"] == ["Go streams SSE", "Python runs LangGraph", "Renderer writes PPTX"]
    assert all(event["payload"]["status"] in {"ready", "warning"} for event in slide_ready_events)

    artifact_events = [event for event in events if event.get("event_type") == "artifact"]
    assert artifact_events
    artifact_path = tmp_path / "11111111-1111-1111-1111-111111111111" / "exports" / "deck.pptx"
    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 0


def test_outline_prompt_adds_uploaded_file_section_only_when_present():
    state = {
        "deck_title": "Market report",
        "user_description": "Create a market report deck",
        "uploaded_file_name": "",
        "uploaded_file_text": "",
    }

    prompt_without_file = outline_prompt(state)
    assert "Uploaded file:" not in prompt_without_file
    assert "File content:" not in prompt_without_file

    prompt_with_file = outline_prompt(
        {
            **state,
            "uploaded_file_name": "report.pdf",
            "uploaded_file_text": "### report.pdf\nParsed file body.",
        }
    )
    assert "Uploaded documents:" in prompt_with_file
    assert "### report.pdf" in prompt_with_file
    assert "Parsed file body." in prompt_with_file


def test_outline_can_regenerate_within_the_same_checkpoint(tmp_path):
    graph = build_deck_graph(FakeLLM(), tmp_path, InMemorySaver())
    config = {"configurable": {"thread_id": "outline-regenerate"}}
    _, first_interrupts = _collect_graph_stream(
        graph,
        {"request_id": "outline-regenerate", "deck_title": "Deck", "user_description": "Two slides"},
        config,
    )
    assert first_interrupts

    _, regenerated_interrupts = _collect_graph_stream(
        graph,
        Command(resume={"action": "regenerate_outline"}),
        config,
    )
    assert regenerated_interrupts
    assert regenerated_interrupts[0].value["stage"] == "outline_review"


def test_parse_uploaded_files_combines_multiple_documents():
    names, text = parse_uploaded_files(
        GenerateDeckInput(
            user_id=1,
            username="tester",
            user_description="Create a deck",
            source_files=[
                DeckSourceFile(filename="one.md", content=b"# One\nhello"),
                DeckSourceFile(filename="two.txt", content="第二份资料".encode("utf-8")),
            ],
        )
    )

    assert names == "one.md, two.txt"
    assert "### one.md" in text
    assert "hello" in text
    assert "### two.txt" in text
    assert "第二份资料" in text


def test_uploaded_file_chunks_keep_file_page_and_paragraph_provenance():
    _names, _text, chunks = parse_uploaded_files_with_provenance(
        GenerateDeckInput(
            user_id=1,
            username="tester",
            user_description="Create a deck",
            source_files=[DeckSourceFile(filename="report.txt", content=b"[Page 2]\nRevenue reached 120 million.\n\nMargin was 42%.")],
        )
    )
    assert [chunk["page"] for chunk in chunks] == [2, 2]
    assert all(chunk["source_file"] == "report.txt" for chunk in chunks)
    assert chunks[0]["section"] == "paragraph-1"


def test_uploaded_resource_reference_is_read_from_controlled_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("DECK_SOURCES_DIR", str(tmp_path))
    path = tmp_path / "source.md"
    data = ("完整附件内容\n" * 3000).encode("utf-8")
    path.write_bytes(data)
    names, text, chunks = parse_uploaded_files_with_provenance(
        GenerateDeckInput(
            user_id=1,
            username="tester",
            user_description="Create a deck",
            source_files=[DeckSourceFile(filename="source.md", resource_id="resource-1", local_path=str(path), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))],
        )
    )
    assert names == "source.md"
    assert text.count("完整附件内容") == 3000
    assert chunks


def test_uploaded_resource_reference_rejects_paths_outside_controlled_directory(tmp_path, monkeypatch):
    import pytest

    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    monkeypatch.setenv("DECK_SOURCES_DIR", str(allowed))
    with pytest.raises(ValueError, match="outside"):
        parse_uploaded_files_with_provenance(
            GenerateDeckInput(
                user_id=1,
                username="tester",
                user_description="Create a deck",
                source_files=[DeckSourceFile(filename="outside.md", local_path=str(outside), size_bytes=outside.stat().st_size)],
            )
        )


def test_writer_prompt_contains_only_relevant_facts_and_source_chunks():
    state = {
        "deck_title": "D",
        "brief": {
            "audience": "Board",
            "facts": [
                {"id": "fact#1", "statement": "Revenue reached 40%", "source_excerpt": "Revenue reached 40%"},
                {"id": "fact#2", "statement": "Hiring added 20 people", "source_excerpt": "Hiring added 20 people"},
            ],
        },
        "source_chunks": [
            {"source_file": "revenue.md", "page": 1, "section": "p1", "excerpt": "Revenue reached 40%"},
            {"source_file": "hiring.md", "page": 1, "section": "p1", "excerpt": "Hiring added 20 people"},
        ],
    }
    prompt = slide_prompt(state, {"title": "Revenue", "key_message": "Revenue growth", "role": "evidence"}, [], 2)
    assert "fact#1" in prompt
    assert "revenue.md" in prompt
    assert "fact#2" not in prompt
