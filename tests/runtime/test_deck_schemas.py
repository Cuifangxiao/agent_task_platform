import copy

import pytest
from pydantic import ValidationError

from core.deck.agents.processor.generation import _validate_design_plan
from core.deck.agents.processor.layout import deck_plan_from_outline
from core.deck.domain.schemas import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    PROMPT_VERSION,
    RENDERER_VERSION,
    SCHEMA_VERSION,
    DeckOutlineSpec,
    DeckPlan,
    GlobalStyleSpec,
    HtmlSlideSpec,
    LayoutFamily,
    SlideDesignPlan,
    SlideOutlineSpec,
    SlideRole,
)


PALETTE = {
    "primary_color": "#1570EF",
    "secondary_color": "#B692F6",
    "accent_color": "#F79009",
    "background_color": "#101828",
    "surface_color": "#1D2939",
    "text_primary": "#FFFFFF",
    "text_secondary": "#D0D5DD",
    "text_muted": "#98A2B3",
    "border_color": "#475467",
    "success_color": "#12B76A",
}


def _slide_payload(index: int, page_count: int) -> dict:
    if index == 1:
        role = "cover"
        layout = "cover_minimal"
    elif index == page_count:
        role = "closing"
        layout = "closing_resolution"
    else:
        role = "insight"
        layout = "editorial_split" if index % 2 else "three_points"
    return {
        "id": f"s{index:02d}",
        "title": f"Page {index}",
        "type": role,
        "key_points": [f"Point {index}"],
        "layout_suggestion": layout,
        "content_mode": "mixed",
        "content_note": f"Source section {index}.",
    }


def _outline_payload(page_count: int = 3) -> dict:
    return {
        "title": "Three-stage deck",
        "subtitle": "Outline, visual plan, HTML",
        "metadata": {
            "author": "",
            "date": "",
            "language": "en-US",
            "keywords": ["deck", "html"],
            "design_style": "Dark executive editorial",
            "source_summary": "A source-grounded explanation of the new pipeline.",
            "global_style_spec": PALETTE,
        },
        "slides": [_slide_payload(index, page_count) for index in range(1, page_count + 1)],
    }


def _design_plan_payload(slide_id: str = "s02") -> dict:
    return {
        "slide_id": slide_id,
        "design_rationale": {
            "request_analysis": "Show the approved evidence clearly.",
            "content_structure": "Assertion first, evidence second.",
            "layout_reasoning": "Use the locked split layout for the evidence hierarchy.",
            "design_choices": "Use restrained spacing and approved typography.",
            "consistency_check": "The palette and layout match the deck-wide lock.",
        },
        "design_specifications": {
            "layout_type": "three_points",
            "color_scheme": PALETTE,
            "typography": {
                "font_family": "Noto Sans SC, Inter",
                "title": "48px/1.15 700",
                "heading": "28px/1.2 700",
                "body": "21px/1.4 400",
                "caption": "16px/1.3 400",
            },
            "key_elements": ["assertion title", "three evidence blocks"],
            "visual_hierarchy": ["title", "evidence", "source note"],
        },
    }


def test_deck_schema_and_prompt_versions_match_the_three_stage_contract():
    assert SCHEMA_VERSION == "deck-state.v6"
    assert PROMPT_VERSION == "html-deck.v12"
    assert RENDERER_VERSION == "chromium-html.v2"
    assert (CANVAS_WIDTH, CANVAS_HEIGHT) == (1600, 900)


def test_global_style_is_complete_hex_normalized_and_readable():
    style = GlobalStyleSpec.model_validate(
        {key: value.lower() for key, value in PALETTE.items()}
    )

    assert style.model_dump(mode="json") == PALETTE

    incomplete = dict(PALETTE)
    incomplete.pop("success_color")
    with pytest.raises(ValidationError, match="success_color"):
        GlobalStyleSpec.model_validate(incomplete)

    with pytest.raises(ValidationError, match="#RRGGBB"):
        GlobalStyleSpec.model_validate({**PALETTE, "accent_color": "rgba(1,2,3,.5)"})

    low_contrast = {
        **PALETTE,
        "background_color": "#FFFFFF",
        "surface_color": "#FFFFFF",
    }
    with pytest.raises(ValidationError, match="contrast"):
        GlobalStyleSpec.model_validate(low_contrast)


def test_global_style_does_not_reject_decorative_colors_by_palette_role_alone():
    style = GlobalStyleSpec.model_validate(
        {
            **PALETTE,
            "primary_color": "#2563EB",
            "background_color": "#0F172A",
            "surface_color": "#1E293B",
            "text_primary": "#F8FAFC",
            "text_secondary": "#CBD5E1",
            "text_muted": "#94A3B8",
        }
    )

    assert style.primary_color == "#2563EB"


@pytest.mark.parametrize("page_count", [1, 30])
def test_deck_outline_accepts_the_full_supported_page_range(page_count):
    outline = DeckOutlineSpec.model_validate(_outline_payload(page_count))

    assert len(outline.slides) == page_count
    assert outline.slides[0].id == "s01"
    assert outline.slides[-1].id == f"s{page_count:02d}"


@pytest.mark.parametrize("page_count", [0, 31])
def test_deck_outline_rejects_page_counts_outside_one_to_thirty(page_count):
    with pytest.raises(ValidationError):
        DeckOutlineSpec.model_validate(_outline_payload(page_count))


@pytest.mark.parametrize(
    "identifiers",
    [
        ["s01", "s03", "s04"],
        ["s01", "s01", "s03"],
        ["s02", "s03", "s04"],
    ],
)
def test_deck_outline_rejects_nonconsecutive_duplicate_or_non_one_based_ids(identifiers):
    payload = _outline_payload(3)
    for slide, identifier in zip(payload["slides"], identifiers):
        slide["id"] = identifier

    with pytest.raises(ValidationError, match="unique, consecutive, and ordered"):
        DeckOutlineSpec.model_validate(payload)


def test_renderer_deck_plan_preserves_outline_order_and_boundary_roles():
    outline = DeckOutlineSpec.model_validate(_outline_payload(3))
    plan = deck_plan_from_outline(outline)

    assert [slide.index for slide in plan.slides] == [1, 2, 3]
    assert [slide.slide_id for slide in plan.slides] == ["s01", "s02", "s03"]
    assert plan.slides[0].role is SlideRole.COVER
    assert plan.slides[-1].role is SlideRole.CLOSING

    invalid = plan.model_dump(mode="json")
    invalid["slides"][0]["role"] = SlideRole.CONTEXT.value
    with pytest.raises(ValidationError, match="first slide must have role=cover"):
        DeckPlan.model_validate(invalid)

    invalid = plan.model_dump(mode="json")
    invalid["slides"][-1]["role"] = SlideRole.DECISION.value
    with pytest.raises(ValidationError, match="final slide must have role=closing"):
        DeckPlan.model_validate(invalid)


def test_cover_uses_deck_subtitle_instead_of_internal_content_note():
    payload = _outline_payload(3)
    payload["subtitle"] = "Audience-facing promise"
    payload["slides"][0]["key_points"] = []
    payload["slides"][0]["content_note"] = (
        "封面页。来源：通用知识。目的：吸引注意。可提及内部参考。"
    )

    plan = deck_plan_from_outline(DeckOutlineSpec.model_validate(payload))

    assert plan.slides[0].takeaway == "Audience-facing promise"
    assert plan.slides[0].content_intent == payload["slides"][0]["content_note"]


def test_design_plan_uses_auditable_rationale_and_exact_global_palette():
    outline_slide = SlideOutlineSpec.model_validate(_slide_payload(2, 3))
    global_style = GlobalStyleSpec.model_validate(PALETTE).model_dump(mode="json")
    plan = SlideDesignPlan.model_validate(_design_plan_payload())

    validated = _validate_design_plan(
        plan,
        outline_slide=outline_slide,
        expected_layout=LayoutFamily.THREE_POINTS,
        global_style=global_style,
        approved_fonts=["Noto Sans SC", "Inter"],
    )
    assert validated.design_specifications.color_scheme.model_dump(mode="json") == global_style
    assert "thinking_process" not in validated.model_dump(mode="json")

    unapproved_font_payload = copy.deepcopy(_design_plan_payload())
    unapproved_font_payload["design_specifications"]["typography"]["font_family"] = (
        "Remote Sans"
    )
    with pytest.raises(ValueError, match="approved local fonts"):
        _validate_design_plan(
            SlideDesignPlan.model_validate(unapproved_font_payload),
            outline_slide=outline_slide,
            expected_layout=LayoutFamily.THREE_POINTS,
            global_style=global_style,
            approved_fonts=["Noto Sans SC", "Inter"],
        )

    conflicting_payload = copy.deepcopy(_design_plan_payload())
    conflicting_payload["design_specifications"]["color_scheme"]["accent_color"] = "#DC6803"
    conflicting = SlideDesignPlan.model_validate(conflicting_payload)
    with pytest.raises(ValueError, match="exactly equal GlobalStyleSpec"):
        _validate_design_plan(
            conflicting,
            outline_slide=outline_slide,
            expected_layout=LayoutFamily.THREE_POINTS,
            global_style=global_style,
        )

    with pytest.raises(ValidationError, match="Extra inputs"):
        SlideDesignPlan.model_validate(
            {**_design_plan_payload(), "thinking_process": {"hidden": "reasoning"}}
        )


def test_html_slide_schema_keeps_fragment_and_strict_field_boundaries():
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
    with pytest.raises(ValidationError, match="safe body fragment"):
        HtmlSlideSpec.model_validate(
            {**valid, "body_html": "<style>.leak{color:red}</style><section>Safe</section>"}
        )

    safe = HtmlSlideSpec.model_validate(
        {**valid, "body_html": '<section class="slide"><header>Safe</header></section>'}
    )
    assert "<header" in safe.body_html
