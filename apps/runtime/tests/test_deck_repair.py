from types import SimpleNamespace

import core.deck.pipeline.repair as repair_pipeline
import core.deck.pipeline.review as review_pipeline
from core.deck.pipeline.repair import MAX_REPAIR_ROUNDS, has_fixable_must_fix, repair_deck
from core.deck.pipeline.review import validate_deck
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, default_theme


def test_repair_loop_validates_repairs_and_terminates(monkeypatch):
    emitted = []
    body = (
        "This sentence has enough detail to survive the soft limiter. "
        "This second sentence should be removed before a hard mid-word truncation happens because it is too long."
    )
    deck = DeckSpec(
        title="Deck",
        narrative="",
        audience="",
        goal="",
        language="en",
        theme=default_theme(),
        slides=[
            SlideSpec(
                index=1,
                role="evidence",
                component="content-basic",
                title="A repairable slide title",
                content={"body": body, "bullets": ["One clear point.", "Two clear points.", "Third point should be dropped."]},
            )
        ],
    )

    class FakeBrowser:
        def __init__(self):
            self.rendered_slide = None

        def render_slide(self, request_id, slide, html, *, version, theme, svg=""):
            assert request_id == "req"
            assert html
            assert svg
            assert version == 2
            assert theme.name == default_theme().name
            self.rendered_slide = slide
            return SimpleNamespace(
                issues=[],
                png_rel="slides/1.png",
                html_rel="slides/1.html",
                svg_rel="slides/1.svg",
                thumb_b64="",
                version=version,
                renderer="fake",
            )

    fake_browser = FakeBrowser()
    monkeypatch.setattr(repair_pipeline, "get_stream_writer", lambda: emitted.append)
    monkeypatch.setattr(review_pipeline, "get_stream_writer", lambda: emitted.append)

    state = {"request_id": "req", "deck_spec": deck.to_dict(), "validation_issues": [], "repair_round": 0, "brief": {}}
    state.update(validate_deck(state))
    assert has_fixable_must_fix(state)

    state.update(repair_deck(state, {}, HTMLSlideRenderer(), fake_browser))
    state.update(validate_deck(state))

    assert state["repair_round"] == 1
    assert [issue["kind"] for issue in state["validation_issues"]] == ["svg_artist_fallback"]
    assert state["validation_issues"][0]["severity"] == "must_fix"
    assert fake_browser.rendered_slide is not None
    final_slide = DeckSpec.from_dict(state["deck_spec"]).slides[0]
    assert final_slide.content["body"] == body
    assert final_slide.content["bullets"] == ["One clear point.", "Two clear points.", "Third point should be dropped."]
    assert final_slide.layout_variant == "split"
    assert any(event.get("event_type") == "repair_progress" for event in emitted)


def test_repair_budget_stops_fixable_must_fix_pages():
    assert not has_fixable_must_fix(
        {
            "repair_round": MAX_REPAIR_ROUNDS,
            "validation_issues": [{"page": 1, "severity": "must_fix", "kind": "density"}],
        }
    )
