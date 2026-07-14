from core.deck import events
from core.deck.contract import EventType, PptStage, ResourceType


def test_preview_event_uses_typed_page_and_relative_resources():
    event = events.slide_preview(
        "Preview ready",
        {
            "index": 2,
            "total": 5,
            "title": "Architecture",
            "png": "previews/page-002.png",
            "html": "previews/page-002.html",
            "svg": "svg_output/page-002.svg",
        },
    )

    assert event.type is EventType.ARTIFACT_READY
    assert event.stage is PptStage.RENDERING
    assert event.page.page_index == 2
    assert event.page.page_count == 5
    assert [item.type for item in event.resources] == [
        ResourceType.PAGE_PREVIEW,
        ResourceType.HTML,
        ResourceType.SVG,
    ]
    assert [item.relative_path for item in event.resources] == [
        "previews/page-002.png",
        "previews/page-002.html",
        "svg_output/page-002.svg",
    ]
    assert all("_v" not in item.relative_path for item in event.resources)


def test_stage_progress_maps_done_to_step_completed():
    event = events.stage_progress("writer", "writer completed", {"status": "done"})

    assert event.type is EventType.STEP_COMPLETED
    assert event.stage is PptStage.GENERATING_CONTENT
