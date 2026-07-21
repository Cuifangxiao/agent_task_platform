#!/usr/bin/env python3
"""Generate browser-verified HTML-first deck samples.

This is intentionally model-free.  It exercises the deterministic fallback
recipes, trusted shell, Chromium QA/capture, and image-only PPTX exporter as a
small acceptance harness that can also be used as a visual template gallery.

Example (macOS development machine)::

    DECK_SAMPLE_CHINESE_FONT="PingFang SC" \
    DECK_SAMPLE_LATIN_FONT="Arial" \
      python apps/runtime/scripts/generate_html_deck_samples.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from core.deck.design.html_catalog import get_html_theme  # noqa: E402
from core.deck.export.image_pptx import ImagePptxExporter  # noqa: E402
from core.deck.html.assembler import HTMLAssembler  # noqa: E402
from core.deck.html.fallback import build_fallback_html_spec  # noqa: E402
from core.deck.rendering.html_browser import (  # noqa: E402
    HTMLBrowserRenderer,
    RenderProfile,
    SecureBrowserSession,
)
from core.deck.schemas import (  # noqa: E402
    PROMPT_VERSION,
    RENDERER_VERSION,
    ContentBlock,
    ContentBlockType,
    DeckPlan,
    DesignSpec,
    LayoutFamily,
    PageRhythm,
    SlideArtifactMetadata,
    SlideContentSpec,
    SlidePlan,
    SlideRole,
)


@dataclass(frozen=True)
class SampleSlide:
    family: LayoutFamily
    role: SlideRole
    title: str
    takeaway: str
    body: str = ""
    bullets: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    right: tuple[str, ...] = ()
    steps: tuple[tuple[str, str], ...] = ()
    metric_value: str = ""
    metric_label: str = ""
    icons: tuple[str, ...] = ("target", "lightbulb", "circle-check")


@dataclass(frozen=True)
class SampleDeck:
    request_id: str
    title: str
    core_message: str
    theme_id: str
    language: str
    slides: tuple[SampleSlide, ...] = field(default_factory=tuple)


def _template_gallery() -> SampleDeck:
    slides = (
        SampleSlide(LayoutFamily.COVER_MINIMAL, SlideRole.COVER, "十种版式，一套渲染契约", "HTML 是预览与 PPTX 的唯一视觉真源。", "从结构化内容到受控浏览器渲染，每一步都有明确边界。", icons=("rocket",)),
        SampleSlide(LayoutFamily.SECTION_BREAK, SlideRole.SECTION, "先讲清楚，再设计", "章节页负责转场，不承担信息堆叠。", "用强节奏变化为下一段叙事留出呼吸。"),
        SampleSlide(LayoutFamily.STATEMENT_FOCUS, SlideRole.INSIGHT, "好页面只表达一个判断", "标题应当是结论，而不是主题标签。", "受众在三秒内就能判断这一页为什么重要。", icons=("lightbulb",)),
        SampleSlide(LayoutFamily.EDITORIAL_SPLIT, SlideRole.CONTEXT, "内容与结论形成编辑式对话", "主栏解释，侧栏负责收束。", "将背景、证据和关键提示分置于不同阅读层级。", bullets=("主叙事保持连续", "侧栏只保留高价值信号", "留白也是信息结构")),
        SampleSlide(LayoutFamily.THREE_POINTS, SlideRole.SOLUTION, "三件事构成完整答案", "并列项彼此独立，也共同支撑结论。", steps=(("结构", "先确定每页的沟通任务"), ("设计", "用统一令牌建立视觉秩序"), ("验证", "让规则在截图前自动执行"))),
        SampleSlide(LayoutFamily.COMPARISON, SlideRole.DECISION, "从双渲染链路走向单一真源", "同一张 PNG 同时进入预览与最终文件。", left=("预览和导出各自渲染", "环境差异难以追溯", "返工依赖人工发现"), right=("HTML 只渲染一次", "版本与哈希完整记录", "确定性规则先行拦截")),
        SampleSlide(LayoutFamily.PROCESS, SlideRole.PROCESS, "五步完成一页可交付内容", "每一步都产生可检查的中间产物。", steps=(("规划", "锁定页面任务"), ("写作", "生成结构化内容"), ("排版", "组合可信 HTML"), ("检查", "测量 DOM 规则"), ("交付", "截图并写入 PPTX"))),
        SampleSlide(LayoutFamily.TIMELINE, SlideRole.PROCESS, "改造按风险逐步收敛", "越靠近交付，产物越确定。", steps=(("Brief", "明确输入边界"), ("Plan", "建立完整叙事"), ("HTML", "生成页面结构"), ("QA", "修复可测缺陷"), ("PPTX", "复用最终截图"))),
        SampleSlide(LayoutFamily.KEY_METRIC, SlideRole.EVIDENCE, "一致性不再依赖运气", "预览与导出复用同一像素级产物。", "渲染环境、字体与内容哈希都随页面记录。", metric_value="100%", metric_label="预览与最终 PPTX 视觉同源"),
        SampleSlide(LayoutFamily.CLOSING_RESOLUTION, SlideRole.CLOSING, "现在可以稳定地生成整套演示", "下一步是接入真实模型输出并持续扩充主题。", "保留严格契约，让创意发生在安全边界之内。", icons=("arrow-up-right",)),
    )
    return SampleDeck("template-gallery", "HTML-first 模板画廊", "十种完整页面配方共享统一设计与渲染契约。", "alpine_research", "zh-CN", slides)


def _sample_decks() -> tuple[SampleDeck, ...]:
    business = SampleDeck(
        "chinese-business",
        "增长经营复盘",
        "把增长从活动驱动升级为可复用的经营系统。",
        "executive_signal",
        "zh-CN",
        (
            SampleSlide(LayoutFamily.COVER_MINIMAL, SlideRole.COVER, "增长不是更多活动，而是更短的学习闭环", "一套从洞察到复盘的经营策略示例。", "管理层讨论稿 · 方法框架", icons=("trending-up",)),
            SampleSlide(LayoutFamily.STATEMENT_FOCUS, SlideRole.INSIGHT, "高价值用户路径值得优先验证", "先验证最可能形成长期价值的关键场景。", "将预算从广泛触达转向高意向场景，有助于更快获得可行动的学习。", icons=("lightbulb",)),
            SampleSlide(LayoutFamily.KEY_METRIC, SlideRole.EVIDENCE, "复购应该成为增长观察重点", "留存、复购与推荐共同构成长周期经营信号。", "下一阶段可优先验证高价值客群的服务体验。", metric_value="复购", metric_label="建议重点观察的增长来源"),
            SampleSlide(LayoutFamily.COMPARISON, SlideRole.DECISION, "经营重心需要从流量规模转向用户价值", "资源分配应围绕长期贡献，而非一次转化。", left=("以曝光和线索量为目标", "渠道各自优化局部指标", "活动结束后经验难复用"), right=("以用户终身价值为目标", "跨渠道共用一套分层策略", "每轮实验沉淀可复制打法")),
            SampleSlide(LayoutFamily.PROCESS, SlideRole.PROCESS, "用五步闭环提升每轮经营质量", "每两周完成一次从洞察到复盘的完整循环。", steps=(("识别", "定位高潜人群"), ("设计", "匹配价值主张"), ("触达", "统一渠道节奏"), ("验证", "观察关键行为"), ("复盘", "沉淀可复用规则"))),
            SampleSlide(LayoutFamily.CLOSING_RESOLUTION, SlideRole.CLOSING, "下一阶段只做三件真正影响增长的事", "聚焦高价值用户、缩短实验周期、统一经营指标。", "本周完成负责人和资源确认，下周进入第一轮闭环。", icons=("arrow-up-right",)),
        ),
    )
    product = SampleDeck(
        "tech-product",
        "Atlas Copilot Product Brief",
        "Move from an AI demo to a dependable team workflow.",
        "midnight_product",
        "en-US",
        (
            SampleSlide(LayoutFamily.COVER_MINIMAL, SlideRole.COVER, "Atlas unifies team decisions", "Reliable, human-steered AI operations.", "Product strategy · 2026", icons=("rocket",)),
            SampleSlide(LayoutFamily.SECTION_BREAK, SlideRole.SECTION, "Design for the handoff, not only the answer", "Work must survive across tools and time.", "A durable workflow needs context, action, and evidence."),
            SampleSlide(LayoutFamily.THREE_POINTS, SlideRole.SOLUTION, "One workspace connects the full operating loop", "Each layer gets stronger as one system.", steps=(("Understand", "Ground every task in shared team context"), ("Act", "Execute through permissioned tools and workflows"), ("Verify", "Attach evidence to every consequential result"))),
            SampleSlide(LayoutFamily.PROCESS, SlideRole.PROCESS, "A five-stage run keeps humans in control", "Autonomy grows after work becomes observable.", steps=(("Frame", "Set outcome and scope"), ("Gather", "Resolve trusted context"), ("Execute", "Run bounded actions"), ("Review", "Inspect evidence and risk"), ("Publish", "Deliver an addressable artifact"))),
            SampleSlide(LayoutFamily.TIMELINE, SlideRole.DECISION, "Ship capability in three confidence layers", "Reliability milestones lead adoption milestones.", steps=(("Now", "Read and summarize"), ("Next", "Draft with evidence"), ("Pilot", "Act behind approval"), ("Scale", "Automate repeatable runs"), ("Learn", "Optimize from outcomes"))),
            SampleSlide(LayoutFamily.CLOSING_RESOLUTION, SlideRole.CLOSING, "Start with one weekly workflow", "Prove trust and time saved before expanding.", "Pilot: weekly product review preparation.", icons=("arrow-up-right",)),
        ),
    )
    editorial = SampleDeck(
        "warm-editorial",
        "把复杂工作讲成人能理解的故事",
        "清晰不是删掉细节，而是安排好读者遇见细节的顺序。",
        "paper_editorial",
        "zh-CN",
        (
            SampleSlide(LayoutFamily.COVER_MINIMAL, SlideRole.COVER, "一份好演示，先让人愿意继续看", "用叙事、留白与证据建立阅读节奏。", "编辑式演示设计小册", icons=("book-open",)),
            SampleSlide(LayoutFamily.EDITORIAL_SPLIT, SlideRole.CONTEXT, "读者不是沿着目录理解，而是沿着问题理解", "每一页都应该回答上页自然产生的疑问。", "目录只是结构，问题链才是体验。先找到真正的阅读动机，再安排信息出现的顺序。", bullets=("从受众的已知开始", "一次只引入一个关键变化", "用结论把证据重新收束"), icons=("target",)),
            SampleSlide(LayoutFamily.STATEMENT_FOCUS, SlideRole.INSIGHT, "留白不是没内容，而是在告诉读者哪里重要", "视觉安静下来，判断才会被真正看见。", "不要平均分配注意力；让一个最重要的观点拥有最大的空间。", icons=("lightbulb",)),
            SampleSlide(LayoutFamily.THREE_POINTS, SlideRole.SOLUTION, "清晰来自三种编辑动作", "删减、排序与强调共同塑造理解。", steps=(("删减", "移除不能改变结论的信息"), ("排序", "让证据在需要时恰好出现"), ("强调", "用对比建立明确阅读入口"))),
            SampleSlide(LayoutFamily.COMPARISON, SlideRole.DECISION, "从信息陈列转向观点表达", "演示不是资料仓库，而是一次有方向的交流。", left=("标题只是章节名称", "要点按来源简单堆叠", "每页使用相同视觉重量"), right=("标题直接说出判断", "证据围绕结论重新组织", "视觉层级体现信息价值")),
            SampleSlide(LayoutFamily.CLOSING_RESOLUTION, SlideRole.CLOSING, "先写下你希望观众记住的那句话", "如果这句话足够清楚，整套演示就有了方向。", "从一句结论开始，而不是从一张空白页面开始。", icons=("arrow-up-right",)),
        ),
    )
    return business, product, editorial


def _content_for(slide: SampleSlide, slide_id: str) -> SlideContentSpec:
    blocks: list[ContentBlock] = []
    if slide.family is LayoutFamily.THREE_POINTS:
        points = slide.steps or tuple((item, "") for item in slide.bullets[:3])
        blocks.extend(ContentBlock(type=ContentBlockType.ICON_LABEL, label=label, text=text) for label, text in points[:3])
    elif slide.family is LayoutFamily.COMPARISON:
        blocks.extend(
            (
                ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="left", items=list(slide.left)),
                ContentBlock(type=ContentBlockType.COMPARISON_SIDE, side="right", items=list(slide.right)),
            )
        )
    elif slide.family in {LayoutFamily.PROCESS, LayoutFamily.TIMELINE}:
        block_type = ContentBlockType.PROCESS_STEP if slide.family is LayoutFamily.PROCESS else ContentBlockType.TIMELINE_ITEM
        blocks.extend(
            ContentBlock(type=block_type, ordinal=index, label=label, text=text)
            for index, (label, text) in enumerate(slide.steps[:5], start=1)
        )
    elif slide.family is LayoutFamily.KEY_METRIC:
        blocks.append(ContentBlock(type=ContentBlockType.METRIC, value=slide.metric_value, label=slide.metric_label))
        if slide.body:
            blocks.append(ContentBlock(type=ContentBlockType.PARAGRAPH, text=slide.body))
    else:
        blocks.append(ContentBlock(type=ContentBlockType.PARAGRAPH, text=slide.body or slide.takeaway))
        if slide.bullets:
            blocks.append(ContentBlock(type=ContentBlockType.BULLET_LIST, items=list(slide.bullets[:4])))
    return SlideContentSpec(
        slide_id=slide_id,
        assertion_title=slide.title,
        takeaway=slide.takeaway,
        content_blocks=blocks,
        suggested_icon_keys=list(slide.icons),
        source_refs=[],
    )


def _plan_for(deck: SampleDeck) -> DeckPlan:
    plans = [
        SlidePlan(
            slide_id=f"s{index:02d}",
            index=index,
            role=slide.role,
            assertion_title=slide.title,
            takeaway=slide.takeaway,
            content_intent=f"Demonstrate {slide.family.value}",
            layout_family=slide.family,
            rhythm=PageRhythm.ANCHOR if index in {1, len(deck.slides)} else PageRhythm.BREATHING,
            transition_from_previous="Continue the narrative" if index > 1 else "",
        )
        for index, slide in enumerate(deck.slides, start=1)
    ]
    return DeckPlan(
        deck_title=deck.title,
        core_message=deck.core_message,
        narrative_arc=[slide.title for slide in deck.slides],
        slides=plans,
    )


def _design_for(theme_id: str, chinese_font: str, latin_font: str) -> DesignSpec:
    design = get_html_theme(theme_id).to_design_spec()
    typography = design.typography.model_copy(
        update={"chinese_font": chinese_font, "latin_font": latin_font}
    )
    return DesignSpec.model_validate(
        design.model_dump(mode="json") | {"typography": typography.model_dump(mode="json")}
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(value, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: Any) -> None:
    _write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _contact_sheet(previews: Iterable[Path], destination: Path) -> None:
    paths = list(previews)
    columns = 2
    thumb_width, thumb_height = 640, 360
    label_height, gap = 34, 24
    rows = (len(paths) + columns - 1) // columns
    sheet = Image.new(
        "RGB",
        (columns * thumb_width + (columns + 1) * gap, rows * (thumb_height + label_height) + (rows + 1) * gap),
        "#E9ECEF",
    )
    draw = ImageDraw.Draw(sheet)
    for index, path in enumerate(paths):
        row, column = divmod(index, columns)
        x = gap + column * (thumb_width + gap)
        y = gap + row * (thumb_height + label_height + gap)
        with Image.open(path) as source:
            preview = source.convert("RGB").resize((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            sheet.paste(preview, (x, y + label_height))
        draw.rectangle((x, y, x + thumb_width, y + label_height), fill="#20262B")
        draw.text((x + 12, y + 9), f"{index + 1:02d}  {path.stem}", fill="#FFFFFF")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp.png")
    try:
        sheet.save(temporary, format="PNG", optimize=True)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _generate_deck(
    deck: SampleDeck,
    *,
    output_root: Path,
    renderer: HTMLBrowserRenderer,
    assembler: HTMLAssembler,
    exporter: ImagePptxExporter,
    chinese_font: str,
    latin_font: str,
) -> dict[str, Any]:
    project = exporter.project_paths(deck.request_id)
    design = _design_for(deck.theme_id, chinese_font, latin_font)
    plan = _plan_for(deck)
    fonts = (chinese_font, latin_font)
    environment = renderer.metadata(font_families=fonts)
    _write_json(project.design_spec_json, design.model_dump(mode="json"))
    _write_json(project.deck_plan_json, plan.model_dump(mode="json"))

    completed: list[dict[str, Any]] = []
    previews: list[Path] = []
    total_renderer_diagnostics = 0
    for plan_slide, story in zip(plan.slides, deck.slides, strict=True):
        content = _content_for(story, plan_slide.slide_id)
        html_spec = build_fallback_html_spec(content, plan_slide)
        assembly = assembler.assemble(
            body_html=html_spec.body_html,
            page_css=html_spec.page_css,
            design_spec=design,
            language=deck.language,
            declared_icon_keys=html_spec.icon_keys,
            expected_layout=plan_slide.layout_family.value,
        )
        assembly_errors = [item for item in assembly.diagnostics if item.severity == "error"]
        if assembly_errors:
            raise RuntimeError(f"{deck.request_id}/{plan_slide.slide_id}: assembly failed: {assembly_errors[0].code}")

        measurement = renderer.measure(assembly.document, font_families=fonts)
        renderer_errors = [item for item in measurement.diagnostics if item.get("severity") == "error"]
        if renderer_errors:
            first = renderer_errors[0]
            raise RuntimeError(
                f"{deck.request_id}/{plan_slide.slide_id}: Chromium QA failed: "
                f"{first.get('code')} {first.get('message')}"
            )
        total_renderer_diagnostics += len(measurement.diagnostics)

        html_path = project.preview_html(plan_slide.index)
        png_path = project.preview_png(plan_slide.index)
        _write_text(html_path, assembly.document)
        capture = renderer.capture(assembly.document, png_path, font_families=fonts)
        previews.append(png_path)

        content_payload = content.model_dump(mode="json")
        html_payload = html_spec.model_dump(mode="json")
        content_hash = _sha256_text(_canonical_json(content_payload))
        html_hash = _sha256_text(assembly.document)
        artifact_hash = _sha256_text(f"{content_hash}:{html_hash}:{capture.sha256}")
        diagnostics_path = project.slide_diagnostics(plan_slide.index)
        diagnostics_payload = {
            "schema": "deck-slide-diagnostics.v1",
            "slide_id": plan_slide.slide_id,
            "status": "passed",
            "assembly": [item.to_dict() for item in assembly.diagnostics],
            "renderer": list(measurement.diagnostics),
            "blocked_requests": list(measurement.blocked_requests),
            "fonts": measurement.fonts,
            "dom_nodes": measurement.dom_nodes,
            "svg_paths": measurement.svg_paths,
            "measure_elapsed_ms": measurement.elapsed_ms,
            "capture_elapsed_ms": capture.elapsed_ms,
        }
        _write_json(project.slide_content_spec(plan_slide.index), content_payload)
        _write_json(project.html_slide_spec(plan_slide.index), html_payload)
        _write_json(diagnostics_path, diagnostics_payload)

        metadata = SlideArtifactMetadata(
            slide_id=plan_slide.slide_id,
            index=plan_slide.index,
            title=plan_slide.assertion_title,
            layout_family=plan_slide.layout_family,
            layout_variant=html_spec.layout_variant,
            composition_summary=html_spec.composition_summary,
            html_path=project.relative(html_path),
            preview_path=project.relative(png_path),
            content_hash=content_hash,
            html_hash=html_hash,
            preview_hash=capture.sha256,
            artifact_hash=artifact_hash,
            event_id=f"sample:{deck.request_id}:{plan_slide.slide_id}:{capture.sha256[:16]}",
            renderer_version=RENDERER_VERSION,
            prompt_version=PROMPT_VERSION,
            base_css_hash=assembly.base_css_hash,
            icon_registry_version=assembly.icon_registry_version,
            render_environment=environment,
            model_metadata={"generator": "deterministic-fallback", "sample": True},
            repair_attempts=0,
            fallback_used=True,
            diagnostics_path=project.relative(diagnostics_path),
        )
        completed.append(metadata.model_dump(mode="json"))

    export = exporter.export(deck.request_id, completed)
    contact_sheet = project.reports / "contact-sheet.png"
    _contact_sheet(previews, contact_sheet)
    _write_json(project.slide_artifact_manifest, {"schema": "slide-artifacts.v1", "slides": completed})
    _write_json(
        project.quality_report,
        {
            "schema": "deck-quality-report.v1",
            "status": "passed",
            "slide_count": len(completed),
            "renderer_diagnostic_count": total_renderer_diagnostics,
            "contact_sheet": project.relative(contact_sheet),
        },
    )
    _write_json(
        project.generation_manifest,
        {
            "schema": "deck-sample-generation.v1",
            "request_id": deck.request_id,
            "title": deck.title,
            "theme_id": deck.theme_id,
            "language": deck.language,
            "fonts": list(fonts),
            "slide_count": len(completed),
            "image_based_pptx": True,
            "pptx": project.relative(export.path),
            "contact_sheet": project.relative(contact_sheet),
        },
    )
    return {
        "request_id": deck.request_id,
        "root": project.root.relative_to(output_root).as_posix(),
        "slide_count": len(completed),
        "pptx": project.relative(export.path),
        "contact_sheet": project.relative(contact_sheet),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=RUNTIME_ROOT / "artifacts" / "deck-v2-samples",
        help="Artifact parent directory (default: apps/runtime/artifacts/deck-v2-samples).",
    )
    parser.add_argument(
        "--chinese-font",
        default=os.getenv("DECK_SAMPLE_CHINESE_FONT", "Noto Sans CJK SC"),
        help="Installed Chinese font passed to the strict Chromium font check.",
    )
    parser.add_argument(
        "--latin-font",
        default=os.getenv("DECK_SAMPLE_LATIN_FONT", "Inter"),
        help="Installed Latin font passed to the strict Chromium font check.",
    )
    parser.add_argument(
        "--device-scale-factor",
        type=float,
        default=2.0,
        choices=(1.0, 2.0),
        help="Preview raster scale; production uses 2.0 (3200x1800).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    assembler = HTMLAssembler()
    exporter = ImagePptxExporter(output_root)
    renderer = HTMLBrowserRenderer(
        SecureBrowserSession(),
        profile=RenderProfile(device_scale_factor=args.device_scale_factor),
    )
    decks = (_template_gallery(), *_sample_decks())
    results: list[dict[str, Any]] = []
    try:
        for deck in decks:
            print(f"Generating {deck.request_id} ({len(deck.slides)} slides)...", flush=True)
            results.append(
                _generate_deck(
                    deck,
                    output_root=output_root,
                    renderer=renderer,
                    assembler=assembler,
                    exporter=exporter,
                    chinese_font=args.chinese_font,
                    latin_font=args.latin_font,
                )
            )
    finally:
        renderer.session.close()

    index = {
        "schema": "deck-sample-suite.v1",
        "renderer": RENDERER_VERSION,
        "fonts": [args.chinese_font, args.latin_font],
        "device_scale_factor": args.device_scale_factor,
        "decks": results,
    }
    _write_json(output_root / "index.json", index)
    print(f"Generated {sum(item['slide_count'] for item in results)} slides at {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
