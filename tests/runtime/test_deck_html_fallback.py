import pytest

from core.deck.design.html_catalog import get_html_theme
from core.deck.html.assembler import HTMLAssembler
from core.deck.helpers import extract_number_tokens
from core.deck.html.fallback import _truncate_display_width, build_fallback_html_spec
from core.deck.html.icons import icon_registry
from core.deck.schemas import (
    ContentBlock,
    ContentBlockType,
    LayoutFamily,
    PageRhythm,
    SlideContentSpec,
    SlidePlan,
    SlideRole,
)


def _content() -> SlideContentSpec:
    return SlideContentSpec(
        slide_id="s02",
        assertion_title="可信 HTML 成为唯一渲染源 <script>alert(1)</script>",
        takeaway="同一张 PNG 同时服务于预览和最终 PPTX。",
        content_blocks=[
            ContentBlock(type=ContentBlockType.PARAGRAPH, text="结构化内容先于视觉布局。"),
            ContentBlock(type=ContentBlockType.BULLET_LIST, items=["可验证", "可修复", "可追溯"]),
            ContentBlock(type=ContentBlockType.METRIC, label="一致性", value="100%"),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="left", items=["双渲染链路", "难以复现"]),
            ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="right", items=["单一渲染链路", "稳定导出"]),
            ContentBlock(type=ContentBlockType.PROCESS_STEP, ordinal=1, label="生成", text="输出 HTML"),
            ContentBlock(type=ContentBlockType.PROCESS_STEP, ordinal=2, label="校验", text="检查 DOM"),
            ContentBlock(type=ContentBlockType.TIMELINE_ITEM, ordinal=1, label="现在", text="完成迁移"),
            ContentBlock(type=ContentBlockType.ICON_LABEL, label="安全", text="无远程资源"),
        ],
        suggested_icon_keys=["rocket", "target", "circle-check"],
        source_refs=[],
    )


@pytest.mark.parametrize("family", list(LayoutFamily))
def test_each_recipe_has_a_deterministic_executable_fallback(family):
    role = SlideRole.COVER if family is LayoutFamily.COVER_MINIMAL else SlideRole.INSIGHT
    if family is LayoutFamily.CLOSING_RESOLUTION:
        role = SlideRole.CLOSING
    plan = SlidePlan(
        slide_id="s02",
        index=2,
        role=role,
        assertion_title="HTML-first pipeline",
        takeaway="One render, one artifact.",
        content_intent="Explain the architecture",
        layout_family=family,
        rhythm=PageRhythm.BREATHING,
    )
    first = build_fallback_html_spec(_content(), plan)
    second = build_fallback_html_spec(_content(), plan)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.layout_family is family
    assert f'data-layout="{family.value}"' in first.body_html
    assert first.page_css.startswith(".slide")
    assert set(first.icon_keys).issubset(icon_registry().keys())

    assembled = HTMLAssembler().assemble(
        body_html=first.body_html,
        page_css=first.page_css,
        design_spec=get_html_theme("paper_editorial").to_design_spec(),
        declared_icon_keys=first.icon_keys,
    )
    assert not [item for item in assembled.diagnostics if item.severity == "error"]
    assert 'class="slide' in assembled.body_html
    assert "<script" not in assembled.body_html


def test_fallback_escapes_content_instead_of_trusting_it_as_markup():
    plan = SlidePlan(
        slide_id="s02",
        index=2,
        role=SlideRole.INSIGHT,
        assertion_title="Safe output",
        takeaway="Escape source text",
        content_intent="Security test",
        layout_family=LayoutFamily.STATEMENT_FOCUS,
        rhythm=PageRhythm.BREATHING,
    )

    result = build_fallback_html_spec(_content(), plan)

    assert "<script>alert(1)</script>" not in result.body_html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result.body_html


def test_fallback_title_truncation_accounts_for_cjk_display_width():
    latin = _truncate_display_width("A" * 100, 44)
    chinese = _truncate_display_width("界" * 100, 44)

    assert latin == "A" * 37 + "…"
    assert chinese == "界" * 21 + "…"


def test_fallback_keeps_normal_latin_titles_intact():
    assert _truncate_display_width("Atlas unifies team decisions", 44) == "Atlas unifies team decisions"


def test_fallback_title_truncation_never_splits_an_approved_number():
    rendered = _truncate_display_width(("界" * 20) + "2026年度报告", 44)

    assert rendered == ("界" * 20) + "…"
    assert extract_number_tokens(rendered) == []


@pytest.mark.parametrize("number", ["２０２６．５０％", "２０２６，５０％"])
def test_fallback_title_truncation_never_splits_fullwidth_decimal(number):
    rendered = _truncate_display_width(("界" * 15) + number + "年度报告", 44)

    assert rendered == ("界" * 15) + "…"
    assert extract_number_tokens(rendered) == []
