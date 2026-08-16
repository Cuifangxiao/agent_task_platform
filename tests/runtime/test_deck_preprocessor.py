import json

import pytest

import core.deck.agents.preprocessor.graph as preprocessor_graph
from core.deck.agents.preprocessor import PREPROCESSOR_NODE_SEQUENCE, Preprocessor
from core.deck.domain.schemas import SCHEMA_VERSION, DeckOutlineSpec, LayoutFamily, SlideRole
from core.deck.runtime.llm import StructuredOutputError


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


def _outline_payload(page_count: int) -> dict:
    interior_layouts = [
        LayoutFamily.EDITORIAL_SPLIT.value,
        LayoutFamily.THREE_POINTS.value,
        LayoutFamily.COMPARISON.value,
        LayoutFamily.KEY_METRIC.value,
    ]
    slides = []
    for index in range(1, page_count + 1):
        if index == 1:
            role = SlideRole.COVER.value
            layout = LayoutFamily.COVER_MINIMAL.value
        elif index == page_count:
            role = SlideRole.CLOSING.value
            layout = LayoutFamily.CLOSING_RESOLUTION.value
        else:
            role = SlideRole.INSIGHT.value
            layout = interior_layouts[(index - 2) % len(interior_layouts)]
        slides.append(
            {
                "id": f"s{index:02d}",
                "title": f"Page {index}",
                "type": role,
                "key_points": [
                    f"Evidence for page {index}",
                    f"Implication for page {index}",
                ],
                "layout_suggestion": layout,
                "content_mode": "mixed",
                "content_note": f"Use source section {index}.",
            }
        )
    return {
        "title": "Transformer Overview",
        "subtitle": "From mechanism to application",
        "metadata": {
            "author": "",
            "date": "",
            "language": "en-US",
            "keywords": ["transformer", "attention"],
            "design_style": "Dark executive editorial",
            "source_summary": "A concise explanation of Transformer architecture.",
            "global_style_spec": PALETTE,
        },
        "slides": slides,
    }


class _BoundOutlineLLM:
    def __init__(self, parent, model_type):
        self.parent = parent
        self.model_type = model_type

    def invoke(self, messages):
        payload, _ = json.JSONDecoder().raw_decode(messages[-1][1])
        self.parent.calls.append((self.model_type, payload))
        page_count = self.parent.returned_page_count
        if page_count is None:
            page_count = payload["requested_page_count"] or 4
        return _outline_payload(page_count)


class _OutlineLLM:
    def __init__(self, *, returned_page_count: int | None = None):
        self.returned_page_count = returned_page_count
        self.calls = []

    def with_structured_output(self, model_type, **_kwargs):
        assert model_type is DeckOutlineSpec
        return _BoundOutlineLLM(self, model_type)


def _initial_state(request_id: str = "outline-test", *, pages: int = 3) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": request_id,
        "request_id": request_id,
        "user_description": f"Create a {pages} page Transformer presentation.",
        "uploaded_file_text": "Attention connects every token to relevant context.",
        "source_chunks": [
            {
                "source_file": "research.md",
                "page": 1,
                "section": "Attention",
                "excerpt": "Attention connects every token to relevant context.",
            }
        ],
        "local_assets": [
            {
                "asset_id": "attention-map",
                "kind": "image",
                "original_name": "attention-map.png",
                "mime_type": "image/png",
                "relative_path": "sources/assets/asset-001.png",
                "size_bytes": 128,
                "sha256": "a" * 64,
                "trusted_local_only": True,
                "visual_analysis_available": False,
                "html_embedding_allowed": False,
            }
        ],
    }


def test_preprocessor_is_one_outline_node_and_calls_outline_model_once(tmp_path):
    llm = _OutlineLLM()
    result = Preprocessor({"outline": llm}, tmp_path).run(_initial_state())

    assert PREPROCESSOR_NODE_SEQUENCE == ("generate_deck_outline",)
    assert len(llm.calls) == 1
    assert llm.calls[0][0] is DeckOutlineSpec

    payload = llm.calls[0][1]
    assert payload["requested_page_count"] == 3
    assert payload["hard_constraints"]["minimum_page_count"] == 1
    assert payload["hard_constraints"]["maximum_page_count"] == 30
    assert payload["hard_constraints"]["canvas"] == "1600x900 (16:9)"
    assert set(payload["hard_constraints"]["supported_layout_types"]) == {
        layout.value for layout in LayoutFamily
    }
    assert payload["available_assets"]["source_files"] == ["research.md"]
    assert payload["available_assets"]["local_assets"][0]["relative_path"] == (
        "sources/assets/asset-001.png"
    )

    outline = DeckOutlineSpec.model_validate(result["deck_outline"])
    assert len(outline.slides) == 3
    assert [slide.id for slide in outline.slides] == ["s01", "s02", "s03"]
    assert outline.slides[0].type is SlideRole.COVER
    assert outline.slides[-1].type is SlideRole.CLOSING
    assert result["global_style_spec"] == outline.metadata.global_style_spec.model_dump(mode="json")
    assert result["requested_page_count"] == 3

    project = tmp_path / "outline-test"
    persisted_outline = json.loads((project / "deck-outline.json").read_text(encoding="utf-8"))
    persisted_style = json.loads((project / "global-style.json").read_text(encoding="utf-8"))
    assert persisted_outline == outline.model_dump(mode="json")
    assert persisted_style == outline.metadata.global_style_spec.model_dump(mode="json")
    assert (project / "deck-plan.json").is_file()
    assert (project / "design-spec.json").is_file()


@pytest.mark.parametrize("page_count", [1, 30])
def test_outline_phase_accepts_supported_page_count_boundaries(page_count, tmp_path):
    llm = _OutlineLLM()
    request_id = f"boundary-{page_count}"

    result = Preprocessor({"outline": llm}, tmp_path).run(
        _initial_state(request_id, pages=page_count)
    )

    outline = DeckOutlineSpec.model_validate(result["deck_outline"])
    assert len(outline.slides) == page_count
    assert outline.slides[-1].id == f"s{page_count:02d}"
    assert len(llm.calls) == 1


@pytest.mark.parametrize("page_count", [0, 31])
def test_outline_phase_rejects_explicit_page_count_outside_system_range(page_count, tmp_path):
    llm = _OutlineLLM()

    with pytest.raises(ValueError, match="between 1 and 30"):
        Preprocessor({"outline": llm}, tmp_path).run(
            _initial_state(f"out-of-range-{page_count}", pages=page_count)
        )

    assert llm.calls == []


@pytest.mark.parametrize(
    ("request_text", "page_count"),
    [
        ("Make an 8-page deck", 8),
        ("Page count: 12", 12),
        ("生成八页演示文稿", 8),
        ("生成十二张幻灯片", 12),
    ],
)
def test_explicit_page_count_parser_supports_common_request_forms(request_text, page_count):
    assert preprocessor_graph._requested_page_count(request_text) == page_count


@pytest.mark.parametrize("schema_version", ["", "deck-state.v5"])
def test_outline_phase_rejects_missing_or_legacy_state_version(schema_version, tmp_path):
    llm = _OutlineLLM()
    state = _initial_state("legacy-state")
    state["schema_version"] = schema_version

    with pytest.raises(ValueError, match="restart the task with deck-state.v6"):
        Preprocessor({"outline": llm}, tmp_path).run(state)

    assert llm.calls == []


def test_explicit_page_count_mismatch_retries_and_never_persists_partial_outline(tmp_path):
    llm = _OutlineLLM(returned_page_count=4)

    with pytest.raises(StructuredOutputError, match="requested 3"):
        Preprocessor({"outline": llm}, tmp_path).run(_initial_state("mismatch", pages=3))

    assert len(llm.calls) == 3
    assert not (tmp_path / "mismatch" / "deck-outline.json").exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda payload: payload["slides"][0].update(
                {"type": SlideRole.CONTEXT.value}
            ),
            "first outline slide",
        ),
        (
            lambda payload: payload["slides"][-1].update(
                {"type": SlideRole.DECISION.value}
            ),
            "final outline slide",
        ),
        (
            lambda payload: payload["slides"][1].update(
                {"layout_suggestion": payload["slides"][0]["layout_suggestion"]}
            ),
            "adjacent slides",
        ),
        (
            lambda payload: payload["slides"][1].update({"key_points": []}),
            "2-4 substantive key_points",
        ),
    ],
)
def test_outline_business_validation_enforces_boundary_roles_and_layout_diversity(
    mutate, message
):
    payload = _outline_payload(3)
    mutate(payload)
    outline = DeckOutlineSpec.model_validate(payload)

    with pytest.raises(ValueError, match=message):
        preprocessor_graph._validate_outline_output(  # pylint: disable=protected-access
            outline,
            requested_page_count=3,
        )


def test_outline_phase_requires_outline_model(monkeypatch):
    monkeypatch.setattr(preprocessor_graph, "get_stream_writer", lambda: lambda _event: None)

    with pytest.raises(ValueError, match="missing llm for deck node 'outline'"):
        preprocessor_graph.generate_deck_outline(
            _initial_state("missing-outline"),
            {"unsupported": object()},
        )
