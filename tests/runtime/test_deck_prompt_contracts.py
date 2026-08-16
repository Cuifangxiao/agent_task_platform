from pathlib import Path

import pytest

from core.deck.domain.schemas import PROMPT_VERSION
from core.deck.runtime.llm import PROMPT_DIR, load_node_prompt


ACTIVE_PROMPTS = (
    "deck_outline_planner",
    "slide_visual_planner",
    "slide_html_builder",
    "html_repair",
)


@pytest.mark.parametrize("node_key", ACTIVE_PROMPTS)
def test_active_prompts_require_one_schema_only_result(node_key):
    prompt = load_node_prompt(node_key)

    assert 1_000 <= len(prompt) <= 16_000
    assert "schema" in prompt.casefold()
    assert "Markdown" in prompt
    assert "代码围栏" in prompt or "fence" in prompt
    assert "额外字段" in prompt or "fields outside the schema" in prompt
    assert "```" not in prompt


def test_prompt_directory_contains_only_the_three_stage_flow_and_repair():
    prompt_names = {
        path.stem
        for path in Path(PROMPT_DIR).glob("*.md")
        if path.is_file()
    }

    assert prompt_names == set(ACTIVE_PROMPTS)
    for retired in (
        "requirement_parser",
        "content_ideator",
        "page_mapper",
        "design_director",
        "slide_content",
        "html_slide_designer",
    ):
        with pytest.raises(ValueError, match="missing deck node prompt file"):
            load_node_prompt(retired)


def test_prompt_version_records_the_three_stage_semantic_upgrade():
    assert PROMPT_VERSION == "html-deck.v12"


def test_outline_prompt_covers_complete_input_and_deck_wide_invariants():
    prompt = load_node_prompt("deck_outline_planner")

    for requirement in (
        "DeckOutlineSpec",
        "user_request",
        "source_markdown",
        "available_assets",
        "requested_page_count",
        "hard_constraints",
        "1–30",
        "s01",
        "cover_minimal",
        "closing_resolution",
        "相邻页不得使用相同布局",
        "唯一的颜色来源",
        "#RRGGBB",
    ):
        assert requirement in prompt
    assert "不生成 HTML" in prompt
    assert "思维链" in prompt
    assert "2–4 个有实质信息的 `key_points`" in prompt
    assert "不得返回空数组" in prompt
    assert "具体数据、案例、定义或证据摘要" in prompt
    assert "稳定、通用、非时效性的解释性知识" in prompt
    assert "仅供后续模型规划、溯源和审计的内部字段" in prompt
    assert "封面必须提供简洁、面向观众的 `subtitle`" in prompt


def test_outline_prompt_matches_the_palette_contrast_validator():
    prompt = load_node_prompt("deck_outline_planner")

    for requirement in (
        "text_primary",
        "text_secondary",
        "text_muted",
        "background_color",
        "surface_color",
        "4.5:1",
        "3:1",
    ):
        assert requirement in prompt
    assert "不要求它们与每一种表面色都达到固定对比度" in prompt


def test_visual_prompt_locks_slide_identity_layout_palette_and_local_context():
    prompt = load_node_prompt("slide_visual_planner")

    for requirement in (
        "SlideDesignPlan",
        "SlideOutlineSpec",
        "global_style_spec",
        "neighbor_slides",
        "locked_layout",
        "layout_family",
        "color_scheme",
        "1600×900",
        "source_markdown",
        "relevant_sources",
        "relevant_assets",
        "content_density_contract",
        "approved_fonts",
        "私有推理",
        "远程图片",
        "渐变",
    ):
        assert requirement in prompt
    assert "不写 HTML" in prompt
    assert "不要只复述 `key_points`" in prompt
    assert "不能只设计“标题 + 一句泛泛说明 + 装饰图形”" in prompt
    assert "不是观众可见文案" in prompt


def test_html_builder_receives_the_full_design_plan_and_preserves_security_contract():
    prompt = load_node_prompt("slide_html_builder")

    for requirement in (
        "HtmlSlideSpec",
        "SlideDesignPlan",
        "slide_design_plan",
        "global_style_spec",
        "source_markdown",
        "relevant_sources",
        "content_density_contract",
        "body fragment",
        "page_css",
        ".slide",
        "1600×900",
        "Unsplash",
        "FontAwesome CDN",
        "JavaScript",
        "渐变",
        "available_icon_keys",
    ):
        assert requirement in prompt
    assert "不是只有标题、单句或空卡片" in prompt
    assert "封面使用 `deck.title`、`deck.subtitle`" in prompt

def test_repair_prompt_preserves_stage_two_plan_and_existing_qa_limits():
    prompt = load_node_prompt("html_repair")

    for requirement in (
        "global_style_spec",
        "slide_outline",
        "slide_design_plan",
        "current_html_spec",
        "diagnostics",
        "repair_attempt",
        "repairs_remaining_after_this",
        "will not replace the page with a program template",
        "Do not switch layout family",
        "1600×900",
        "TEXT_OVERFLOW",
        "BOX_OVERLAP",
        "LOW_TEXT_CONTRAST",
        "available_icon_keys",
    ):
        assert requirement in prompt
    assert "not audience-facing copy" in prompt
