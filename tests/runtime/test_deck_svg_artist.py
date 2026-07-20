from types import SimpleNamespace
import json
import logging

import core.deck.agents.generation.graph as generation_graph
from core.deck.agents.design.graph import build_style_options
from core.deck.design import get_template_pack, template_preview_path, template_svg_source
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.rendering.artist import build_svg_artist_prompt, render_svg_artwork, resolve_page_blueprint, validate_artist_svg
from core.deck.contract import EventType, PptStage
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
        title="Growth report",
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


def test_artist_uses_local_fallback_when_single_model_svg_fails_validation():
    deck, slide = _deck_and_slide()
    llm = SequenceTextLLM([_valid_svg(body=""), _valid_svg()])

    result = render_svg_artwork(llm=llm, state=_state(), deck=deck, slide=slide, outline={})

    assert result.render_mode == "deterministic_fallback"
    assert len(llm.calls) == 1
    assert any(item["kind"] == "svg_content_omission" for item in result.rejected_issues)
    assert any(issue.kind == "svg_artist_fallback" for issue in result.issues)


def test_fixed_component_renderer_is_the_no_model_fallback():
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
    assert "Dominant message / evidence" not in prompt


def test_artist_rereads_persisted_lock_and_sends_only_current_page(tmp_path):
    deck, slide = _deck_and_slide()
    lock = {
        "schema": "ppt-spec-lock",
        "spec_hash": "locked-hash",
        "immutable_visual_tokens": {"palette": {"accent": "#2A9D8F"}},
        "page_locks": [
            {"index": 1, "layout_id": "consulting_evidence"},
            {"index": 2, "layout_id": "consulting_compare"},
        ],
    }
    (tmp_path / "spec-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    state = _state() | {
        "project_dir": str(tmp_path),
        "execution_lock": {"stale": True},
        "design_spec": {"art_direction": {"visual_style": "restrained"}, "large": "x" * 30000},
    }

    prompt = build_svg_artist_prompt(
        state=state,
        deck=deck,
        slide=slide,
        outline={},
        blueprint=resolve_page_blueprint(state, slide, {}),
        previous_visual_signatures=[],
    )

    assert '"stale"' not in prompt
    assert '"layout_id": "consulting_evidence"' in prompt
    assert '"layout_id": "consulting_compare"' not in prompt
    assert "x" * 200 not in prompt


def test_artist_gate_rejects_pages_that_ignore_locked_geometry():
    deck, slide = _deck_and_slide()
    normalized = render_svg_artwork(llm=SequenceTextLLM([_valid_svg()]), state=_state(), deck=deck, slide=slide, outline={}).svg

    issues = validate_artist_svg(
        normalized,
        slide=slide,
        theme=deck.theme,
        blueprint={"geometry": {"required_focus": [1190, 300, 40, 40]}},
    )

    assert any(issue.kind == "svg_blueprint_geometry" for issue in issues)


def test_slide_pipeline_routes_explicit_designer_to_full_svg_artist(monkeypatch, caplog):
    emitted = []
    monkeypatch.setattr(generation_graph, "get_stream_writer", lambda: emitted.append)
    caplog.set_level(logging.INFO, logger=generation_graph.__name__)
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
        "deck_title": "Growth report",
        "user_description": "Explain the growth driver.",
        "theme": default_theme().to_dict(),
        "brief": {"audience": "Leadership", "goal": "Align investment", "language": "en"},
        "plan": {
            "title": "Growth report",
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

    result = generation_graph.write_and_design_slides(
        state,
        {"writer": writer_llm, "designer": designer_llm},
        HTMLSlideRenderer(),
        browser,
    )

    assert len(designer_llm.calls) == 1
    assert result["artifacts"][0]["svg_renderer"] == "llm_svg"
    assert result["artifacts"][0]["template_id"] == "consulting_signal"
    assert "Market growth" in browser.svg
    assert [
        (event.type, event.stage, event.page.page_index if event.page else None)
        for event in emitted
    ] == [
        (EventType.STEP_STARTED, PptStage.GENERATING_PAGES, None),
        (EventType.STEP_STARTED, PptStage.GENERATING_PAGES, 1),
        (EventType.ARTIFACT_READY, PptStage.GENERATING_PAGES, 1),
    ]
    assert [event.progress.current for event in emitted[1:]] == [1, 1]
    assert [event.progress.total for event in emitted[1:]] == [1, 1]
    assert "event=ppt_slide_designer_end" in caplog.text
    assert "model_call_count=1" in caplog.text


def test_slide_pipeline_falls_back_after_one_invalid_designer_call(monkeypatch):
    monkeypatch.setattr(generation_graph, "get_stream_writer", lambda: lambda _event: None)
    writer_llm = StaticJSONLLM(
        {
            "index": 1,
            "role": "evidence",
            "component": "content-basic",
            "title": "Market growth",
            "content": {"body": "Enterprise renewals lead.", "bullets": ["Retention", "Expansion"]},
        }
    )
    designer_llm = SequenceTextLLM(["not svg"])
    state = {
        **_state(),
        "request_id": "first-page-gate",
        "deck_title": "Growth report",
        "theme": default_theme().to_dict(),
        "brief": {"audience": "Leadership", "goal": "Align investment", "language": "en"},
        "plan": {"title": "Growth report", "narrative": "Explain", "slides": [{"index": 1, "title": "Market growth", "role": "evidence", "component": "content-basic"}]},
    }

    result = generation_graph.write_and_design_slides(
        state,
        {"writer": writer_llm, "designer": designer_llm},
        HTMLSlideRenderer(),
        CapturingBrowser(),
    )

    assert len(designer_llm.calls) == 1
    assert result["artifacts"][0]["svg_renderer"] == "deterministic_fallback"


def test_style_uses_one_static_catalog_cover_without_designer_rendering(tmp_path):
    options = build_style_options(
        {
            "request_id": "static-style-covers",
            "deck_title": "Clinical report",
            "user_description": "医院病例与临床研究汇报",
            "brief": {"audience": "医生", "goal": "说明临床证据"},
            "plan": {"title": "Clinical report", "slides": [{"index": 1}]},
        },
        tmp_path,
    )

    assert len(options) == 1
    assert options[0]["template_id"] == "medical_university"
    assert options[0]["template_source"]["kind"] == "ppt-master"
    assert all(option["sample_render_mode"] == "static_template_cover" for option in options)
    assert all(option["cover_html"] == "" and option["thumb_b64"] == "" for option in options)
    for option in options:
        cover = tmp_path / "static-style-covers" / option["cover_png"]
        source = template_preview_path(get_template_pack(option["template_id"]))
        assert cover.is_file()
        assert cover.suffix == ".png"
        assert cover.read_bytes() == source.read_bytes()
