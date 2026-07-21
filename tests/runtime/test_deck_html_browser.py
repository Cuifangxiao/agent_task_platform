from pathlib import Path

import pytest
from PIL import Image

from core.deck.design_system.catalog import get_html_theme
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.fallback import build_fallback_html_spec
from core.deck.html.browser import (
    BrowserEnvironmentError,
    HTMLBrowserRenderer,
    RenderProfile,
)
from core.deck.domain.schemas import (
    ContentBlock,
    ContentBlockType,
    LayoutFamily,
    PageRhythm,
    SlideContentSpec,
    SlidePlan,
    SlideRole,
)


@pytest.fixture(scope="module")
def chromium_renderer():
    renderer = HTMLBrowserRenderer(profile=RenderProfile(device_scale_factor=2.0))
    try:
        renderer.metadata(font_families=())
    except BrowserEnvironmentError as exc:
        renderer.close()
        pytest.skip(f"Playwright Chromium is unavailable in this environment: {exc}")
    try:
        yield renderer
    finally:
        renderer.close()


def _content() -> SlideContentSpec:
    return SlideContentSpec(
        slide_id="s02",
        assertion_title="Trusted HTML is the single render source",
        takeaway="One PNG serves both preview and final export.",
        content_blocks=[
            ContentBlock(type=ContentBlockType.PARAGRAPH, text="Structure comes before visual composition."),
            ContentBlock(type=ContentBlockType.BULLET_LIST, items=["Verifiable", "Repairable", "Traceable"]),
            ContentBlock(type=ContentBlockType.METRIC, label="Visual consistency", value="100%"),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="left", items=["Two render paths", "Hard to reproduce"]),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="right", items=["One render path", "Stable output"]),
            ContentBlock(type=ContentBlockType.PROCESS_STEP, ordinal=1, label="Plan", text="Lock the intent"),
            ContentBlock(type=ContentBlockType.PROCESS_STEP, ordinal=2, label="Render", text="Capture the page"),
            ContentBlock(type=ContentBlockType.TIMELINE_ITEM, ordinal=1, label="Now", text="Use one artifact"),
            ContentBlock(type=ContentBlockType.ICON_LABEL, label="Safe", text="No remote assets"),
        ],
        suggested_icon_keys=["rocket", "target", "circle-check"],
        source_refs=[],
    )


def _assembled(family: LayoutFamily):
    role = SlideRole.COVER if family is LayoutFamily.COVER_MINIMAL else SlideRole.INSIGHT
    if family is LayoutFamily.CLOSING_RESOLUTION:
        role = SlideRole.CLOSING
    plan = SlidePlan(
        slide_id="s02",
        index=2,
        role=role,
        assertion_title="Trusted HTML is the single render source",
        takeaway="One PNG serves both preview and final export.",
        content_intent="Exercise a browser-verified fallback recipe",
        layout_family=family,
        rhythm=PageRhythm.BREATHING,
    )
    html_spec = build_fallback_html_spec(_content(), plan)
    result = HTMLAssembler().assemble(
        body_html=html_spec.body_html,
        page_css=html_spec.page_css,
        design_spec=get_html_theme("paper_editorial").to_design_spec(),
        language="en-US",
        declared_icon_keys=html_spec.icon_keys,
        expected_layout=family.value,
    )
    assert not [item for item in result.diagnostics if item.severity == "error"]
    return result


@pytest.mark.parametrize("family", list(LayoutFamily))
def test_all_fallback_recipes_pass_real_chromium_dom_qa(chromium_renderer, family):
    assembled = _assembled(family)

    measurement = chromium_renderer.measure(assembled.document, font_families=())

    assert not [item for item in measurement.diagnostics if item.get("severity") == "error"]
    assert measurement.blocked_requests == ()
    assert 0 < measurement.dom_nodes <= chromium_renderer.profile.max_dom_nodes
    assert measurement.svg_paths <= chromium_renderer.profile.max_svg_paths


def test_chromium_capture_is_opaque_exact_size_and_deterministic(chromium_renderer, tmp_path):
    document = _assembled(LayoutFamily.COVER_MINIMAL).document
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"

    first = chromium_renderer.capture(document, first_path, font_families=())
    second = chromium_renderer.capture(document, second_path, font_families=())

    assert (first.width, first.height) == (3200, 1800)
    assert (second.width, second.height) == (3200, 1800)
    assert first.opaque is second.opaque is True
    assert first.sha256 == second.sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    with Image.open(first_path) as image:
        assert image.size == (3200, 1800)
        assert "A" not in image.getbands()


def _qa_document(body_html: str, page_css: str):
    result = HTMLAssembler().assemble(
        body_html=body_html,
        page_css=page_css,
        design_spec=get_html_theme("paper_editorial").to_design_spec(),
        expected_layout="statement_focus",
    )
    assert not [item for item in result.diagnostics if item.severity == "error"]
    return result.document


def test_browser_rejects_tiny_low_contrast_semantic_text(chromium_renderer):
    document = _qa_document(
        '<section class="slide"><div class="title" data-box="title" data-title-lines="2"><h1>Visible title</h1></div>'
        '<div class="tiny" data-box="content">Hidden instruction</div></section>',
        ".slide .title{position:absolute;left:100px;top:100px;width:900px;height:100px;}"
        ".slide .tiny{position:absolute;left:100px;top:300px;width:600px;height:40px;font-size:1px;color:var(--slide-bg);}",
    )

    measurement = chromium_renderer.measure(document, font_families=())
    codes = {item.get("code") for item in measurement.diagnostics}

    assert {"FONT_TOO_SMALL", "LOW_TEXT_CONTRAST"}.issubset(codes)


def test_browser_rejects_semantic_overlap_and_text_overflow(chromium_renderer):
    overlap = _qa_document(
        '<section class="slide"><div class="a" data-box="content">First box</div>'
        '<div class="b" data-box="content">Second box</div></section>',
        ".slide .a{position:absolute;left:120px;top:180px;width:600px;height:160px;padding:24px;background:var(--surface);}"
        ".slide .b{position:absolute;left:120px;top:180px;width:600px;height:160px;padding:24px;background:var(--surface);}",
    )
    overflow = _qa_document(
        f'<section class="slide"><div class="overflow" data-box="content">{"W" * 400}</div></section>',
        ".slide .overflow{position:absolute;left:120px;top:180px;width:240px;height:48px;overflow:hidden;white-space:nowrap;}",
    )

    overlap_codes = {item.get("code") for item in chromium_renderer.measure(overlap, font_families=()).diagnostics}
    overflow_codes = {item.get("code") for item in chromium_renderer.measure(overflow, font_families=()).diagnostics}

    assert "BOX_OVERLAP" in overlap_codes
    assert "TEXT_OVERFLOW" in overflow_codes


def test_browser_blocks_remote_requests_even_without_shell_csp(chromium_renderer):
    document = """<!doctype html><html><head><style>
    html,body,.slide{width:1600px;height:900px;margin:0;overflow:hidden;background:#fff;color:#111}
    .slide{position:relative;font:24px Arial,sans-serif}.copy{position:absolute;left:100px;top:100px}
    </style></head><body><section class="slide" data-layout="statement_focus">
    <div class="copy" data-box="content">Safe local copy</div>
    <div data-decorative="true"><img src="https://deck.invalid/tracker.png" alt=""></div>
    </section></body></html>"""

    measurement = chromium_renderer.measure(document, font_families=())

    assert "https://deck.invalid/tracker.png" in measurement.blocked_requests
