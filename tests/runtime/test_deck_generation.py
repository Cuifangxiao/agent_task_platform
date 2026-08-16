from types import SimpleNamespace

import pytest

import core.deck.agents.processor.generation as generation
from core.deck.agents.processor.design import default_design_spec
from core.deck.html.browser import BrowserContentError
from core.deck.domain.schemas import (
    ContentBlockType,
    LayoutFamily,
    PageRhythm,
    SlideOutlineSpec,
    SlidePlan,
    SlideRole,
)


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
        "code": "ASSERTION_TITLE_MISSING",
        "category": "content",
        "severity": "error",
        "selector": "",
        "message": "HTML omitted the approved title.",
        "suggestion": "Use only approved content.",
        "box": {},
        "details": {},
    }
    state = {
        "request_id": "request-with-sanitizer-error",
        "design_spec": default_design_spec().model_dump(mode="json"),
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


def test_validation_fails_explicitly_after_model_repair_budget(tmp_path):
    state = {
        "current_slide_plan": {"index": 3},
        "current_repair_attempts": 2,
        "current_diagnostics": [
            {
                "code": "TEXT_OVERFLOW",
                "category": "content",
                "severity": "error",
                "message": "Approved copy exceeds its semantic box.",
            }
        ],
    }

    with pytest.raises(
        BrowserContentError,
        match=r"Page 3 validation failed after 2 repair attempt\(s\): TEXT_OVERFLOW",
    ):
        generation.validate_dom(state, tmp_path, max_repairs=2)


def test_relevant_sources_selects_late_positive_match_and_drops_zero_score_chunks():
    slide = SlidePlan(
        slide_id="s02",
        index=2,
        role=SlideRole.EVIDENCE,
        assertion_title="EBITDA margin proof",
        takeaway="Use appendix-evidence-late",
        content_intent="Ground the page in appendix-evidence-late.",
        layout_family=LayoutFamily.KEY_METRIC,
        rhythm=PageRhythm.ANCHOR,
    )
    chunks = [
        {
            "source_file": "report.md",
            "page": index,
            "section": f"generic-{index}",
            "excerpt": "Unrelated introductory material.",
        }
        for index in range(1, 12)
    ]
    chunks.append(
        {
            "source_file": "appendix.md",
            "page": 12,
            "section": "appendix-evidence-late",
            "excerpt": "EBITDA margin reached 35%.",
        }
    )

    result = generation._relevant_sources(
        {
            "source_chunks": chunks,
            "current_slide_outline": {
                "key_points": ["EBITDA margin reached 35%."],
                "content_note": "Use appendix-evidence-late on page 12.",
            },
        },
        slide,
    )

    assert [item["page"] for item in result] == [12]


def test_key_metric_prefers_business_value_over_year_and_source_locator():
    outline = SlideOutlineSpec(
        id="s02",
        title="2026 Revenue Growth",
        type=SlideRole.EVIDENCE,
        key_points=["Revenue grew 35%"],
        layout_suggestion=LayoutFamily.KEY_METRIC,
        content_mode="data",
        content_note="Source page 12: audited revenue table.",
    )
    slide = SlidePlan(
        slide_id="s02",
        index=2,
        role=SlideRole.EVIDENCE,
        assertion_title=outline.title,
        takeaway=outline.key_points[0],
        content_intent=outline.content_note,
        layout_family=LayoutFamily.KEY_METRIC,
        rhythm=PageRhythm.ANCHOR,
    )

    content = generation._deterministic_content_spec(outline, slide)

    metrics = [block for block in content.content_blocks if block.type is ContentBlockType.METRIC]
    assert [block.value for block in metrics] == ["35%"]


def test_formula_key_metric_preserves_approved_points_without_numeric_kpi():
    outline = SlideOutlineSpec(
        id="s04",
        title="Self-Attention",
        type=SlideRole.INSIGHT,
        key_points=[
            "Q、K、V 由输入线性变换得到",
            "权重 = softmax(QK^T / √d_k)",
            "缩放因子控制点积方差，避免 softmax 饱和",
        ],
        layout_suggestion=LayoutFamily.KEY_METRIC,
        content_mode="data",
        content_note="Explain the formula without inventing a KPI.",
    )
    slide = SlidePlan(
        slide_id="s04",
        index=4,
        role=SlideRole.INSIGHT,
        assertion_title=outline.title,
        takeaway=outline.key_points[0],
        content_intent=outline.content_note,
        layout_family=LayoutFamily.KEY_METRIC,
        rhythm=PageRhythm.DENSE,
    )

    content = generation._deterministic_content_spec(outline, slide)

    assert [block.type for block in content.content_blocks] == [ContentBlockType.BULLET_LIST]
    assert content.content_blocks[0].items == outline.key_points


def test_data_mode_preserves_the_locked_non_metric_layout_structure():
    outline = SlideOutlineSpec(
        id="s02",
        title="Regional comparison",
        type=SlideRole.EVIDENCE,
        key_points=["East 35%", "West 28%"],
        layout_suggestion=LayoutFamily.COMPARISON,
        content_mode="data",
        content_note="Compare the two approved regional values.",
    )
    slide = SlidePlan(
        slide_id="s02",
        index=2,
        role=SlideRole.EVIDENCE,
        assertion_title=outline.title,
        takeaway=outline.key_points[0],
        content_intent=outline.content_note,
        layout_family=LayoutFamily.COMPARISON,
        rhythm=PageRhythm.DENSE,
    )

    content = generation._deterministic_content_spec(outline, slide)

    assert [block.type for block in content.content_blocks] == [
        ContentBlockType.COMPARISON_SIDE,
        ContentBlockType.COMPARISON_SIDE,
    ]
