"""Conservative executable recipes used after semantic HTML retries exhaust."""

from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from typing import Iterable

from core.deck.design.html_catalog import get_recipe
from core.deck.html.icons import icon_registry
from core.deck.schemas import (
    ContentBlock,
    ContentBlockType,
    HtmlSlideSpec,
    LayoutFamily,
    SlideContentSpec,
    SlidePlan,
)


def build_fallback_html_spec(
    content_spec: SlideContentSpec | dict,
    slide_plan: SlidePlan | dict,
) -> HtmlSlideSpec:
    content = content_spec if isinstance(content_spec, SlideContentSpec) else SlideContentSpec.model_validate(content_spec)
    plan = slide_plan if isinstance(slide_plan, SlidePlan) else SlidePlan.model_validate(slide_plan)
    recipe = get_recipe(plan.layout_family)
    renderer = _RENDERERS[plan.layout_family]
    body, css, icons = renderer(content, plan)
    return HtmlSlideSpec(
        slide_id=plan.slide_id,
        layout_family=plan.layout_family,
        layout_variant=recipe.variants[0],
        composition_summary=f"Program fallback using {recipe.family.value}: {recipe.structure}",
        body_html=body,
        page_css=COMMON_CSS + "\n" + css,
        icon_keys=icons,
        estimated_density="low" if plan.rhythm.value == "breathing" else "medium",
    )


def _cover(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    icon = _icon(content, "rocket")
    body = f"""
<section class="slide recipe-cover" data-layout="cover_minimal">
  <div class="cover-orbit" data-decorative="true"></div>
  <div class="cover-copy">
    <div class="eyebrow" data-box="caption">PRESENTATION</div>
    <div class="cover-title" data-box="title" data-title-lines="2"><h1>{_text(_display_title(content, plan))}</h1></div>
    <p class="cover-takeaway" data-box="content">{_text(_display_takeaway(content, plan))}</p>
  </div>
  <div class="cover-icon" data-decorative="true"><span data-icon="{icon}"></span></div>
  <div class="page-number" data-box="footer">01</div>
</section>""".strip()
    css = """
.slide.recipe-cover{background:linear-gradient(112deg,var(--slide-bg) 0 72%,var(--surface) 72%);}
.slide.recipe-cover .cover-copy{position:absolute;left:var(--canvas-margin);top:154px;width:1010px;}
.slide.recipe-cover .cover-title{margin-top:30px;width:1010px;padding-bottom:16px;}
.slide.recipe-cover h1{font-size:var(--font-cover-title-size);font-weight:var(--font-cover-title-weight);line-height:var(--font-cover-title-line-height);letter-spacing:-0.04em;}
.slide.recipe-cover .cover-takeaway{width:820px;margin-top:38px;color:var(--text-muted);font-size:28px;line-height:1.45;}
.slide.recipe-cover .cover-orbit{position:absolute;right:0;top:84px;width:400px;height:520px;border:74px solid var(--accent);border-radius:50% 0 0 50%;opacity:.14;}
.slide.recipe-cover .cover-icon{position:absolute;right:142px;top:302px;width:118px;height:118px;color:var(--accent);}
.slide.recipe-cover .cover-icon .icon{width:118px;height:118px;}
""".strip()
    return body, css, [icon]


def _section(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    body = f"""
<section class="slide recipe-section" data-layout="section_break">
  <div class="section-field" data-decorative="true"></div>
  <div class="section-number" data-decorative="true">{plan.index:02d}</div>
  <div class="section-copy">
    <div class="eyebrow" data-box="caption">SECTION</div>
    <div class="section-title" data-box="title" data-title-lines="2"><h1>{_text(_display_title(content, plan))}</h1></div>
    <p data-box="content">{_text(_display_takeaway(content, plan))}</p>
  </div>
  <div class="page-number" data-box="footer">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-section .section-field{position:absolute;left:0;top:0;width:520px;height:900px;background:var(--accent);}
.slide.recipe-section .section-number{position:absolute;left:54px;bottom:0;width:420px;height:280px;overflow:hidden;color:var(--slide-bg);font-size:270px;font-weight:800;line-height:1;opacity:.16;}
.slide.recipe-section .section-copy{position:absolute;left:620px;top:228px;width:820px;}
.slide.recipe-section .section-title{margin-top:24px;width:820px;padding-bottom:14px;}
.slide.recipe-section h1{font-size:64px;line-height:1.08;}
.slide.recipe-section p{width:720px;margin-top:34px;color:var(--text-muted);font-size:26px;line-height:1.5;}
""".strip()
    return body, css, []


def _statement(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    support = _support_text(content, exclude={content.takeaway})
    icon = _icon(content, "lightbulb")
    body = f"""
<section class="slide recipe-statement" data-layout="statement_focus">
  <div class="statement-mark" data-decorative="true"></div>
  <div class="statement-icon" data-decorative="true"><span data-icon="{icon}"></span></div>
  <div class="statement-title" data-box="title" data-title-lines="3"><h1>{_text(_display_title(content, plan))}</h1></div>
  <div class="statement-support" data-box="content">
    <p>{_text(_display_takeaway(content, plan))}</p>
    {_paragraph(support, class_name="muted")}
  </div>
  <div class="page-number" data-box="footer">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-statement{background:radial-gradient(circle at 78% 18%,var(--surface) 0 16%,transparent 42%),var(--slide-bg);}
.slide.recipe-statement .statement-mark{position:absolute;left:112px;top:128px;width:12px;height:560px;background:var(--secondary);}
.slide.recipe-statement .statement-title{position:absolute;left:174px;top:158px;width:1160px;padding-bottom:14px;}
.slide.recipe-statement h1{font-size:72px;line-height:1.08;}
.slide.recipe-statement .statement-support{position:absolute;left:180px;bottom:136px;width:1050px;display:grid;grid-template-columns:1.2fr .8fr;gap:72px;border-top:2px solid var(--divider);padding-top:30px;}
.slide.recipe-statement .statement-support p:first-child{color:var(--accent);font-size:28px;font-weight:700;}
.slide.recipe-statement .statement-icon{position:absolute;right:104px;bottom:96px;width:80px;height:80px;color:var(--secondary);}
.slide.recipe-statement .statement-icon .icon{width:80px;height:80px;}
""".strip()
    return body, css, [icon]


def _editorial_split(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    blocks = _group_blocks(content.content_blocks)
    body_text = _truncate(
        _first_text(blocks, ContentBlockType.PARAGRAPH, ContentBlockType.CALLOUT) or content.takeaway,
        100,
    )
    items = _items(blocks, limit=4)
    icon = _icon(content, "target")
    body = f"""
<section class="slide recipe-split" data-layout="editorial_split">
  {_page_header(content, plan)}
  <div class="split-primary" data-box="content">
    <p class="lead">{_text(body_text)}</p>
    {_list(items)}
  </div>
  <aside class="split-signal" data-box="visual">
    <div class="split-icon"><span data-icon="{icon}"></span></div>
    <div class="eyebrow" data-box="caption">TAKEAWAY</div>
    <p>{_text(_display_takeaway(content, plan))}</p>
  </aside>
  <div class="page-number" data-box="footer">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-split .split-primary{position:absolute;left:var(--canvas-margin);top:258px;width:900px;bottom:116px;padding-right:60px;border-right:1px solid var(--divider);}
.slide.recipe-split .lead{width:790px;color:var(--text-primary);font-size:30px;font-weight:600;line-height:1.38;}
.slide.recipe-split ul{display:grid;gap:22px;margin-top:38px;}
.slide.recipe-split li{position:relative;padding-left:22px;border-left:8px solid var(--secondary);color:var(--text-muted);}
.slide.recipe-split .split-signal{position:absolute;right:var(--canvas-margin);top:270px;width:390px;min-height:420px;padding:38px;background:var(--surface);border-top:8px solid var(--accent);}
.slide.recipe-split .split-signal p{margin-top:24px;color:var(--text-primary);font-size:30px;font-weight:700;line-height:1.3;}
.slide.recipe-split .split-icon{width:62px;height:62px;margin-bottom:48px;color:var(--accent);}
.slide.recipe-split .split-icon .icon{width:62px;height:62px;}
""".strip()
    return body, css, [icon]


def _three_points(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    points = _point_pairs(content, limit=3)
    icons = [_icon_for_index(content, index) for index in range(len(points))]
    cards = "".join(
        f"""<article class="point">
  <div class="point-top"><span class="point-number">{index + 1:02d}</span><span class="point-icon"><span data-icon="{icons[index]}"></span></span></div>
  <h2>{_text(title)}</h2><p>{_text(text)}</p>
</article>"""
        for index, (title, text) in enumerate(points)
    )
    body = f"""
<section class="slide recipe-three" data-layout="three_points">
  {_page_header(content, plan)}
  <div class="points" data-box="content">{cards}</div>
  <div class="takeaway-line" data-box="footer">{_text(_display_takeaway(content, plan))}</div>
  <div class="page-number" data-box="caption">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-three .points{position:absolute;left:var(--canvas-margin);right:var(--canvas-margin);top:266px;display:grid;grid-template-columns:repeat(3,1fr);gap:var(--column-gap);padding-bottom:38px;}
.slide.recipe-three .point{min-height:390px;padding:30px 0;border-top:3px solid var(--divider);}
.slide.recipe-three .point:nth-child(2){transform:translateY(38px);border-top-color:var(--accent);}
.slide.recipe-three .point-top{display:flex;align-items:center;justify-content:space-between;color:var(--accent);}
.slide.recipe-three .point-number{font-size:21px;font-weight:800;letter-spacing:.12em;}
.slide.recipe-three .point-icon{width:44px;height:44px;}
.slide.recipe-three .point-icon .icon{width:44px;height:44px;}
.slide.recipe-three .point h2{margin-top:50px;font-size:31px;line-height:1.2;}
.slide.recipe-three .point p{margin-top:22px;color:var(--text-muted);font-size:22px;line-height:1.5;}
.slide.recipe-three .takeaway-line{position:absolute;left:var(--canvas-margin);bottom:60px;color:var(--accent);font-size:21px;font-weight:700;}
""".strip()
    return body, css, icons


def _comparison(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    left, right = _comparison_sides(content)
    body = f"""
<section class="slide recipe-comparison" data-layout="comparison">
  {_page_header(content, plan)}
  <div class="compare-grid" data-box="content">
    <article class="compare-side left"><div class="eyebrow" data-box="caption">CURRENT</div>{_list(left)}</article>
    <div class="compare-arrow" data-decorative="true">→</div>
    <article class="compare-side right"><div class="eyebrow" data-box="caption">TARGET</div>{_list(right)}</article>
  </div>
  <div class="answer-band" data-box="footer">{_text(_display_takeaway(content, plan))}</div>
  <div class="page-number" data-box="caption">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-comparison .compare-grid{position:absolute;left:var(--canvas-margin);right:var(--canvas-margin);top:252px;display:grid;grid-template-columns:1fr 92px 1fr;align-items:stretch;}
.slide.recipe-comparison .compare-side{min-height:390px;padding:36px 42px;background:var(--surface);border:1px solid var(--divider);}
.slide.recipe-comparison .compare-side.right{border-top:8px solid var(--accent);}
.slide.recipe-comparison .compare-side ul{display:grid;gap:24px;margin-top:32px;}
.slide.recipe-comparison .compare-side li{position:relative;padding-left:18px;border-left:7px solid var(--secondary);color:var(--text-muted);}
.slide.recipe-comparison .compare-arrow{display:flex;align-items:center;justify-content:center;color:var(--accent);font-size:54px;font-weight:300;}
.slide.recipe-comparison .answer-band{position:absolute;left:290px;right:290px;bottom:54px;padding:18px 34px;background:var(--accent);color:var(--slide-bg);font-size:23px;font-weight:700;text-align:center;}
""".strip()
    return body, css, []


def _process(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    steps = _steps(content, ContentBlockType.PROCESS_STEP, limit=5)
    rendered = "".join(
        f'<article class="process-step"><span>{index + 1:02d}</span><h2>{_text(title)}</h2><p>{_text(text)}</p></article>'
        for index, (title, text) in enumerate(steps)
    )
    body = f"""
<section class="slide recipe-process" data-layout="process">
  {_page_header(content, plan)}
  <div class="process-track" data-box="content">{rendered}</div>
  <div class="process-outcome" data-box="footer">{_text(_display_takeaway(content, plan))}</div>
  <div class="page-number" data-box="caption">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-process .process-track{position:absolute;left:var(--canvas-margin);right:var(--canvas-margin);top:286px;display:grid;grid-template-columns:repeat(5,1fr);gap:24px;background-image:linear-gradient(var(--divider),var(--divider));background-position:left 34px;background-repeat:no-repeat;background-size:100% 2px;}
.slide.recipe-process .process-step{position:relative;padding-top:82px;}
.slide.recipe-process .process-step>span{position:absolute;left:0;top:0;display:flex;width:68px;height:68px;align-items:center;justify-content:center;border-radius:50%;background:var(--accent);color:var(--slide-bg);font-size:21px;font-weight:800;}
.slide.recipe-process .process-step h2{font-size:27px;line-height:1.2;}
.slide.recipe-process .process-step p{margin-top:17px;color:var(--text-muted);font-size:21px;line-height:1.45;}
.slide.recipe-process .process-outcome{position:absolute;left:var(--canvas-margin);bottom:60px;padding-left:22px;border-left:6px solid var(--secondary);color:var(--text-primary);font-size:22px;font-weight:650;}
""".strip()
    return body, css, []


def _timeline(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    steps = _steps(content, ContentBlockType.TIMELINE_ITEM, limit=5)
    rendered = "".join(
        f'<article class="milestone"><span class="dot"></span><div class="time">{_text(title)}</div><p>{_text(text)}</p></article>'
        for title, text in steps
    )
    body = f"""
<section class="slide recipe-timeline" data-layout="timeline">
  {_page_header(content, plan)}
  <div class="timeline-axis" data-box="content">{rendered}</div>
  <div class="timeline-takeaway" data-box="footer">{_text(_display_takeaway(content, plan))}</div>
  <div class="page-number" data-box="caption">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-timeline .timeline-axis{position:absolute;left:var(--canvas-margin);right:var(--canvas-margin);top:390px;display:grid;grid-template-columns:repeat(5,1fr);gap:28px;border-top:3px solid var(--divider);}
.slide.recipe-timeline .milestone{position:relative;padding-top:38px;}
.slide.recipe-timeline .milestone:nth-child(even){transform:translateY(-132px);}
.slide.recipe-timeline .dot{position:absolute;left:0;top:-12px;width:22px;height:22px;border:5px solid var(--slide-bg);border-radius:50%;background:var(--accent);outline:2px solid var(--accent);}
.slide.recipe-timeline .time{color:var(--accent);font-size:21px;font-weight:800;}
.slide.recipe-timeline .milestone p{margin-top:14px;color:var(--text-muted);font-size:21px;line-height:1.45;}
.slide.recipe-timeline .timeline-takeaway{position:absolute;right:var(--canvas-margin);bottom:76px;width:720px;text-align:right;color:var(--text-primary);font-size:25px;font-weight:700;}
""".strip()
    return body, css, []


def _key_metric(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    metric = next((block for block in content.content_blocks if block.type is ContentBlockType.METRIC), None)
    value = _truncate(metric.value, 12) if metric else "—"
    label = _truncate(metric.label or metric.text, 48) if metric else _display_title(content, plan)
    support = _support_text(content, exclude={label, value})
    body = f"""
<section class="slide recipe-metric" data-layout="key_metric">
  {_page_header(content, plan)}
  <div class="metric-hero" data-box="metric">
    <div class="metric-value">{_text(value)}</div>
    <div class="metric-label">{_text(label)}</div>
  </div>
  <div class="metric-explain" data-box="content">
    <div class="eyebrow" data-box="caption">WHY IT MATTERS</div>
    <p>{_text(_display_takeaway(content, plan))}</p>
    {_paragraph(support, class_name="muted")}
  </div>
  <div class="metric-arc" data-decorative="true"></div>
  <div class="page-number" data-box="footer">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-metric .metric-hero{position:absolute;left:var(--canvas-margin);top:266px;width:760px;}
.slide.recipe-metric .metric-value{color:var(--accent);font-size:170px;font-weight:800;line-height:.92;letter-spacing:-.06em;}
.slide.recipe-metric .metric-label{width:650px;margin-top:34px;color:var(--text-primary);font-size:31px;font-weight:700;line-height:1.28;}
.slide.recipe-metric .metric-explain{position:absolute;right:var(--canvas-margin);top:290px;width:520px;padding:42px;background:var(--surface);border-left:8px solid var(--secondary);}
.slide.recipe-metric .metric-explain p{margin-top:24px;color:var(--text-primary);font-size:29px;font-weight:700;line-height:1.32;}
.slide.recipe-metric .metric-explain .muted{margin-top:24px;font-size:21px;}
.slide.recipe-metric .metric-arc{position:absolute;left:0;bottom:0;width:340px;height:230px;border:48px solid var(--secondary);border-radius:0 100% 0 0;opacity:.09;}
""".strip()
    return body, css, []


def _closing(content: SlideContentSpec, plan: SlidePlan) -> tuple[str, str, list[str]]:
    icon = _icon(content, "arrow-up-right")
    action = _support_text(content, exclude={content.takeaway})
    body = f"""
<section class="slide recipe-closing" data-layout="closing_resolution">
  <div class="closing-field" data-decorative="true"></div>
  <div class="closing-copy">
    <div class="eyebrow" data-box="caption">RESOLUTION</div>
    <div class="closing-title" data-box="title" data-title-lines="2"><h1>{_text(_display_title(content, plan))}</h1></div>
    <p data-box="content">{_text(_display_takeaway(content, plan))}</p>
  </div>
  <div class="closing-action" data-box="footer"><span data-icon="{icon}"></span><span>{_text(action or "Next step")}</span></div>
  <div class="page-number" data-box="caption">{plan.index:02d}</div>
</section>""".strip()
    css = """
.slide.recipe-closing{background:linear-gradient(128deg,var(--slide-bg) 0 66%,var(--accent) 66%);}
.slide.recipe-closing .closing-field{position:absolute;right:160px;top:118px;width:330px;height:590px;border:3px solid var(--slide-bg);opacity:.34;transform:rotate(8deg);}
.slide.recipe-closing .closing-copy{position:absolute;left:var(--canvas-margin);top:172px;width:970px;}
.slide.recipe-closing .closing-title{margin-top:30px;width:970px;padding-bottom:14px;}
.slide.recipe-closing h1{font-size:72px;line-height:1.08;}
.slide.recipe-closing .closing-copy p{width:820px;margin-top:42px;color:var(--text-muted);font-size:29px;line-height:1.45;}
.slide.recipe-closing .closing-action{position:absolute;left:var(--canvas-margin);bottom:96px;display:flex;align-items:center;gap:18px;color:var(--accent);font-size:22px;font-weight:700;}
.slide.recipe-closing .closing-action .icon{width:36px;height:36px;}
""".strip()
    return body, css, [icon]


def _page_header(content: SlideContentSpec, plan: SlidePlan) -> str:
    return f"""<header class="page-header" data-box="title" data-title-lines="2">
  <div class="eyebrow">{_text(plan.role.value)}</div>
  <h1>{_text(_display_title(content, plan))}</h1>
</header>"""


def _group_blocks(blocks: Iterable[ContentBlock]) -> dict[ContentBlockType, list[ContentBlock]]:
    grouped: dict[ContentBlockType, list[ContentBlock]] = defaultdict(list)
    for block in blocks:
        grouped[block.type].append(block)
    return grouped


def _first_text(grouped: dict[ContentBlockType, list[ContentBlock]], *types: ContentBlockType) -> str:
    for block_type in types:
        for block in grouped.get(block_type, []):
            if block.text:
                return block.text
    return ""


def _items(grouped: dict[ContentBlockType, list[ContentBlock]], *, limit: int) -> list[str]:
    values: list[str] = []
    for blocks in grouped.values():
        for block in blocks:
            values.extend(block.items)
            if block.type in {ContentBlockType.ICON_LABEL, ContentBlockType.HEADING} and (block.text or block.label):
                values.append(block.text or block.label)
    return [_truncate(value, 42) for value in _unique(values)[:limit]]


def _point_pairs(content: SlideContentSpec, *, limit: int) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for block in content.content_blocks:
        if block.type in {ContentBlockType.ICON_LABEL, ContentBlockType.HEADING, ContentBlockType.CALLOUT}:
            title = block.label or block.text
            text = block.text if block.label else (block.items[0] if block.items else "")
            if title:
                pairs.append((_truncate(title, 30), _truncate(text, 64)))
        elif block.items:
            pairs.extend((_truncate(item, 30), "") for item in block.items)
    if not pairs:
        pairs = [(_display_title(content, None), _display_takeaway(content, None))]
    return pairs[:limit]


def _comparison_sides(content: SlideContentSpec) -> tuple[list[str], list[str]]:
    left: list[str] = []
    right: list[str] = []
    for block in content.content_blocks:
        target = left if block.side != "right" else right
        values = block.items or [block.text or block.label]
        target.extend(_truncate(value, 42) for value in values if value)
    if not right and len(left) > 1:
        midpoint = (len(left) + 1) // 2
        left, right = left[:midpoint], left[midpoint:]
    return (left or [_display_title(content, None)])[:3], (right or [_display_takeaway(content, None)])[:3]


def _steps(content: SlideContentSpec, block_type: ContentBlockType, *, limit: int) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for block in content.content_blocks:
        if block.type is block_type:
            result.append(
                (
                    _truncate(block.label or f"Step {block.ordinal or len(result) + 1}", 18),
                    _truncate(block.text, 52),
                )
            )
        elif block.items and not result:
            result.extend((str(index + 1), _truncate(item, 52)) for index, item in enumerate(block.items))
    return (result or [("01", _display_takeaway(content, None))])[:limit]


def _support_text(content: SlideContentSpec, *, exclude: set[str]) -> str:
    for block in content.content_blocks:
        for candidate in (block.text, *block.items, block.label):
            if candidate and candidate not in exclude:
                return _truncate(candidate, 60)
    return ""


def _icon(content: SlideContentSpec, fallback: str) -> str:
    known = set(icon_registry().keys())
    return next((key for key in content.suggested_icon_keys if key in known), fallback)


def _icon_for_index(content: SlideContentSpec, index: int) -> str:
    known = set(icon_registry().keys())
    approved = [key for key in content.suggested_icon_keys if key in known]
    fallbacks = ["target", "lightbulb", "circle-check"]
    return approved[index] if index < len(approved) else fallbacks[index % len(fallbacks)]


def _text(value: object) -> str:
    return html.escape(str(value or "").strip(), quote=True)


def _paragraph(value: str, *, class_name: str = "") -> str:
    return f'<p class="{html.escape(class_name, quote=True)}">{_text(value)}</p>' if value else ""


def _list(items: Iterable[str]) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    return "<ul>" + "".join(f"<li>{_text(item)}</li>" for item in values) + "</ul>" if values else ""


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


TITLE_DISPLAY_WIDTH_BUDGET = {
    # Browser-tested conservative widths. East Asian wide/full-width glyphs
    # consume two units, while Latin glyphs consume one. This keeps the same
    # fallback safe for Chinese, English, and mixed-script titles.
    LayoutFamily.COVER_MINIMAL: 44,
    LayoutFamily.SECTION_BREAK: 48,
    LayoutFamily.STATEMENT_FOCUS: 84,
    LayoutFamily.EDITORIAL_SPLIT: 80,
    LayoutFamily.THREE_POINTS: 80,
    LayoutFamily.COMPARISON: 80,
    LayoutFamily.PROCESS: 80,
    LayoutFamily.TIMELINE: 80,
    LayoutFamily.KEY_METRIC: 80,
    LayoutFamily.CLOSING_RESOLUTION: 48,
}

TAKEAWAY_CHAR_BUDGET = {
    LayoutFamily.COVER_MINIMAL: 48,
    LayoutFamily.SECTION_BREAK: 48,
    LayoutFamily.STATEMENT_FOCUS: 48,
    LayoutFamily.EDITORIAL_SPLIT: 48,
    LayoutFamily.THREE_POINTS: 50,
    LayoutFamily.COMPARISON: 48,
    LayoutFamily.PROCESS: 50,
    LayoutFamily.TIMELINE: 50,
    LayoutFamily.KEY_METRIC: 36,
    LayoutFamily.CLOSING_RESOLUTION: 60,
}


def _display_title(content: SlideContentSpec, plan: SlidePlan | None) -> str:
    family = plan.layout_family if plan is not None else LayoutFamily.EDITORIAL_SPLIT
    return _truncate_display_width(content.assertion_title, TITLE_DISPLAY_WIDTH_BUDGET[family])


def _display_takeaway(content: SlideContentSpec, plan: SlidePlan | None) -> str:
    family = plan.layout_family if plan is not None else LayoutFamily.EDITORIAL_SPLIT
    return _truncate(content.takeaway, TAKEAWAY_CHAR_BUDGET[family])


def _truncate(value: str, limit: int) -> str:
    clean = str(value or "").strip()
    return clean if len(clean) <= limit else clean[: max(1, limit - 1)].rstrip() + "…"


def _truncate_display_width(value: str, limit: int) -> str:
    clean = str(value or "").strip()
    maximum = max(1, int(limit))

    def width(character: str) -> float:
        if unicodedata.combining(character):
            return 0.0
        if character.isspace():
            return 0.55
        if unicodedata.east_asian_width(character) in {"W", "F"}:
            return 2.0
        # Latin titles should not be shortened as aggressively as CJK.  The
        # browser matrix still needs to distinguish very wide glyphs (W/M)
        # from narrow ones (i/l), so use small font-width classes instead of
        # treating every visible character as a full CJK em.
        if character in "WwMm@%&QO":
            return 2.0
        if character in "ilI1|!.,'`:\";":
            return 0.55
        if character.isupper():
            return 1.15
        if character.isdigit():
            return 0.95
        return 1.0

    if sum(width(character) for character in clean) <= maximum:
        return clean
    ellipsis = "…"
    remaining = max(0, maximum - width(ellipsis))
    result: list[str] = []
    used = 0.0
    for character in clean:
        character_width = width(character)
        if used + character_width > remaining:
            break
        result.append(character)
        used += character_width
    cutoff = len(result)
    prefix = "".join(result).rstrip()
    if cutoff < len(clean):
        # Never turn one approved numeric token into a new one (for example
        # 2026 -> 20…). Remove the incomplete token before adding ellipsis.
        numeric_suffix = re.search(r"\d[\d,.，．]*[%％]?$", prefix)
        next_character = clean[cutoff : cutoff + 1]
        if numeric_suffix and next_character and re.match(r"[\d,.，．%％]", next_character):
            prefix = prefix[: numeric_suffix.start()].rstrip(" -–—:：,，.。")
    return prefix + ellipsis


COMMON_CSS = """
.slide .page-header{position:absolute;left:var(--canvas-margin);right:var(--canvas-margin);top:62px;padding-bottom:30px;border-bottom:1px solid var(--divider);}
.slide .page-header h1{margin-top:14px;max-width:1320px;}
.slide h1{overflow-wrap:anywhere;word-break:break-word;}
""".strip()

_RENDERERS = {
    LayoutFamily.COVER_MINIMAL: _cover,
    LayoutFamily.SECTION_BREAK: _section,
    LayoutFamily.STATEMENT_FOCUS: _statement,
    LayoutFamily.EDITORIAL_SPLIT: _editorial_split,
    LayoutFamily.THREE_POINTS: _three_points,
    LayoutFamily.COMPARISON: _comparison,
    LayoutFamily.PROCESS: _process,
    LayoutFamily.TIMELINE: _timeline,
    LayoutFamily.KEY_METRIC: _key_metric,
    LayoutFamily.CLOSING_RESOLUTION: _closing,
}


__all__ = ["build_fallback_html_spec"]
