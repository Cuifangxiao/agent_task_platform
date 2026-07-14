"""Source document parsing and deterministic deck-title helpers."""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from core.deck.assets import (
    IMAGE_MIME_TYPES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_EDGE,
    MAX_IMAGE_PIXELS,
    is_image_filename,
)
from core.deck.contract import GeneratePptInput, InputResource
from core.deck.paths import DeckProjectPaths
from core.tools.document_parser import parse_uploaded_document

MAX_SOURCE_BYTES = 20 * 1024 * 1024


def persist_normalized_source_bundle(
    request_dir: Path,
    *,
    source_names: str,
    normalized_text: str,
    source_chunks: list[dict[str, Any]],
) -> None:
    """Persist the normalized content contract before strategy begins."""

    project = DeckProjectPaths(Path(request_dir).resolve()).ensure()
    if normalized_text.strip():
        project.sources.joinpath("normalized-source.md").write_text(
            normalized_text.rstrip() + "\n",
            encoding="utf-8",
        )
    project.sources.joinpath("source-chunks.json").write_text(
        json.dumps(source_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    project.sources.joinpath("source-manifest.json").write_text(
        json.dumps(
            {
                "schema": "deck-source-manifest",
                "source_names": source_names,
                "normalized_markdown": "normalized-source.md" if normalized_text.strip() else "",
                "chunks": len(source_chunks),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_uploaded_files(
    inp: GeneratePptInput,
    *,
    max_total_chars: int | None = None,
    max_chars_per_file: int | None = None,
) -> tuple[str, str]:
    names, text, _chunks = parse_uploaded_files_with_provenance(
        inp, max_total_chars=max_total_chars, max_chars_per_file=max_chars_per_file
    )
    return names, text


def parse_uploaded_files_with_provenance(
    inp: GeneratePptInput,
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

        # Image attachments are first-class visual assets. Sending their binary
        # data to the text document parser creates garbage context and can make
        # outline generation fail before the asset planner sees them.
        if is_image_filename(filename):
            continue

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


def materialize_source_images(inp: GeneratePptInput, request_dir: Path) -> list[dict[str, Any]]:
    """Decode and re-encode uploaded images into a request-scoped safe directory.

    Re-encoding strips user filenames and embedded payloads/metadata from the
    file consumed by the browser and PPTX exporter. Only the sanitized image is
    written into the task artifact directory.
    """

    images_dir = request_dir.resolve() / "images"
    source_images = [item for item in collect_source_files(inp) if is_image_filename(item.filename)]
    if not source_images:
        return []
    images_dir.mkdir(parents=True, exist_ok=True)

    result: list[dict[str, Any]] = []
    for index, source_file in enumerate(source_images, start=1):
        original_name = Path(source_file.filename.strip() or f"image-{index}").name
        data = _source_content(source_file)
        if not data:
            raise ValueError(f"uploaded image is empty: {original_name}")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"uploaded image exceeds 20 MB: {original_name}")

        image, detected_extension = _decode_source_image(data, original_name)
        digest = hashlib.sha256(data).hexdigest()
        stored_name = f"source-{index:02d}-{digest[:12]}{detected_extension}"
        stored_path = images_dir / stored_name
        _save_sanitized_image(image, stored_path, detected_extension)
        stored_bytes = stored_path.stat().st_size
        if stored_bytes > MAX_IMAGE_BYTES:
            stored_path.unlink(missing_ok=True)
            raise ValueError(f"sanitized image exceeds 20 MB: {original_name}")

        result.append(
            {
                "asset_id": f"source-image-{index:02d}",
                "source": "user_upload",
                "status": "approved",
                "original_name": original_name,
                "filename": stored_name,
                "relative_path": f"images/{stored_name}",
                "svg_href": f"../images/{stored_name}",
                "mime_type": IMAGE_MIME_TYPES[detected_extension],
                "width": image.width,
                "height": image.height,
                "size_bytes": stored_bytes,
                "sha256": hashlib.sha256(stored_path.read_bytes()).hexdigest(),
            }
        )
    return result


def _decode_source_image(data: bytes, original_name: str) -> tuple[Image.Image, str]:
    try:
        with Image.open(io.BytesIO(data)) as opened:
            detected = str(opened.format or "").upper()
            extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(detected)
            if extension is None:
                raise ValueError(f"unsupported uploaded image format: {original_name}")
            width, height = opened.size
            if width <= 0 or height <= 0 or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE:
                raise ValueError(f"uploaded image dimensions exceed 8192 px: {original_name}")
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError(f"uploaded image exceeds 40 megapixels: {original_name}")
            opened.load()
            image = ImageOps.exif_transpose(opened).copy()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"uploaded image cannot be decoded safely: {original_name}") from exc
    if image.width > MAX_IMAGE_EDGE or image.height > MAX_IMAGE_EDGE or image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError(f"uploaded image dimensions are unsafe after orientation: {original_name}")
    return image, extension


def _save_sanitized_image(image: Image.Image, path: Path, extension: str) -> None:
    if extension == ".png":
        target = image.convert("RGBA" if "A" in image.getbands() else "RGB")
        target.save(path, format="PNG", optimize=True)
        return
    if extension == ".jpg":
        image.convert("RGB").save(path, format="JPEG", quality=92, optimize=True, progressive=False)
        return
    target = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    target.save(path, format="WEBP", quality=92, method=6)


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


def collect_source_files(inp: GeneratePptInput) -> list[InputResource]:
    return [item for item in inp.attachments if item.content]


def _source_content(source_file: InputResource) -> bytes:
    data = source_file.content
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError("deck source resource size is invalid")
    expected = source_file.sha256.strip().lower()
    if expected and hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("deck source resource checksum mismatch")
    return data


def join_source_file_names(source_files: list[InputResource], *, max_chars: int = 2000) -> str:
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
