import pytest

from core.tools.document_parser import DocumentParseError, parse_uploaded_document


def test_parse_uploaded_document_reads_text_file():
    text = parse_uploaded_document("notes.md", "第一段\n第二段".encode())

    assert "第一段" in text
    assert "第二段" in text


def test_parse_uploaded_document_rejects_image_file():
    with pytest.raises(DocumentParseError):
        parse_uploaded_document("image.png", b"\x89PNG\r\n\x1a\n")
