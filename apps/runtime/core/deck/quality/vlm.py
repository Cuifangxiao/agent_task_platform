"""Visual review helpers for rendered slide previews."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from core.deck.quality.issues import ValidationIssue

ALLOWED_VLM_KINDS = {
    "overflow",
    "crowding",
    "alignment",
    "hierarchy",
    "contrast",
    "repetition",
    "template_drift",
    "style_drift",
    "data_legibility",
    "asset_quality",
    "mismatch",
    "polish",
}


def build_contact_sheet(
    slide_paths: list[Path],
    output_path: Path,
    *,
    columns: int = 3,
    start_index: int = 1,
) -> Path:
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
        draw.text((x, y - 18), f"Slide {start_index + index}", fill=(20, 33, 61))
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


def review_prompt(design_context: dict[str, Any] | None = None) -> str:
    context = json.dumps(design_context or {}, ensure_ascii=False, separators=(",", ":"))
    return "\n".join(
        [
            "Act as a demanding presentation Art Director performing visual QA on the rendered contact sheet.",
            "Judge both individual pages and the deck as one visual sequence. Inspect at least these dimensions:",
            "1) text overflow, clipping and readability at presentation distance;",
            "2) whether the key message is visually dominant within three seconds;",
            "3) composition, balance, negative space, grid and alignment;",
            "4) contrast, color meaning, chart/data legibility and asset resolution;",
            "5) fidelity to the locked template and art direction;",
            "6) excessive repetition of the same card grid, split, motif or visual weight;",
            "7) mismatch between semantic content and chosen visual form;",
            "8) final polish such as awkward gaps, accidental tangencies and weak grouping.",
            "Do not reward a page merely for being clean. A technically valid but generic, monotonous or template-drifting page is an issue.",
            "Use must_fix for problems that harm comprehension, look visibly broken, violate the locked template, or repeat so strongly that the deck feels auto-generated.",
            "Use nice_to_have for meaningful polish improvements only; do not invent issues without visible evidence.",
            "Return exactly one JSON object and no Markdown:",
            '{"issues":[{"page":1,"severity":"must_fix|nice_to_have","kind":"overflow|crowding|alignment|hierarchy|contrast|repetition|template_drift|style_drift|data_legibility|asset_quality|mismatch|polish","description":"visible evidence","suggestion":"specific composition-level repair"}]}',
            "Every issue must name the exact slide number shown above its thumbnail and describe a concrete visible defect.",
            f"Locked design context: {context}",
        ]
    )


def review_message_content(
    contact_sheet_path: Path,
    *,
    design_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    image_b64 = base64.b64encode(contact_sheet_path.read_bytes()).decode("ascii")
    return [
        {"type": "text", "text": review_prompt(design_context)},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
    ]


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
