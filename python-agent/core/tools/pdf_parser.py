"""PDF text parsing helpers for document-backed deck generation."""

from __future__ import annotations

import re
from io import BytesIO


class PDFParseError(ValueError):
    """Raised when uploaded PDF bytes cannot be converted into usable text."""


def parse_pdf_text(data: bytes, *, max_chars: int = 60000) -> str:
    if not data:
        return ""

    text = _parse_with_pypdf(data)
    if not text:
        text = _parse_with_text_fallback(data)
    text = _normalize_text(text)
    if not text:
        raise PDFParseError("uploaded PDF did not contain extractable text")
    return text[:max_chars]


def _parse_with_pypdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - optional runtime dependency path
        return ""

    try:
        reader = PdfReader(BytesIO(data))
        pages: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            text = text.strip()
            if text:
                pages.append(f"[Page {index}]\n{text}")
        return "\n\n".join(pages)
    except Exception as exc:  # pragma: no cover - depends on malformed PDFs
        raise PDFParseError(f"failed to parse uploaded PDF: {exc}") from exc


def _parse_with_text_fallback(data: bytes) -> str:
    decoded = data.decode("latin-1", errors="ignore")
    chunks: list[str] = []

    for match in re.finditer(r"\(([^()]*)\)\s*Tj", decoded):
        chunks.append(_decode_pdf_literal(match.group(1)))

    for array_match in re.finditer(r"\[(.*?)\]\s*TJ", decoded, flags=re.DOTALL):
        for item in re.finditer(r"\(([^()]*)\)", array_match.group(1)):
            chunks.append(_decode_pdf_literal(item.group(1)))

    return " ".join(chunks)


def _decode_pdf_literal(value: str) -> str:
    value = value.replace(r"\(", "(").replace(r"\)", ")")
    value = value.replace(r"\\", "\\")
    value = value.replace(r"\n", "\n").replace(r"\r", "\n").replace(r"\t", "\t")
    return value


def _normalize_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)
