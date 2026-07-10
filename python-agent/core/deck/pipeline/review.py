"""Deterministic validation node for deck specs and rendered artifacts."""

from __future__ import annotations

import logging
import re
from typing import Any

from langgraph.config import get_stream_writer

import core.deck.events as events
from core.deck.pipeline.state import DeckGraphState
from core.deck.quality import ValidationIssue, dedupe_issue_dicts, issues_to_dicts, validate_deck_spec, validate_theme_contrast
from core.deck.spec import DeckSpec

logger = logging.getLogger(__name__)


def validate_deck(state: DeckGraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    request_id = str(state.get("request_id") or "")
    logger.info("deck validate start: request_id=%s", request_id)
    deck = DeckSpec.from_dict(state.get("deck_spec") or {})
    issues = validate_theme_contrast(deck) + validate_deck_spec(deck) + validate_fact_sources(deck, state.get("brief") or {})
    current = list(state.get("validation_issues") or [])
    if issues:
        current.extend(issues_to_dicts(issues))
    current = dedupe_issue_dicts(current)
    must_fix = sum(1 for issue in current if issue.get("severity") == "must_fix")
    logger.info("deck validate done: request_id=%s issues=%s must_fix=%s", request_id, len(current), must_fix)
    writer(events.validation_report(current))
    return {"validation_issues": current}


def validate_fact_sources(deck: DeckSpec, brief: dict[str, Any]) -> list[ValidationIssue]:
    facts = {str(item.get("id") or "").strip(): item for item in brief.get("facts") or [] if isinstance(item, dict)}
    has_external_facts = any(str(fact.get("source_file") or "") not in {"", "user_request"} for fact in facts.values())
    issues: list[ValidationIssue] = []
    for slide in deck.slides:
        missing = [source for source in slide.sources if source not in facts]
        if missing:
            issues.append(
                ValidationIssue(
                    page=slide.index,
                    severity="must_fix",
                    kind="missing_fact_source",
                    description=f"Slide cites missing fact ids: {', '.join(missing)}.",
                    suggestion="Use only fact ids from the brief or remove unsupported citations.",
                )
            )
        visible_text = " ".join(_visible_slide_text_values(slide))
        supported_text_parts: list[str] = []
        for source in slide.sources:
            fact = facts.get(source)
            if not fact:
                continue
            supported_text_parts.extend(str(fact.get(key) or "") for key in ("statement", "value", "unit", "source_excerpt"))
        if has_external_facts and slide.role in {"context", "evidence"} and visible_text.strip() and not supported_text_parts:
            issues.append(
                ValidationIssue(
                    page=slide.index,
                    severity="must_fix",
                    kind="unsupported_evidence_claim",
                    description="This evidence slide does not cite any fact from the supplied source material.",
                    suggestion="Cite at least one matching fact id or rewrite the page as an explicitly source-free interpretation.",
                )
            )
        visible_claims = _claim_tokens(visible_text)
        supported_claims = _claim_tokens(" ".join(supported_text_parts))
        for claim_kind, issue_kind, label in (
            ("number", "unsupported_numeric_claim", "numeric claims"),
            ("date", "unsupported_date_claim", "date claims"),
            ("money", "unsupported_money_claim", "money claims"),
            ("entity", "unsupported_entity_claim", "key entities"),
        ):
            unsupported = sorted(visible_claims[claim_kind] - supported_claims[claim_kind])
            if not unsupported:
                continue
            issues.append(
                ValidationIssue(
                    page=slide.index,
                    severity="must_fix",
                    kind=issue_kind,
                    description=f"Visible {label} lack a matching fact source: {', '.join(unsupported)}.",
                    suggestion="Cite a matching fact id or remove the unsupported claim.",
                )
            )
    return issues


def _visible_numbers(text: str) -> list[str]:
    return sorted(_claim_tokens(text)["number"])


def _visible_slide_text_values(slide: Any) -> list[str]:
    values = [slide.title]

    def walk(value: Any) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            values.append(str(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(slide.content)
    if slide.chart:
        unit = str(slide.chart.get("unit") or "")
        for item in slide.chart.get("series") or []:
            if not isinstance(item, dict):
                continue
            walk(item.get("label"))
            value = item.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(f"{value}{unit}")
    return values


def _claim_tokens(text: str) -> dict[str, set[str]]:
    value = text or ""
    numbers = {
        _normalize_claim(match)
        for match in re.findall(r"(?<![\w#])\d[\d,]*(?:\.\d+)?%?", value)
    }
    dates = {
        _normalize_claim(match)
        for match in re.findall(
            r"\b\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?\b|\d{4}年\d{1,2}月(?:\d{1,2}日)?|\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}\b",
            value,
            flags=re.IGNORECASE,
        )
    }
    money = {
        _normalize_claim(match)
        for match in re.findall(
            r"(?:[$€£¥￥]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:k|m|bn|million|billion|万|亿))?|\b(?:USD|EUR|GBP|CNY|RMB)\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:k|m|bn|million|billion|万|亿))?)",
            value,
            flags=re.IGNORECASE,
        )
    }
    entity_patterns = (
        r"\b[A-Z][A-Z0-9&.-]{1,}\b",
        r"\b[A-Z][a-z]+[A-Z][A-Za-z0-9]*\b",
        r"\b(?:[A-Z][a-zA-Z0-9&.-]+\s+){1,3}[A-Z][a-zA-Z0-9&.-]+\b",
        r"[\u4e00-\u9fff]{2,20}(?:公司|集团|大学|委员会|政府|研究院|银行|平台|系统|产品)",
    )
    entities = {
        _normalize_claim(match)
        for pattern in entity_patterns
        for match in re.findall(pattern, value)
    }
    return {"number": numbers, "date": dates, "money": money, "entity": entities}


def _normalize_claim(value: str) -> str:
    return re.sub(r"[\s,]+", "", value).casefold()
