import pytest
from pydantic import ValidationError

import core.deck.agents.planning.nodes as planning_nodes
from core.deck.agents.planning.nodes import _deterministic_brief, _deterministic_plan
from core.deck.agents.design.nodes import _has_readable_contrast, _lock_approved_identity
from core.deck.design.html_catalog import (
    get_html_theme,
    list_html_themes,
    recipe_catalog,
    resolve_theme_id,
)
from core.deck.schemas import (
    ContentBlock,
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


def test_deck_plan_locks_order_ids_and_boundary_roles():
    plan = DeckPlan(
        deck_title="V2 deck",
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


def test_theme_aliases_resolve_to_a_full_design_spec():
    resolved = resolve_theme_id("consulting_signal")
    design = get_html_theme(resolved).to_design_spec()

    assert resolved == "executive_signal"
    assert design.theme_id == resolved
    assert design.typography.page_title.size_px >= 46
    assert design.typography.body.size_px >= 21


def test_deterministic_planning_fallback_bounds_adversarial_source_fields():
    very_long_fact = "增长率达到123%。" + ("超长来源内容" * 200)
    brief = _deterministic_brief(
        "题" * 400,
        "请生成一份稳健的经营汇报",
        very_long_fact,
        source_chunks=[
            {
                "source_file": "f" * 500,
                "page": -10,
                "section": "s" * 500,
                "excerpt": very_long_fact,
            }
        ],
        requested_count=2,
        requested_language="not a valid browser language tag",
    )
    plan = _deterministic_plan(brief)

    assert brief.language == "zh-CN"
    assert all(len(fact.statement) <= 500 and fact.page >= 0 for fact in brief.facts)
    assert len(brief.communication_job) <= 500
    assert len(plan.slides[0].assertion_title) <= 120
    assert len(plan.slides[0].takeaway) <= 320


class _StaticStructuredLLM:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def with_structured_output(self, _model_type):
        return self

    def invoke(self, _messages):
        self.calls += 1
        return self.response


def test_plan_deck_falls_back_when_model_title_invents_qualitative_fact(monkeypatch):
    emitted = []
    monkeypatch.setattr(planning_nodes, "get_stream_writer", lambda: emitted.append)
    brief = _deterministic_brief(
        "Workflow proposal",
        "Explain the product without customer evidence.",
        "",
        source_chunks=[],
        requested_count=2,
        requested_language="en-US",
    )
    fallback = _deterministic_plan(brief)
    slides = list(fallback.slides)
    slides[0] = slides[0].model_copy(
        update={
            "assertion_title": "CEO Alice said customers adopted Atlas.",
            "takeaway": "Atlas has reduced onboarding uncertainty.",
        }
    )
    invented = fallback.model_copy(
        update={
            "core_message": "A fabricated customer account anchors the story.",
            "slides": slides,
        }
    )
    llm = _StaticStructuredLLM(invented)

    result = planning_nodes.plan_deck(
        {"brief": brief.model_dump(mode="json")},
        {"outline": llm},
    )

    assert result["deck_plan"] == fallback.model_dump(mode="json")
    assert llm.calls == 1
    assert any("unsupported qualitative factual claims" in event.message for event in emitted)


def test_planner_guard_allows_exact_source_and_ordinary_framing():
    sourced_claim = "CEO Alice said customers adopted Atlas."
    brief = _deterministic_brief(
        "Workflow proposal",
        sourced_claim,
        "",
        source_chunks=[],
        requested_count=2,
        requested_language="en-US",
    )

    planning_nodes._assert_source_safe_qualitative_claims(
        [
            sourced_claim,
            "Customer outcomes",
            "Teams can improve handoffs by reducing ambiguity.",
        ],
        brief,
    )


def test_normalize_then_plan_does_not_reuse_invented_brief_customer_claim(monkeypatch):
    emitted = []
    monkeypatch.setattr(planning_nodes, "get_stream_writer", lambda: emitted.append)
    description = "Explain the workflow clearly."
    state = {
        "deck_title": "Workflow proposal",
        "user_description": description,
        "uploaded_file_text": "",
        "source_chunks": [],
        "expected_slide_count": 2,
        "language": "en-US",
    }
    deterministic = _deterministic_brief(
        state["deck_title"],
        description,
        "",
        source_chunks=[],
        requested_count=2,
        requested_language="en-US",
    )
    invented_claim = "Customers use Atlas."
    poisoned = deterministic.model_copy(update={"audience_outcome": invented_claim})
    brief_llm = _StaticStructuredLLM(poisoned)

    normalized = planning_nodes.normalize_request(state, {"brief": brief_llm})
    normalized_brief = planning_nodes.DeckBrief.model_validate(normalized["brief"])
    assert normalized_brief.audience_outcome == deterministic.audience_outcome
    assert invented_claim not in str(normalized["brief"])

    outline_llm = _StaticStructuredLLM(_deterministic_plan(normalized_brief))
    planned = planning_nodes.plan_deck(
        {"brief": normalized["brief"]},
        {"outline": outline_llm},
    )

    assert invented_claim not in str(planned["deck_plan"])
    assert brief_llm.calls == outline_llm.calls == 1
    assert any("unsupported qualitative factual claims" in event.message for event in emitted)


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
