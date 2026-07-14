from core.deck.pipeline.brief import build_brief, normalize_brief
from core.deck.pipeline.style import theme_is_valid
from core.deck.quality.rules import validate_dom_result
from core.deck.spec import ThemeTokens


def test_validate_dom_result_maps_browser_findings_to_must_fix_issues():
    issues = validate_dom_result(
        [{"kind": "overflow", "detail": "Text content overflows.", "text": "Long title"}],
        page=2,
    )

    assert len(issues) == 1
    assert issues[0].page == 2
    assert issues[0].severity == "must_fix"
    assert issues[0].kind == "dom_overflow"


def test_normalize_brief_falls_back_when_model_omits_facts():
    brief = normalize_brief(
        {"audience": "Execs", "suggested_pages": 99},
        fallback={"facts": [{"id": "fact#1", "statement": "Known fact"}]},
    )

    assert brief["facts"] == [{"id": "fact#1", "statement": "Known fact"}]
    assert brief["audience"] == "Execs"
    assert brief["suggested_pages"] == 30


def test_fact_ledger_keeps_all_source_facts_with_provenance():
    chunks = [
        {
            "source_file": "report.pdf",
            "page": index + 1,
            "section": "paragraph-1",
            "excerpt": f"Metric {index} reached {index + 10}%.",
        }
        for index in range(30)
    ]

    brief = build_brief("Report", "Board update", "", source_chunks=chunks)
    source_facts = [fact for fact in brief["facts"] if fact["source_file"] == "report.pdf"]

    assert len(source_facts) == 30
    assert source_facts[-1]["page"] == 30


def test_theme_is_valid_rejects_low_contrast_tokens():
    low_contrast = ThemeTokens.from_dict(
        {
            "name": "Bad",
            "palette": {
                "bg": "#FFFFFF",
                "surface": "#FFFFFF",
                "ink": "#FFFFFF",
                "muted": "#EEEEEE",
                "accent": "#000000",
                "accent2": "#111111",
                "line": "#DDDDDD",
            },
            "fonts": {"display": "Noto Sans SC", "body": "Noto Sans SC", "display_weight": 700},
            "type_scale": {"h1": 44, "h2": 30, "body": 18, "caption": 13},
        }
    )

    assert theme_is_valid(low_contrast) is False
