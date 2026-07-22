from types import SimpleNamespace

import core.deck.pipeline.generation as generation
from core.deck.design_system.catalog import get_html_theme
from core.deck.domain.schemas import LayoutFamily, PageRhythm, SlidePlan, SlideRole


def _slide(slide_id: str, index: int) -> dict:
    return SlidePlan(
        slide_id=slide_id,
        index=index,
        role=SlideRole.INSIGHT,
        assertion_title=f"Page {index}",
        takeaway="Keep progress scoped to one request.",
        content_intent="Exercise request-scoped runtime behavior.",
        layout_family=LayoutFamily.STATEMENT_FOCUS,
        rhythm=PageRhythm.BREATHING,
    ).model_dump(mode="json")


def test_render_skips_speculative_capture_when_sanitizer_already_reported_error(
    monkeypatch,
    tmp_path,
):
    calls = []

    class RecordingRenderer:
        def measure(self, document, *, font_families):
            calls.append(
                {
                    "document": document,
                    "font_families": font_families,
                }
            )
            return SimpleNamespace(diagnostics=())

        def measure_and_capture(self, *_args, **_kwargs):
            raise AssertionError("existing errors must use measurement without capture")

    diagnostic = {
        "code": "HTML_INTRODUCED_NUMBERS",
        "category": "content",
        "severity": "error",
        "selector": "",
        "message": "HTML introduced an unsupported number.",
        "suggestion": "Use only approved content.",
        "box": {},
        "details": {},
    }
    state = {
        "request_id": "request-with-sanitizer-error",
        "design_spec": get_html_theme("paper_editorial")
        .to_design_spec()
        .model_dump(mode="json"),
        "current_slide_plan": _slide("s01", 1),
        "current_assembled_html": "<section class='slide'></section>",
        "current_diagnostics": [diagnostic],
    }
    monkeypatch.setattr(generation, "get_stream_writer", lambda: lambda _event: None)

    result = generation.render_html_and_measure_dom(state, tmp_path, RecordingRenderer())

    assert len(calls) == 1
    assert result["current_speculative_preview_path"] == ""
    assert result["current_speculative_preview_hash"] == ""
    assert result["current_diagnostics"] == [diagnostic]


def test_generation_nodes_track_completed_slides_per_request(monkeypatch):
    completed_counts = []
    nodes = object.__new__(generation.GenerationNodes)
    nodes.progress_tracker = generation.SlideProgressTracker()
    monkeypatch.setattr(
        generation,
        "emit_slide_preview",
        lambda _state, *, completed_count: completed_counts.append(completed_count) or {},
    )

    for request_id, slide_id, index in (
        ("request-a", "s01", 1),
        ("request-a", "s02", 2),
        ("request-b", "s01", 1),
        ("request-b", "s02", 2),
    ):
        nodes.emit_slide_preview(
            {
                "request_id": request_id,
                "current_slide_plan": _slide(slide_id, index),
            }
        )

    assert completed_counts == [1, 2, 1, 2]
