import json
from types import SimpleNamespace

import core.deck.agents.design.graph as design_lock
from core.deck.contract import EventType, PptStage
from core.deck.agents.design.graph import build_design_contract, compile_design_package, freeze_design_contract, page_contract
from core.deck.agents.generation.graph import apply_design_contract
from core.deck.agents.planning.graph import parse_outline_slides
from core.deck.spec import SlideSpec, default_theme


def _state():
    return {
        "request_id": "55555555-5555-4555-8555-555555555555",
        "deck_title": "Locked deck",
        "brief": {"audience": "Leadership", "goal": "Decide", "language": "en"},
        "theme": default_theme().to_dict(),
        "plan": {
            "title": "Locked deck",
            "narrative": "One coherent story",
            "slides": [
                {"index": 1, "title": "Open", "role": "hook", "component": "cover", "key_message": "Set context"},
                {"index": 2, "title": "Compare", "role": "turn", "component": "comparison", "key_message": "Show the change"},
            ],
        },
    }


def test_design_contract_is_stable_and_contains_executable_template_sources():
    first = build_design_contract(_state())
    second = build_design_contract(_state())

    assert first["schema"] == "ppt-design-spec"
    assert first["spec_hash"] == second["spec_hash"]
    assert first["pages"][0]["page_rhythm"] == "anchor"
    assert first["pages"][1]["layout_variant"] == "split"
    assert first["pages"][0]["layout_id"] == "editorial_hero"
    assert first["pages"][0]["template_svg_source"].startswith('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720"')
    assert first["pages"][0]["template_provenance"]["catalog_source"] == "design_system/template_packs.json"
    assert first["template"]["template_id"] == "editorial_freeform"
    assert first["template"]["applied"] is False
    assert page_contract({"design_contract": first}, 2)["component"] == "comparison"


def test_freeze_design_contract_replaces_old_spec_with_authoritative_artifacts(tmp_path, monkeypatch):
    emitted = []
    monkeypatch.setattr(design_lock, "get_stream_writer", lambda: emitted.append)

    result = freeze_design_contract(_state(), tmp_path)

    request_dir = tmp_path / _state()["request_id"]
    expected = {
        "design-spec.json",
        "design-spec.md",
        "spec-lock.json",
        "spec-lock.md",
        "page-blueprints.json",
        "asset-manifest.json",
        "template-manifest.json",
    }
    assert expected.issubset({path.name for path in request_dir.iterdir()})
    persisted = json.loads((request_dir / "design-spec.json").read_text(encoding="utf-8"))
    assert persisted["spec_hash"] == result["design_contract"]["spec_hash"]
    assert result["design_contract"] == result["design_spec"]
    assert "## 10. Page blueprints" in (request_dir / "design-spec.md").read_text(encoding="utf-8")
    assert "## Executor rules" in (request_dir / "spec-lock.md").read_text(encoding="utf-8")
    assert (request_dir / "templates" / "page-001.svg").read_text(encoding="utf-8") == persisted["pages"][0]["template_svg_source"]
    assert emitted[-1].type is EventType.STEP_COMPLETED
    assert emitted[-1].stage is PptStage.GENERATING_ASSETS


def test_design_contract_reapplies_locked_component_without_losing_generated_text():
    state = _state()
    state["design_contract"] = build_design_contract(state)
    slide = SlideSpec(
        2,
        "turn",
        "content-basic",
        "Compare",
        {"body": "Manual handoffs become shared context", "bullets": []},
    )

    locked = apply_design_contract(slide, state)

    assert locked.component == "comparison"
    assert locked.layout_variant == "split"
    assert locked.spec_hash == state["design_contract"]["spec_hash"]
    assert "Manual handoffs become shared context" in locked.content["left"] + locked.content["right"]


def test_explicit_style_model_refines_blueprints_but_default_model_is_not_used():
    class ArtDirector:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "design_narrative": {"visual_throughline": "A precise signal rail"},
                        "pages": [
                            {"index": 1, "layout_id": "editorial_hero", "composition": "Model-refined hero", "reading_order": ["title", "signal"], "emphasis": "Decision"},
                            {"index": 2, "layout_id": "editorial_split", "composition": "Model-refined comparison", "reading_order": ["answer", "evidence"], "emphasis": "Delta"},
                        ],
                    }
                )
            )

    model = ArtDirector()
    default_package = compile_design_package(_state(), llm_registry={"default": model})
    assert model.calls == 0
    assert default_package["page_blueprints"][1]["composition"] != "Model-refined comparison"

    refined = compile_design_package(_state(), llm_registry={"style": model})
    assert model.calls == 1
    assert refined["page_blueprints"][1]["composition"] == "Model-refined comparison"
    assert refined["page_blueprints"][1]["template_provenance"]["art_director_refined"] is True


def test_outline_parser_keeps_page_rhythm_layout_visualization_and_evidence():
    slides = parse_outline_slides(
        """# Strategy\n\n## 1. Decision\n- 核心信息：增长来自续费\n- 角色：evidence\n- 组件：chart-takeaway\n- 页面节奏：密集\n- 版式意图：左侧趋势，右侧结论\n- 可视化：折线趋势图\n- 证据：2025 年续费率 82%\n"""
    )

    assert slides == [
        {
            "index": 1,
            "title": "Decision",
            "key_message": "增长来自续费",
            "role": "evidence",
            "component": "chart-takeaway",
            "layout": "content",
            "page_rhythm": "dense",
            "layout_intent": "左侧趋势，右侧结论",
            "visualization": "折线趋势图",
            "evidence": "2025 年续费率 82%",
        }
    ]


def test_design_package_assigns_only_approved_request_image_hrefs():
    state = _state()
    state["source_images"] = [
        {
            "asset_id": "source-image-01",
            "filename": "source-01-a1b2c3.png",
            "svg_href": "../images/source-01-a1b2c3.png",
            "mime_type": "image/png",
            "width": 1200,
            "height": 800,
            "sha256": "abc",
        }
    ]
    state["plan"]["slides"][0]["visualization"] = "使用上传图片作为主视觉"

    package = compile_design_package(state)

    assert package["asset_manifest"]["assets"][0]["href"] == "../images/source-01-a1b2c3.png"
    assert package["page_blueprints"][0]["asset_ids"] == ["source-image-01"]
    assert package["page_blueprints"][1]["asset_ids"] == []


def test_design_package_rotates_multiple_approved_images_across_image_pages():
    state = _state()
    state["source_images"] = [
        {"asset_id": "image-a", "filename": "a.png", "svg_href": "../images/a.png"},
        {"asset_id": "image-b", "filename": "b.png", "svg_href": "../images/b.png"},
    ]
    for page in state["plan"]["slides"]:
        page["visualization"] = "使用图片作为主视觉"

    package = compile_design_package(state)

    assert package["page_blueprints"][0]["asset_ids"] == ["image-a"]
    assert package["page_blueprints"][1]["asset_ids"] == ["image-b"]
