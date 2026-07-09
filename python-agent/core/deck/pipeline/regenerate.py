"""Post-export single-slide regeneration flow."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from core.deck.contract import GenerateDeckOutput, ResumeDeckInput
from core.deck.export import DeckExporter
from core.deck.model_config import build_deck_llms
from core.deck.paths import artifact_dir
from core.deck.pipeline.llm import invoke_json_with_retries, llm_for_node, load_node_prompt, normalize_llm_registry
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.browser import BrowserRenderer, BrowserSession
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.spec import DeckSpec, SlideSpec, normalize_slide_spec

PAYLOAD_JSON_MAX_BYTES = 64_000


def regenerate_slide_from_artifacts(request_id: str, inp: ResumeDeckInput) -> Iterator[GenerateDeckOutput]:
    payload = _decode_payload_json(inp.payload_json)
    try:
        index = int(payload.get("index") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("payload_json.index is required for regenerate_slide") from exc
    feedback = str(payload.get("feedback") or "").strip()
    if index <= 0:
        raise ValueError("payload_json.index is required for regenerate_slide")

    try:
        llm_registry = normalize_llm_registry(build_deck_llms(inp))
        writer_llm = llm_for_node(llm_registry, "writer", aliases=("slides",))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="error",
            stage="repair",
            message="Regenerating a slide requires a writer model configuration.",
            done=True,
            error=str(exc),
            sequence=1,
        )
        return

    output_root = artifact_dir()
    request_dir = output_root / request_id
    spec_path = request_dir / "spec.json"
    if not spec_path.exists():
        raise ValueError("deck spec artifact is missing; regenerate_slide requires an exported deck")
    deck = DeckSpec.from_dict(json.loads(spec_path.read_text(encoding="utf-8")))
    slide = next((item for item in deck.slides if item.index == index), None)
    if slide is None:
        raise ValueError(f"slide {index} not found in deck spec")

    browser_session = BrowserSession()
    try:
        html_renderer = HTMLSlideRenderer()
        browser = BrowserRenderer(output_root, browser_session)
        exporter = DeckExporter(output_root, browser_session)

        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="repair_progress",
            stage="repair",
            message=f"Regenerating slide {index}",
            payload_json=json.dumps({"index": index, "round": 1, "kind": "user_feedback"}, ensure_ascii=False),
            sequence=1,
        )
        raw_slide = invoke_json_with_retries(
            writer_llm,
            system=load_node_prompt("repair"),
            user=regenerate_slide_prompt(deck, slide, feedback),
            attempts=3,
        )
        rewritten = normalize_slide_spec(
            raw_slide,
            index=index,
            outline={"title": slide.title, "role": slide.role, "component": slide.component},
            deck_title=deck.title,
            narrative=deck.narrative,
        )
        rewritten = remove_feedback_echoes(rewritten, feedback, fallback=slide)
        deck.slides = [rewritten if item.index == index else item for item in deck.slides]
        slide = rewritten
        spec_issues = validate_slide_spec(slide)
        artifact = browser.render_slide(request_id, slide, html_renderer.render(deck, slide), version=next_slide_version(request_dir, index), theme=deck.theme)
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="slide_preview",
            stage="design_slides",
            message=f"Slide {index} preview updated",
            payload_json=json.dumps(
                {
                    "index": index,
                    "png": artifact.png_rel,
                    "html": artifact.html_rel,
                    "thumb_b64": artifact.thumb_b64,
                    "version": artifact.version,
                    "status": "ready" if not (artifact.issues or spec_issues) else "warning",
                    "issues": issues_to_dicts(spec_issues + artifact.issues),
                },
                ensure_ascii=False,
            ),
            sequence=2,
        )
        result = exporter.export(request_id, deck)
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="artifact",
            stage="export",
            message="PPTX file ready",
            artifact_path=str(result.path),
            payload_json=json.dumps(
                {"request_id": request_id, "filename": result.path.name, "path": str(result.path), "relative_path": "deck.pptx", "warnings": result.warnings},
                ensure_ascii=False,
            ),
            sequence=3,
        )
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="done",
            stage="done",
            message="Deck regeneration complete",
            done=True,
            sequence=4,
        )
    finally:
        browser_session.close()


def next_slide_version(request_dir: Path, index: int) -> int:
    slides_dir = request_dir / "slides"
    version = 1
    for path in slides_dir.glob(f"{index}_v*.html"):
        match = re.search(r"_v(\d+)\.html$", path.name)
        if match:
            version = max(version, int(match.group(1)) + 1)
    return max(2, version)


def regenerate_slide_prompt(deck: DeckSpec, slide: SlideSpec, feedback: str) -> str:
    payload = {
        "task": "Rewrite exactly one SlideSpec v2 using the user feedback.",
        "rules": [
            "Return only one JSON object for the replacement slide.",
            "Keep the same slide index.",
            "Use the feedback as editing guidance only; do not copy the feedback sentence into visible content.",
            "Preserve source-backed facts and source ids when still relevant.",
        ],
        "deck": {
            "title": deck.title,
            "narrative": deck.narrative,
            "audience": deck.audience,
            "goal": deck.goal,
            "language": deck.language,
        },
        "original_slide": slide.to_dict(),
        "user_feedback": feedback,
        "component_catalog": _component_catalog_text(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def remove_feedback_echoes(slide: SlideSpec, feedback: str, *, fallback: SlideSpec) -> SlideSpec:
    marker = feedback.strip()
    if not marker:
        return slide

    def clean(value: Any) -> Any:
        if isinstance(value, str):
            return value.replace(marker, "").strip(" \n\t-—:：")
        if isinstance(value, list):
            return [clean(item) for item in value]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        return value

    slide.title = str(clean(slide.title) or fallback.title)
    cleaned_content = clean(slide.content)
    slide.content = cleaned_content if isinstance(cleaned_content, dict) else dict(fallback.content)
    slide.notes = str(clean(slide.notes) or "")
    if slide.chart:
        cleaned_chart = clean(slide.chart)
        slide.chart = cleaned_chart if isinstance(cleaned_chart, dict) else None
    return slide


def _decode_payload_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if len(raw.encode("utf-8")) > PAYLOAD_JSON_MAX_BYTES:
        raise ValueError("payload_json exceeds 64KB")
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("payload_json must be a JSON object")
    return decoded


def _component_catalog_text() -> str:
    path = Path(__file__).resolve().parent.parent / "design_system" / "components.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
