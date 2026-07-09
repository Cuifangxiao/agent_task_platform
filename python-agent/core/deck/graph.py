"""LangGraph workflow for HTML-first deck generation."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.deck.browser import BrowserRenderer
from core.deck.exporter import DeckExporter
from core.deck.html_renderer import HTMLSlideRenderer, load_theme_presets
from core.deck.spec import DeckSpec, SlideSpec, ThemeTokens, default_theme, normalize_slide_spec
from core.deck.validators import issues_to_dicts, validate_deck_spec, validate_slide_spec, validate_theme_contrast


PROMPT_DIR = Path(__file__).with_name("prompts")


class DeckGraphState(TypedDict, total=False):
    request_id: str
    deck_title: str
    user_description: str
    uploaded_file_name: str
    uploaded_file_text: str
    brief: dict[str, Any]
    outline_markdown: str
    plan: dict[str, Any]
    style_options: list[dict[str, Any]]
    theme: dict[str, Any]
    slides: list[dict[str, Any]]
    deck_spec: dict[str, Any]
    artifacts: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    artifact_path: str


def build_deck_graph(llms: Any, output_dir: Path, checkpointer: Any = None):
    llm_registry = _normalize_llm_registry(llms)
    html_renderer = HTMLSlideRenderer()
    browser = BrowserRenderer(output_dir)
    exporter = DeckExporter(output_dir)

    def prepare_brief(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        _emit_agent(writer, "brief", "Researcher", "整理输入材料和事实表。")
        writer({"event_type": "stage_started", "stage": "brief", "message": "Preparing deck brief"})

        deck_title = str(state.get("deck_title") or "").strip() or "Untitled presentation"
        description = _truncate(str(state.get("user_description") or "").strip(), 5000)
        uploaded_file_text = _truncate(str(state.get("uploaded_file_text") or "").strip(), 60000)
        brief = _build_brief(deck_title, description, uploaded_file_text)
        writer(
            {
                "event_type": "fact_sheet_ready",
                "stage": "brief",
                "message": "Fact sheet ready",
                "payload": {
                    "facts_count": len(brief.get("facts") or []),
                    "audience": brief.get("audience", ""),
                    "goal": brief.get("goal", ""),
                    "suggested_pages": brief.get("suggested_pages", 8),
                },
            }
        )
        return {
            "deck_title": _truncate(deck_title, 120),
            "user_description": description,
            "uploaded_file_name": str(state.get("uploaded_file_name") or "").strip(),
            "uploaded_file_text": uploaded_file_text,
            "brief": brief,
        }

    def generate_outline(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        _emit_agent(writer, "outline", "Strategist", "规划叙事顺序并流式输出 Markdown 大纲。")
        writer({"event_type": "stage_started", "stage": "outline", "message": "Generating deck outline"})

        parts: list[str] = []
        messages = [("system", _load_node_prompt("outline")), ("human", _outline_prompt(state))]
        for delta in _stream_llm_text(_llm_for_node(llm_registry, "outline"), messages):
            if not delta:
                continue
            parts.append(delta)
            writer(
                {
                    "event_type": "text_delta",
                    "stage": "outline",
                    "message": "Streaming outline markdown",
                    "channel": "outline_markdown",
                    "text_delta": delta,
                }
            )

        outline_markdown = _normalize_outline_markdown("".join(parts), state)
        plan = _outline_markdown_to_plan(outline_markdown, state)
        writer(
            {
                "event_type": "outline",
                "stage": "outline",
                "message": "Outline ready",
                "content": outline_markdown,
                "outline_markdown": outline_markdown,
                "payload": {"outline_markdown": outline_markdown},
            }
        )
        return {"outline_markdown": outline_markdown, "plan": plan}

    def review_outline(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        outline_markdown = str(state.get("outline_markdown") or "").strip()
        writer({"event_type": "stage_progress", "stage": "outline_review", "message": "Waiting for outline confirmation"})
        decision = interrupt({"stage": "outline_review", "outline_markdown": outline_markdown})

        if isinstance(decision, dict):
            action = str(decision.get("action") or "approve_outline").strip().lower()
            if action in {"continue", "approve", "approved", "approve_outline"}:
                edited_outline = decision.get("outline_markdown") or decision.get("content")
                if isinstance(edited_outline, str) and edited_outline.strip():
                    edited_outline = _normalize_outline_markdown(edited_outline, state)
                    return {"outline_markdown": edited_outline, "plan": _outline_markdown_to_plan(edited_outline, state)}
        return {}

    def prepare_style_options(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        _emit_agent(writer, "style", "Art Director", "生成两套风格 tokens 和封面样张。")
        writer({"event_type": "stage_started", "stage": "style", "message": "Preparing visual style options"})
        options = _build_style_options(state, html_renderer, browser)
        writer(
            {
                "event_type": "style_options",
                "stage": "style",
                "message": "Style options ready",
                "payload": {"options": _public_style_options(options)},
            }
        )
        return {"style_options": options}

    def review_style(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        options = state.get("style_options") or []
        writer({"event_type": "stage_progress", "stage": "style_review", "message": "Waiting for style selection"})
        decision = interrupt({"stage": "style_review", "style_options": _public_style_options(options)})
        style_id = ""
        feedback = ""
        if isinstance(decision, dict):
            style_id = str(decision.get("style_id") or decision.get("styleId") or "").strip()
            feedback = str(decision.get("feedback") or "").strip()
            payload = decision.get("payload")
            if isinstance(payload, dict):
                style_id = style_id or str(payload.get("style_id") or payload.get("styleId") or "").strip()
                feedback = feedback or str(payload.get("feedback") or "").strip()

        selected = _select_style_option(options, style_id)
        theme = ThemeTokens.from_dict(selected.get("tokens") or {})
        if feedback:
            theme.motif = (theme.motif + " | " + feedback).strip(" |")
        writer(
            {
                "event_type": "stage_progress",
                "stage": "style_review",
                "message": f"Selected style: {theme.name}",
                "payload": {"style_id": selected.get("style_id"), "name": theme.name, "feedback": feedback},
            }
        )
        return {"theme": theme.to_dict()}

    def write_and_design_slides(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        plan_slides = _plan_slide_outlines(state)
        total = len(plan_slides)
        theme = ThemeTokens.from_dict(state.get("theme") or default_theme().to_dict())
        brief = state.get("brief") or {}
        deck = DeckSpec(
            title=str((state.get("plan") or {}).get("title") or state.get("deck_title") or "Untitled presentation").strip(),
            narrative=str((state.get("plan") or {}).get("narrative") or "").strip(),
            audience=str(brief.get("audience") or ""),
            goal=str(brief.get("goal") or ""),
            language=str(brief.get("language") or "zh"),
            theme=theme,
            slides=[],
        )
        slide_llm = _llm_for_node(llm_registry, "writer", aliases=("slides",))
        writer(
            {
                "event_type": "stage_started",
                "stage": "slides",
                "message": f"Generating {total} HTML slide previews",
                "payload": {"total": total},
            }
        )

        slides: list[SlideSpec] = []
        artifacts: list[dict[str, Any]] = []
        all_issues: list[dict[str, Any]] = []
        for index, outline in enumerate(plan_slides, start=1):
            title = str(outline.get("title") or _fallback_slide_title(index, state)).strip()
            legacy_payload = _legacy_slide_payload(
                {
                    "index": index,
                    "title": title,
                    "key_message": str(outline.get("key_message") or "").strip(),
                    "component": str(outline.get("component") or ""),
                    "role": str(outline.get("role") or ""),
                },
                status="running",
            )
            writer(
                {
                    "event_type": "slide_started",
                    "stage": "slides",
                    "message": f"Generating slide {index}/{total}: {title}",
                    "payload": legacy_payload,
                }
            )

            raw_slide: dict[str, Any] = {}
            try:
                raw_slide = _invoke_json_with_retries(
                    slide_llm,
                    system=_load_node_prompt("writer"),
                    user=_slide_prompt(state, outline, slides, total),
                    attempts=3,
                )
            except ValueError as exc:
                writer(
                    {
                        "event_type": "stage_progress",
                        "stage": "slides",
                        "message": f"Slide {index} writer output needed deterministic recovery: {exc}",
                        "payload": {"index": index, "status": "warning", "warning": str(exc)},
                    }
                )

            slide = normalize_slide_spec(
                raw_slide,
                index=index,
                outline=outline,
                deck_title=deck.title,
                narrative=deck.narrative,
            )
            slide = _repair_placeholder_slide(slide, outline, deck)
            spec_issues = validate_slide_spec(slide)
            if spec_issues:
                all_issues.extend(issues_to_dicts(spec_issues))

            slides.append(slide)
            deck.slides = slides
            writer(
                {
                    "event_type": "slide_spec_ready",
                    "stage": "slides",
                    "message": f"Slide {slide.index} spec ready: {slide.title}",
                    "payload": {"index": slide.index, "title": slide.title, "component": slide.component, "role": slide.role},
                }
            )

            html = html_renderer.render(deck, slide)
            artifact = browser.render_slide(str(state["request_id"]), slide, html, theme=theme)
            artifact_payload = {
                "index": slide.index,
                "png": artifact.png_rel,
                "html": artifact.html_rel,
                "thumb_b64": artifact.thumb_b64,
                "version": artifact.version,
                "status": "ready" if not artifact.issues else "warning",
                "renderer": artifact.renderer,
            }
            artifacts.append(artifact_payload)
            writer(
                {
                    "event_type": "slide_preview",
                    "stage": "design_slides",
                    "message": f"Slide {slide.index} preview ready",
                    "payload": artifact_payload,
                }
            )
            writer(
                {
                    "event_type": "slide_ready",
                    "stage": "slides",
                    "message": f"Slide {slide.index} ready: {slide.title}",
                    "payload": _legacy_slide_payload(slide.to_dict(), status=artifact_payload["status"]),
                }
            )
            if artifact.issues:
                all_issues.extend(issues_to_dicts(artifact.issues))

        deck.slides = slides
        return {
            "slides": [slide.to_dict() for slide in slides],
            "deck_spec": deck.to_dict(),
            "artifacts": artifacts,
            "validation_issues": all_issues,
        }

    def validate_deck(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        deck = DeckSpec.from_dict(state.get("deck_spec") or {})
        issues = validate_theme_contrast(deck) + validate_deck_spec(deck)
        current = list(state.get("validation_issues") or [])
        if issues:
            current.extend(issues_to_dicts(issues))
        writer(
            {
                "event_type": "validation_report",
                "stage": "validate",
                "message": "Validation complete",
                "payload": {"issues": current},
            }
        )
        return {"validation_issues": current}

    def coherence_pass(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        _emit_agent(writer, "coherence", "Editor", "检查跨页标题、组件节奏和术语一致性。")
        writer({"event_type": "stage_progress", "stage": "coherence", "message": "Coherence pass complete"})
        return {}

    def export_deck(state: DeckGraphState) -> dict[str, Any]:
        writer = get_stream_writer()
        writer({"event_type": "stage_started", "stage": "export", "message": "Exporting PPTX file"})
        deck = DeckSpec.from_dict(state.get("deck_spec") or {})
        total = len(deck.slides)
        writer({"event_type": "export_progress", "stage": "export", "message": "Preparing PPTX", "payload": {"current": 0, "total": total}})
        result = exporter.export(str(state["request_id"]), deck)
        for warning in result.warnings:
            writer(
                {
                    "event_type": "stage_progress",
                    "stage": "export",
                    "message": warning,
                    "payload": {"status": "warning", "warning": warning},
                }
            )
        writer({"event_type": "export_progress", "stage": "export", "message": "PPTX export complete", "payload": {"current": total, "total": total}})
        writer(
            {
                "event_type": "artifact",
                "stage": "export",
                "message": "PPTX file ready",
                "artifact_path": str(result.path),
                "payload": {
                    "request_id": state["request_id"],
                    "filename": result.path.name,
                    "path": str(result.path),
                    "relative_path": "deck.pptx",
                    "warnings": result.warnings,
                },
            }
        )
        return {"artifact_path": str(result.path)}

    graph = StateGraph(DeckGraphState)
    graph.add_node("brief", prepare_brief)
    graph.add_node("outline", generate_outline)
    graph.add_node("outline_review", review_outline)
    graph.add_node("style", prepare_style_options)
    graph.add_node("style_review", review_style)
    graph.add_node("write_slides", write_and_design_slides)
    graph.add_node("validate", validate_deck)
    graph.add_node("coherence", coherence_pass)
    graph.add_node("export", export_deck)

    graph.add_edge(START, "brief")
    graph.add_edge("brief", "outline")
    graph.add_edge("outline", "outline_review")
    graph.add_edge("outline_review", "style")
    graph.add_edge("style", "style_review")
    graph.add_edge("style_review", "write_slides")
    graph.add_edge("write_slides", "validate")
    graph.add_edge("validate", "coherence")
    graph.add_edge("coherence", "export")
    graph.add_edge("export", END)
    return graph.compile(checkpointer=checkpointer)


@lru_cache(maxsize=None)
def _load_node_prompt(node_key: str) -> str:
    path = PROMPT_DIR / f"{node_key}.md"
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"missing deck node prompt file: {path}") from exc
    if not prompt:
        raise ValueError(f"deck node prompt file is empty: {path}")
    return prompt


def _normalize_llm_registry(llms: Any) -> dict[str, Any]:
    if isinstance(llms, dict):
        registry: dict[str, Any] = {}
        for key, value in llms.items():
            node_key = str(key).strip().lower()
            if node_key and value is not None:
                registry[node_key] = value
        return registry
    return {"default": llms}


def _llm_for_node(registry: dict[str, Any], node_key: str, *, aliases: Iterable[str] = ()) -> Any:
    keys = (node_key, *aliases, "default")
    for key in keys:
        llm = registry.get(key)
        if llm is not None:
            return llm
    raise ValueError(f"missing llm for deck node '{node_key}'")


def _outline_prompt(state: DeckGraphState) -> str:
    return _compose_context_prompt(
        {
            "deck_title_hint": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": state.get("brief") or {},
        },
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def _slide_prompt(
    state: DeckGraphState,
    current_slide_outline: dict[str, Any],
    previous_slides: list[SlideSpec],
    total_slides: int,
) -> str:
    return _compose_context_prompt(
        {
            "deck_title": state.get("deck_title", ""),
            "user_description": state.get("user_description", ""),
            "brief": state.get("brief") or {},
            "outline_markdown": state.get("outline_markdown", ""),
            "total_slides": total_slides,
            "current_slide_outline": current_slide_outline,
            "previous_slides": [slide.to_dict() for slide in previous_slides[-2:]],
        },
        uploaded_file_text=state.get("uploaded_file_text", ""),
    )


def _compose_context_prompt(payload: dict[str, Any], *, uploaded_file_text: str) -> str:
    parts = ["Request context:", json.dumps(payload, ensure_ascii=False)]
    uploaded_file_text = (uploaded_file_text or "").strip()
    if uploaded_file_text:
        parts.extend(["", "Uploaded documents:", uploaded_file_text])
    return "\n".join(parts)


def _stream_llm_text(llm: Any, messages: list[tuple[str, str]]):
    if hasattr(llm, "stream"):
        for chunk in llm.stream(messages):
            text = _extract_text(chunk)
            if text:
                yield text
        return
    response = llm.invoke(messages)
    text = _extract_text(response)
    if text:
        yield text


def _invoke_json_with_retries(llm: Any, system: str, user: str, *, attempts: int) -> dict[str, Any]:
    last_error: Exception | None = None
    prompt = user
    for attempt in range(max(1, attempts)):
        try:
            response = llm.invoke([("system", system), ("human", prompt)])
            return _parse_json_object(_extract_text(response))
        except ValueError as exc:
            last_error = exc
            prompt = user + f"\n\nYour previous response was invalid JSON: {exc}. Return only one JSON object."
    raise ValueError(str(last_error or "model did not return JSON"))


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end < start:
            raise ValueError("model did not return a JSON object") from exc
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as fallback_exc:
            raise ValueError("model did not return a JSON object") from fallback_exc
    if not isinstance(parsed, dict):
        raise ValueError("model JSON output must be an object")
    return parsed


def _normalize_outline_markdown(markdown: str, state: DeckGraphState) -> str:
    cleaned = (markdown or "").strip()
    cleaned = re.sub(r"^```(?:markdown|md)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    if cleaned:
        return cleaned
    title = str(state.get("deck_title") or "Untitled presentation").strip()
    return f"# {title}\n> Outline generated from the user request."


def _outline_markdown_to_plan(markdown: str, state: DeckGraphState) -> dict[str, Any]:
    title = _first_markdown_heading(markdown) or str(state.get("deck_title") or "Untitled presentation").strip()
    narrative = _first_blockquote(markdown)
    return {"title": title, "narrative": narrative, "slides": _parse_outline_slides(markdown)}


def _first_markdown_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if match:
            return match.group(1).strip()
    return ""


def _first_blockquote(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped.lstrip(">").strip()
    return ""


def _parse_outline_slides(markdown: str) -> list[dict[str, Any]]:
    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^##\s*(?:(\d+)[.)、-]?\s*)?(.+?)\s*$", line)
        if heading:
            if current is not None:
                slides.append(current)
            current = {
                "index": int(heading.group(1) or (len(slides) + 1)),
                "title": _clean_markdown_inline(heading.group(2)),
                "key_message": "",
                "role": "",
                "component": "",
                "layout": "content",
            }
            continue
        if current is None or not line.startswith("-"):
            continue
        item = line.lstrip("-").strip()
        label, value = _split_outline_field(item)
        label_lower = label.lower()
        if label in {"核心信息", "关键信息"} or "key" in label_lower:
            current["key_message"] = value
        elif label in {"角色", "叙事角色"} or "role" in label_lower:
            current["role"] = value
        elif label in {"组件", "component"} or "component" in label_lower:
            current["component"] = value
        elif label in {"版式", "布局"} or "layout" in label_lower:
            current["layout"] = value
        elif not current.get("key_message"):
            current["key_message"] = _clean_markdown_inline(item)
    if current is not None:
        slides.append(current)
    return [
        {
            "index": index + 1,
            "title": str(slide.get("title") or f"Slide {index + 1}").strip(),
            "key_message": str(slide.get("key_message") or "").strip(),
            "role": str(slide.get("role") or ("hook" if index == 0 else "evidence")).strip(),
            "component": str(slide.get("component") or _component_from_layout(slide.get("layout"), index)).strip(),
            "layout": str(slide.get("layout") or ("title" if index == 0 else "content")).strip(),
        }
        for index, slide in enumerate(slides[:30])
    ]


def _split_outline_field(item: str) -> tuple[str, str]:
    match = re.match(r"^([^:：]+)[:：]\s*(.*)$", item)
    if not match:
        return "", _clean_markdown_inline(item)
    return match.group(1).strip(), _clean_markdown_inline(match.group(2))


def _clean_markdown_inline(value: str) -> str:
    return re.sub(r"[*_`]+", "", value or "").strip()


def _plan_slide_outlines(state: DeckGraphState) -> list[dict[str, Any]]:
    raw_plan_slides = (state.get("plan") or {}).get("slides") or []
    parsed_slides = raw_plan_slides if isinstance(raw_plan_slides, list) else []
    if not parsed_slides:
        parsed_slides = _parse_outline_slides(str(state.get("outline_markdown") or ""))
    page_count = _target_slide_count(state, parsed_slides)
    outlines: list[dict[str, Any]] = []
    for index in range(page_count):
        source = parsed_slides[index] if index < len(parsed_slides) and isinstance(parsed_slides[index], dict) else {}
        outlines.append(
            {
                "index": index + 1,
                "title": str(source.get("title") or _fallback_slide_title(index + 1, state)).strip(),
                "key_message": str(source.get("key_message") or "").strip(),
                "role": str(source.get("role") or ("hook" if index == 0 else "evidence")).strip(),
                "component": str(source.get("component") or _component_from_layout(source.get("layout"), index)).strip(),
                "layout": str(source.get("layout") or ("title" if index == 0 else "content")).strip(),
            }
        )
    return outlines


def _target_slide_count(state: DeckGraphState, raw_slides: list[Any] | None = None) -> int:
    plan_slides = (state.get("plan") or {}).get("slides") or []
    if isinstance(plan_slides, list) and plan_slides:
        return _clamp_slide_count(len(plan_slides))
    if raw_slides:
        return _clamp_slide_count(len(raw_slides))
    parsed = _parse_outline_slides(str(state.get("outline_markdown") or ""))
    if parsed:
        return _clamp_slide_count(len(parsed))
    return _clamp_slide_count((state.get("brief") or {}).get("suggested_pages") or 8)


def _component_from_layout(value: Any, index: int) -> str:
    layout = str(value or "").strip().lower()
    if index == 0 or layout == "title":
        return "cover"
    if layout == "section":
        return "section"
    if layout == "comparison":
        return "comparison"
    if layout == "summary":
        return "summary"
    return "content-basic"


def _build_brief(deck_title: str, description: str, uploaded_file_text: str) -> dict[str, Any]:
    facts = []
    source_text = "\n".join(part for part in (description, uploaded_file_text) if part)
    for index, sentence in enumerate(_extract_fact_sentences(source_text)[:18], start=1):
        facts.append({"id": f"fact#{index}", "statement": sentence, "source_file": ""})
    language = "en" if _looks_english(description or deck_title) else "zh"
    return {
        "facts": facts,
        "audience": _infer_audience(description),
        "goal": _infer_goal(description),
        "occasion": "",
        "language": language,
        "suggested_pages": _infer_page_count(description),
    }


def _extract_fact_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", cleaned)
    facts: list[str] = []
    for part in parts:
        item = part.strip(" -#\t")
        if len(item) < 8:
            continue
        if any(char.isdigit() for char in item) or len(item) <= 120:
            facts.append(item[:180])
    return facts


def _infer_audience(description: str) -> str:
    match = re.search(r"(?:面向|给|for)\s*([^,，。.!?\n]{2,24})", description or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else "general audience"


def _infer_goal(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return "communicate the main idea clearly"
    return _truncate(text.splitlines()[0], 120)


def _infer_page_count(description: str) -> int:
    match = re.search(r"(\d{1,2})\s*(?:页|slides?|pages?)", description or "", flags=re.IGNORECASE)
    if match:
        return _clamp_slide_count(match.group(1))
    return 8


def _looks_english(text: str) -> bool:
    if not text:
        return False
    ascii_letters = sum(1 for char in text if "a" <= char.lower() <= "z")
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return ascii_letters > cjk * 2


def _build_style_options(state: DeckGraphState, html_renderer: HTMLSlideRenderer, browser: BrowserRenderer) -> list[dict[str, Any]]:
    presets = load_theme_presets()
    if len(presets) == 1:
        presets.append(default_theme())
    title = str((state.get("plan") or {}).get("title") or state.get("deck_title") or "Untitled presentation")
    narrative = str((state.get("plan") or {}).get("narrative") or (state.get("brief") or {}).get("goal") or "")
    options: list[dict[str, Any]] = []
    for index, theme in enumerate(presets[:2], start=1):
        style_id = f"style_{index}"
        html = html_renderer.render_cover_sample(title, narrative, theme)
        artifact = browser.render_style_sample(str(state["request_id"]), style_id, html, theme=theme)
        options.append(
            {
                "style_id": style_id,
                "name": theme.name,
                "tokens": theme.to_dict(),
                "cover_png": artifact["png"],
                "cover_html": artifact["html"],
                "thumb_b64": artifact["thumb_b64"],
                "tokens_summary": _tokens_summary(theme),
            }
        )
    return options


def _public_style_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "style_id": item.get("style_id"),
            "name": item.get("name"),
            "cover_png": item.get("cover_png"),
            "cover_html": item.get("cover_html"),
            "thumb_b64": item.get("thumb_b64"),
            "tokens_summary": item.get("tokens_summary"),
        }
        for item in options
    ]


def _select_style_option(options: list[dict[str, Any]], style_id: str) -> dict[str, Any]:
    for option in options:
        if str(option.get("style_id") or "") == style_id:
            return option
    return options[0] if options else {"style_id": "style_1", "tokens": default_theme().to_dict(), "name": default_theme().name}


def _tokens_summary(theme: ThemeTokens) -> str:
    palette = theme.palette
    return f"{theme.name}: bg {palette.get('bg')}, ink {palette.get('ink')}, accent {palette.get('accent')} / {palette.get('accent2')}; {theme.motif}"


def _repair_placeholder_slide(slide: SlideSpec, outline: dict[str, Any], deck: DeckSpec) -> SlideSpec:
    issues = validate_slide_spec(slide)
    if not any(issue.kind == "placeholder_text" for issue in issues):
        return slide
    key_message = str(outline.get("key_message") or deck.narrative or slide.title).strip()
    slide.title = str(outline.get("title") or slide.title).strip()
    if slide.component == "content-basic":
        slide.content = {"body": key_message, "bullets": [key_message] if key_message else []}
    elif slide.component in {"cover", "section", "end"}:
        slide.content["subtitle"] = key_message
    else:
        slide.content["items"] = [key_message or slide.title]
    return slide


def _legacy_slide_payload(slide: dict[str, Any], *, status: str) -> dict[str, Any]:
    content = slide.get("content") if isinstance(slide.get("content"), dict) else {}
    bullets = content.get("bullets") or content.get("items") or content.get("steps") or []
    if not isinstance(bullets, list):
        bullets = []
    return {
        "index": slide.get("index"),
        "title": slide.get("title", ""),
        "key_message": content.get("body") or content.get("subtitle") or content.get("takeaway") or slide.get("key_message") or "",
        "layout": slide.get("component") or slide.get("layout") or "content-basic",
        "component": slide.get("component") or "",
        "role": slide.get("role") or "",
        "bullets": [str(item) for item in bullets],
        "visual_suggestion": "",
        "speaker_notes": slide.get("notes", ""),
        "status": status,
    }


def _emit_agent(writer: Any, stage: str, agent: str, message: str) -> None:
    writer({"event_type": "agent_message", "stage": stage, "message": message, "payload": {"agent": agent, "message": message}})


def _fallback_slide_title(index: int, state: DeckGraphState) -> str:
    deck_title = str(state.get("deck_title") or "Untitled presentation")
    if index == 1:
        return deck_title
    return f"{deck_title} - Part {index}"


def _clamp_slide_count(value: Any) -> int:
    try:
        page_count = int(value)
    except (TypeError, ValueError):
        page_count = 8
    return max(1, min(page_count, 30))


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars]
