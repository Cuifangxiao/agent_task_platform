import pytest

from core.deck.agents.processor.design import default_design_spec
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.browser import DOM_MEASUREMENT_JS, RenderProfile


@pytest.fixture(scope="module")
def chromium_browser():
    playwright = pytest.importorskip("playwright.sync_api")
    manager = playwright.sync_playwright().start()
    try:
        browser = manager.chromium.launch(headless=True, chromium_sandbox=True)
    except Exception as exc:  # pragma: no cover - depends on developer workstation setup
        manager.stop()
        pytest.skip(f"Playwright Chromium is unavailable: {exc}")
    try:
        yield browser
    finally:
        browser.close()
        manager.stop()


def _measure(
    browser,
    body_html: str,
    page_css: str,
    *,
    expected_layout: str = "statement_focus",
    declared_icon_keys=(),
):
    assembled = HTMLAssembler().assemble(
        body_html=body_html,
        page_css=page_css,
        design_spec=default_design_spec(),
        expected_layout=expected_layout,
        declared_icon_keys=list(declared_icon_keys),
    )
    assert not [item for item in assembled.diagnostics if item.severity == "error"]
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    try:
        page.set_content(assembled.document)
        return page.evaluate(DOM_MEASUREMENT_JS, RenderProfile().to_js())
    finally:
        page.close()


def test_dom_qa_detects_decorative_covering_a_later_text_rect(chromium_browser):
    body = """
    <section class="slide" data-layout="statement_focus">
      <div class="copy" data-box="content">First line<br>Second line</div>
      <div class="veil" data-decorative="true"></div>
    </section>
    """
    css = """
    .slide .copy{position:absolute;left:100px;top:100px;width:620px;z-index:1;color:var(--text-primary);font-size:32px;line-height:1.5}
    .slide .veil{position:absolute;left:90px;top:145px;width:650px;height:58px;z-index:2;background:var(--accent)}
    """

    result = _measure(chromium_browser, body, css)
    occlusions = [item for item in result["diagnostics"] if item["code"] == "TEXT_OCCLUDED"]

    assert len(occlusions) == 1
    assert occlusions[0]["details"]["occluder"] == "div.veil"
    assert occlusions[0]["box"]["y"] > 140


def test_dom_qa_does_not_treat_text_ancestors_as_occluders(chromium_browser):
    body = """
    <section class="slide" data-layout="statement_focus">
      <div class="copy" data-box="content">First line<br>Second line</div>
    </section>
    """
    css = """
    .slide .copy{position:absolute;left:100px;top:100px;width:620px;color:var(--text-primary);font-size:32px;line-height:1.5}
    """

    result = _measure(chromium_browser, body, css)

    assert "TEXT_OCCLUDED" not in {item["code"] for item in result["diagnostics"]}


def test_dom_qa_detects_descendant_overlay_covering_parent_text(chromium_browser):
    body = """
    <section class="slide" data-layout="statement_focus">
      <div class="copy" data-box="content">Covered text<div class="veil" data-decorative="true"></div></div>
    </section>
    """
    css = """
    .slide .copy{position:absolute;left:100px;top:100px;width:620px;height:90px;color:var(--text-primary);font-size:32px;line-height:1.5}
    .slide .veil{position:absolute;inset:0;z-index:2;background:var(--accent)}
    """

    result = _measure(chromium_browser, body, css)

    assert "TEXT_OCCLUDED" in {item["code"] for item in result["diagnostics"]}
