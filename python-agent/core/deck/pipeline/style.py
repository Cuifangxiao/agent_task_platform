"""Style option generation and style-review interrupt nodes."""

from __future__ import annotations

import json
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.types import interrupt

import core.deck.events as events
from core.deck.pipeline.helpers import emit_agent
from core.deck.pipeline.llm import invoke_json_with_retries, load_node_prompt
from core.deck.runtime_metrics import record_fallback
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import validate_theme_contrast
from core.deck.rendering.browser import BrowserRenderer
from core.deck.rendering.html import HTMLSlideRenderer, load_theme_presets
from core.deck.spec import DeckSpec, ThemeTokens, default_theme


def prepare_style_options(
    state: DeckGraphState,
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
    llm_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    writer = get_stream_writer()
    emit_agent(writer, "style", "Art Director", "生成两套风格 tokens 和封面样张。")
    writer(events.stage_started("style", "Preparing visual style options"))
    options = build_style_options(state, html_renderer, browser, llm_registry)
    writer(events.style_options({"options": public_style_options(options)}))
    return {"style_options": options}


def review_style(state: DeckGraphState, llm_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    writer = get_stream_writer()
    options = state.get("style_options") or []
    if str(state.get("generation_mode") or "") == "quick":
        selected, _matched = select_style_option(options, "")
        theme = ThemeTokens.from_dict(selected.get("tokens") or {})
        writer(events.stage_progress("style_review", f"Quick mode selected style: {theme.name}", {"status": "done"}))
        return {"theme": theme.to_dict()}
    writer(events.stage_progress("style_review", "Waiting for style selection"))
    decision = interrupt({"stage": "style_review", "style_options": public_style_options(options)})
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
        writer(events.stage_progress("style_review", f"Unknown style_id '{style_id}', selected the first style option.", {"status": "warning", "style_id": style_id}))
    theme = ThemeTokens.from_dict(selected.get("tokens") or {})
    if feedback and llm_registry and llm_registry.get("style") is not None:
        try:
            adjusted = adjust_theme_from_feedback(theme, feedback, llm_registry["style"])
            if theme_is_valid(adjusted):
                theme = adjusted
            else:
                record_fallback()
                writer(events.stage_progress("style_review", "Style feedback produced low-contrast tokens; kept selected style.", {"status": "warning"}))
        except ValueError as exc:
            record_fallback()
            writer(events.stage_progress("style_review", f"Style feedback model output was unusable; kept selected style: {exc}", {"status": "warning"}))
    elif feedback:
        record_fallback()
        theme.motif = (theme.motif + " | " + feedback).strip(" |")
    writer(
        events.stage_progress(
            "style_review",
            f"Selected style: {theme.name}",
            {"style_id": selected.get("style_id"), "name": theme.name, "feedback": feedback},
        )
    )
    return {"theme": theme.to_dict()}


def build_style_options(
    state: DeckGraphState,
    html_renderer: HTMLSlideRenderer,
    browser: BrowserRenderer,
    llm_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    presets = load_theme_presets()
    if len(presets) == 1:
        presets.append(default_theme())
    title = str((state.get("plan") or {}).get("title") or state.get("deck_title") or "Untitled presentation")
    narrative = str((state.get("plan") or {}).get("narrative") or (state.get("brief") or {}).get("goal") or "")
    selected_presets = select_presets_with_model(state, presets, llm_registry) if llm_registry and llm_registry.get("style") is not None else presets[:2]
    options: list[dict[str, Any]] = []
    for index, theme in enumerate(selected_presets[:2], start=1):
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
                "tokens_summary": tokens_summary(theme),
            }
        )
    return options


def select_presets_with_model(state: DeckGraphState, presets: list[ThemeTokens], llm_registry: dict[str, Any]) -> list[ThemeTokens]:
    payload = {
        "brief": state.get("brief") or {},
        "outline_summary": state.get("outline_markdown") or "",
        "presets": [theme.to_dict() for theme in presets],
        "instruction": "Choose two presets or return two complete ThemeTokens. Keep contrast accessible.",
    }
    try:
        raw = invoke_json_with_retries(llm_registry["style"], system=load_node_prompt("style"), user=json.dumps(payload, ensure_ascii=False), attempts=2)
    except ValueError:
        record_fallback()
        return presets[:2]
    themes = _themes_from_style_output(raw, presets)
    valid = [theme for theme in themes if theme_is_valid(theme)]
    if len(valid) >= 2:
        return valid[:2]
    record_fallback()
    return presets[:2]


def adjust_theme_from_feedback(theme: ThemeTokens, feedback: str, style_llm: Any) -> ThemeTokens:
    raw = invoke_json_with_retries(
        style_llm,
        system=load_node_prompt("style"),
        user=json.dumps({"task": "Adjust this ThemeTokens object from feedback.", "theme": theme.to_dict(), "feedback": feedback}, ensure_ascii=False),
        attempts=2,
    )
    tokens = raw.get("tokens") if isinstance(raw.get("tokens"), dict) else raw
    return ThemeTokens.from_dict(tokens if isinstance(tokens, dict) else theme.to_dict())


def theme_is_valid(theme: ThemeTokens) -> bool:
    deck = DeckSpec(title="", narrative="", audience="", goal="", language="zh", theme=theme, slides=[])
    return not any(issue.severity == "must_fix" for issue in validate_theme_contrast(deck))


def _themes_from_style_output(raw: dict[str, Any], presets: list[ThemeTokens]) -> list[ThemeTokens]:
    by_name = {theme.name.casefold(): theme for theme in presets}
    themes: list[ThemeTokens] = []
    for item in raw.get("themes") or raw.get("options") or []:
        if isinstance(item, dict):
            themes.append(ThemeTokens.from_dict(item.get("tokens") if isinstance(item.get("tokens"), dict) else item))
    for item in raw.get("preset_names") or raw.get("selected") or raw.get("style_ids") or []:
        key = str(item).casefold()
        if key in by_name:
            themes.append(by_name[key])
    return themes


def public_style_options(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def select_style_option(options: list[dict[str, Any]], style_id: str) -> tuple[dict[str, Any], bool]:
    for option in options:
        if str(option.get("style_id") or "") == style_id:
            return option, True
    return (options[0], False) if options else ({"style_id": "style_1", "tokens": default_theme().to_dict(), "name": default_theme().name}, False)


def tokens_summary(theme: ThemeTokens) -> str:
    palette = theme.palette
    return f"{theme.name}: bg {palette.get('bg')}, ink {palette.get('ink')}, accent {palette.get('accent')} / {palette.get('accent2')}; {theme.motif}"
