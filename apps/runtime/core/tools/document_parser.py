"""Document parsing helpers for uploaded prompt context files."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree

from core.tools.pdf_parser import parse_pdf_text


class DocumentParseError(ValueError):
    """Raised when uploaded document bytes cannot be converted into text."""


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".log",
    ".html",
    ".htm",
}


def parse_uploaded_document(filename: str, data: bytes, *, max_chars: int | None = 60000) -> str:
    if not data:
        return ""

    suffix = Path(filename or "").suffix.lower()
    if suffix in IMAGE_SUFFIXES or _looks_like_image(data):
        raise DocumentParseError("image files are not supported for deck generation yet")

    if suffix in PDF_SUFFIXES or data.startswith(b"%PDF"):
        return parse_pdf_text(data, max_chars=max_chars)

    if suffix in DOCX_SUFFIXES or _looks_like_docx(data):
        text = _parse_docx_text(data)
    else:
        if data.startswith(b"PK"):
            raise DocumentParseError("uploaded compressed file type is not supported")
        text = _parse_text_file(data)

    text = _normalize_text(text)
    if not text:
        raise DocumentParseError("uploaded file did not contain extractable text")
    return text[:max_chars] if max_chars is not None else text


def _looks_like_image(data: bytes) -> bool:
    signatures = (
        b"\x89PNG\r\n\x1a\n",
        b"\xff\xd8\xff",
        b"GIF87a",
        b"GIF89a",
        b"RIFF",
        b"BM",
    )
    return any(data.startswith(signature) for signature in signatures)


def _looks_like_docx(data: bytes) -> bool:
    return data.startswith(b"PK") and b"word/document.xml" in data[:4096]


def _parse_docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
    except Exception as exc:
        raise DocumentParseError(f"failed to parse uploaded DOCX: {exc}") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentParseError(f"failed to parse uploaded DOCX XML: {exc}") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(namespace + "p"):
        texts = [node.text or "" for node in paragraph.iter(namespace + "t")]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _parse_text_file(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue

    decoded = data.decode("latin-1", errors="ignore")
    if _binary_noise_ratio(decoded) > 0.08:
        raise DocumentParseError("uploaded file type is not supported")
    return decoded


def _binary_noise_ratio(value: str) -> float:
    if not value:
        return 1
    noisy = sum(1 for ch in value if ord(ch) < 32 and ch not in "\n\r\t")
    return noisy / len(value)


def _normalize_text(value: str) -> str:
    value = re.sub(r"<(script|style)\b.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in value.splitlines()]
    normalized: list[str] = []
    for line in lines:
        if line or (normalized and normalized[-1] != ""):
            normalized.append(line)
    return "\n".join(normalized).strip()
