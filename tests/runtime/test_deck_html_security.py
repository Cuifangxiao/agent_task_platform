import pytest

from core.deck.design_system.catalog import get_html_theme
from core.deck.agents.processor.generation import (
    _assert_content_source_safe,
    _content_contract_diagnostics,
)
from core.deck.domain.text import extract_number_tokens
from core.deck.html.assembler import HTMLAssembler
from core.deck.html.icons import icon_registry
from core.deck.html.sanitizer import sanitize_css, sanitize_html_fragment
from core.deck.runtime.llm import StructuredOutputError
from core.deck.domain.schemas import ContentBlock, DeckBrief, SlideContentSpec, SlidePlan


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


def test_html_content_contract_rejects_drift_and_invented_numbers():
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
        "HTML_INTRODUCED_NUMBERS",
        "METRIC_VALUE_MISSING",
    }


def _factuality_slide() -> SlidePlan:
    return SlidePlan(
        slide_id="s02",
        index=2,
        role="evidence",
        assertion_title="Clarity makes the workflow easier to follow",
        takeaway="Use a simple sequence to guide the audience.",
        content_intent="Explain the workflow clearly.",
        layout_family="statement_focus",
        rhythm="breathing",
    )


def _factuality_brief(
    *,
    source_text: str = "Only supplied fact: the product exists.",
) -> DeckBrief:
    return DeckBrief(
        topic="Workflow clarity",
        audience="Product leaders",
        goal="Explain a clearer workflow.",
        audience_outcome="Understand the proposed sequence.",
        tone="Clear and direct",
        language="en-US",
        slide_count=2,
        communication_job="Present a concise workflow proposal.",
        source_text=source_text,
    )


@pytest.mark.parametrize(
    "block",
    [
        ContentBlock(type="quote", text="CEO Alice says Acme customers love the result."),
        ContentBlock(type="paragraph", text="“Customers love it”"),
        ContentBlock(type="paragraph", text="‘Customers rely on Atlas’"),
        ContentBlock(type="paragraph", text="CEO Alice said the launch succeeded."),
        ContentBlock(type="paragraph", text="Alice Chen is the CEO of Acme."),
        ContentBlock(type="paragraph", text="Alice Chen, CEO of Acme, leads the launch."),
        ContentBlock(type="paragraph", text="Alice Chen (CEO of Acme)."),
        ContentBlock(type="paragraph", text="张三，Acme 首席执行官。"),
        ContentBlock(type="paragraph", text="Acme adopted Atlas across its workflow."),
        ContentBlock(type="paragraph", text="Acme uses Atlas."),
        ContentBlock(type="paragraph", text="Acme relies on Atlas."),
        ContentBlock(type="paragraph", text="Acme uses the platform."),
        ContentBlock(type="paragraph", text="Acme relies on the system."),
        ContentBlock(type="paragraph", text="Acme currently relies on the system."),
        ContentBlock(type="paragraph", text="Acme uses our product."),
        ContentBlock(type="paragraph", text="eBay uses Atlas."),
        ContentBlock(type="paragraph", text="Acme is an Atlas customer."),
        ContentBlock(type="paragraph", text="Customers use Atlas."),
        ContentBlock(type="paragraph", text="Teams rely on Atlas."),
        ContentBlock(
            type="paragraph",
            text="Acme customers adopted the workflow and praised the result.",
        ),
        ContentBlock(
            type="paragraph",
            text="Atlas has reduced onboarding uncertainty.",
        ),
        ContentBlock(
            type="paragraph",
            text="Atlas improves onboarding clarity.",
        ),
        ContentBlock(type="paragraph", text="Atlas makes onboarding faster."),
        ContentBlock(type="paragraph", text="Atlas drove onboarding faster."),
        ContentBlock(type="paragraph", text="Atlas drove faster onboarding."),
        ContentBlock(type="paragraph", text="Onboarding is now faster with Atlas."),
        ContentBlock(type="paragraph", text="客户使用Atlas。"),
        ContentBlock(type="paragraph", text="客户依赖 Atlas。"),
        ContentBlock(type="paragraph", text="腾讯使用Atlas。"),
        ContentBlock(type="paragraph", text="阿里巴巴部署了Atlas。"),
        ContentBlock(type="paragraph", text="Acme 是 Atlas 的客户。"),
        ContentBlock(type="paragraph", text="Costs were reduced by Acme."),
        ContentBlock(type="paragraph", text="Atlas让交付更快。"),
        ContentBlock(type="paragraph", text="Atlas 让交付更快。"),
        ContentBlock(
            type="paragraph",
            text="Atlas may help teams, and Acme adopted Atlas across its workflow.",
        ),
        ContentBlock(
            type="paragraph",
            text="建议优化流程，某客户已经采用Atlas并实现增长。",
        ),
    ],
)
def test_content_guard_rejects_unsupported_qualitative_factual_claims(block: ContentBlock):
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[block],
    )

    with pytest.raises(StructuredOutputError, match="unsupported qualitative factual claims"):
        _assert_content_source_safe(content, _factuality_brief(), slide)


def test_content_guard_allows_exact_source_support_for_quote():
    slide = _factuality_slide()
    quote = "CEO Alice says Acme customers love the result."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="quote", text=quote)],
    )

    _assert_content_source_safe(
        content,
        _factuality_brief(source_text=f"Interview transcript: “{quote}”"),
        slide,
    )


@pytest.mark.parametrize(
    "source",
    [
        "“Acme customers adopted Atlas.”",
        "Source: “Acme customers adopted Atlas.”",
        "Fact: supporting evidence — “Acme customers adopted Atlas.”",
        "Evidence: “Acme customers adopted Atlas.”",
        "Quote: “Acme customers adopted Atlas.”",
        "Interview transcript: “Acme customers adopted Atlas.”",
        "Transcript: “Acme customers adopted Atlas.”",
        "Alice stated: “Acme customers adopted Atlas.”",
        "张三表示：“Acme customers adopted Atlas。”",
        "事实：‘Acme customers adopted Atlas。’",
    ],
)
def test_content_guard_allows_only_authoritative_quoted_sources(source: str):
    slide = _factuality_slide()
    claim = "Acme customers adopted Atlas."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=claim)],
    )

    _assert_content_source_safe(
        content,
        _factuality_brief(source_text=source),
        slide,
    )


@pytest.mark.parametrize(
    ("source", "claim"),
    [
        ("Customers do not use Atlas.", "Customers use Atlas."),
        ("Customers use Atlas only in supervised pilots.", "Customers use Atlas."),
        ("It is false that “Customers use Atlas.”", "Customers use Atlas."),
        (
            "The allegation ‘Customers use Atlas.’ was disproven.",
            "Customers use Atlas.",
        ),
        (
            "Suppose “Acme customers adopted Atlas.”",
            "Acme customers adopted Atlas.",
        ),
        (
            "The open question is “Acme customers adopted Atlas?”",
            "Acme customers adopted Atlas.",
        ),
        (
            "Hypothetical example: ‘Acme customers adopted Atlas.’",
            "Acme customers adopted Atlas.",
        ),
        ("假设“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("开放问题是“Acme customers adopted Atlas？”", "Acme customers adopted Atlas."),
        ("假想示例：‘Acme customers adopted Atlas。’", "Acme customers adopted Atlas."),
        ("The report contains “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("For example: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("e.g., “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("Example: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("Scenario: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("Consider “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("Sample: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("Illustration: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("For example, Alice stated: “Acme customers adopted Atlas.”", "Acme customers adopted Atlas."),
        ("例如：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("比如，‘Acme customers adopted Atlas。’", "Acme customers adopted Atlas."),
        ("举例：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("场景：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("情景：‘Acme customers adopted Atlas。’", "Acme customers adopted Atlas."),
        ("考虑是否“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("试想：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("示例：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
        ("例如，张三表示：“Acme customers adopted Atlas。”", "Acme customers adopted Atlas."),
    ],
)
def test_content_guard_rejects_source_with_changed_polarity_or_dropped_caveat(
    source: str,
    claim: str,
):
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=claim)],
    )

    with pytest.raises(StructuredOutputError, match="unsupported qualitative factual claims"):
        _assert_content_source_safe(
            content,
            _factuality_brief(source_text=source),
            slide,
        )


def test_content_guard_requires_source_text_to_support_the_claim():
    slide = _factuality_slide()
    claim = "Acme customers adopted the workflow and praised the result."
    block = ContentBlock(
        type="paragraph",
        text=claim,
    )
    approved = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[block],
    )

    _assert_content_source_safe(approved, _factuality_brief(source_text=claim), slide)
    with pytest.raises(StructuredOutputError, match="unsupported qualitative factual claims"):
        _assert_content_source_safe(
            approved,
            _factuality_brief(source_text="A documented customer interview is available."),
            slide,
        )


def test_content_guard_allows_source_free_headings_and_advisory_prose():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="heading", text="Customer outcomes"),
            ContentBlock(type="paragraph", text="Use “clarity” as the organizing principle."),
            ContentBlock(
                type="paragraph",
                text="Use “Atlas makes onboarding faster” as a heading.",
            ),
            ContentBlock(
                type="paragraph",
                text="The phrase ‘Customers rely on Atlas’ is a draft label.",
            ),
            ContentBlock(
                type="paragraph",
                text="Use “customer success team” as the navigation label.",
            ),
            ContentBlock(
                type="paragraph",
                text="建议把“客户成功路径”作为页面标题。",
            ),
            ContentBlock(type="paragraph", text="Design for clarity, not complexity."),
            ContentBlock(
                type="paragraph",
                text="Teams can improve handoffs by reducing ambiguity.",
            ),
            ContentBlock(
                type="paragraph",
                text="Atlas can improve handoffs by reducing ambiguity.",
            ),
            ContentBlock(
                type="paragraph",
                text="Atlas can make onboarding faster.",
            ),
        ],
    )

    _assert_content_source_safe(content, _factuality_brief(), slide)


def test_content_guard_allows_abstract_design_usage_framing():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="paragraph", text="Clarity relies on hierarchy."),
            ContentBlock(type="paragraph", text="Structure uses contrast to guide attention."),
            ContentBlock(type="paragraph", text="Design uses whitespace to establish focus."),
            ContentBlock(
                type="paragraph",
                text="Communication relies on a clear sequence.",
            ),
            ContentBlock(type="paragraph", text="Hierarchy uses levels to organize content."),
            ContentBlock(type="paragraph", text="Context relies on sequencing."),
            ContentBlock(type="paragraph", text="Messaging prefers clarity."),
            ContentBlock(type="paragraph", text="Evidence uses examples."),
            ContentBlock(
                type="paragraph",
                text="Information Architecture relies on hierarchy.",
            ),
        ],
    )

    _assert_content_source_safe(content, _factuality_brief(), slide)


def test_content_guard_allows_nonassertive_or_customer_experience_framing():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="paragraph", text="This is a customer journey."),
            ContentBlock(type="paragraph", text="提升客户体验。"),
            ContentBlock(type="paragraph", text="What if Acme uses Atlas?"),
            ContentBlock(type="paragraph", text="Acme uses Atlas?"),
        ],
    )

    _assert_content_source_safe(content, _factuality_brief(), slide)


def test_html_content_contract_rejects_designer_introduced_qualitative_claim():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text="Design for clarity, not complexity.")],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<blockquote>CEO Alice said customers adopted Atlas.</blockquote></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_rejects_designer_introduced_chinese_named_usage():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text="Design for clarity, not complexity.")],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<p>腾讯使用Atlas。</p></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_rejects_substantive_quote_in_plain_paragraph():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text="Design for clarity, not complexity.")],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<p>“Customers love it”</p></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_reconstructs_claim_split_across_siblings():
    slide = _factuality_slide()
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text="Design for clarity, not complexity.")],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<div><span>Customers</span><span> use Atlas.</span></div></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_allows_metalinguistic_quoted_label():
    slide = _factuality_slide()
    label_instruction = "Use “Atlas makes onboarding faster” as a heading."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=label_instruction)],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        f"<p>{label_instruction}</p></section>",
        slide,
        content,
        fallback_used=False,
    )

    assert diagnostics == []


def test_html_content_contract_rejects_approved_claim_with_dropped_caveat():
    slide = _factuality_slide()
    approved_claim = "Atlas improves onboarding clarity only during supervised pilots."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=approved_claim)],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<p>Atlas improves onboarding clarity.</p></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_rejects_shortened_duplicate_beside_exact_claim():
    slide = _factuality_slide()
    approved_claim = "Atlas improves onboarding clarity only during supervised pilots."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=approved_claim)],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        f"<p>{approved_claim}</p>"
        '<p>Atlas improves onboarding clarity.</p></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_allows_exact_claim_split_across_inline_nodes():
    slide = _factuality_slide()
    approved_claim = "Atlas improves onboarding clarity only during supervised pilots."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="paragraph", text=approved_claim)],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        '<p><span>Atlas improves onboarding clarity</span>'
        '<span> only during supervised pilots.</span></p></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert diagnostics == []


def test_html_content_contract_accepts_approved_quote():
    slide = _factuality_slide()
    quote = "CEO Alice says Acme customers love the result."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[ContentBlock(type="quote", text=quote)],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        f"<blockquote>{quote}</blockquote></section>",
        slide,
        content,
        fallback_used=False,
    )

    assert diagnostics == []


def test_html_content_contract_rejects_merged_reordered_approved_quotes():
    slide = _factuality_slide()
    first = "Customers use Atlas in supervised pilots."
    second = "CEO Alice confirmed the pilot scope."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="quote", text=first),
            ContentBlock(type="quote", text=second),
        ],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        f'<blockquote>{second} {first}</blockquote></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert "HTML_INTRODUCED_QUALITATIVE_CLAIM" in {
        item["code"] for item in diagnostics
    }


def test_html_content_contract_accepts_separate_exact_approved_quotes():
    slide = _factuality_slide()
    first = "Customers use Atlas in supervised pilots."
    second = "CEO Alice confirmed the pilot scope."
    content = SlideContentSpec(
        slide_id=slide.slide_id,
        assertion_title=slide.assertion_title,
        takeaway=slide.takeaway,
        content_blocks=[
            ContentBlock(type="quote", text=first),
            ContentBlock(type="quote", text=second),
        ],
    )

    diagnostics = _content_contract_diagnostics(
        '<section class="slide"><h1>Clarity makes the workflow easier to follow</h1>'
        '<p>Use a simple sequence to guide the audience.</p>'
        f'<blockquote>{first}</blockquote><blockquote>{second}</blockquote></section>',
        slide,
        content,
        fallback_used=False,
    )

    assert diagnostics == []


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
