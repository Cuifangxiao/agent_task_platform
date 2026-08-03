import pytest

from core.deck.pipeline import events
from core.deck.domain.contracts import EventType, PptStage, ResourceType


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


def test_page_start_keeps_page_identity_separate_from_completion_progress():
    event = events.slide_started(
        "Generating page 2/5: Architecture",
        {"index": 2, "total": 5, "title": "Architecture"},
    )

    assert event.type is EventType.STEP_STARTED
    assert event.stage is PptStage.GENERATING_PAGES
    assert event.page.page_index == 2
    assert event.page.page_count == 5
    assert event.progress.current == 0
    assert event.progress.total == 5
    assert event.progress.percent == 0.0
    assert event.progress.unit == "page"


def test_out_of_order_preview_uses_completed_count_instead_of_page_index():
    event = events.slide_preview(
        "Preview ready",
        {
            "event_id": "slide-preview:task-1:s05:0123456789abcdef0123",
            "artifact_hash": "a" * 64,
            "index": 5,
            "completed": 1,
            "total": 5,
            "title": "Closing",
            "png": "previews/page-005.png",
        },
    )

    assert event.page.page_index == 5
    assert event.progress.current == 1
    assert event.progress.percent == 20.0


def test_slide_design_progress_maps_done_to_step_completed():
    event = events.stage_progress(
        "generate_slide_design", "visual plan completed", {"status": "done"}
    )

    assert event.type is EventType.STEP_COMPLETED
    assert event.stage is PptStage.GENERATING_PAGES


@pytest.mark.parametrize(
    "stage",
    [
        "brief",
        "writer",
        "parse_requirements",
        "create_logical_outline",
        "map_outline_to_pages",
        "generate_slide_content",
    ],
)
def test_removed_or_unknown_stage_names_are_rejected(stage):
    with pytest.raises(ValueError, match="unknown deck pipeline stage"):
        events.stage_for(stage)
