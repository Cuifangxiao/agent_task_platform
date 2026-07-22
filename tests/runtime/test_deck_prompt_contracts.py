import re

import pytest

from core.deck.runtime.llm import load_node_prompt
from core.deck.domain.schemas import PROMPT_VERSION


ACTIVE_PROMPTS = (
    "requirement_parser",
    "content_ideator",
    "page_mapper",
    "design_director",
    "slide_content",
    "html_slide_designer",
    "html_repair",
)


@pytest.mark.parametrize("node_key", ACTIVE_PROMPTS)
def test_active_prompts_are_formal_role_contracts(node_key):
    prompt = load_node_prompt(node_key)

    assert 1_000 <= len(prompt) <= 16_000
    assert prompt.startswith("<role>\n")
    assert "<input_contract>" in prompt
    assert "<quality_gate>" in prompt
    assert "<output_contract>" in prompt
    assert "silently" in prompt.casefold()
    assert "schema-valid" in prompt
    assert "Return exactly one" in prompt
    assert "Markdown fence" in prompt
    assert "```" not in prompt

    opening = re.findall(r"(?m)^<([a-z_]+)>$", prompt)
    closing = re.findall(r"(?m)^</([a-z_]+)>$", prompt)
    assert opening == closing


def test_prompt_version_records_the_semantic_prompt_upgrade():
    assert PROMPT_VERSION == "html-deck.v7"


def test_brief_prompt_makes_raw_input_authoritative_without_evidence_extraction():
    prompt = load_node_prompt("requirement_parser")

    for requirement in (
        "<authority_order>",
        "<interpretation_workflow>",
        "communication_job",
        "raw_user_input",
        "source_mode",
        "Do not create chapters",
        "semantic",
    ):
        assert requirement in prompt
    assert "deterministic_brief" not in prompt


def test_planner_prompt_covers_narrative_layout_rhythm_and_whole_deck_coherence():
    prompt = load_node_prompt("content_ideator")

    for requirement in (
        "<narrative_workflow>",
        "<hard_constraints>",
        "<separation_boundary>",
        "<quality_gate>",
        "no concept of slides or layouts",
        "must not create, count, reserve, or allocate pages",
        "Those decisions belong to later processor modules",
        "repaginated",
    ):
        assert requirement in prompt


def test_page_mapper_prompt_allows_only_asset_a_and_semantic_page_fields():
    prompt = load_node_prompt("page_mapper")

    for requirement in (
        "scene narrative template",
        "only template asset",
        "page_no, page_category, and topic",
        "Do not output layout_id",
        "layout_template",
        "layout_family",
        "content payloads",
        "media",
        "HTML",
        "SVG",
        "processor phase",
    ):
        assert requirement in prompt


def test_design_prompt_locks_catalog_tokens_and_defines_page_rhythm():
    prompt = load_node_prompt("design_director")

    for requirement in (
        "<execution_lock>",
        "Copy these values from the selected theme exactly",
        "Anchor pages:",
        "Dense pages:",
        "Breathing pages:",
        "dashboard-like seas of equal cards",
    ):
        assert requirement in prompt


def test_content_prompt_defines_every_content_block_and_evidence_policy():
    prompt = load_node_prompt("slide_content")

    for block_type in (
        "paragraph",
        "bullet_list",
        "metric",
        "quote",
        "callout",
        "comparison_side",
        "process_step",
        "timeline_item",
        "icon_label",
        "heading",
    ):
        assert f"`{block_type}`" in prompt
    assert "<factuality_policy>" in prompt
    assert "Preserve exact values, units, signs, time periods, scope, and uncertainty" in prompt


def test_html_prompt_defines_composition_fidelity_dom_and_css_contracts():
    prompt = load_node_prompt("html_slide_designer")

    for requirement in (
        "<design_workflow>",
        "<recipe_execution>",
        "<content_fidelity>",
        "<semantic_dom_contract>",
        "<css_contract>",
        "<anti_template_patterns>",
        "every non-empty approved content block",
        "layout_preference",
        "--slide-bg",
        "data-title-lines",
        "1600×900",
    ):
        assert requirement in prompt


def test_repair_prompt_maps_diagnostics_to_minimal_safe_changes():
    prompt = load_node_prompt("html_repair")

    for requirement in (
        "<repair_invariants>",
        "<priority_order>",
        "<minimal_change_ladder>",
        "<diagnostic_playbook>",
        "ASSERTION_TITLE_MISSING",
        "TEXT_OVERFLOW",
        "BOX_OVERLAP",
        "LOW_TEXT_CONTRAST",
        "available_icon_keys",
        "deterministic fallback",
    ):
        assert requirement in prompt
