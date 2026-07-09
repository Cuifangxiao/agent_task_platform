"""Visual review helpers for rendered slide previews."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from core.deck.quality.issues import ValidationIssue

ALLOWED_VLM_KINDS = {"overflow", "crowding", "alignment", "hierarchy", "style_drift", "mismatch", "polish"}


def build_contact_sheet(slide_paths: list[Path], output_path: Path, *, columns: int = 3) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    thumbs: list[Image.Image] = []
    for path in slide_paths:
        try:
            image = Image.open(path).convert("RGB")
        except OSError:
            continue
        image.thumbnail((420, 236))
        thumbs.append(image.copy())
    if not thumbs:
        Image.new("RGB", (420, 236), (247, 243, 234)).save(output_path)
        return output_path
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 440, rows * 270), (247, 243, 234))
    draw = ImageDraw.Draw(sheet)
    for index, image in enumerate(thumbs):
        x = (index % columns) * 440 + 10
        y = (index // columns) * 270 + 24
        sheet.paste(image, (x, y))
        draw.text((x, y - 18), f"Slide {index + 1}", fill=(20, 33, 61))
    sheet.save(output_path)
    return output_path


def parse_vlm_issues(raw: Any) -> list[ValidationIssue]:
    data = _coerce_json(raw)
    if isinstance(data, dict):
        data = data.get("issues") or []
    if not isinstance(data, list):
        return []
    issues: list[ValidationIssue] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in ALLOWED_VLM_KINDS:
            continue
        try:
            page = int(item.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        if page <= 0:
            continue
        severity = str(item.get("severity") or "nice_to_have").strip()
        if severity not in {"must_fix", "nice_to_have"}:
            severity = "nice_to_have"
        issues.append(
            ValidationIssue(
                page=page,
                severity=severity,
                kind=f"vlm_{kind}",
                description=str(item.get("description") or kind).strip(),
                suggestion=str(item.get("suggestion") or "").strip(),
            )
        )
    return issues


def review_prompt(contact_sheet_path: Path) -> str:
    return "\n".join(
        [
            "Review the slide contact sheet for visual quality.",
            f"Contact sheet path: {contact_sheet_path}",
            "Return JSON: {\"issues\":[{\"page\":1,\"severity\":\"must_fix|nice_to_have\",\"kind\":\"overflow|crowding|alignment|hierarchy|style_drift|mismatch|polish\",\"description\":\"...\",\"suggestion\":\"...\"}]}",
            "Only use kinds from the enum. Use must_fix only for defects likely to hurt comprehension.",
        ]
    )


def _coerce_json(raw: Any) -> Any:
    content = getattr(raw, "content", raw)
    if not isinstance(content, str):
        return content
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = min([pos for pos in (cleaned.find("{"), cleaned.find("[")) if pos >= 0], default=-1)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None
