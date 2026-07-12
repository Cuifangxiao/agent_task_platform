"""Strategist art-direction generation and user selection."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

import core.deck.events as events
from core.deck.design.catalog import TemplatePack, get_template_pack, list_template_packs, public_template_catalog
from core.deck.pipeline.helpers import emit_agent
from core.deck.pipeline.llm import invoke_json_with_retries, llm_log_context, load_node_prompt
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import validate_theme_contrast
from core.deck.rendering.artist import render_svg_artwork
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer
from core.deck.rendering.svg import svg_preview_html
from core.deck.runtime_metrics import record_fallback
from core.deck.spec import DeckSpec, SlideSpec, ThemeTokens


logger = logging.getLogger(__name__)


def prepare_style_options(
    state: DeckGraphState,
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
    llm_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "style", "Strategist / Art Director", "生成三套包含八项确认、模板策略和视觉系统的可执行方向。")
    writer(events.stage_started("style", "Building complete art-direction options"))
    options = build_style_options(state, html_renderer, browser, llm_registry)
    writer(events.style_options({"options": public_style_options(options), "review_type": "art_direction"}))
    return {"style_options": options}


def review_style(state: DeckGraphState, llm_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    writer = get_stream_writer()
    options = state.get("style_options") or []
    if str(state.get("generation_mode") or "") == "quick":
        selected, _matched = select_style_option(options, "")
        theme = ThemeTokens.from_dict(selected.get("tokens") or {})
        writer(events.stage_progress("style_review", f"Quick mode selected art direction: {selected.get('name')}", {"status": "done"}))
        return _selected_style_state(selected, theme, feedback="")

    writer(events.stage_progress("style_review", "Waiting for art-direction confirmation"))
    decision = interrupt(
        {
            "stage": "style_review",
            "review_type": "art_direction",
            "style_options": public_style_options(options),
            "allowed_actions": ["choose_style"],
        }
    )
    style_id = ""
    feedback = ""
    if isinstance(decision, dict):
        style_id = str(decision.get("style_id") or decision.get("styleId") or "").strip()
        feedback = str(decision.get("feedback") or "").strip()
        payload = decision.get("payload")
        if isinstance(payload, dict):
            style_id = style_id or str(payload.get("style_id") or payload.get("styleId") or "").strip()
            feedback = feedback or str(payload.get("feedback") or "").strip()

    selected, matched = select_style_option(options, style_id)
    if style_id and not matched:
        writer(events.stage_progress("style_review", f"Unknown style_id '{style_id}', selected the recommended direction.", {"status": "warning", "style_id": style_id}))
    theme = ThemeTokens.from_dict(selected.get("tokens") or {})
    if feedback and llm_registry and llm_registry.get("style") is not None:
        try:
            adjusted = adjust_theme_from_feedback(theme, feedback, llm_registry["style"])
            if theme_is_valid(adjusted):
                theme = adjusted
            else:
                record_fallback()
                writer(events.stage_progress("style_review", "Feedback produced low-contrast tokens; kept the selected palette.", {"status": "warning"}))
        except ValueError as exc:
            record_fallback()
            writer(events.stage_progress("style_review", f"Could not apply token feedback; kept the selected direction: {exc}", {"status": "warning"}))
    elif feedback:
        theme.motif = (theme.motif + " | " + feedback).strip(" |")

    writer(
        events.stage_progress(
            "style_review",
            f"Selected art direction: {selected.get('name')}",
            {
                "style_id": selected.get("style_id"),
                "name": selected.get("name"),
                "template_id": selected.get("template_id"),
                "template_mode": selected.get("template_mode"),
                "feedback": feedback,
            },
        )
    )
    return _selected_style_state(selected, theme, feedback=feedback)


def build_style_options(
    state: DeckGraphState,
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
    llm_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    request_id = str(state.get("request_id") or "")
    started_at = time.perf_counter()
    packs = _candidate_packs(state)
    raw_directions: list[dict[str, Any]] = []
    if llm_registry and llm_registry.get("style") is not None:
        with llm_log_context(request_id=request_id, stage="style", operation="style_directions"):
            raw_directions = _model_directions(state, llm_registry["style"])

    normalized: list[dict[str, Any]] = []
    for raw in raw_directions:
        option = _normalize_direction(raw, state)
        if option and option["template_id"] not in {item["template_id"] for item in normalized}:
            normalized.append(option)
    for pack in packs:
        if pack.template_id in {item["template_id"] for item in normalized}:
            continue
        normalized.append(_direction_from_pack(pack, state))
    option_limit = 1 if str(state.get("generation_mode") or "") == "quick" else 3
    normalized = normalized[:option_limit]

    title = str((state.get("plan") or {}).get("title") or state.get("deck_title") or "Untitled presentation")
    narrative = str((state.get("plan") or {}).get("narrative") or (state.get("brief") or {}).get("goal") or "")
    options: list[dict[str, Any]] = []
    preview_metadata = llm_registry.get("__metadata__") if isinstance(llm_registry, dict) else None
    preview_designer = (
        llm_registry.get("designer")
        if option_limit > 1
        and isinstance(preview_metadata, dict)
        and isinstance(preview_metadata.get("designer"), dict)
        else None
    )
    logger.info(
        "event=ppt_style_options_start request_id=%s option_count=%s has_preview_model=%s",
        request_id,
        len(normalized),
        preview_designer is not None,
    )
    for index, direction in enumerate(normalized, start=1):
        option_started_at = time.perf_counter()
        theme = ThemeTokens.from_dict(direction["tokens"])
        style_id = f"direction_{index}"
        logger.info(
            "event=ppt_style_option_start request_id=%s style_id=%s option_index=%s option_count=%s template_id=%s",
            request_id,
            style_id,
            index,
            len(normalized),
            direction.get("template_id", ""),
        )
        with llm_log_context(request_id=request_id, stage="style", operation=f"style_preview_{index}"):
            html, sample_render_mode = _style_sample_html(
                request_id=request_id,
                title=title,
                narrative=narrative,
                theme=theme,
                direction=direction,
                designer_llm=preview_designer,
                html_renderer=html_renderer,
            )
        capture_started_at = time.perf_counter()
        artifact = browser.render_style_sample(str(state["request_id"]), style_id, html, theme=theme)
        capture_duration_ms = round((time.perf_counter() - capture_started_at) * 1000)
        options.append(
            {
                **direction,
                "style_id": style_id,
                "cover_png": artifact["png"],
                "cover_html": artifact["html"],
                "thumb_b64": artifact.get("thumb_b64", ""),
                "tokens_summary": tokens_summary(theme),
                "sample_render_mode": sample_render_mode,
            }
        )
        logger.info(
            "event=ppt_style_option_end request_id=%s style_id=%s option_index=%s duration_ms=%s "
            "capture_duration_ms=%s render_mode=%s",
            request_id,
            style_id,
            index,
            round((time.perf_counter() - option_started_at) * 1000),
            capture_duration_ms,
            sample_render_mode,
        )
    logger.info(
        "event=ppt_style_options_end request_id=%s option_count=%s duration_ms=%s",
        request_id,
        len(options),
        round((time.perf_counter() - started_at) * 1000),
    )
    return options


def _style_sample_html(
    *,
    request_id: str = "",
    title: str,
    narrative: str,
    theme: ThemeTokens,
    direction: dict[str, Any],
    designer_llm: Any | None,
    html_renderer: HTMLSlideRenderer,
) -> tuple[str, str]:
    if designer_llm is None:
        return html_renderer.render_cover_sample(title, narrative, theme), "deterministic_preview"

    template_id = str(direction.get("template_id") or "editorial_freeform")
    sample_slide = SlideSpec(
        index=1,
        role="hook",
        component="cover",
        title=title[:72] or "Presentation",
        content={"eyebrow": "ART DIRECTION", "subtitle": narrative[:160]},
        spec_hash=f"style-preview-{template_id}",
    )
    sample_deck = DeckSpec(
        title=sample_slide.title,
        narrative=narrative[:200],
        audience="style review",
        goal="compare visual directions",
        language="zh",
        theme=theme,
        slides=[sample_slide],
    )
    artwork = render_svg_artwork(
        llm=designer_llm,
        state={
            "request_id": request_id,
            "template_id": template_id,
            "art_direction": direction,
            "design_contract": {
                "spec_hash": sample_slide.spec_hash,
                "template": {
                    "template_id": template_id,
                    "template_mode": direction.get("template_mode"),
                },
                "pages": [
                    {
                        "index": 1,
                        "component": "cover",
                        "page_rhythm": "anchor",
                        "template_id": template_id,
                    }
                ],
            },
        },
        deck=sample_deck,
        slide=sample_slide,
        outline={"index": 1, "title": sample_slide.title, "role": "hook", "component": "cover"},
        max_repairs=1,
    )
    return svg_preview_html(artwork.svg), artwork.render_mode


def _model_directions(state: DeckGraphState, style_llm: Any) -> list[dict[str, Any]]:
    payload = {
        "task": "Create three executable presentation art directions and eight-confirmation bundles.",
        "brief": state.get("brief") or {},
        "outline": state.get("plan") or {},
        "outline_markdown": state.get("outline_markdown") or "",
        "template_preference": state.get("template_id") or "auto",
        "image_strategy_preference": state.get("image_strategy") or "auto",
        "available_assets": state.get("source_images") or [],
        "template_catalog": public_template_catalog(),
    }
    try:
        raw = invoke_json_with_retries(style_llm, system=load_node_prompt("style"), user=json.dumps(payload, ensure_ascii=False), attempts=2)
    except ValueError:
        record_fallback()
        return []
    return [item for item in raw.get("directions") or raw.get("options") or [] if isinstance(item, dict)]


def _normalize_direction(raw: dict[str, Any], state: DeckGraphState) -> dict[str, Any] | None:
    valid_ids = {pack.template_id for pack in list_template_packs()}
    requested = str(state.get("template_id") or "auto").strip()
    template_id = requested if requested not in {"", "auto"} and requested in valid_ids else str(raw.get("template_id") or "").strip()
    if template_id not in valid_ids:
        return None
    pack = get_template_pack(template_id)
    theme = ThemeTokens.from_dict(raw.get("theme") or pack.theme.to_dict())
    if not theme_is_valid(theme):
        theme = pack.theme
    confirmations = _confirmations(pack, state)
    if isinstance(raw.get("confirmations"), dict):
        for key in confirmations:
            value = str(raw["confirmations"].get(key) or "").strip()
            if value:
                confirmations[key] = value
    principles = [str(item).strip() for item in raw.get("layout_principles") or [] if str(item).strip()] or pack.layout_principles
    return {
        "name": str(raw.get("name") or pack.name).strip(),
        "communication_mode": str(raw.get("communication_mode") or pack.communication_mode).strip(),
        "visual_style": str(raw.get("visual_style") or pack.visual_style).strip(),
        "template_id": pack.template_id,
        "template_mode": pack.template_mode,
        "template_summary": pack.summary,
        "rationale": str(raw.get("rationale") or pack.summary).strip(),
        "confirmations": confirmations,
        "layout_principles": principles,
        "image_strategy": str(raw.get("image_strategy") or pack.image_strategy).strip(),
        "icon_strategy": pack.icon_strategy,
        "tokens": theme.to_dict(),
    }


def _direction_from_pack(pack: TemplatePack, state: DeckGraphState) -> dict[str, Any]:
    return {
        "name": pack.name,
        "communication_mode": pack.communication_mode,
        "visual_style": pack.visual_style,
        "template_id": pack.template_id,
        "template_mode": pack.template_mode,
        "template_summary": pack.summary,
        "rationale": pack.summary,
        "confirmations": _confirmations(pack, state),
        "layout_principles": pack.layout_principles,
        "image_strategy": pack.image_strategy,
        "icon_strategy": pack.icon_strategy,
        "tokens": pack.theme.to_dict(),
    }


def _confirmations(pack: TemplatePack, state: DeckGraphState) -> dict[str, str]:
    brief = state.get("brief") if isinstance(state.get("brief"), dict) else {}
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    palette = pack.theme.palette
    source_images = state.get("source_images") or []
    image_text = f"使用 {len(source_images)} 张已上传图片，严格按证据或主视觉用途分配" if source_images else f"{pack.image_strategy}；无已上传图片时不虚构照片素材"
    return {
        "canvas": "PPT 16:9 / 1280×720",
        "page_count": f"{len(plan.get('slides') or [])} 页；按已确认叙事逐页执行",
        "audience": f"{brief.get('audience') or '通用受众'}；目标：{brief.get('goal') or '清晰传达'}",
        "style": f"{pack.communication_mode} + {pack.visual_style}",
        "colors": f"背景 {palette.get('bg')} / 主色 {palette.get('accent')} / 强调 {palette.get('accent2')}，遵循 60-30-10",
        "icons": f"{pack.icon_strategy.get('library', 'geometric')} 单一图标语言，图标只表达信息关系",
        "typography": f"标题 {pack.theme.fonts.get('display')}，正文 {pack.theme.fonts.get('body')}，正文基线 {pack.theme.type_scale.get('body')}px",
        "images": image_text,
    }


def _candidate_packs(state: DeckGraphState) -> list[TemplatePack]:
    packs = list(list_template_packs())
    requested = str(state.get("template_id") or "auto").strip()
    if requested not in {"", "auto"}:
        selected = get_template_pack(requested)
        return [selected, *[pack for pack in packs if pack.template_id != selected.template_id]][:3]
    haystack = " ".join(
        [
            str(state.get("user_description") or ""),
            str((state.get("brief") or {}).get("audience") or ""),
            str((state.get("brief") or {}).get("goal") or ""),
        ]
    ).casefold()
    preferred = "editorial_freeform"
    if any(token in haystack for token in ("董事", "战略", "经营", "汇报", "board", "executive", "finance")):
        preferred = "consulting_signal"
    elif any(token in haystack for token in ("架构", "技术", "ai", "software", "platform", "系统")):
        preferred = "technical_blueprint"
    elif any(token in haystack for token in ("答辩", "论文", "研究", "academic", "thesis")):
        preferred = "academic_defense"
    elif any(token in haystack for token in ("发布", "路演", "产品", "launch", "pitch", "keynote")):
        preferred = "keynote_canvas"
    selected = get_template_pack(preferred)
    remainder = [pack for pack in packs if pack.template_id != selected.template_id]
    diversity_order = ["editorial_freeform", "consulting_signal", "keynote_canvas", "technical_blueprint", "academic_defense"]
    remainder.sort(key=lambda pack: diversity_order.index(pack.template_id) if pack.template_id in diversity_order else 99)
    return [selected, *remainder][:3]


def adjust_theme_from_feedback(theme: ThemeTokens, feedback: str, style_llm: Any) -> ThemeTokens:
    raw = invoke_json_with_retries(
        style_llm,
        system=load_node_prompt("style"),
        user=json.dumps(
            {
                "task": "Adjust only the ThemeTokens object from feedback; keep accessibility and PPT-safe fonts.",
                "theme": theme.to_dict(),
                "feedback": feedback,
            },
            ensure_ascii=False,
        ),
        attempts=2,
    )
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else raw.get("theme") if isinstance(raw.get("theme"), dict) else raw
    return ThemeTokens.from_dict(tokens if isinstance(tokens, dict) else theme.to_dict())


def theme_is_valid(theme: ThemeTokens) -> bool:
    deck = DeckSpec(title="", narrative="", audience="", goal="", language="zh", theme=theme, slides=[])
    return not any(issue.severity == "must_fix" for issue in validate_theme_contrast(deck))


def public_style_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = (
        "style_id",
        "name",
        "communication_mode",
        "visual_style",
        "template_id",
        "template_mode",
        "template_summary",
        "rationale",
        "confirmations",
        "layout_principles",
        "image_strategy",
        "icon_strategy",
        "cover_png",
        "cover_html",
        "thumb_b64",
        "tokens_summary",
        "sample_render_mode",
    )
    return [{key: item.get(key) for key in keys} for item in options]


def select_style_option(options: list[dict[str, Any]], style_id: str) -> tuple[dict[str, Any], bool]:
    for option in options:
        if str(option.get("style_id") or "") == style_id:
            return option, True
    if options:
        return options[0], False
    pack = get_template_pack("editorial_freeform")
    fallback = _direction_from_pack(pack, {}) | {"style_id": "direction_1"}
    return fallback, False


def _selected_style_state(selected: dict[str, Any], theme: ThemeTokens, *, feedback: str) -> dict[str, Any]:
    art_direction = {
        key: selected.get(key)
        for key in (
            "style_id",
            "name",
            "communication_mode",
            "visual_style",
            "template_id",
            "template_mode",
            "template_summary",
            "rationale",
            "confirmations",
            "layout_principles",
            "image_strategy",
            "icon_strategy",
        )
    }
    art_direction["feedback"] = feedback
    return {
        "theme": theme.to_dict(),
        "art_direction": art_direction,
        "template_id": str(selected.get("template_id") or "editorial_freeform"),
    }


def tokens_summary(theme: ThemeTokens) -> str:
    palette = theme.palette
    return f"{theme.name}: bg {palette.get('bg')}, ink {palette.get('ink')}, accent {palette.get('accent')} / {palette.get('accent2')}; {theme.motif}"


__all__ = [
    "adjust_theme_from_feedback",
    "build_style_options",
    "prepare_style_options",
    "public_style_options",
    "review_style",
    "select_style_option",
    "theme_is_valid",
    "tokens_summary",
]
