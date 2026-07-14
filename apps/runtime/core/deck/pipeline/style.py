"""Choose one recommended art direction automatically."""

from __future__ import annotations

import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.design.catalog import TemplatePack, get_template_pack, list_template_packs, template_preview_path
from core.deck.paths import DeckProjectPaths
from core.deck.pipeline.helpers import emit_agent
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import validate_theme_contrast
from core.deck.spec import DeckSpec, ThemeTokens


logger = logging.getLogger(__name__)
_SAFE_TEMPLATE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def prepare_style_options(
    state: DeckGraphState,
    output_root: Path,
) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "style", "Template Curator", "从静态模板目录选择首个推荐视觉方向。")
    writer(events.stage_started("style", "Selecting the recommended style"))
    options = build_style_options(state, output_root)
    selected, _ = select_style_option(options, "")
    theme = ThemeTokens.from_dict(selected.get("tokens") or {})
    public_options = public_style_options([selected])
    writer(events.style_options({"options": public_options}))
    return {"style_options": options, **_selected_style_state(selected, theme)}


def build_style_options(
    state: DeckGraphState,
    output_root: Path,
) -> list[dict[str, Any]]:
    request_id = str(state.get("request_id") or "")
    started_at = time.perf_counter()
    packs = _candidate_packs(state)
    selected_packs = packs[:1]
    options: list[dict[str, Any]] = []
    logger.info(
        "event=ppt_style_options_start request_id=%s option_count=%s preview_mode=static_template_cover",
        request_id,
        len(selected_packs),
    )
    for index, pack in enumerate(selected_packs, start=1):
        option_started_at = time.perf_counter()
        direction = _direction_from_pack(pack, state)
        theme = ThemeTokens.from_dict(direction["tokens"])
        style_id = f"direction_{index}"
        logger.info(
            "event=ppt_style_option_start request_id=%s style_id=%s option_index=%s option_count=%s template_id=%s",
            request_id,
            style_id,
            index,
            len(selected_packs),
            direction.get("template_id", ""),
        )
        cover_path = _materialize_template_cover(output_root, request_id, style_id, pack)
        options.append(
            {
                **direction,
                "style_id": style_id,
                "cover_png": cover_path,
                "cover_html": "",
                "thumb_b64": "",
                "tokens_summary": tokens_summary(theme),
                "sample_render_mode": "static_template_cover",
                "preview_note": "模板封面示意；生成阶段会根据内容自动适配。",
            }
        )
        logger.info(
            "event=ppt_style_option_end request_id=%s style_id=%s option_index=%s duration_ms=%s render_mode=%s",
            request_id,
            style_id,
            index,
            round((time.perf_counter() - option_started_at) * 1000),
            "static_template_cover",
        )
    logger.info(
        "event=ppt_style_options_end request_id=%s option_count=%s duration_ms=%s",
        request_id,
        len(options),
        round((time.perf_counter() - started_at) * 1000),
    )
    return options


def _materialize_template_cover(output_root: Path, request_id: str, style_id: str, pack: TemplatePack) -> str:
    if not _SAFE_TEMPLATE_ID_RE.fullmatch(pack.template_id):
        raise ValueError(f"unsafe template id in catalog: {pack.template_id}")
    source = template_preview_path(pack)
    project = DeckProjectPaths.for_request(output_root, request_id).ensure()
    destination_dir = project.templates / "catalog" / pack.template_id
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{style_id}{source.suffix.casefold()}"
    shutil.copyfile(source, destination)
    return project.relative(destination)


def _direction_from_pack(pack: TemplatePack, state: DeckGraphState) -> dict[str, Any]:
    return {
        "name": pack.name,
        "communication_mode": pack.communication_mode,
        "visual_style": pack.visual_style,
        "template_id": pack.template_id,
        "template_mode": pack.template_mode,
        "template_summary": pack.summary,
        "rationale": pack.summary,
        "execution_assumptions": _execution_assumptions(pack, state),
        "layout_principles": pack.layout_principles,
        "image_strategy": pack.image_strategy,
        "icon_strategy": pack.icon_strategy,
        "template_source": dict(pack.source),
        "tokens": pack.theme.to_dict(),
    }


def _execution_assumptions(pack: TemplatePack, state: DeckGraphState) -> dict[str, str]:
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
    by_id = {pack.template_id: pack for pack in packs}
    requested = str(state.get("template_id") or "auto").strip()
    haystack = " ".join(
        [
            str(state.get("user_description") or ""),
            str((state.get("brief") or {}).get("audience") or ""),
            str((state.get("brief") or {}).get("goal") or ""),
            str((state.get("plan") or {}).get("title") or ""),
            str((state.get("plan") or {}).get("narrative") or ""),
            str(state.get("outline_markdown") or ""),
        ]
    ).casefold()
    diversity_order = [
        "editorial_freeform",
        "consulting_signal",
        "keynote_canvas",
        "technical_blueprint",
        "psychology_attachment",
        "pixel_retro",
        "academic_defense",
        "ai_ops",
        "medical_university",
        "government_blue",
        "government_red",
    ]
    order_index = {template_id: index for index, template_id in enumerate(diversity_order)}

    preferred = _preferred_template_id(haystack)
    requested_id = requested if requested not in {"", "auto"} and requested in by_id else ""

    def score(pack: TemplatePack) -> tuple[int, int, str]:
        keyword_hits = sum(1 for keyword in pack.keywords if keyword.casefold() in haystack)
        priority = keyword_hits * 20
        if pack.template_id == preferred:
            priority += 200
        if pack.template_id == requested_id:
            priority += 10_000
        return (-priority, order_index.get(pack.template_id, 999), pack.template_id)

    return sorted(packs, key=score)


def _preferred_template_id(haystack: str) -> str:
    mappings = (
        ("medical_university", ("医疗", "医院", "医学", "病例", "健康", "medical", "health", "clinical")),
        ("psychology_attachment", ("心理", "咨询", "治疗", "依恋", "psychology", "counseling")),
        ("pixel_retro", ("像素", "复古", "游戏", "极客", "pixel", "retro", "gaming")),
        ("government_red", ("政务红", "党建", "public affairs")),
        ("government_blue", ("政务", "政府", "政策", "城市治理", "基建", "civic", "policy")),
        ("ai_ops", ("运维", "运营商", "通信", "电信", "ai ops", "telecom", "operations")),
        ("academic_defense", ("答辩", "论文", "课题", "学术", "academic", "thesis")),
        ("consulting_signal", ("董事", "战略", "经营", "财务", "board", "executive", "finance")),
        ("technical_blueprint", ("架构", "技术", "软件", "平台", "系统", "ai", "software", "architecture")),
        ("keynote_canvas", ("发布", "路演", "产品", "演讲", "launch", "pitch", "keynote")),
    )
    return next((template_id for template_id, tokens in mappings if any(token in haystack for token in tokens)), "editorial_freeform")


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
        "template_source",
        "rationale",
        "execution_assumptions",
        "layout_principles",
        "image_strategy",
        "icon_strategy",
        "cover_png",
        "cover_html",
        "thumb_b64",
        "tokens_summary",
        "sample_render_mode",
        "preview_note",
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


def _selected_style_state(selected: dict[str, Any], theme: ThemeTokens) -> dict[str, Any]:
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
            "template_source",
            "rationale",
            "execution_assumptions",
            "layout_principles",
            "image_strategy",
            "icon_strategy",
        )
    }
    return {
        "theme": theme.to_dict(),
        "art_direction": art_direction,
        "template_id": str(selected.get("template_id") or "editorial_freeform"),
    }


def tokens_summary(theme: ThemeTokens) -> str:
    palette = theme.palette
    return f"{theme.name}: bg {palette.get('bg')}, ink {palette.get('ink')}, accent {palette.get('accent')} / {palette.get('accent2')}; {theme.motif}"


__all__ = [
    "build_style_options",
    "prepare_style_options",
    "public_style_options",
    "select_style_option",
    "theme_is_valid",
    "tokens_summary",
]
