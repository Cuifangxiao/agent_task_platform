import json
from types import SimpleNamespace

from PIL import Image

import core.deck.pipeline.style as style_pipeline
import core.deck.pipeline.vlm_review as vlm_review_pipeline
from core.deck.pipeline.review import validate_fact_sources
from core.deck.spec import DeckSpec, SlideSpec, default_theme
from core.deck.pipeline.brief import build_brief, normalize_brief
from core.deck.pipeline.style import theme_is_valid
from core.deck.quality.rules import validate_dom_result
from core.deck.quality.vlm import parse_vlm_issues
from core.deck.spec import ThemeTokens, default_theme


def test_validate_dom_result_maps_browser_findings_to_must_fix_issues():
    issues = validate_dom_result([{"kind": "overflow", "detail": "Text content overflows.", "text": "Long title"}], page=2)

    assert len(issues) == 1
    assert issues[0].page == 2
    assert issues[0].severity == "must_fix"
    assert issues[0].kind == "dom_overflow"


def test_parse_vlm_issues_filters_unknown_kinds():
    response = SimpleNamespace(
        content='{"issues":[{"page":1,"severity":"must_fix","kind":"crowding","description":"Too dense"},{"page":2,"kind":"unknown"}]}'
    )

    issues = parse_vlm_issues(response)

    assert len(issues) == 1
    assert issues[0].kind == "vlm_crowding"
    assert issues[0].severity == "must_fix"


def test_review_vlm_sends_contact_sheet_as_image_block(tmp_path, monkeypatch):
    request_id = "req"
    slides_dir = tmp_path / request_id / "slides"
    slides_dir.mkdir(parents=True)
    Image.new("RGB", (320, 180), (10, 20, 30)).save(slides_dir / "1.png")
    emitted = []

    class FakeReviewLLM:
        def __init__(self):
            self.messages = None

        def invoke(self, messages):
            self.messages = messages
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "issues": [
                            {
                                "page": 1,
                                "severity": "must_fix",
                                "kind": "crowding",
                                "description": "Too dense",
                            }
                        ]
                    }
                )
            )

    fake_llm = FakeReviewLLM()
    monkeypatch.setattr(vlm_review_pipeline, "get_stream_writer", lambda: emitted.append)

    result = vlm_review_pipeline.review_vlm(
        {"request_id": request_id, "slides": [{"index": 1}], "validation_issues": [], "repair_round": 2},
            {"review": fake_llm, "__capabilities__": {"review": {"supports_image_input": True}}},
        tmp_path,
    )

    assert result["vlm_reviewed"] is True
    human_content = fake_llm.messages[1].content
    assert any(block.get("type") == "text" for block in human_content)
    image_blocks = [block for block in human_content if block.get("type") == "image_url"]
    assert image_blocks
    assert image_blocks[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert any(event.get("stage") == "review_vlm" and (event.get("payload") or {}).get("status") == "warning" for event in emitted)


def test_review_vlm_uses_global_and_batched_contact_sheets_for_large_decks(tmp_path, monkeypatch):
    request_id = "large"
    slides_dir = tmp_path / request_id / "slides"
    slides_dir.mkdir(parents=True)
    for index in range(1, 10):
        Image.new("RGB", (64, 36), (index, index, index)).save(slides_dir / f"{index}.png")

    class CountingReviewLLM:
        def __init__(self):
            self.calls = 0

        def invoke(self, _messages):
            self.calls += 1
            return SimpleNamespace(content='{"issues": []}')

    llm = CountingReviewLLM()
    monkeypatch.setattr(vlm_review_pipeline, "get_stream_writer", lambda: lambda _event: None)
    result = vlm_review_pipeline.review_vlm(
        {"request_id": request_id, "slides": [{"index": index} for index in range(1, 10)], "validation_issues": [], "repair_round": 0},
        {"review": llm, "__capabilities__": {"review": {"supports_image_input": True}}},
        tmp_path,
    )
    assert result["vlm_reviewed"] is True
    assert llm.calls == 3
    assert (tmp_path / request_id / "review" / "contact_sheet.png").exists()
    assert (tmp_path / request_id / "review" / "batch_1.png").exists()
    assert (tmp_path / request_id / "review" / "batch_2.png").exists()


def test_normalize_brief_falls_back_when_model_omits_facts():
    brief = normalize_brief({"audience": "Execs", "suggested_pages": 99}, fallback={"facts": [{"id": "fact#1", "statement": "Known fact"}]})

    assert brief["facts"] == [{"id": "fact#1", "statement": "Known fact"}]
    assert brief["audience"] == "Execs"
    assert brief["suggested_pages"] == 30


def test_vlm_model_without_image_support_is_explicit(monkeypatch, tmp_path):
    emitted = []
    monkeypatch.setattr(vlm_review_pipeline, "get_stream_writer", lambda: emitted.append)
    result = vlm_review_pipeline.review_vlm(
        {"request_id": "req", "slides": [], "validation_issues": []},
        {"review": object(), "__capabilities__": {"review": {"supports_image_input": False}}},
        tmp_path,
    )
    assert result["validation_issues"][0]["kind"] == "vlm_image_unsupported"
    assert any((event.get("payload") or {}).get("error_code") == "vlm_image_unsupported" for event in emitted)


def test_visible_number_requires_matching_fact_source():
    slide = SlideSpec(
        index=1,
        role="evidence",
        component="content-basic",
        title="Revenue grew 42%",
        content={"body": "Revenue reached 120 million", "bullets": []},
        sources=["fact#1"],
    )
    deck = DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[slide])
    issues = validate_fact_sources(
        deck,
        {"facts": [{"id": "fact#1", "statement": "Revenue grew 40%", "value": "40", "unit": "%", "source_file": "report.pdf", "page": 2}]},
    )
    assert any(issue.kind == "unsupported_numeric_claim" and issue.severity == "must_fix" for issue in issues)


def test_fact_ledger_keeps_all_source_facts_with_provenance():
    chunks = [
        {"source_file": "report.pdf", "page": index + 1, "section": "paragraph-1", "excerpt": f"Metric {index} reached {index + 10}%."}
        for index in range(30)
    ]
    brief = build_brief("Report", "Board update", "", source_chunks=chunks)
    source_facts = [fact for fact in brief["facts"] if fact["source_file"] == "report.pdf"]
    assert len(source_facts) == 30
    assert source_facts[-1]["page"] == 30


def test_dates_money_and_key_entities_require_matching_sources():
    slide = SlideSpec(
        index=1,
        role="evidence",
        component="content-basic",
        title="OPENAI plan for 2027-06-01",
        content={"body": "USD 25 million for Example Group"},
        sources=[],
    )
    issues = validate_fact_sources(
        DeckSpec(title="D", narrative="", audience="", goal="", language="en", theme=default_theme(), slides=[slide]),
        {"facts": []},
    )
    kinds = {issue.kind for issue in issues}
    assert {"unsupported_numeric_claim", "unsupported_date_claim", "unsupported_money_claim", "unsupported_entity_claim"} <= kinds


def test_theme_is_valid_rejects_low_contrast_tokens():
    low_contrast = ThemeTokens.from_dict(
        {
            "name": "Bad",
            "palette": {"bg": "#FFFFFF", "surface": "#FFFFFF", "ink": "#FFFFFF", "muted": "#EEEEEE", "accent": "#000000", "accent2": "#111111", "line": "#DDDDDD"},
            "fonts": {"display": "Noto Sans SC", "body": "Noto Sans SC", "display_weight": 700},
            "type_scale": {"h1": 44, "h2": 30, "body": 18, "caption": 13},
        }
    )

    assert theme_is_valid(low_contrast) is False


def test_review_style_falls_back_when_feedback_model_returns_invalid_json(monkeypatch):
    theme = default_theme()
    emitted = []

    class InvalidJsonLLM:
        def __init__(self):
            self.messages = []

        def invoke(self, messages):
            self.messages.append(messages)
            return SimpleNamespace(content="not json")

    style_llm = InvalidJsonLLM()
    monkeypatch.setattr(style_pipeline, "get_stream_writer", lambda: emitted.append)
    monkeypatch.setattr(style_pipeline, "interrupt", lambda _payload: {"style_id": "style_1", "feedback": "更冷峻一点"})

    result = style_pipeline.review_style(
        {
            "style_options": [
                {
                    "style_id": "style_1",
                    "name": theme.name,
                    "tokens": theme.to_dict(),
                }
            ]
        },
        {"style": style_llm},
    )

    assert result["theme"] == theme.to_dict()
    first_prompt = style_llm.messages[0][1][1]
    assert '"feedback": "更冷峻一点"' in first_prompt
    assert any(event.get("stage") == "style_review" and (event.get("payload") or {}).get("status") == "warning" for event in emitted)
