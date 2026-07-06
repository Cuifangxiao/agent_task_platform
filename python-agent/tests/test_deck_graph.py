import json
from types import SimpleNamespace

from core.deck.graph import build_deck_graph


class FakeLLM:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages):
        del messages
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "PPTAgent MVP",
                        "narrative": "Move from model plumbing to deck generation.",
                        "slides": [
                            {
                                "index": 1,
                                "title": "PPTAgent MVP",
                                "key_message": "A focused deck generator is now possible.",
                                "layout": "title",
                                "visual_suggestion": "Architecture diagram",
                            },
                            {
                                "index": 2,
                                "title": "Architecture",
                                "key_message": "Go owns HTTP, Python owns agent workflow.",
                                "layout": "content",
                                "visual_suggestion": "Layered system",
                            },
                        ],
                    }
                )
            )
        return SimpleNamespace(
            content=json.dumps(
                {
                    "slides": [
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
                }
            )
        )


def test_deck_graph_streams_and_renders_pptx(tmp_path):
    graph = build_deck_graph(FakeLLM(), tmp_path)
    events = []

    for part in graph.stream(
        {
            "request_id": "11111111-1111-1111-1111-111111111111",
            "topic": "PPTAgent MVP",
            "audience": "developer",
            "goal": "show the first usable flow",
            "page_count": 2,
            "language": "en",
            "style": "concise",
            "source_text": "",
        },
        stream_mode=["custom", "updates"],
        version="v2",
    ):
        if part["type"] == "custom":
            events.append(part["data"])

    artifact_events = [event for event in events if event.get("event_type") == "artifact"]
    assert artifact_events
    artifact_path = tmp_path / "11111111-1111-1111-1111-111111111111.pptx"
    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 0

