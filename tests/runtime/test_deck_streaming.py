from pathlib import Path

from core.deck import events
from core.deck.contract import EventType, GeneratePptInput, PipelineEvent, PptStage, ResourceType
from core.deck.service import DeckService


class SuccessfulGraph:
    def stream(self, initial_state, *, stream_mode):
        assert stream_mode == ["custom", "values"]
        root = Path(initial_state["project_dir"])
        preview = root / "previews" / "page-001.png"
        preview.write_bytes(b"preview")
        pptx = root / "exports" / "deck.pptx"
        pptx.write_bytes(b"pptx")
        yield (
            "custom",
            events.slide_preview(
                "Page preview ready",
                {"index": 1, "total": 1, "title": "Demo", "png": "previews/page-001.png"},
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
                "export",
                "PPTX ready",
                str(pptx),
                {"relative_path": "exports/deck.pptx", "filename": "deck.pptx"},
            ),
        )
        yield ("values", {**initial_state, "artifact_path": str(pptx), "final_slides": [{"index": 1}]})


class FailingGraph:
    def stream(self, _initial_state, *, stream_mode):
        assert stream_mode == ["custom", "values"]
        yield ("custom", events.stage_started("outline", "Planning"))
        raise RuntimeError("private pipeline details")


def _service(monkeypatch, tmp_path, graph):
    monkeypatch.setenv("DECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr("core.deck.service.build_ppt_llms", lambda _inp: {})
    monkeypatch.setattr("core.deck.service.build_ppt_graph", lambda *_args: graph)
    return DeckService()


def test_service_sequences_events_and_emits_one_last_success_terminal(monkeypatch, tmp_path):
    result = list(
        _service(monkeypatch, tmp_path, SuccessfulGraph()).generate_ppt(
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
    assert preview.relative_path == "previews/page-001.png"
    assert not Path(preview.relative_path).is_absolute()
    assert preview.size_bytes == len(b"preview")
    assert (tmp_path / "task-1" / "exports" / "deck.pptx").is_file()


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
