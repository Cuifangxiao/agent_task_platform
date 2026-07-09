from core.deck.spec import normalize_slide_spec
from core.deck.validators import validate_slide_spec


def test_normalize_slide_spec_accepts_legacy_slide_json_and_compacts():
    slide = normalize_slide_spec(
        {
            "title": "Architecture",
            "key_message": "Go owns HTTP, Python owns agent workflow.",
            "layout": "content",
            "bullets": [
                "Go streams SSE to the browser",
                "Python runs LangGraph and renders artifacts",
                "PowerPoint export stays downloadable",
                "This extra point is still allowed",
                "This point is dropped by density limits",
            ],
            "speaker_notes": "Explain the runtime boundary.",
        },
        index=2,
        outline={"role": "evidence", "component": "content-basic"},
        deck_title="PPTAgent MVP",
    )

    assert slide.component == "content-basic"
    assert slide.role == "evidence"
    assert slide.content["body"] == "Go owns HTTP, Python owns agent workflow."
    assert len(slide.content["bullets"]) == 4
    assert slide.notes == "Explain the runtime boundary."


def test_validate_slide_spec_rejects_placeholder_text():
    slide = normalize_slide_spec(
        {"title": "TODO", "layout": "content", "bullets": ["Key point 1"]},
        index=1,
        outline={},
        deck_title="Deck",
    )

    issues = validate_slide_spec(slide)
    assert any(issue.kind == "placeholder_text" for issue in issues)
