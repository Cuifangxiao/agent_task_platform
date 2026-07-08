import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.deck.contract import DeckSourceFile, GenerateDeckInput
from core.deck.graph import _outline_prompt, build_deck_graph
from core.deck.service import _parse_uploaded_files


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


def test_deck_graph_streams_markdown_outline_and_renders_pptx(tmp_path):
    graph = build_deck_graph(FakeLLM(), tmp_path, InMemorySaver())
    events = []
    interrupts = []
    config = {"configurable": {"thread_id": "11111111-1111-1111-1111-111111111111"}}

    for part in graph.stream(
        {
            "request_id": "11111111-1111-1111-1111-111111111111",
            "deck_title": "PPTAgent MVP",
            "user_description": "Show the first usable PPTAgent flow in 2 slides.",
            "uploaded_file_name": "",
            "uploaded_file_text": "",
        },
        config=config,
        stream_mode=["custom", "updates"],
    ):
        mode, data = part
        if mode == "custom":
            events.append(data)
        if mode == "updates" and "__interrupt__" in data:
            interrupts.extend(data["__interrupt__"])

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

    for part in graph.stream(
        Command(resume={"action": "continue", "outline_markdown": outline_events[0]["outline_markdown"]}),
        config=config,
        stream_mode=["custom", "updates"],
    ):
        mode, data = part
        if mode == "custom":
            events.append(data)

    slide_started_events = [event for event in events if event.get("event_type") == "slide_started"]
    assert [event["payload"]["index"] for event in slide_started_events] == [1, 2]
    assert slide_started_events[0]["payload"]["status"] == "running"

    slide_ready_events = [event for event in events if event.get("event_type") == "slide_ready"]
    assert [event["payload"]["title"] for event in slide_ready_events] == ["PPTAgent MVP", "Architecture"]
    assert slide_ready_events[1]["payload"]["bullets"] == ["Go streams SSE", "Python runs LangGraph", "Renderer writes PPTX"]
    assert all(event["payload"]["status"] == "ready" for event in slide_ready_events)

    slide_reviewed_events = [event for event in events if event.get("event_type") == "slide_reviewed"]
    assert [event["payload"]["status"] for event in slide_reviewed_events] == ["ok", "ok"]

    artifact_events = [event for event in events if event.get("event_type") == "artifact"]
    assert artifact_events
    artifact_path = tmp_path / "11111111-1111-1111-1111-111111111111.pptx"
    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 0


def test_outline_prompt_adds_uploaded_file_section_only_when_present():
    state = {
        "deck_title": "Market report",
        "user_description": "Create a market report deck",
        "uploaded_file_name": "",
        "uploaded_file_text": "",
    }

    prompt_without_file = _outline_prompt(state)
    assert "Uploaded file:" not in prompt_without_file
    assert "File content:" not in prompt_without_file

    prompt_with_file = _outline_prompt(
        {
            **state,
            "uploaded_file_name": "report.pdf",
            "uploaded_file_text": "### report.pdf\nParsed file body.",
        }
    )
    assert "Uploaded documents:" in prompt_with_file
    assert "### report.pdf" in prompt_with_file
    assert "Parsed file body." in prompt_with_file


def test_parse_uploaded_files_combines_multiple_documents():
    names, text = _parse_uploaded_files(
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
