"""Source document parsing and deterministic deck-title helpers."""

from __future__ import annotations

import re

from core.deck.contract import DeckSourceFile, GenerateDeckInput
from core.tools.document_parser import parse_uploaded_document

MAX_TOTAL_SOURCE_CHARS = 60_000
MAX_CHARS_PER_SOURCE_FILE = 20_000


def parse_uploaded_files(inp: GenerateDeckInput, *, max_total_chars: int = MAX_TOTAL_SOURCE_CHARS, max_chars_per_file: int = MAX_CHARS_PER_SOURCE_FILE) -> tuple[str, str]:
    source_files = collect_source_files(inp)
    if not source_files:
        return "", ""

    sections: list[str] = []
    remaining = max_total_chars
    for index, source_file in enumerate(source_files):
        filename = source_file.filename.strip() or "uploaded-file"

        if remaining <= 0:
            omitted_count = len(source_files) - index
            sections.append(f"[{omitted_count} additional attachment(s) omitted because the context budget was reached.]")
            break

        text = parse_uploaded_document(
            filename,
            source_file.content,
            max_chars=min(max_chars_per_file, remaining),
        ).strip()
        if not text:
            continue

        sections.append(f"### {filename}\n{text}")
        remaining -= len(text)

    return join_source_file_names(source_files), "\n\n".join(sections)


def collect_source_files(inp: GenerateDeckInput) -> list[DeckSourceFile]:
    source_files = [DeckSourceFile(filename=item.filename.strip(), content=item.content) for item in inp.source_files if item.content]
    if not source_files and inp.source_file:
        source_files.append(
            DeckSourceFile(
                filename=(inp.source_file_filename or "uploaded-file").strip(),
                content=inp.source_file,
            )
        )
    return source_files


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
