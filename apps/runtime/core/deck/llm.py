"""LLM registry, prompt loading, streaming, and JSON parsing for deck nodes."""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from core.deck.runtime_metrics import record_model_call, record_model_response, record_retry, record_token_usage


PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
logger = logging.getLogger(__name__)
_LLM_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("deck_llm_log_context", default={})


@contextmanager
def llm_log_context(
    *,
    request_id: str | None = None,
    stage: str | None = None,
    slide_index: int | None = None,
    operation: str | None = None,
):
    """Attach safe correlation fields to model-call logs in the current task."""

    values = dict(_LLM_LOG_CONTEXT.get())
    for key, value in {
        "request_id": request_id,
        "stage": stage,
        "slide_index": slide_index,
        "operation": operation,
    }.items():
        if value is not None:
            values[key] = value
    token = _LLM_LOG_CONTEXT.set(values)
    try:
        yield
    finally:
        _LLM_LOG_CONTEXT.reset(token)


@lru_cache(maxsize=None)
def load_node_prompt(node_key: str) -> str:
    path = PROMPT_DIR / f"{node_key}.md"
    try:
        prompt = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ValueError(f"missing deck node prompt file: {path}") from exc
    if not prompt:
        raise ValueError(f"deck node prompt file is empty: {path}")
    return prompt


def normalize_llm_registry(llms: Any) -> dict[str, Any]:
    if isinstance(llms, dict):
        registry: dict[str, Any] = {}
        for key, value in llms.items():
            node_key = str(key).strip().lower()
            if node_key and value is not None:
                registry[node_key] = value
        return registry
    return {"default": llms}


def llm_for_node(registry: dict[str, Any], node_key: str, *, aliases: Iterable[str] = ()) -> Any:
    keys = (node_key, *aliases, "default")
    for key in keys:
        llm = registry.get(key)
        if llm is not None:
            return llm
    raise ValueError(f"missing llm for deck node '{node_key}'")


def compose_context_prompt(payload: dict[str, Any], *, uploaded_file_text: str) -> str:
    parts = ["Request context:", json.dumps(payload, ensure_ascii=False)]
    uploaded_file_text = (uploaded_file_text or "").strip()
    if uploaded_file_text:
        parts.extend(["", "Uploaded documents:", uploaded_file_text])
    return "\n".join(parts)


def stream_llm_text(llm: Any, messages: list[tuple[str, str]]):
    started_at = time.perf_counter()
    input_chars = sum(len(str(content)) for _role, content in messages)
    _log_call_start(llm, kind="stream", attempt=1, attempts=1, input_chars=input_chars)
    if hasattr(llm, "stream"):
        record_model_call()
        output_chars = 0
        first_chunk_ms = 0
        try:
            for chunk in llm.stream(messages):
                record_token_usage(chunk)
                text = extract_text(chunk)
                if text:
                    if not first_chunk_ms:
                        first_chunk_ms = round((time.perf_counter() - started_at) * 1000)
                    output_chars += len(text)
                    yield text
        except Exception as exc:
            _log_call_error(
                llm,
                kind="stream",
                attempt=1,
                attempts=1,
                started_at=started_at,
                phase="stream",
                exc=exc,
                output_chars=output_chars,
            )
            raise
        _log_call_end(
            llm,
            kind="stream",
            attempt=1,
            attempts=1,
            started_at=started_at,
            output_chars=output_chars,
            first_chunk_ms=first_chunk_ms,
        )
        return
    try:
        response = llm.invoke(messages)
        record_model_response(response)
        text = extract_text(response)
    except Exception as exc:
        _log_call_error(
            llm,
            kind="invoke",
            attempt=1,
            attempts=1,
            started_at=started_at,
            phase="invoke",
            exc=exc,
        )
        raise
    _log_call_end(
        llm,
        kind="invoke",
        attempt=1,
        attempts=1,
        started_at=started_at,
        output_chars=len(text),
    )
    if text:
        yield text


def invoke_json_with_retries(llm: Any, system: str, user: str, *, attempts: int) -> dict[str, Any]:
    last_error: Exception | None = None
    prompt = user
    total_attempts = max(1, attempts)
    for attempt in range(total_attempts):
        started_at = time.perf_counter()
        phase = "invoke"
        output_chars = 0
        _log_call_start(
            llm,
            kind="json",
            attempt=attempt + 1,
            attempts=total_attempts,
            input_chars=len(system) + len(prompt),
        )
        try:
            record_model_call()
            response = llm.invoke([("system", system), ("human", prompt)])
            record_token_usage(response)
            phase = "parse"
            text = extract_text(response)
            output_chars = len(text)
            parsed = parse_json_object(text)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_error = exc
            _log_call_error(
                llm,
                kind="json",
                attempt=attempt + 1,
                attempts=total_attempts,
                started_at=started_at,
                phase=phase,
                exc=exc,
                output_chars=output_chars,
            )
            if attempt + 1 < total_attempts:
                record_retry()
            prompt = user + f"\n\nYour previous response was invalid JSON: {exc}. Return only one JSON object."
            continue
        _log_call_end(
            llm,
            kind="json",
            attempt=attempt + 1,
            attempts=total_attempts,
            started_at=started_at,
            output_chars=output_chars,
        )
        return parsed
    raise ValueError(str(last_error or "model did not return JSON"))


def invoke_text_with_retries(llm: Any, system: str, user: str, *, attempts: int) -> str:
    """Invoke a model for a non-JSON artifact and reject empty responses.

    SVG authoring is deliberately handled as text instead of asking a JSON model
    to escape a complete XML document. Callers choose the number of transport or
    empty-output attempts; the SVG Artist uses exactly one attempt and relies on
    deterministic validation plus a local fallback.
    """

    last_error: Exception | None = None
    prompt = user
    total_attempts = max(1, attempts)
    for attempt in range(total_attempts):
        started_at = time.perf_counter()
        output_chars = 0
        _log_call_start(
            llm,
            kind="text",
            attempt=attempt + 1,
            attempts=total_attempts,
            input_chars=len(system) + len(prompt),
        )
        try:
            record_model_call()
            response = llm.invoke([("system", system), ("human", prompt)])
            record_token_usage(response)
            result = extract_text(response).strip()
            output_chars = len(result)
            if not result:
                raise ValueError("model returned an empty text artifact")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_error = exc
            _log_call_error(
                llm,
                kind="text",
                attempt=attempt + 1,
                attempts=total_attempts,
                started_at=started_at,
                phase="invoke_or_validate",
                exc=exc,
                output_chars=output_chars,
            )
            if attempt + 1 < total_attempts:
                record_retry()
            prompt = user + f"\n\nThe previous attempt failed: {exc}. Return the requested artifact only."
            continue
        _log_call_end(
            llm,
            kind="text",
            attempt=attempt + 1,
            attempts=total_attempts,
            started_at=started_at,
            output_chars=output_chars,
        )
        return result
    raise ValueError(str(last_error or "model did not return a text artifact"))


def _log_call_start(llm: Any, *, kind: str, attempt: int, attempts: int, input_chars: int) -> None:
    request_id, stage, slide_index, operation = _log_context_values()
    logger.info(
        "event=ppt_llm_call_start request_id=%s stage=%s slide_index=%s operation=%s kind=%s model=%s "
        "attempt=%s attempts=%s input_chars=%s",
        request_id,
        stage,
        slide_index,
        operation,
        kind,
        _model_name(llm),
        attempt,
        attempts,
        input_chars,
    )


def _log_call_end(
    llm: Any,
    *,
    kind: str,
    attempt: int,
    attempts: int,
    started_at: float,
    output_chars: int,
    first_chunk_ms: int = 0,
) -> None:
    request_id, stage, slide_index, operation = _log_context_values()
    logger.info(
        "event=ppt_llm_call_end request_id=%s stage=%s slide_index=%s operation=%s kind=%s model=%s "
        "attempt=%s attempts=%s duration_ms=%s first_chunk_ms=%s output_chars=%s",
        request_id,
        stage,
        slide_index,
        operation,
        kind,
        _model_name(llm),
        attempt,
        attempts,
        round((time.perf_counter() - started_at) * 1000),
        first_chunk_ms,
        output_chars,
    )


def _log_call_error(
    llm: Any,
    *,
    kind: str,
    attempt: int,
    attempts: int,
    started_at: float,
    phase: str,
    exc: Exception,
    output_chars: int = 0,
) -> None:
    request_id, stage, slide_index, operation = _log_context_values()
    logger.warning(
        "event=ppt_llm_call_error request_id=%s stage=%s slide_index=%s operation=%s kind=%s model=%s "
        "attempt=%s attempts=%s duration_ms=%s phase=%s output_chars=%s error_type=%s error=%s",
        request_id,
        stage,
        slide_index,
        operation,
        kind,
        _model_name(llm),
        attempt,
        attempts,
        round((time.perf_counter() - started_at) * 1000),
        phase,
        output_chars,
        type(exc).__name__,
        _safe_error(exc),
    )


def _log_context_values() -> tuple[str, str, int, str]:
    values = _LLM_LOG_CONTEXT.get()
    return (
        str(values.get("request_id") or ""),
        str(values.get("stage") or ""),
        int(values.get("slide_index") or 0),
        str(values.get("operation") or ""),
    )


def _model_name(llm: Any) -> str:
    for attribute in ("model_name", "model"):
        value = getattr(llm, attribute, None)
        if value:
            return str(value)
    return type(llm).__name__


def _safe_error(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500]


def extract_text(response: Any) -> str:
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


def parse_json_object(text: str) -> dict[str, Any]:
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
