"""Source document parsing and deterministic deck-title helpers."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.deck.contract import DeckSourceFile, GenerateDeckInput
from core.deck.paths import source_dir
from core.tools.document_parser import parse_uploaded_document

MAX_SOURCE_BYTES = 20 * 1024 * 1024


def parse_uploaded_files(
    inp: GenerateDeckInput,
    *,
    max_total_chars: int | None = None,
    max_chars_per_file: int | None = None,
) -> tuple[str, str]:
    names, text, _chunks = parse_uploaded_files_with_provenance(
        inp, max_total_chars=max_total_chars, max_chars_per_file=max_chars_per_file
    )
    return names, text


def parse_uploaded_files_with_provenance(
    inp: GenerateDeckInput,
    *,
    max_total_chars: int | None = None,
    max_chars_per_file: int | None = None,
) -> tuple[str, str, list[dict[str, Any]]]:
    source_files = collect_source_files(inp)
    if not source_files:
        return "", "", []

    sections: list[str] = []
    remaining = max_total_chars
    chunks: list[dict[str, Any]] = []
    for index, source_file in enumerate(source_files):
        filename = source_file.filename.strip() or "uploaded-file"

        if remaining is not None and remaining <= 0:
            omitted_count = len(source_files) - index
            sections.append(f"[{omitted_count} additional attachment(s) omitted because the context budget was reached.]")
            break

        content = _source_content(source_file)
        per_file_limit = max_chars_per_file
        if remaining is not None:
            per_file_limit = min(per_file_limit, remaining) if per_file_limit is not None else remaining
        text = parse_uploaded_document(filename, content, max_chars=per_file_limit).strip()
        if not text:
            continue

        sections.append(f"### {filename}\n{text}")
        chunks.extend(_provenance_chunks(filename, text))
        if remaining is not None:
            remaining -= len(text)

    return join_source_file_names(source_files), "\n\n".join(sections), chunks


def _provenance_chunks(filename: str, text: str) -> list[dict[str, Any]]:
    page = 1
    paragraph = 0
    result: list[dict[str, Any]] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal paragraph, buffer
        excerpt = " ".join(buffer).strip()
        if not excerpt:
            buffer = []
            return
        paragraph += 1
        result.append(
            {
                "id": f"source#{len(result) + 1}",
                "source_file": filename,
                "page": page,
                "section": f"paragraph-{paragraph}",
                "excerpt": excerpt,
            }
        )
        buffer = []

    for line in text.splitlines():
        page_match = re.fullmatch(r"\[Page\s+(\d+)\]", line.strip(), flags=re.IGNORECASE)
        if page_match:
            flush()
            page = int(page_match.group(1))
            paragraph = 0
            continue
        if not line.strip():
            flush()
            continue
        buffer.append(line.strip())
        if sum(len(item) for item in buffer) >= 900:
            flush()
    flush()
    return result


def collect_source_files(inp: GenerateDeckInput) -> list[DeckSourceFile]:
    source_files = [
        DeckSourceFile(
            filename=item.filename.strip(),
            content=item.content,
            resource_id=item.resource_id,
            local_path=item.local_path,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
        )
        for item in inp.source_files
        if item.content or item.local_path
    ]
    if not source_files and inp.source_file:
        source_files.append(
            DeckSourceFile(
                filename=(inp.source_file_filename or "uploaded-file").strip(),
                content=inp.source_file,
            )
        )
    return source_files


def _source_content(source_file: DeckSourceFile) -> bytes:
    if source_file.content:
        return source_file.content
    raw_path = source_file.local_path.strip()
    if not raw_path:
        return b""
    allowed_root = source_dir()
    path = Path(raw_path).expanduser().resolve(strict=True)
    try:
        path.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("deck source resource is outside the controlled source directory") from exc
    if not path.is_file():
        raise ValueError("deck source resource is not a regular file")
    size = path.stat().st_size
    if size > MAX_SOURCE_BYTES or (source_file.size_bytes > 0 and size != source_file.size_bytes):
        raise ValueError("deck source resource size is invalid")
    data = path.read_bytes()
    expected = source_file.sha256.strip().lower()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("deck source resource checksum mismatch")
    return data


def join_source_file_names(source_files: list[DeckSourceFile], *, max_chars: int = 2000) -> str:
    names: list[str] = []
    used = 0
    for index, source_file in enumerate(source_files):
        name = source_file.filename.strip() or "uploaded-file"
        next_used = used + len(name) + (2 if names else 0)
        if next_used > max_chars:
            names.append(f"... (+{len(source_files) - index} more)")
            break
        names.append(name)
        used = next_used
    return ", ".join(names)


def derive_deck_title(description: str, file_name: str, uploaded_file_text: str) -> str:
    for candidate in (description, file_name, uploaded_file_text):
        candidate = (candidate or "").strip()
        if candidate:
            return compact_title(candidate)
    return "Untitled presentation"


def compact_title(value: str) -> str:
    first_line = value.splitlines()[0].strip()
    first_sentence = split_sentence(first_line)[0].strip()
    title = first_sentence or first_line
    if len(title) > 80:
        return title[:77].rstrip() + "..."
    return title


def split_sentence(value: str) -> list[str]:
    return re.split(r"[。.!?！？]", value, maxsplit=1)
