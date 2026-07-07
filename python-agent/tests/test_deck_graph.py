import json
from types import SimpleNamespace

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from core.deck.graph import _outline_prompt, build_deck_graph


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
    graph = build_deck_graph(FakeLLM(), tmp_path, InMemorySaver())
    events = []
    interrupts = []
    config = {"configurable": {"thread_id": "11111111-1111-1111-1111-111111111111"}}

    for part in graph.stream(
        {
            "request_id": "11111111-1111-1111-1111-111111111111",
            "deck_title": "PPTAgent MVP",
            "audience": "developer",
            "goal": "show the first usable flow",
            "page_count": 2,
            "language": "en",
            "style": "concise",
            "source_text": "",
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

    assert interrupts
    outline_events = [event for event in events if event.get("event_type") == "outline"]
    assert outline_events

    for part in graph.stream(
        Command(resume={"action": "continue", "outline": outline_events[0]["payload"]}),
        config=config,
        stream_mode=["custom", "updates"],
    ):
        mode, data = part
        if mode == "custom":
            events.append(data)

    artifact_events = [event for event in events if event.get("event_type") == "artifact"]
    assert artifact_events
    artifact_path = tmp_path / "11111111-1111-1111-1111-111111111111.pptx"
    assert artifact_path.exists()
    assert artifact_path.stat().st_size > 0


def test_outline_prompt_adds_uploaded_file_section_only_when_present():
    state = {
        "deck_title": "市场汇报",
        "audience": "管理层",
        "goal": "说明机会",
        "page_count": 3,
        "language": "zh-CN",
        "style": "concise",
        "user_description": "生成一份市场汇报 PPT",
        "source_text": "",
        "uploaded_file_name": "",
        "uploaded_file_text": "",
    }

    prompt_without_file = _outline_prompt(state)
    assert "用户上传了文件" not in prompt_without_file
    assert "文件内容如下" not in prompt_without_file

    prompt_with_file = _outline_prompt(
        {
            **state,
            "uploaded_file_name": "report.pdf",
            "uploaded_file_text": "这是解析后的文件正文。",
        }
    )
    assert "用户上传了文件：report.pdf" in prompt_with_file
    assert "文件内容如下：" in prompt_with_file
    assert "这是解析后的文件正文。" in prompt_with_file
