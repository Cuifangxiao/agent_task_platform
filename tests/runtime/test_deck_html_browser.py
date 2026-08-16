import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from core.deck.agents.processor.design import default_design_spec
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.browser import (
    BrowserEnvironmentError,
    BrowserMeasurement,
    HTMLBrowserRenderer,
    RenderProfile,
    SecureBrowserSession,
    SecureBrowserSessionPool,
    resolve_browser_concurrency,
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


def _assembled():
    """Build a minimal valid model-authored slide document through the shell."""

    result = HTMLAssembler().assemble(
        body_html=(
            '<section class="slide">'
            '<div class="title" data-box="title" data-title-lines="2">'
            "<h1>Trusted HTML is the single render source</h1></div>"
            '<div class="copy" data-box="content">One PNG serves both preview and final export.</div>'
            "</section>"
        ),
        page_css=(
            ".slide .title{position:absolute;left:100px;top:120px;width:1200px;}"
            ".slide .title h1{font-size:48px;line-height:1.2;color:var(--text-primary);margin:0}"
            ".slide .copy{position:absolute;left:100px;top:320px;width:1000px;"
            "font-size:26px;line-height:1.5;color:var(--text-primary)}"
        ),
        design_spec=default_design_spec(),
        language="en-US",
        expected_layout="statement_focus",
    )
    assert not [item for item in result.diagnostics if item.severity == "error"]
    return result


def test_valid_slide_passes_real_chromium_dom_qa(chromium_renderer):
    assembled = _assembled()

    measurement = chromium_renderer.measure(assembled.document, font_families=())

    assert not [item for item in measurement.diagnostics if item.get("severity") == "error"]
    assert measurement.blocked_requests == ()
    assert 0 < measurement.dom_nodes <= chromium_renderer.profile.max_dom_nodes
    assert measurement.svg_paths <= chromium_renderer.profile.max_svg_paths


def test_chromium_capture_is_opaque_exact_size_and_deterministic(chromium_renderer, tmp_path):
    document = _assembled().document
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"

    first_render = chromium_renderer.measure_and_capture(document, first_path, font_families=())
    second_render = chromium_renderer.measure_and_capture(document, second_path, font_families=())
    first = first_render.captured_png
    second = second_render.captured_png

    assert not first_render.measurement.diagnostics
    assert not second_render.measurement.diagnostics
    assert first is not None
    assert second is not None
    assert (first.width, first.height) == (3200, 1800)
    assert (second.width, second.height) == (3200, 1800)
    assert first.opaque is second.opaque is True
    assert first.sha256 == second.sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    with Image.open(first_path) as image:
        assert image.size == (3200, 1800)
        assert "A" not in image.getbands()


def test_measure_and_capture_skips_screenshot_for_invalid_dom(monkeypatch, tmp_path):
    class FakeContext:
        closed = False

        def close(self):
            self.closed = True

    context = FakeContext()
    measurement = BrowserMeasurement(
        diagnostics=(
            {
                "code": "CONTENT_HIDDEN",
                "category": "content",
                "severity": "error",
            },
        ),
        blocked_requests=(),
        fonts={},
        dom_nodes=1,
        svg_paths=0,
        elapsed_ms=1,
    )
    session = SecureBrowserSession()
    monkeypatch.setattr(session, "_open_page", lambda _profile: (context, object(), []))
    monkeypatch.setattr(session, "_load_stable_document", lambda *_args: None)
    monkeypatch.setattr(session, "_measure_page", lambda *_args, **_kwargs: measurement)
    monkeypatch.setattr(
        session,
        "_screenshot_page",
        lambda *_args, **_kwargs: pytest.fail("invalid DOM must not be screenshotted"),
    )
    try:
        rendered = session.measure_and_capture(
            "<section class='slide'></section>",
            tmp_path / "invalid.png",
            profile=RenderProfile(),
            font_families=(),
        )
    finally:
        session.close()

    assert rendered.measurement is measurement
    assert rendered.captured_png is None
    assert context.closed is True
    assert not (tmp_path / "invalid.png").exists()


def test_hidden_slide_returns_diagnostics_without_timing_out_capture(chromium_renderer, tmp_path):
    document = _assembled().document.replace(
        "</head>",
        "<style>.slide{display:none!important}</style></head>",
    )
    png_path = tmp_path / "hidden.png"

    rendered = chromium_renderer.measure_and_capture(document, png_path, font_families=())

    assert any(item.get("severity") == "error" for item in rendered.measurement.diagnostics)
    assert rendered.captured_png is None
    assert not png_path.exists()


def test_font_canvas_probe_is_cached_for_the_browser_session():
    class FakePage:
        def __init__(self):
            self.families: list[str] = []

        def evaluate(self, _probe, family):
            self.families.append(family)
            return family != "Missing Font"

    session = SecureBrowserSession()
    first_page = FakePage()
    second_page = FakePage()
    try:
        assert session._font_status(first_page, ("Deck Sans", "Missing Font")) == {
            "Deck Sans": True,
            "Missing Font": False,
        }
        assert session._font_status(second_page, ("Deck Sans", "Missing Font")) == {
            "Deck Sans": True,
            "Missing Font": False,
        }
        assert session._font_status(second_page, ("Deck Serif",)) == {"Deck Serif": True}
    finally:
        session.close()

    assert first_page.families == ["Deck Sans", "Missing Font"]
    assert second_page.families == ["Deck Serif"]


def test_browser_session_pool_runs_bounded_operations_in_parallel(monkeypatch):
    active = 0
    maximum = 0
    lock = threading.Lock()
    sessions = []

    class FakeSession:
        def __init__(self):
            self.closed = False
            sessions.append(self)

        def describe(self, **_kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.03)
                return {"renderer": "fake"}
            finally:
                with lock:
                    active -= 1

        def close(self):
            self.closed = True

    monkeypatch.setenv("DECK_BROWSER_CONCURRENCY", "9")
    assert resolve_browser_concurrency(slide_concurrency=4) == 4
    pool = SecureBrowserSessionPool(3, session_factory=FakeSession)
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(
                    lambda _index: pool.describe(
                        profile=RenderProfile(),
                        font_families=(),
                    ),
                    range(6),
                )
            )
        assert results == [{"renderer": "fake"}] * 6
        assert maximum == 3
    finally:
        pool.close()

    assert len(sessions) == 3
    assert all(session.closed for session in sessions)


def _qa_document(body_html: str, page_css: str):
    result = HTMLAssembler().assemble(
        body_html=body_html,
        page_css=page_css,
        design_spec=default_design_spec(),
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
    assert {
        item.get("severity")
        for item in measurement.diagnostics
        if item.get("code") in {"FONT_TOO_SMALL", "LOW_TEXT_CONTRAST"}
    } == {"warning"}


def test_browser_allows_16px_auxiliary_text_but_not_16px_body_copy(chromium_renderer):
    document = _qa_document(
        '<section class="slide"><div class="title" data-box="title"><h1>Readable title</h1></div>'
        '<div class="eyebrow" data-box="eyebrow">SECTION LABEL</div>'
        '<div class="copy" data-box="content">Body copy must remain readable.</div>'
        '<div class="page-number" data-box="page-number">08</div></section>',
        ".slide .title{position:absolute;left:100px;top:80px;width:900px;height:80px;}"
        ".slide .eyebrow{position:absolute;left:100px;top:190px;width:500px;height:30px;font-size:16px;color:var(--text-primary);}"
        ".slide .copy{position:absolute;left:100px;top:280px;width:700px;height:40px;font-size:16px;color:var(--text-primary);}"
        ".slide .page-number{font-size:16px;color:var(--text-primary);}",
    )

    diagnostics = chromium_renderer.measure(document, font_families=()).diagnostics
    tiny = [item for item in diagnostics if item.get("code") == "FONT_TOO_SMALL"]

    assert len(tiny) == 1
    assert tiny[0].get("details") == {"fontSize": 16, "minimum": 21}


def test_browser_enforces_title_line_budget_on_non_heading_title_box(chromium_renderer):
    document = _qa_document(
        '<section class="slide"><div class="title" data-box="title">First line<br>Second line</div></section>',
        ".slide .title{position:absolute;left:100px;top:80px;width:900px;font-size:48px;line-height:1.1;color:var(--text-primary);}",
    )

    diagnostics = chromium_renderer.measure(document, font_families=()).diagnostics
    title_budget = [item for item in diagnostics if item.get("code") == "TITLE_LINE_BUDGET"]

    assert len(title_budget) == 1
    assert title_budget[0].get("details") == {"lines": 2, "allowed": 1}


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

    overlap_diagnostics = chromium_renderer.measure(overlap, font_families=()).diagnostics
    overlap_codes = {item.get("code") for item in overlap_diagnostics}
    overflow_codes = {item.get("code") for item in chromium_renderer.measure(overflow, font_families=()).diagnostics}

    assert "BOX_OVERLAP" in overlap_codes
    assert "TEXT_OVERFLOW" in overflow_codes
    overlap_item = next(item for item in overlap_diagnostics if item.get("code") == "BOX_OVERLAP")
    assert ".a" in overlap_item["selector"] and ".b" in overlap_item["selector"]
    assert overlap_item["details"]["firstBox"] == overlap_item["details"]["secondBox"]
    assert overlap_item["box"]["width"] == 600
    assert overlap_item["severity"] == "warning"
    overflow_item = next(item for item in chromium_renderer.measure(overflow, font_families=()).diagnostics if item.get("code") == "TEXT_OVERFLOW")
    assert overflow_item["severity"] == "warning"


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
