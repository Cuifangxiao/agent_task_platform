from core.deck.agents.generation.graph import relevant_source_context
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import COMPONENTS, DeckSpec, SlideSpec, default_theme


def test_cjk_source_ranking_does_not_require_whitespace_tokens():
    state = {
        "source_chunks": [
            {"source_file": "a.md", "page": 1, "section": "intro", "excerpt": "无关的组织背景介绍。"},
            {"source_file": "b.md", "page": 2, "section": "growth", "excerpt": "市场增长主要由企业客户续费驱动。"},
        ]
    }

    context = relevant_source_context(
        state,
        {"title": "市场增长的驱动因素", "key_message": "企业客户续费", "role": "evidence"},
        limit=1,
    )

    assert "b.md" in context
    assert "企业客户续费" in context


def test_every_catalogued_component_has_three_geometry_variants():
    renderer = HTMLSlideRenderer()
    content = {
        "subtitle": "Subtitle",
        "body": "Body",
        "bullets": ["A", "B"],
        "items": ["A", "B", "C"],
        "steps": ["A", "B"],
        "cards": [{"value": "1", "label": "A", "caption": "B"}],
        "left": ["A"],
        "right": ["B"],
        "rows": [["A", "B"]],
        "columns": ["A", "B"],
        "quote": "Quote",
        "takeaway": "Takeaway",
    }
    deck = DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[])
    for component in COMPONENTS:
        for variant in ("balanced", "editorial", "split"):
            slide = SlideSpec(
                index=1,
                role="evidence",
                component=component,
                title="Title",
                content=content,
                chart={"type": "bar", "series": [{"label": "A", "value": 1}], "unit": ""},
                layout_variant=variant,
                design={"accent_slot": "title"},
            )
            html = renderer.render(deck, slide)
            assert f"variant-{variant}" in html
            assert "design-accent-slot-title" in html
        assert f'variant-editorial[data-component="{component}"]' in html
        assert f'variant-split[data-component="{component}"]' in html


def test_motif_changes_deterministic_decorative_geometry():
    renderer = HTMLSlideRenderer()
    first_theme = default_theme()
    second_theme = default_theme()
    second_theme.motif = "diagonal signal rails"
    slide = SlideSpec(index=1, role="hook", component="cover", title="Title", content={"subtitle": "Subtitle"})
    first = renderer.render(
        DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=first_theme, slides=[]),
        slide,
    )
    second = renderer.render(
        DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=second_theme, slides=[]),
        slide,
    )
    assert first != second
    assert "--motif-offset" in first and "--motif-angle" in second
