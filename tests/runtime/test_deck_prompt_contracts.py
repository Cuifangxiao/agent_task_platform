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
    assert "Markdown fence" in prompt
    assert "extra keys" in prompt or "fields outside the schema" in prompt
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
    assert PROMPT_VERSION == "html-deck.v8"


def test_outline_prompt_covers_complete_input_and_deck_wide_invariants():
    prompt = load_node_prompt("deck_outline_planner")

    for requirement in (
        "DeckOutlineSpec",
        "user_request",
        "source_markdown",
        "available_assets",
        "requested_page_count",
        "hard_constraints",
        "1-30",
        "s01",
        "cover_minimal",
        "closing_resolution",
        "same layout to adjacent pages",
        "only color source",
        "#RRGGBB",
    ):
        assert requirement in prompt
    assert "Do not generate HTML" in prompt
    assert "chain of thought" in prompt


def test_visual_prompt_locks_slide_identity_layout_palette_and_local_context():
    prompt = load_node_prompt("slide_visual_planner")

    for requirement in (
        "SlideDesignPlan",
        "SlideOutlineSpec",
        "global_style_spec",
        "neighboring page",
        "locked_layout",
        "layout_family",
        "color_scheme",
        "1600x900",
        "source/asset slices",
        "approved fonts",
        "hidden chain-of-thought",
        "remote images",
        "gradients",
    ):
        assert requirement in prompt
    assert "Do not write HTML" in prompt


def test_html_builder_receives_the_full_design_plan_and_preserves_security_contract():
    prompt = load_node_prompt("slide_html_builder")

    for requirement in (
        "HtmlSlideSpec",
        "SlideDesignPlan",
        "slide_design_plan",
        "global_style_spec",
        "source/asset slices",
        "HTML body fragment",
        "CSS separately",
        "scoped beneath `.slide`",
        "1600x900",
        "Unsplash",
        "CDN resources",
        "JavaScript",
        "gradients",
        "approved local icon keys",
    ):
        assert requirement in prompt

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
        "deterministic fallback",
        "Do not switch layout family",
        "1600×900",
        "TEXT_OVERFLOW",
        "BOX_OVERLAP",
        "LOW_TEXT_CONTRAST",
        "available_icon_keys",
    ):
        assert requirement in prompt
