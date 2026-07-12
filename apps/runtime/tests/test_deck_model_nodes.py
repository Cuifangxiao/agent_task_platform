import json
from types import SimpleNamespace

import core.deck.pipeline.coherence as coherence_pipeline
from core.deck.pipeline.slides import apply_designer_direction, relevant_source_context
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import COMPONENTS, DeckSpec, SlideSpec, default_theme


class CountingJSONLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, messages):
        assert messages
        self.calls += 1
        return SimpleNamespace(content=json.dumps(self.payload))


def test_cjk_source_ranking_does_not_require_whitespace_tokens():
    state = {
        "source_chunks": [
            {"source_file": "a.md", "page": 1, "section": "intro", "excerpt": "无关的组织背景介绍。"},
            {"source_file": "b.md", "page": 2, "section": "growth", "excerpt": "市场增长主要由企业客户续费驱动。"},
        ]
    }

    context = relevant_source_context(state, {"title": "市场增长的驱动因素", "key_message": "企业客户续费", "role": "evidence"}, limit=1)

    assert "b.md" in context
    assert "企业客户续费" in context


def test_designer_model_is_called_and_only_controlled_fields_apply():
    llm = CountingJSONLLM(
        {
            "component": "comparison",
            "layout_variant": "split",
            "design": {"emphasis": "right", "unsafe_html": "<script>"},
            "url": "https://invalid.example",
        }
    )
    slide = SlideSpec(index=1, role="evidence", component="content-basic", title="T", content={"left": ["A"], "right": ["B"]})
    result = apply_designer_direction(llm, slide, {}, default_theme())
    assert llm.calls == 1
    assert result.component == "comparison"
    assert result.layout_variant == "split"
    assert result.design == {"emphasis": "right"}


def test_coherence_model_is_called_and_duplicate_titles_are_fixed(monkeypatch):
    emitted = []
    monkeypatch.setattr(coherence_pipeline, "get_stream_writer", lambda: emitted.append)
    llm = CountingJSONLLM({"issues": [], "title_updates": {"2": "Second viewpoint"}})
    deck = DeckSpec(
        title="D",
        narrative="",
        audience="",
        goal="",
        language="en",
        theme=default_theme(),
        slides=[
            SlideSpec(index=1, role="hook", component="cover", title="Same", content={"subtitle": "A"}),
            SlideSpec(index=2, role="evidence", component="content-basic", title="Same", content={"body": "B"}),
        ],
    )
    result = coherence_pipeline.review_coherence({"deck_spec": deck.to_dict(), "validation_issues": []}, {"coherence": llm})
    assert llm.calls == 1
    assert result["slides"][1]["title"] == "Second viewpoint"
    assert not any(issue["kind"] == "duplicate_title" for issue in result["validation_issues"])
    assert any((event.get("payload") or {}).get("reason") == "title_update" for event in emitted)


def test_coherence_applies_safe_terminology_keeps_locked_page_order_and_rejects_number_changes(monkeypatch):
    monkeypatch.setattr(coherence_pipeline, "get_stream_writer", lambda: lambda _event: None)
    llm = CountingJSONLLM(
        {
            "slide_order": [2, 1],
            "term_replacements": {"client": "customer", "Revenue 40%": "Revenue 50%"},
            "issues": [],
        }
    )
    deck = DeckSpec(
        title="D",
        narrative="",
        audience="",
        goal="",
        language="en",
        theme=default_theme(),
        slides=[
            SlideSpec(index=1, role="evidence", component="content-basic", title="Revenue 40%", content={"body": "client result"}),
            SlideSpec(index=2, role="hook", component="cover", title="Opening", content={"subtitle": "client context"}),
        ],
    )
    result = coherence_pipeline.review_coherence({"deck_spec": deck.to_dict(), "validation_issues": []}, {"coherence": llm})
    assert [slide["title"] for slide in result["slides"]] == ["Revenue 40%", "Opening"]
    assert any(issue["kind"] == "page_order_locked" for issue in result["validation_issues"])
    assert result["slides"][0]["content"]["body"] == "customer result"
    assert result["slides"][1]["content"]["subtitle"] == "customer context"


def test_every_catalogued_component_has_three_rendered_geometry_variants():
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
    first = renderer.render(DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=first_theme, slides=[]), slide)
    second = renderer.render(DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=second_theme, slides=[]), slide)
    assert first != second
    assert "--motif-offset" in first and "--motif-angle" in second
