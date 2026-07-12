from types import SimpleNamespace
import json

import core.deck.pipeline.slides as slides_pipeline
from core.deck.pipeline.style import _style_sample_html
from core.deck.design import template_svg_source
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.rendering.artist import build_svg_artist_prompt, render_svg_artwork, resolve_page_blueprint
from core.deck.spec import DeckSpec, SlideSpec, default_theme


class SequenceTextLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.responses.pop(0))


class StaticJSONLLM:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, messages):
        assert messages
        return SimpleNamespace(content=json.dumps(self.payload))


class CapturingBrowser:
    def __init__(self):
        self.svg = ""

    def render_slide(self, request_id, slide, html, *, theme, svg):
        del request_id, html, theme
        self.svg = svg
        return SimpleNamespace(
            index=slide.index,
            version=1,
            html_rel=f"slides/{slide.index}.html",
            png_rel=f"slides/{slide.index}.png",
            svg_rel=f"slides/{slide.index}.svg",
            renderer="test-browser",
            issues=[],
        )


def _deck_and_slide():
    slide = SlideSpec(
        index=1,
        role="evidence",
        component="content-basic",
        title="Market growth",
        content={"body": "Enterprise renewals lead.", "bullets": ["Retention", "Expansion"]},
        spec_hash="locked-hash",
    )
    deck = DeckSpec(
        title="Growth review",
        narrative="Explain the growth driver.",
        audience="Leadership",
        goal="Align investment",
        language="en",
        theme=default_theme(),
        slides=[slide],
    )
    return deck, slide


def _valid_svg(body="Enterprise renewals lead."):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<g id="background"><rect id="background-field" x="0" y="0" width="1280" height="720" fill="#F7F3EA"/></g>
<g id="header"><text id="headline" x="72" y="110" font-size="44" font-family="Noto Sans SC" fill="#14213D">Market growth</text><line id="headline-rule" x1="72" y1="142" x2="540" y2="142" stroke="#2A9D8F" stroke-width="4"/></g>
<g id="evidence"><rect id="evidence-field" x="72" y="190" width="760" height="390" fill="#FFFFFF" stroke="#D8D3C8" stroke-width="2"/><text id="body" x="110" y="250" font-size="24" font-family="Noto Sans SC" fill="#14213D">{body}</text><circle id="retention-dot" cx="118" cy="340" r="9" fill="#E76F51"/><text id="retention" x="145" y="348" font-size="20" font-family="Noto Sans SC" fill="#14213D">Retention</text><circle id="expansion-dot" cx="118" cy="425" r="9" fill="#E76F51"/><text id="expansion" x="145" y="433" font-size="20" font-family="Noto Sans SC" fill="#14213D">Expansion</text></g>
<g id="footer"><line id="footer-rule" x1="72" y1="650" x2="1200" y2="650" stroke="#D8D3C8" stroke-width="1"/><text id="page-number" x="1180" y="680" font-size="13" font-family="Noto Sans SC" fill="#58677A">1</text></g>
</svg>"""


def _valid_cover_svg():
    return """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<g id="background"><rect id="background-field" x="0" y="0" width="1280" height="720" fill="#F7F3EA"/></g>
<g id="eyebrow"><text id="eyebrow-text" x="84" y="126" font-size="18" font-family="Noto Sans SC" fill="#2A9D8F">ART DIRECTION</text><line id="eyebrow-rule" x1="84" y1="148" x2="330" y2="148" stroke="#2A9D8F" stroke-width="3"/></g>
<g id="hero"><text id="hero-title" x="84" y="286" font-size="58" font-family="Noto Sans SC" fill="#14213D">Market growth</text><rect id="hero-field" x="920" y="80" width="290" height="520" fill="#2A9D8F"/></g>
<g id="support"><text id="hero-subtitle" x="88" y="390" font-size="24" font-family="Noto Sans SC" fill="#14213D">Enterprise renewals lead.</text><circle id="focus-dot" cx="1065" cy="334" r="58" fill="#E76F51"/></g>
</svg>"""


def _state():
    return {
        "template_id": "consulting_signal",
        "design_contract": {
            "spec_hash": "locked-hash",
            "pages": [
                {
                    "index": 1,
                    "template_id": "consulting_signal",
                    "layout_id": "consulting_evidence",
                    "page_rhythm": "dense",
                    "composition": "Evidence canvas with implication rail",
                    "template_svg": '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><g id="real-template-source"><rect id="evidence-zone" x="68" y="174" width="820" height="440" fill="none" stroke="#005587"/></g></svg>',
                }
            ],
        },
    }


def test_artist_uses_full_svg_model_output_and_injects_actual_template_source():
    deck, slide = _deck_and_slide()
    llm = SequenceTextLLM([_valid_svg()])

    result = render_svg_artwork(
        llm=llm,
        state=_state(),
        deck=deck,
        slide=slide,
        outline={},
        previous_visual_signatures=[{"page": -1, "geometry_fingerprint": "different"}],
    )

    assert result.render_mode == "llm_svg"
    assert result.repair_count == 0
    assert result.issues == []
    assert result.template_id == "consulting_signal"
    assert result.layout_id == "consulting_evidence"
    assert 'data-spec-hash="locked-hash"' in result.svg
    assert 'data-text="true"' in result.svg
    human_prompt = llm.calls[0][1][1]
    assert "ACTUAL TEMPLATE SVG STRUCTURE SOURCE" in human_prompt
    assert "real-template-source" in human_prompt
    assert "previous_two_visual_signatures" in human_prompt
    assert "geometry_fingerprint" in human_prompt


def test_artist_repairs_content_omission_before_accepting_svg():
    deck, slide = _deck_and_slide()
    llm = SequenceTextLLM([_valid_svg(body=""), _valid_svg()])

    result = render_svg_artwork(llm=llm, state=_state(), deck=deck, slide=slide, outline={})

    assert result.render_mode == "llm_svg"
    assert result.repair_count == 1
    assert len(llm.calls) == 2
    assert any(item["kind"] == "svg_content_omission" for item in result.rejected_issues)
    assert "DETERMINISTIC QUALITY ISSUES" in llm.calls[1][1][1]


def test_fixed_component_renderer_is_only_the_bounded_failure_fallback():
    deck, slide = _deck_and_slide()

    result = render_svg_artwork(llm=None, state=_state(), deck=deck, slide=slide, outline={})

    assert result.render_mode == "deterministic_fallback"
    assert any(issue.kind == "svg_artist_fallback" for issue in result.issues)
    assert 'data-template-id="consulting_signal"' in result.svg


def test_catalog_materializes_layout_geometry_as_svg_structure():
    source = template_svg_source("consulting_signal", "consulting_evidence")

    assert source.startswith("<svg")
    assert 'data-layout-id="consulting_evidence"' in source
    assert 'id="zone-evidence"' in source
    assert 'id="zone-implication"' in source


def test_prompt_falls_back_to_catalog_svg_when_blueprint_has_no_inline_source():
    deck, slide = _deck_and_slide()
    state = {"template_id": "technical_blueprint", "page_blueprints": [{"index": 1, "layout_id": "blueprint_layers"}]}
    blueprint = resolve_page_blueprint(state, slide, {})

    prompt = build_svg_artist_prompt(
        state=state,
        deck=deck,
        slide=slide,
        outline={},
        blueprint=blueprint,
        previous_visual_signatures=[],
    )

    assert "ACTUAL TEMPLATE SVG STRUCTURE SOURCE" in prompt
    assert 'data-template-id="technical_blueprint"' in prompt
    assert 'id="zone-layers"' in prompt


def test_slide_pipeline_routes_explicit_designer_to_full_svg_artist(monkeypatch):
    emitted = []
    monkeypatch.setattr(slides_pipeline, "get_stream_writer", lambda: emitted.append)

    def legacy_selector_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy component selector was called")

    monkeypatch.setattr(slides_pipeline, "apply_designer_direction", legacy_selector_must_not_run)
    writer_llm = StaticJSONLLM(
        {
            "index": 1,
            "role": "evidence",
            "component": "content-basic",
            "title": "Market growth",
            "content": {"body": "Enterprise renewals lead.", "bullets": ["Retention", "Expansion"]},
        }
    )
    designer_llm = SequenceTextLLM([_valid_svg()])
    browser = CapturingBrowser()
    state = {
        **_state(),
        "request_id": "artist-main-path",
        "deck_title": "Growth review",
        "user_description": "Explain the growth driver.",
        "theme": default_theme().to_dict(),
        "brief": {"audience": "Leadership", "goal": "Align investment", "language": "en"},
        "plan": {
            "title": "Growth review",
            "narrative": "Explain the growth driver.",
            "slides": [
                {
                    "index": 1,
                    "title": "Market growth",
                    "key_message": "Enterprise renewals lead.",
                    "role": "evidence",
                    "component": "content-basic",
                }
            ],
        },
    }

    result = slides_pipeline.write_and_design_slides(
        state,
        {"writer": writer_llm, "designer": designer_llm},
        HTMLSlideRenderer(),
        browser,
    )

    assert len(designer_llm.calls) == 1
    assert result["artifacts"][0]["svg_renderer"] == "llm_svg"
    assert result["artifacts"][0]["template_id"] == "consulting_signal"
    assert "Market growth" in browser.svg


def test_style_checkpoint_can_show_a_real_designer_svg_sample():
    designer = SequenceTextLLM([_valid_cover_svg()])
    theme = default_theme()

    html, render_mode = _style_sample_html(
        title="Market growth",
        narrative="Enterprise renewals lead.",
        theme=theme,
        direction={
            "template_id": "consulting_signal",
            "template_mode": "layout_pack",
            "communication_mode": "top_consulting",
            "visual_style": "restrained clarity",
        },
        designer_llm=designer,
        html_renderer=HTMLSlideRenderer(),
    )

    assert render_mode == "llm_svg"
    assert 'data-template-id="consulting_signal"' in html
    assert "Market growth" in html
