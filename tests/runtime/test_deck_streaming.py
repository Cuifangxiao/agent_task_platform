from pathlib import Path

from core.deck.pipeline import events
from core.deck.domain.contracts import EventType, GeneratePptInput, PipelineEvent, PptStage, ResourceType
from core.deck.service import DeckService


class SuccessfulGraph:
    def __init__(self):
        self.initial_state = None

    def stream(self, initial_state, *, stream_mode):
        assert stream_mode == ["custom", "values"]
        self.initial_state = dict(initial_state)
        root = Path(initial_state["project_dir"])
        preview = root / "previews" / "page-001.png"
        preview.write_bytes(b"preview")
        pptx = root / "exports" / "deck.pptx"
        pptx.write_bytes(b"pptx")
        yield (
            "custom",
            events.slide_preview(
                "Page preview ready",
                {
                    "event_id": "slide-preview:task-1:s01:0123456789abcdef0123",
                    "artifact_hash": "a" * 64,
                    "index": 1,
                    "total": 1,
                    "title": "Demo",
                    "png": "previews/page-001.png",
                },
            ),
        )
        # A graph node cannot own the task terminal; the service emits it exactly once.
        yield (
            "custom",
            PipelineEvent(EventType.TASK_COMPLETED, PptStage.FINISHED, "must be ignored"),
        )
        yield (
            "custom",
            events.artifact(
                "assemble_image_pptx",
                "PPTX ready",
                str(pptx),
                {"relative_path": "exports/deck.pptx", "filename": "deck.pptx"},
            ),
        )
        yield (
            "values",
            {
                **initial_state,
                "artifact_path": str(pptx),
                "completed_slides": {"s01": {"slide_id": "s01", "index": 1}},
            },
        )


class FailingGraph:
    def stream(self, _initial_state, *, stream_mode):
        assert stream_mode == ["custom", "values"]
        yield ("custom", events.stage_started("plan_deck", "Planning"))
        raise RuntimeError("private pipeline details")


def _service(monkeypatch, tmp_path, graph):
    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("core.deck.service.build_ppt_llms", lambda _inp: {})
    return DeckService(graph_factory=lambda *_args: graph)


def test_service_sequences_events_and_emits_one_last_success_terminal(monkeypatch, tmp_path):
    graph = SuccessfulGraph()
    result = list(
        _service(monkeypatch, tmp_path, graph).generate_ppt(
            GeneratePptInput(task_id="task-1", description="Create a one-page demo")
        )
    )

    assert [event.sequence for event in result] == list(range(1, len(result) + 1))
    terminals = [event for event in result if event.type in {EventType.TASK_COMPLETED, EventType.TASK_FAILED}]
    assert terminals == [result[-1]]
    assert result[-1].type is EventType.TASK_COMPLETED
    assert result[-1].stage is PptStage.FINISHED
    assert result[-1].final_result.slide_count == 1
    assert result[-1].final_result.ppt_file.relative_path == "exports/deck.pptx"
    assert result[-1].final_result.ppt_file.type is ResourceType.PPTX
    preview = next(resource for event in result for resource in event.resources if resource.type is ResourceType.PAGE_PREVIEW)
    preview_event = next(event for event in result if preview in event.resources)
    assert preview_event.event_id == "slide-preview:task-1:s01:0123456789abcdef0123"
    assert preview_event.revision == "a" * 64
    assert preview.relative_path == "previews/page-001.png"
    assert not Path(preview.relative_path).is_absolute()
    assert preview.size_bytes == len(b"preview")
    assert (tmp_path / "task-1" / "exports" / "deck.pptx").is_file()
    assert graph.initial_state["user_description"] == "Create a one-page demo"
    assert graph.initial_state["deck_title"] == ""


def test_service_failure_is_the_only_terminal_and_is_last(monkeypatch, tmp_path):
    result = list(
        _service(monkeypatch, tmp_path, FailingGraph()).generate_ppt(
            GeneratePptInput(task_id="task-2", description="Fail after planning")
        )
    )

    assert [event.sequence for event in result] == list(range(1, len(result) + 1))
    terminals = [event for event in result if event.type in {EventType.TASK_COMPLETED, EventType.TASK_FAILED}]
    assert terminals == [result[-1]]
    assert result[-1].type is EventType.TASK_FAILED
    assert result[-1].stage is PptStage.PLANNING
    assert result[-1].message == "PPT generation failed"
    assert result[-1].error.debug_message == "private pipeline details"


def test_service_builds_fresh_graph_and_browser_session_for_each_request(monkeypatch, tmp_path):
    class TrackingBrowserSession:
        def __init__(self, *_args):
            self.closed = False

        def close(self):
            self.closed = True

    calls = []

    def factory(llms, output_dir, browser_session):
        calls.append((llms, output_dir, browser_session))
        return SuccessfulGraph()

    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "core.deck.service.build_ppt_llms",
        lambda inp: {"task_id": inp.task_id},
    )
    monkeypatch.setattr("core.deck.service.SecureBrowserSessionPool", TrackingBrowserSession)
    service = DeckService(graph_factory=factory)

    first = list(
        service.generate_ppt(
            GeneratePptInput(task_id="fresh-1", description="Create the first deck")
        )
    )
    second = list(
        service.generate_ppt(
            GeneratePptInput(task_id="fresh-2", description="Create the second deck")
        )
    )

    assert first[-1].type is EventType.TASK_COMPLETED
    assert second[-1].type is EventType.TASK_COMPLETED
    assert [call[0] for call in calls] == [
        {"task_id": "fresh-1"},
        {"task_id": "fresh-2"},
    ]
    assert [call[1] for call in calls] == [tmp_path.resolve(), tmp_path.resolve()]
    assert calls[0][2] is not calls[1][2]
    assert all(call[2].closed for call in calls)


def test_service_uses_ppt_graph_when_no_factory_is_injected(monkeypatch, tmp_path):
    class TrackingBrowserSession:
        def __init__(self, *_args):
            self.closed = False

        def close(self):
            self.closed = True

    calls = []

    def ppt_graph(llms, output_dir, browser_session):
        calls.append((llms, output_dir, browser_session))
        return SuccessfulGraph()

    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("core.deck.service.build_ppt_llms", lambda _inp: {"brief": object()})
    monkeypatch.setattr("core.deck.service.SecureBrowserSessionPool", TrackingBrowserSession)
    monkeypatch.setattr("core.deck.service.PptGraph", ppt_graph)

    result = list(
        DeckService().generate_ppt(
            GeneratePptInput(task_id="default-graph", description="Use the default graph")
        )
    )

    assert result[-1].type is EventType.TASK_COMPLETED
    assert len(calls) == 1
    assert calls[0][1] == tmp_path.resolve()
    assert calls[0][2].closed is True
