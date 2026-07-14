import re
from pathlib import Path

from core.deck.spec import COMPONENTS
from core.deck.spec import normalize_slide_spec
from core.deck.quality import dedupe_issue_dicts, validate_html_static, validate_slide_spec
from core.deck.pipeline.slides import split_slide_preserving_content


def test_normalize_slide_spec_accepts_legacy_slide_json_without_content_loss():
    slide = normalize_slide_spec(
        {
            "title": "Architecture",
            "key_message": "Go owns HTTP, Python owns agent workflow.",
            "layout": "content",
            "bullets": [
                "Go streams SSE to the browser",
                "Python runs LangGraph and renders artifacts",
                "PowerPoint export stays downloadable",
                "This extra point is still allowed",
                "This point is dropped by density limits",
            ],
            "speaker_notes": "Explain the runtime boundary.",
        },
        index=2,
        outline={"role": "evidence", "component": "content-basic"},
        deck_title="PPTAgent MVP",
    )

    assert slide.component == "content-basic"
    assert slide.role == "evidence"
    assert slide.content["body"] == "Go owns HTTP, Python owns agent workflow."
    assert len(slide.content["bullets"]) == 5
    assert slide.content["bullets"][-1] == "This point is dropped by density limits"
    assert slide.notes == "Explain the runtime boundary."


def test_validate_slide_spec_rejects_placeholder_text():
    slide = normalize_slide_spec(
        {"title": "TODO", "layout": "content", "bullets": ["Key point 1"]},
        index=1,
        outline={},
        deck_title="Deck",
    )

    issues = validate_slide_spec(slide)
    assert any(issue.kind == "placeholder_text" for issue in issues)


def test_normalization_preserves_long_title_many_bullets_and_large_table():
    title = "T" * 140
    bullets = [f"bullet-{index}-" + "x" * 80 for index in range(8)]
    rows = [[f"cell-{row}-{col}-" + "y" * 30 for col in range(6)] for row in range(9)]
    slide = normalize_slide_spec(
        {
            "title": title,
            "component": "data-table",
            "content": {"bullets": bullets, "columns": [f"c{i}" for i in range(6)], "rows": rows},
        },
        index=1,
    )

    assert slide.title == title
    assert slide.content["bullets"] == bullets
    assert slide.content["rows"] == rows


def test_density_split_preserves_every_list_item():
    bullets = [f"point-{index}" for index in range(9)]
    slide = normalize_slide_spec(
        {"title": "Dense", "component": "content-basic", "content": {"body": "Context", "bullets": bullets}},
        index=1,
    )
    parts = split_slide_preserving_content(slide)
    assert parts is not None
    assert parts[0].content["bullets"] + parts[1].content["bullets"] == bullets


def test_density_split_preserves_every_chart_series_entry():
    series = [{"label": f"S{index}", "value": index} for index in range(10)]
    slide = normalize_slide_spec(
        {"title": "Dense chart", "component": "chart-takeaway", "content": {"takeaway": "Trend"}, "chart": {"type": "pie", "series": series, "unit": "%"}},
        index=1,
    )
    assert any(issue.kind == "density" for issue in validate_slide_spec(slide))
    parts = split_slide_preserving_content(slide)
    assert parts is not None
    assert parts[0].chart["series"] + parts[1].chart["series"] == series


def test_density_split_preserves_complete_body_text_across_pages():
    body = "First complete sentence. Second complete sentence with more detail. Third conclusion."
    slide = normalize_slide_spec({"title": "Dense body", "component": "content-basic", "content": {"body": body, "bullets": []}}, index=1)
    parts = split_slide_preserving_content(slide)
    assert parts is not None
    assert parts[0].content["body"] + " " + parts[1].content["body"] == body


def test_validate_html_static_allows_plain_text_urls_but_blocks_external_resources():
    ok_html = '<section class="slide"><p data-text>Read https://example.com for context.</p></section>'
    blocked_html = '<section class="slide"><img src="https://example.com/a.png"><p data-text>Hello</p></section>'

    assert not any(issue.kind == "unsafe_html" for issue in validate_html_static(ok_html, page=1))
    assert any(issue.kind == "unsafe_html" for issue in validate_html_static(blocked_html, page=1))
    assert any(issue.kind == "unsafe_html" for issue in validate_html_static('<section class="slide"><a data-text href="javascript:alert(1)">x</a></section>', page=1))


def test_dedupe_issue_dicts_uses_page_kind_and_description():
    issues = [
        {"page": 1, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
        {"page": 1, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
        {"page": 2, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
    ]

    assert len(dedupe_issue_dicts(issues)) == 2


def test_components_markdown_stays_in_sync_with_spec_components():
    components_doc = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "runtime"
        / "core"
        / "deck"
        / "design_system"
        / "components.md"
    )
    documented = set(re.findall(r"\| `([^`]+)` \|", components_doc.read_text(encoding="utf-8")))

    assert documented == COMPONENTS
