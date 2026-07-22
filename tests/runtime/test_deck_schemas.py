import pytest
from pydantic import ValidationError

from core.deck.agents.processor.design import _has_readable_contrast, _lock_approved_identity
from core.deck.agents.processor.generation import _normalize_content_for_recipe
from core.deck.design_system.catalog import (
    get_html_theme,
    list_html_themes,
    recipe_catalog,
    resolve_theme_id,
)
from core.deck.domain.schemas import (
    ContentBlock,
    DeckBrief,
    DeckPlan,
    HtmlSlideSpec,
    LayoutFamily,
    PageRhythm,
    SlideContentSpec,
    SlidePlan,
    SlideRole,
)


def _slide(index: int, role: SlideRole, layout: LayoutFamily) -> SlidePlan:
    return SlidePlan(
        slide_id=f"s{index:02d}",
        index=index,
        role=role,
        assertion_title=f"Assertion {index}",
        takeaway=f"Takeaway {index}",
        content_intent=f"Explain point {index}",
        layout_family=layout,
        rhythm=PageRhythm.BREATHING,
    )


def _brief(
    *,
    topic: str = "Workflow proposal",
    goal: str = "Explain the product clearly.",
    source_text: str = "",
    slide_count: int = 2,
    language: str = "en-US",
) -> DeckBrief:
    return DeckBrief(
        topic=topic,
        audience="general business audience",
        goal=goal,
        audience_outcome="understand the proposal and decide what to do next",
        tone="clear and concise",
        language=language,
        slide_count=slide_count,
        aspect_ratio="16:9",
        communication_job=(
            "By the end, the audience should understand the proposal and decide what to do next."
        ),
        content_requirements=[],
        brand_preferences=[],
        color_preferences=[],
        style_preferences=[],
        source_mode="general_knowledge",
        source_text=source_text,
    )


def test_deck_plan_locks_order_ids_and_boundary_roles():
    plan = DeckPlan(
        deck_title="HTML deck",
        core_message="HTML is the single authoring source.",
        narrative_arc=["Open", "Resolve"],
        slides=[
            _slide(1, SlideRole.COVER, LayoutFamily.COVER_MINIMAL),
            _slide(2, SlideRole.CLOSING, LayoutFamily.CLOSING_RESOLUTION),
        ],
    )

    assert plan.model_dump(mode="json")["slides"][0]["layout_family"] == "cover_minimal"

    with pytest.raises(ValidationError, match="consecutive"):
        DeckPlan(
            deck_title="Invalid",
            core_message="Invalid ordering",
            narrative_arc=["One"],
            slides=[
                _slide(1, SlideRole.COVER, LayoutFamily.COVER_MINIMAL),
                _slide(3, SlideRole.CLOSING, LayoutFamily.CLOSING_RESOLUTION),
            ],
        )


def test_schema_boundaries_forbid_extra_model_fields_and_full_documents():
    valid = {
        "slide_id": "s01",
        "layout_family": "cover_minimal",
        "layout_variant": "hero-left",
        "composition_summary": "A restrained cover.",
        "body_html": '<section class="slide"><h1>Title</h1></section>',
        "page_css": ".slide h1{color:var(--accent)}",
        "icon_keys": [],
        "estimated_density": "low",
    }

    with pytest.raises(ValidationError, match="Extra inputs"):
        HtmlSlideSpec.model_validate({**valid, "unreviewed": True})
    with pytest.raises(ValidationError, match="safe body fragment"):
        HtmlSlideSpec.model_validate({**valid, "body_html": "<html><body>bad</body></html>"})
    with pytest.raises(ValidationError, match="safe body fragment"):
        HtmlSlideSpec.model_validate({**valid, "body_html": "<script>alert(1)</script>"})

    # Tag-boundary matching must not confuse the safe <header> element with
    # the forbidden document-level <head> element.
    assert "<header" in HtmlSlideSpec.model_validate(
        {**valid, "body_html": '<section class="slide"><header>Safe</header></section>'}
    ).body_html


def test_nested_content_items_have_per_item_length_limits():
    with pytest.raises(ValidationError, match="180 characters"):
        SlideContentSpec(
            slide_id="s01",
            assertion_title="Bounded content",
            takeaway="Keep every nested value bounded.",
            content_blocks=[ContentBlock(type="bullet_list", items=["x" * 181])],
        )


def test_catalog_provides_complete_themes_and_one_recipe_per_layout_family():
    themes = list_html_themes()
    recipes = recipe_catalog().recipes

    assert len(themes) >= 4
    assert len({theme.theme_id for theme in themes}) == len(themes)
    assert {recipe.family for recipe in recipes} == set(LayoutFamily)
    assert len(recipes) == len(LayoutFamily)
    assert all(recipe.variants and recipe.slots for recipe in recipes)
    assert all(theme.to_design_spec().canvas_width == 1600 for theme in themes)
    assert all(theme.to_design_spec().canvas_height == 900 for theme in themes)


def test_catalog_rejects_removed_or_unknown_theme_ids():
    with pytest.raises(ValueError, match="unknown HTML deck theme"):
        resolve_theme_id("removed-theme")

    design = get_html_theme("executive_signal").to_design_spec()
    assert design.theme_id == "executive_signal"
    assert design.typography.page_title.size_px >= 46
    assert design.typography.body.size_px >= 21


@pytest.mark.parametrize("metric_value", ["35%", "复购"])
def test_metric_block_survives_recipe_normalization_without_a_fact_ledger(metric_value):
    brief = _brief(source_text=f"The approved measure is {metric_value}.")
    slide = _slide(2, SlideRole.EVIDENCE, LayoutFamily.KEY_METRIC)
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="metric", value=metric_value, label="Approved measure")
        ],
    )

    normalized = _normalize_content_for_recipe(content, brief, slide)

    assert normalized.content_blocks[0].type.value == "metric"
    assert normalized.content_blocks[0].value == metric_value


def test_style_model_cannot_override_browser_tested_catalog_tokens():
    baseline = get_html_theme("paper_editorial").to_design_spec()
    typography = baseline.typography.model_copy(
        update={
            "cover_title": baseline.typography.cover_title.model_copy(
                update={"size_px": 120, "line_height": 1.8}
            ),
            "page_title": baseline.typography.page_title.model_copy(
                update={"size_px": 120, "line_height": 1.8}
            ),
            "body": baseline.typography.body.model_copy(update={"size_px": 120, "line_height": 1.8}),
            "caption": baseline.typography.caption.model_copy(
                update={"size_px": 120, "line_height": 1.8}
            ),
        }
    )
    proposed = baseline.model_copy(
        update={
            "theme_id": "midnight_product",
            "palette": baseline.palette.model_copy(
                update={
                    "background": "#000000",
                    "surface": "#FFFFFF",
                    "text": "#767676",
                    "muted": "#767676",
                    "accent": "#FFFFFF",
                    "secondary": "#FFFFFF",
                }
            ),
            "typography": typography,
            "spacing": baseline.spacing.model_copy(
                update={"canvas_margin": 180, "base_unit": 16, "column_gap": 96}
            ),
            "shape_style": baseline.shape_style.model_copy(
                update={"radius": 36, "line_width": 4, "shadow": "offset"}
            ),
            "icon_style": "an incompatible filled icon family",
            "motif": "a source-aware descriptive refinement",
        }
    )

    locked = _lock_approved_identity(proposed, baseline)

    assert locked.theme_id == baseline.theme_id
    assert locked.palette == baseline.palette
    assert locked.typography == baseline.typography
    assert locked.spacing == baseline.spacing
    assert locked.shape_style == baseline.shape_style
    assert locked.icon_style == baseline.icon_style
    assert locked.motif == proposed.motif
    assert _has_readable_contrast(locked)
