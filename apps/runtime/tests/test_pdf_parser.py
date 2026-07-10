from core.tools.pdf_parser import _parse_with_text_fallback


def test_parse_pdf_text_fallback_extracts_literal_text():
    data = b"%PDF-1.4\nBT\n(Hello PDF) Tj\n[(Second) 120 (line)] TJ\nET\n%%EOF"

    text = _parse_with_text_fallback(data)

    assert "Hello PDF" in text
    assert "Second" in text
    assert "line" in text
