"""Canonical image-only PPTX exporter for the HTML-first pipeline."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Inches

from core.deck.errors import ExportError
from core.deck.paths import DeckProjectPaths


PAGE_WIDTH = Inches(13.3333333333)
PAGE_HEIGHT = Inches(7.5)


@dataclass(frozen=True)
class ImagePptxExportResult:
    path: Path
    manifest: dict[str, Any]
    warnings: tuple[str, ...] = ()


class ImagePptxExporter:
    """Place exactly one validated PNG on every 16:9 blank slide."""

    def __init__(self, output_root: Path) -> None:
        self._output_root = Path(output_root)

    def project_paths(self, request_id: str) -> DeckProjectPaths:
        return DeckProjectPaths.for_request(self._output_root, request_id).ensure()

    def export(
        self,
        request_id: str,
        completed_slides: dict[str, dict[str, Any]] | Iterable[dict[str, Any]],
    ) -> ImagePptxExportResult:
        project = self.project_paths(request_id)
        slides = _ordered_slides(completed_slides)
        if not slides:
            raise ExportError("Cannot export a PPTX without completed slide previews.", stage="assemble_image_pptx")

        prs = Presentation()
        prs.slide_width = PAGE_WIDTH
        prs.slide_height = PAGE_HEIGHT
        manifest_slides: list[dict[str, Any]] = []
        for item in slides:
            preview = _resolve_preview(project, item)
            width, height = _validate_source_png(preview)
            preview_digest = hashlib.sha256(preview.read_bytes()).hexdigest()
            expected_digest = str(item.get("preview_hash") or "").strip().removeprefix("sha256:")
            if expected_digest and preview_digest != expected_digest:
                raise ExportError(
                    f"Preview changed after capture: {preview.name}",
                    stage="assemble_image_pptx",
                )
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            slide.shapes.add_picture(str(preview), 0, 0, width=PAGE_WIDTH, height=PAGE_HEIGHT)
            manifest_slides.append(
                {
                    "slide_id": str(item.get("slide_id") or ""),
                    "index": int(item.get("index") or 0),
                    "preview": project.relative(preview),
                    "preview_sha256": preview_digest,
                    "pixel_width": width,
                    "pixel_height": height,
                    "export_mode": "image_full_bleed",
                    "editable": False,
                }
            )

        final_path = project.deck_pptx
        temporary = final_path.with_name(f".{final_path.stem}.{os.getpid()}.tmp.pptx")
        try:
            prs.save(temporary)
            structure = _validate_image_pptx(temporary, expected_slides=len(slides))
            os.replace(temporary, final_path)
        except ExportError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise ExportError(f"Image PPTX export failed: {exc}", stage="assemble_image_pptx") from exc
        finally:
            temporary.unlink(missing_ok=True)

        manifest = {
            "schema": "image-pptx-export.v1",
            "image_based": True,
            "editable": False,
            "slide_count": len(slides),
            "slide_width_inches": 13.3333333333,
            "slide_height_inches": 7.5,
            "slides": manifest_slides,
            "structure": structure,
            "artifact": project.relative(final_path),
        }
        _atomic_write_json(project.export_manifest, manifest)
        return ImagePptxExportResult(path=final_path.resolve(), manifest=manifest)


def _ordered_slides(
    completed_slides: dict[str, dict[str, Any]] | Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    values = completed_slides.values() if isinstance(completed_slides, dict) else completed_slides
    result = [dict(item) for item in values if isinstance(item, dict)]
    result.sort(key=lambda item: (int(item.get("index") or 0), str(item.get("slide_id") or "")))
    indexes = [int(item.get("index") or 0) for item in result]
    if indexes != list(range(1, len(result) + 1)):
        raise ExportError("Completed slide indexes must be consecutive before export.", stage="assemble_image_pptx")
    return result


def _resolve_preview(project: DeckProjectPaths, item: dict[str, Any]) -> Path:
    relative_value = str(item.get("preview_path") or item.get("png") or item.get("preview") or "").strip()
    relative = PurePosixPath(relative_value)
    if not relative_value or relative.is_absolute() or ".." in relative.parts:
        raise ExportError("Preview path must stay inside the task directory.", stage="assemble_image_pptx")
    path = project.root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(project.root)
    except ValueError as exc:
        raise ExportError("Preview path escaped the task directory.", stage="assemble_image_pptx") from exc
    if not path.is_file():
        raise ExportError(f"Missing preview PNG: {relative_value}", stage="assemble_image_pptx")
    return path


def _validate_source_png(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if image.format != "PNG":
                raise ExportError(f"Preview is not a PNG: {path.name}", stage="assemble_image_pptx")
            if width <= 0 or height <= 0 or abs(width / height - 16 / 9) > 0.0005:
                raise ExportError(
                    f"Preview is not exact 16:9: {path.name} ({width}x{height})",
                    stage="assemble_image_pptx",
                )
            if (
                ("A" in image.getbands() or "transparency" in image.info)
                and image.convert("RGBA").getchannel("A").getextrema() != (255, 255)
            ):
                raise ExportError(f"Preview contains transparency: {path.name}", stage="assemble_image_pptx")
            return width, height
    except ExportError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ExportError(f"Preview PNG validation failed for {path.name}: {exc}", stage="assemble_image_pptx") from exc


def _validate_image_pptx(path: Path, *, expected_slides: int) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ExportError("PPTX exporter produced an empty file.", stage="assemble_image_pptx")
    try:
        prs = Presentation(str(path))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise ExportError(f"PPTX cannot be opened: {exc}", stage="assemble_image_pptx") from exc
    if len(prs.slides) != expected_slides:
        raise ExportError(
            f"PPTX page count mismatch: expected {expected_slides}, got {len(prs.slides)}.",
            stage="assemble_image_pptx",
        )
    page_details: list[dict[str, Any]] = []
    for index, slide in enumerate(prs.slides, start=1):
        pictures = [shape for shape in slide.shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE]
        other_shapes = [shape for shape in slide.shapes if shape.shape_type != MSO_SHAPE_TYPE.PICTURE]
        if len(pictures) != 1 or other_shapes:
            raise ExportError(
                f"PPTX slide {index} must contain exactly one picture and no editable shapes.",
                stage="assemble_image_pptx",
            )
        picture = pictures[0]
        if picture.left != 0 or picture.top != 0 or picture.width != prs.slide_width or picture.height != prs.slide_height:
            raise ExportError(f"PPTX slide {index} picture is not full bleed.", stage="assemble_image_pptx")
        page_details.append({"index": index, "picture_count": 1, "full_bleed": True})
    return {"openable": True, "pages": page_details}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["ImagePptxExportResult", "ImagePptxExporter"]
