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
from core.deck.pipeline.review import validate_fact_sources
from core.deck.pipeline.slides import apply_design_contract
from core.deck.quality import issues_to_dicts, validate_slide_spec
from core.deck.rendering.artist import render_svg_artwork, visual_signature
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
    design_state = _load_design_state(request_dir)

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
        if not design_state.get("design_contract"):
            design_state["design_contract"] = {
                "spec_hash": slide.spec_hash,
                "pages": [
                    {
                        "index": slide.index,
                        "component": slide.component,
                        "layout_variant": slide.layout_variant,
                    }
                ],
            }
        rewritten = apply_design_contract(rewritten, design_state)
        deck.slides = [rewritten if item.index == index else item for item in deck.slides]
        slide = rewritten
        spec_issues = validate_slide_spec(slide)
        fact_ledger_path = request_dir / "fact-ledger.json"
        if fact_ledger_path.exists():
            try:
                facts = json.loads(fact_ledger_path.read_text(encoding="utf-8"))
                spec_issues.extend(validate_fact_sources(DeckSpec(title=deck.title, narrative=deck.narrative, audience=deck.audience, goal=deck.goal, language=deck.language, theme=deck.theme, slides=[slide]), {"facts": facts}))
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        previous_signatures = _previous_visual_signatures(request_dir, index)
        existing_svg_path = request_dir / "slides" / f"{index}.svg"
        existing_svg = existing_svg_path.read_text(encoding="utf-8") if existing_svg_path.is_file() else ""
        artwork = render_svg_artwork(
            llm=llm_registry.get("designer"),
            state=design_state,
            deck=deck,
            slide=slide,
            outline=_page_blueprint(design_state, index),
            previous_visual_signatures=previous_signatures,
            quality_feedback=[
                {
                    "page": index,
                    "kind": "user_feedback",
                    "description": feedback,
                    "suggestion": "Implement the feedback without echoing it as visible copy.",
                }
            ] if feedback else [],
            existing_svg=existing_svg,
        )
        spec_issues.extend(artwork.issues)
        artifact = browser.render_slide(
            request_id,
            slide,
            html_renderer.render(deck, slide),
            version=next_slide_version(request_dir, index),
            theme=deck.theme,
            svg=artwork.svg,
        )
        (request_dir / f"spec.raw.slide-{index}.v{artifact.version}.json").write_text(
            json.dumps(raw_slide, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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
                    "svg": artifact.svg_rel,
                    "version": artifact.version,
                    "status": "ready" if not (artifact.issues or spec_issues) else "warning",
                    "issues": issues_to_dicts(spec_issues + artifact.issues),
                    "svg_renderer": artwork.render_mode,
                    "template_id": artwork.template_id,
                    "layout_id": artwork.layout_id,
                    "page_rhythm": artwork.page_rhythm,
                    "artist_repair_count": artwork.repair_count,
                    "visual_signature": artwork.visual_signature,
                },
                ensure_ascii=False,
            ),
            sequence=2,
        )
        result = exporter.export(request_id, deck)
        (request_dir / "spec.final.json").write_text(json.dumps(deck.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        issue_dicts = issues_to_dicts(spec_issues + artifact.issues)
        must_fix = any(issue.get("severity") == "must_fix" for issue in issue_dicts)
        quality_status = "needs_review" if must_fix else ("success_with_warnings" if result.warnings or issue_dicts else "success")
        _update_regeneration_manifest(
            request_dir,
            slide=slide,
            artwork=artwork,
            artifact=artifact,
            export_manifest=result.manifest,
            issues=issue_dicts,
            quality_status=quality_status,
        )
        yield GenerateDeckOutput(
            request_id=request_id,
            event_type="artifact",
            stage="export",
            message="PPTX file ready",
            artifact_path=str(result.path),
            payload_json=json.dumps(
                {
                    "request_id": request_id,
                    "filename": result.path.name,
                    "path": str(result.path),
                    "relative_path": "deck.pptx",
                    "warnings": result.warnings,
                    "issues": issue_dicts,
                    "needs_review": must_fix,
                    "quality_status": quality_status,
                    "degraded": bool(result.manifest.get("degraded")),
                    "export_manifest": "export-manifest.json",
                },
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
        "task": "Rewrite exactly one authoritative SlideSpec using the user feedback.",
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


def _load_design_state(request_dir: Path) -> dict[str, Any]:
    mapping = {
        "design_spec": "design-spec.json",
        "execution_lock": "spec-lock.json",
        "asset_manifest": "asset-manifest.json",
    }
    state: dict[str, Any] = {}
    for key, filename in mapping.items():
        path = request_dir / filename
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            state[key] = value
    blueprint_path = request_dir / "page-blueprints.json"
    if blueprint_path.is_file():
        try:
            payload = json.loads(blueprint_path.read_text(encoding="utf-8"))
            state["page_blueprints"] = payload.get("pages") if isinstance(payload, dict) else []
        except (OSError, json.JSONDecodeError):
            pass
    design_spec = state.get("design_spec") if isinstance(state.get("design_spec"), dict) else {}
    state["design_contract"] = design_spec
    template = design_spec.get("template") if isinstance(design_spec.get("template"), dict) else {}
    state["template_id"] = str(template.get("template_id") or "editorial_freeform")
    return state


def _page_blueprint(state: dict[str, Any], index: int) -> dict[str, Any]:
    for item in state.get("page_blueprints") or []:
        if isinstance(item, dict) and int(item.get("index") or 0) == index:
            return item
    return {}


def _previous_visual_signatures(request_dir: Path, index: int) -> list[dict[str, Any]]:
    signatures: list[dict[str, Any]] = []
    for page in range(max(1, index - 2), index):
        path = request_dir / "slides" / f"{page}.svg"
        try:
            svg = path.read_text(encoding="utf-8")
        except OSError:
            continue
        signatures.append(visual_signature(svg))
    return signatures


def _update_regeneration_manifest(
    request_dir: Path,
    *,
    slide: SlideSpec,
    artwork: Any,
    artifact: Any,
    export_manifest: dict[str, Any],
    issues: list[dict[str, Any]],
    quality_status: str,
) -> None:
    path = request_dir / "generation-manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    pages = [item for item in manifest.get("pages") or [] if isinstance(item, dict) and int(item.get("index") or 0) != slide.index]
    exported = next(
        (item for item in export_manifest.get("slides") or [] if isinstance(item, dict) and int(item.get("index") or 0) == slide.index),
        {},
    )
    pages.append(
        {
            "index": slide.index,
            "title": slide.title,
            "role": slide.role,
            "component": slide.component,
            "template_id": artwork.template_id,
            "layout_id": artwork.layout_id,
            "page_rhythm": artwork.page_rhythm,
            "render_mode": artwork.render_mode,
            "svg": artifact.svg_rel,
            "preview": artifact.png_rel,
            "qa_issues": issues,
            "export": exported,
            "regenerated": True,
            "version": artifact.version,
        }
    )
    manifest["pages"] = sorted(pages, key=lambda item: int(item.get("index") or 0))
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    quality.update({"status": quality_status, "last_regenerated_page": slide.index})
    manifest["quality"] = quality
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
