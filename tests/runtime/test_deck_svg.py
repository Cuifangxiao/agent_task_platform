from pathlib import Path
from zipfile import ZipFile

import pytest
from pptx import Presentation

from core.deck.errors import ExportError
from core.deck.export import DeckExporter
from core.deck.export.svg_native import add_svg_as_native_shapes
from core.deck.quality.svg import validate_svg_page
from core.deck.rendering.svg import SVGSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, default_theme


def _deck(slides: list[SlideSpec]) -> DeckSpec:
    return DeckSpec(
        title="Vector deck",
        narrative="One source of truth",
        audience="Team",
        goal="Explain",
        language="en",
        theme=default_theme(),
        slides=slides,
    )


def test_restricted_svg_renderer_covers_core_components_without_quality_errors():
    slides = [
        SlideSpec(1, "hook", "cover", "A clear opening", {"eyebrow": "Strategy", "subtitle": "The narrative starts here."}),
        SlideSpec(2, "context", "content-basic", "Evidence supports the decision", {"body": "Focus on the strongest implication.", "bullets": ["First signal", "Second signal", "Third signal"]}),
        SlideSpec(3, "evidence", "kpi-cards", "Three metrics define success", {"cards": [{"value": "42%", "label": "Adoption", "caption": "Quarter over quarter"}, {"value": "2.4x", "label": "Velocity", "caption": "Release throughput"}]}),
        SlideSpec(4, "evidence", "chart-takeaway", "Growth is concentrated", {"takeaway": "The final segment contributes most of the gain."}, chart={"type": "bar", "series": [{"label": "A", "value": 12}, {"label": "B", "value": 28}, {"label": "C", "value": 46}], "unit": "%"}),
        SlideSpec(5, "summary", "end", "Move from insight to action", {"subtitle": "Agree the next owner and date."}),
        SlideSpec(6, "context", "section", "The next chapter changes the lens", {"subtitle": "Move from context to the operating model."}),
        SlideSpec(7, "context", "agenda", "The discussion follows four decisions", {"items": ["Context", "Evidence", "Options", "Action"]}),
        SlideSpec(8, "evidence", "timeline", "Delivery advances through four milestones", {"items": ["Discover", "Prototype", "Pilot", "Scale"]}),
        SlideSpec(9, "solution", "steps", "Execution is a four-step loop", {"steps": ["Frame", "Build", "Measure", "Adapt"]}),
        SlideSpec(10, "turn", "comparison", "The target model removes two constraints", {"left_title": "Current", "right_title": "Target", "left": ["Manual handoffs", "Slow iteration"], "right": ["Shared context", "Fast learning"]}),
        SlideSpec(11, "evidence", "quadrant", "Four opportunity zones guide priority", {"items": ["Defend", "Improve", "Expand", "Create"]}),
        SlideSpec(12, "evidence", "data-table", "The scorecard makes trade-offs explicit", {"columns": ["Option", "Impact", "Effort"], "rows": [["A", "High", "Low"], ["B", "Medium", "Medium"]]}),
        SlideSpec(13, "turn", "quote", "A principle anchors the choice", {"quote": "Make the important path easy to see and easy to change.", "speaker": "Design principle"}),
        SlideSpec(14, "summary", "summary", "Three commitments close the loop", {"items": ["Name the owner", "Set the date", "Measure the result"]}),
    ]
    deck = _deck(slides)
    renderer = SVGSlideRenderer()

    for slide in slides:
        svg = renderer.render(deck, slide)
        assert 'viewBox="0 0 1280 720"' in svg
        assert 'data-text="true"' in svg
        assert validate_svg_page(svg, page=slide.index, theme=deck.theme) == []


def test_svg_quality_gate_rejects_colors_outside_the_execution_palette():
    slide = SlideSpec(1, "hook", "cover", "Locked color", {"subtitle": "Only approved colors may appear."})
    deck = _deck([slide])
    svg = SVGSlideRenderer().render(deck, slide).replace(deck.theme.palette["accent"], "#123456", 1)

    issues = validate_svg_page(svg, page=1, theme=deck.theme)

    assert any(issue.kind == "svg_color_drift" for issue in issues)


def test_native_svg_export_produces_editable_powerpoint_shapes(tmp_path: Path):
    request_id = "44444444-4444-4444-8444-444444444444"
    slide_spec = SlideSpec(
        1,
        "evidence",
        "comparison",
        "Native SVG keeps the structure editable",
        {"left_title": "Before", "right_title": "After", "left": ["Raster background"], "right": ["Native shapes", "Editable text"]},
    )
    deck = _deck([slide_spec])
    slides_dir = tmp_path / request_id / "slides"
    slides_dir.mkdir(parents=True)
    (slides_dir / "1.svg").write_text(SVGSlideRenderer().render(deck, slide_spec), encoding="utf-8")

    result = DeckExporter(tmp_path).export(request_id, deck)

    assert result.warnings == []
    assert result.manifest["degraded"] is False
    assert result.manifest["slides"][0]["export_mode"] == "native_svg"
    assert result.manifest["slides"][0]["native_shape_count"] > 10
    assert result.manifest["slides"][0]["editable_text_boxes"] >= 5
    exported = Presentation(str(result.path))
    assert len(exported.slides) == 1
    texts = [shape.text for shape in exported.slides[0].shapes if getattr(shape, "has_text_frame", False) and shape.text.strip()]
    assert any("Native SVG" in text for text in texts)
    assert any("Editable text" in text for text in texts)
    with ZipFile(result.path) as package:
        slide_xml = package.read("ppt/slides/slide1.xml").decode("utf-8")
    assert '<a:ea typeface="Noto Sans SC"' in slide_xml


def test_svg_quality_gate_rejects_external_or_unsupported_content():
    slide = SlideSpec(1, "hook", "cover", "Title", {"subtitle": "Subtitle"})
    deck = _deck([slide])
    unsafe = SVGSlideRenderer().render(deck, slide).replace("</svg>", '<foreignObject href="file:///secret">x</foreignObject></svg>')

    issues = validate_svg_page(unsafe, page=1, theme=deck.theme)

    assert {issue.kind for issue in issues} >= {"svg_unsupported_element", "svg_forbidden_attribute"}


def test_native_svg_preflight_does_not_leave_partial_shapes_on_failure(tmp_path: Path):
    svg_path = tmp_path / "invalid.svg"
    svg_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect x="0" y="0" width="100" height="100" fill="#FFFFFF"/><polyline points="0,0"/></svg>',
        encoding="utf-8",
    )
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])

    with pytest.raises(ExportError):
        add_svg_as_native_shapes(slide, svg_path)

    assert len(slide.shapes) == 0


def test_native_svg_export_supports_line_and_pie_chart_primitives(tmp_path: Path):
    request_id = "66666666-6666-4666-8666-666666666666"
    slides = [
        SlideSpec(1, "evidence", "chart-takeaway", "Trend", {"takeaway": "Momentum accelerates."}, chart={"type": "line", "series": [{"label": "Q1", "value": 2}, {"label": "Q2", "value": 5}, {"label": "Q3", "value": 9}], "unit": "x"}),
        SlideSpec(2, "evidence", "chart-takeaway", "Mix", {"takeaway": "The first segment leads."}, chart={"type": "pie", "series": [{"label": "A", "value": 55}, {"label": "B", "value": 30}, {"label": "C", "value": 15}], "unit": "%"}),
        SlideSpec(3, "evidence", "chart-takeaway", "Single observation", {"takeaway": "One point remains an editable native chart."}, chart={"type": "line", "series": [{"label": "Now", "value": 7}], "unit": "x"}),
    ]
    deck = _deck(slides)
    slides_dir = tmp_path / request_id / "slides"
    slides_dir.mkdir(parents=True)
    renderer = SVGSlideRenderer()
    for slide in slides:
        (slides_dir / f"{slide.index}.svg").write_text(renderer.render(deck, slide), encoding="utf-8")

    result = DeckExporter(tmp_path).export(request_id, deck)

    assert result.warnings == []
    assert [item["export_mode"] for item in result.manifest["slides"]] == ["native_svg", "native_svg", "native_svg"]
    assert len(Presentation(str(result.path)).slides) == 3
