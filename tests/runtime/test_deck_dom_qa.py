import pytest

from core.deck.design.catalog import get_html_theme
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.fallback import build_fallback_html_spec
from core.deck.html.browser import DOM_MEASUREMENT_JS, RenderProfile
from core.deck.schemas import (
    ContentBlock,
    ContentBlockType,
    LayoutFamily,
    PageRhythm,
    SlideContentSpec,
    SlidePlan,
    SlideRole,
)


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
    theme_id: str = "paper_editorial",
    expected_layout: str = "statement_focus",
    declared_icon_keys=(),
):
    assembled = HTMLAssembler().assemble(
        body_html=body_html,
        page_css=page_css,
        design_spec=get_html_theme(theme_id).to_design_spec(),
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


@pytest.mark.parametrize(
    "theme_id",
    ["paper_editorial", "executive_signal", "midnight_product", "alpine_research"],
)
@pytest.mark.parametrize("family", list(LayoutFamily))
@pytest.mark.parametrize(
    "title",
    [
        "界" * 120,
        "W" * 120,
        "i" * 120,
        ("数据WMWM" * 20)[:120],
        "🚀" * 120,
    ],
    ids=["cjk", "wide-latin", "narrow-latin", "mixed", "emoji"],
)
def test_catalog_fallbacks_fit_maximum_title_boundaries(chromium_browser, theme_id, family, title):
    takeaway = "同一张 PNG 同时服务于预览和最终 PPTX。"
    content = SlideContentSpec(
        slide_id="s02",
        assertion_title=title,
        takeaway=takeaway,
        content_blocks=[
            ContentBlock(type=ContentBlockType.PARAGRAPH, text="结构化内容先于视觉布局。"),
            ContentBlock(type=ContentBlockType.BULLET_LIST, items=["可验证", "可修复", "可追溯"]),
            ContentBlock(type=ContentBlockType.METRIC, label="一致性", value="100%"),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="left", items=["双链路", "难复现"]),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="right", items=["单链路", "稳定导出"]),
            ContentBlock(type=ContentBlockType.PROCESS_STEP, ordinal=1, label="生成", text="输出 HTML"),
            ContentBlock(type=ContentBlockType.TIMELINE_ITEM, ordinal=1, label="现在", text="完成迁移"),
            ContentBlock(type=ContentBlockType.ICON_LABEL, label="安全", text="无远程资源"),
        ],
        suggested_icon_keys=["rocket", "target", "circle-check"],
    )
    role = SlideRole.COVER if family is LayoutFamily.COVER_MINIMAL else SlideRole.INSIGHT
    if family is LayoutFamily.CLOSING_RESOLUTION:
        role = SlideRole.CLOSING
    plan = SlidePlan(
        slide_id="s02",
        index=2,
        role=role,
        assertion_title=title,
        takeaway=takeaway,
        content_intent="Exercise terminal fallback geometry.",
        layout_family=family,
        rhythm=PageRhythm.BREATHING,
    )
    spec = build_fallback_html_spec(content, plan)

    result = _measure(
        chromium_browser,
        spec.body_html,
        spec.page_css,
        theme_id=theme_id,
        expected_layout=family.value,
        declared_icon_keys=spec.icon_keys,
    )

    assert not [item for item in result["diagnostics"] if item["severity"] == "error"]
