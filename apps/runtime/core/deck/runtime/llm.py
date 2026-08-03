"""LLM registry, prompt loading, and schema-bound invocation for deck nodes."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from core.deck.runtime.metrics import record_model_call, record_retry, record_token_usage


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
logger = logging.getLogger(__name__)
_LLM_LOG_CONTEXT: ContextVar[dict[str, Any]] = ContextVar("deck_llm_log_context", default={})
StructuredModel = TypeVar("StructuredModel", bound=BaseModel)
StructuredOutputMethod = Literal["json_schema", "json_mode", "function_calling"]


class StructuredOutputError(ValueError):
    """The model answered, but its complete response violated the schema."""


class ModelInvocationError(RuntimeError):
    """The provider call failed before a schema-valid answer was available."""


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


def llm_for_node(registry: dict[str, Any], node_key: str) -> Any:
    keys = (node_key, "default")
    for key in keys:
        llm = registry.get(key)
        if llm is not None:
            return llm
    raise ValueError(f"missing llm for deck node '{node_key}'")


def invoke_structured_with_retries(
    llm: Any,
    model_type: type[StructuredModel],
    *,
    system: str,
    user: str,
    attempts: int = 2,
    output_validator: Callable[[StructuredModel], StructuredModel] | None = None,
) -> StructuredModel:
    """Invoke a model through a formal Pydantic schema boundary.

    LangChain-native models use ``with_structured_output``. Other providers
    must return one complete JSON document which is parsed with
    ``model_validate_json``; Markdown fences and substring recovery are
    intentionally not supported.
    """

    structured_llm: Any = None
    if hasattr(llm, "with_structured_output"):
        try:
            method = _structured_output_method(llm)
            if method is None:
                structured_llm = llm.with_structured_output(model_type)
            else:
                structured_llm = llm.with_structured_output(model_type, method=method)
        except (AttributeError, NotImplementedError, TypeError, ValueError):
            structured_llm = None

    schema_json = json.dumps(model_type.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    strict_system = system + "\n\nOutput must conform to this JSON Schema:\n" + schema_json
    prompt = user
    total_attempts = max(1, int(attempts))
    last_validation_error: Exception | None = None
    last_invocation_error: Exception | None = None

    for attempt in range(total_attempts):
        started_at = time.perf_counter()
        output_chars = 0
        _log_call_start(
            llm,
            kind="structured",
            attempt=attempt + 1,
            attempts=total_attempts,
            input_chars=len(strict_system) + len(prompt),
        )
        try:
            record_model_call()
            target = structured_llm or llm
            response = target.invoke([("system", strict_system), ("human", prompt)])
            record_token_usage(response)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # LangChain structured wrappers commonly surface schema/parser
            # failures as plain ValueError.  A raw provider ValueError remains
            # an invocation error; only the structured wrapper gets this
            # validation classification.
            if _is_structured_validation_exception(exc) or (
                structured_llm is not None and isinstance(exc, ValueError)
            ):
                last_validation_error = exc
                _log_call_error(
                    llm,
                    kind="structured",
                    attempt=attempt + 1,
                    attempts=total_attempts,
                    started_at=started_at,
                    phase="validate",
                    exc=exc,
                )
                if attempt + 1 < total_attempts:
                    record_retry()
                    prompt = user + "\n\nThe previous structured response violated the schema. Return one corrected JSON object only."
                    continue
                raise StructuredOutputError(str(exc)) from exc
            last_invocation_error = exc
            _log_call_error(
                llm,
                kind="structured",
                attempt=attempt + 1,
                attempts=total_attempts,
                started_at=started_at,
                phase="invoke",
                exc=exc,
            )
            if attempt + 1 < total_attempts:
                record_retry()
                continue
            raise ModelInvocationError(str(exc)) from exc

        try:
            if isinstance(response, model_type):
                parsed = response
            elif isinstance(response, BaseModel):
                parsed = model_type.model_validate(response.model_dump(mode="json"))
            elif isinstance(response, dict):
                parsed = model_type.model_validate(response)
            else:
                text = extract_text(response).strip()
                output_chars = len(text)
                parsed = model_type.model_validate_json(text)
            if output_validator is not None:
                parsed = output_validator(parsed)
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError) as exc:
            last_validation_error = exc
            _log_call_error(
                llm,
                kind="structured",
                attempt=attempt + 1,
                attempts=total_attempts,
                started_at=started_at,
                phase="validate",
                exc=exc,
                output_chars=output_chars,
            )
            if attempt + 1 < total_attempts:
                record_retry()
                prompt = user + "\n\nThe previous complete response violated the schema. Return one corrected JSON object only."
                continue
            raise StructuredOutputError(str(exc)) from exc

        _log_call_end(
            llm,
            kind="structured",
            attempt=attempt + 1,
            attempts=total_attempts,
            started_at=started_at,
            output_chars=output_chars,
        )
        return parsed

    if last_invocation_error is not None:
        raise ModelInvocationError(str(last_invocation_error)) from last_invocation_error
    raise StructuredOutputError(str(last_validation_error or "model did not return schema-valid output"))


def _is_structured_validation_exception(exc: Exception) -> bool:
    """Distinguish parser/schema failures from provider transport failures."""

    if isinstance(exc, (ValidationError, json.JSONDecodeError, StructuredOutputError)):
        return True
    return type(exc).__name__ in {
        "OutputParserException",
        "SchemaValidationError",
        "StructuredOutputValidationError",
    }


def _structured_output_method(llm: Any) -> StructuredOutputMethod | None:
    metadata = getattr(llm, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    method = str(metadata.get("structured_output_method") or "").strip().lower()
    if method in {"json_schema", "json_mode", "function_calling"}:
        return cast(StructuredOutputMethod, method)
    return None


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
) -> None:
    request_id, stage, slide_index, operation = _log_context_values()
    logger.info(
        "event=ppt_llm_call_end request_id=%s stage=%s slide_index=%s operation=%s kind=%s model=%s "
        "attempt=%s attempts=%s duration_ms=%s output_chars=%s",
        request_id,
        stage,
        slide_index,
        operation,
        kind,
        _model_name(llm),
        attempt,
        attempts,
        round((time.perf_counter() - started_at) * 1000),
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
