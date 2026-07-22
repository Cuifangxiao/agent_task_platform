from core.deck.design_system.catalog import get_html_theme
from core.deck.agents.processor.generation import _content_contract_diagnostics
from core.deck.domain.text import extract_number_tokens
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.icons import icon_registry
from core.deck.html.sanitizer import sanitize_css, sanitize_html_fragment
from core.deck.domain.schemas import ContentBlock, SlideContentSpec, SlidePlan


def _codes(result) -> set[str]:
    return {item.code for item in result.diagnostics}


def test_html_sanitizer_removes_executable_tags_and_attributes():
    result = sanitize_html_fragment(
        '<section class="slide" onclick="alert(1)">'
        '<script>alert(2)</script><img src="https://example.test/a.png">'
        '<p style="color:red" data-box="content">Safe copy</p>'
        '<svg><path d="M0 0 L4 4" onload="alert(3)"/></svg>'
        "</section>"
    )

    assert "<script" not in result.html
    assert "<img" not in result.html
    assert "onclick" not in result.html
    assert "onload" not in result.html
    assert "style=" not in result.html
    assert "https://" not in result.html
    assert "Safe copy" in result.html
    assert {"HTML_TAGS_REMOVED", "HTML_ATTRIBUTES_REMOVED"}.issubset(_codes(result))


def test_css_sanitizer_keeps_scoped_layout_and_drops_network_global_and_fixed_rules():
    result = sanitize_css(
        """
        @import url('https://example.test/evil.css');
        body { color: red; }
        .slide .safe { position:absolute; color:var(--accent); background-image:linear-gradient(var(--accent), var(--secondary)); }
        .slide .unsafe { position:fixed; background-image:url('https://example.test/a.png'); z-index:999; color:var(--unknown); }
        """
    )

    assert ".slide .safe" in result.css
    assert "position:absolute" in result.css
    assert "linear-gradient" in result.css
    assert "@import" not in result.css
    assert "body{" not in result.css
    assert "position:fixed" not in result.css
    assert "url(" not in result.css
    assert "z-index:999" not in result.css
    assert "--unknown" not in result.css
    assert {
        "CSS_AT_RULE_REMOVED",
        "CSS_SELECTOR_REMOVED",
        "CSS_VALUE_REMOVED",
    }.issubset(_codes(result))


def test_css_sanitizer_blocks_style_breakout_and_pathological_layout_values():
    result = sanitize_css(
        """
        .slide .breakout { color:red</style><script>alert(1)</script>; }
        .slide .grid { grid-template-columns:repeat(9999, 1px); }
        .slide .far { transform:translateX(99999px); width:99999px; }
        """
    )

    assert "</style>" not in result.css
    assert "repeat(9999" not in result.css
    assert "99999" not in result.css
    assert "CSS_VALUE_REMOVED" in _codes(result)


def test_css_sanitizer_enforces_locked_theme_colors_fonts_and_no_pseudo_elements():
    result = sanitize_css(
        """
        .slide .locked { color:var(--accent); background:linear-gradient(var(--surface),var(--slide-bg)); border:var(--line-width) solid var(--divider); font-family:var(--font-zh),var(--font-latin); }
        .slide .rogue { color:#ffffff; background:red; border:1px solid rgb(0,0,0); font-family:Arial; }
        .slide:after { content:""; position:absolute; inset:0; background:var(--surface); z-index:50; }
        """
    )

    assert "color:var(--accent)" in result.css
    assert "font-family:var(--font-zh),var(--font-latin)" in result.css
    assert "#ffffff" not in result.css
    assert "background:red" not in result.css
    assert "rgb(" not in result.css
    assert "font-family:Arial" not in result.css
    assert ".slide:after" not in result.css
    assert {"CSS_SELECTOR_REMOVED", "CSS_VALUE_REMOVED"}.issubset(_codes(result))


def test_svg_attribute_budget_removes_extreme_coordinates():
    result = sanitize_html_fragment(
        '<section class="slide"><svg data-box="visual" viewBox="0 0 24 24">'
        '<path d="M999999 0 L1 1"/><polyline points="0,0 999999,1"/>'
        "</svg></section>"
    )

    assert "999999" not in result.html
    assert "HTML_ATTRIBUTES_REMOVED" in _codes(result)


def test_number_evidence_extraction_handles_cjk_but_ignores_ids():
    assert extract_number_tokens("收入增长３５％，slide s01，版本v2.0，编号A1.2，年份２０２６") == [
        "35%",
        "2026",
    ]


def test_html_content_contract_rejects_drift():
    slide = SlidePlan(
        slide_id="s02",
        index=2,
        role="evidence",
        assertion_title="留存率提升来自更短的激活路径",
        takeaway="激活步骤从5步缩短到3步后，留存率达到35%",
        content_intent="Explain the evidence.",
        layout_family="key_metric",
        rhythm="anchor",
    )
    content = SlideContentSpec(
        slide_id="s02",
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="metric", value="35%", label="次日留存率"),
            ContentBlock(type="paragraph", text=slide.takeaway),
        ],
    )

    faithful = _content_contract_diagnostics(
        '<section class="slide"><h1>留存率提升来自更短的激活路径</h1>'
        '<div>激活步骤从5步缩短到3步后，留存率达到35%</div></section>',
        slide,
        content,
        fallback_used=False,
    )
    drifted = _content_contract_diagnostics(
        '<section class="slide"><h1>漂亮的数据看板</h1><div>留存率达到99%</div></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert faithful == []
    assert {item["code"] for item in drifted} == {
        "APPROVED_CONTENT_MISSING",
        "ASSERTION_TITLE_MISSING",
        "METRIC_VALUE_MISSING",
    }


def test_trusted_icon_injection_is_local_allowlisted_and_deterministic():
    registry = icon_registry()
    fragment = '<span data-icon="rocket"></span><span data-icon="not-in-registry"></span>'

    first, resolved, missing = registry.inject(fragment)
    second, resolved_again, missing_again = registry.inject(fragment)

    assert first == second
    assert resolved == resolved_again == ["rocket"]
    assert missing == missing_again == ["not-in-registry"]
    assert 'data-resolved-icon="true"' in first
    assert '<svg viewBox="0 0 24 24"' in first
    assert "not-in-registry" not in first
    assert "<script" not in first
    assert "href=" not in first


def test_assembler_owns_document_shell_csp_and_icon_resolution():
    assembler = HTMLAssembler()
    design = get_html_theme("executive_signal").to_design_spec()
    result = assembler.assemble(
        body_html=(
            '<section class="slide" data-layout="statement_focus">'
            '<h1 data-box="title">Trusted shell</h1>'
            '<span data-icon="rocket"></span><span data-icon="missing-key"></span>'
            "</section>"
        ),
        page_css=".slide h1{color:var(--accent)}",
        design_spec=design,
        declared_icon_keys=["rocket", "missing-key"],
    )

    assert result.document.startswith("<!doctype html>")
    assert "Content-Security-Policy" in result.document
    assert "default-src &#x27;none&#x27;" in result.document
    assert '<style id="theme-style">' in result.document
    assert '<style id="base-style">' in result.document
    assert '<style id="page-style">' in result.document
    assert result.resolved_icon_keys == ("rocket",)
    assert result.missing_icon_keys == ("missing-key",)
    assert "missing-key" not in result.body_html
    assert "ICON_KEYS_MISSING" in _codes(result)
