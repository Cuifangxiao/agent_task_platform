import re
from pathlib import Path

from core.deck.spec import COMPONENTS
from core.deck.spec import normalize_slide_spec
from core.deck.quality import dedupe_issue_dicts, validate_html_static, validate_slide_spec


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


def test_validate_html_static_allows_plain_text_urls_but_blocks_external_resources():
    ok_html = '<section class="slide"><p data-text>Read https://example.com for context.</p></section>'
    blocked_html = '<section class="slide"><img src="https://example.com/a.png"><p data-text>Hello</p></section>'

    assert not any(issue.kind == "unsafe_html" for issue in validate_html_static(ok_html, page=1))
    assert any(issue.kind == "unsafe_html" for issue in validate_html_static(blocked_html, page=1))


def test_dedupe_issue_dicts_uses_page_kind_and_description():
    issues = [
        {"page": 1, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
        {"page": 1, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
        {"page": 2, "kind": "density", "description": "Too much text.", "severity": "must_fix"},
    ]

    assert len(dedupe_issue_dicts(issues)) == 2


def test_components_markdown_stays_in_sync_with_spec_components():
    components_doc = Path(__file__).resolve().parents[1] / "core" / "deck" / "design_system" / "components.md"
    documented = set(re.findall(r"\| `([^`]+)` \|", components_doc.read_text(encoding="utf-8")))

    assert documented == COMPONENTS
