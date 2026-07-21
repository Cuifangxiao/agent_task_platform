import pytest

from core.deck import events
from core.deck.contract import EventType, PptStage, ResourceType


def test_preview_event_uses_typed_page_and_relative_resources():
    stable_event_id = "slide-preview:task-1:s02:0123456789abcdef0123"
    artifact_hash = "a" * 64
    event = events.slide_preview(
        "Preview ready",
        {
            "event_id": stable_event_id,
            "artifact_hash": artifact_hash,
            "index": 2,
            "total": 5,
            "title": "Architecture",
            "png": "previews/page-002.png",
        },
    )

    assert event.type is EventType.ARTIFACT_READY
    assert event.event_id == stable_event_id
    assert event.revision == artifact_hash
    assert event.stage is PptStage.GENERATING_PAGES
    assert event.page.page_index == 2
    assert event.page.page_count == 5
    assert event.progress.current == 2
    assert event.progress.total == 5
    assert event.progress.percent == 40.0
    assert event.progress.unit == "page"
    assert [item.type for item in event.resources] == [ResourceType.PAGE_PREVIEW]
    assert [item.relative_path for item in event.resources] == ["previews/page-002.png"]
    assert all("_v" not in item.relative_path for item in event.resources)


def test_page_start_uses_the_same_page_stage_and_progress_shape_as_preview():
    event = events.slide_started(
        "Generating page 2/5: Architecture",
        {"index": 2, "total": 5, "title": "Architecture"},
    )

    assert event.type is EventType.STEP_STARTED
    assert event.stage is PptStage.GENERATING_PAGES
    assert event.page.page_index == 2
    assert event.page.page_count == 5
    assert event.progress.current == 2
    assert event.progress.total == 5
    assert event.progress.percent == 40.0
    assert event.progress.unit == "page"


def test_stage_progress_maps_done_to_step_completed():
    event = events.stage_progress(
        "generate_slide_content", "content completed", {"status": "done"}
    )

    assert event.type is EventType.STEP_COMPLETED
    assert event.stage is PptStage.GENERATING_PAGES


def test_removed_or_unknown_stage_names_are_rejected():
    with pytest.raises(ValueError, match="unknown deck pipeline stage"):
        events.stage_for("writer")
