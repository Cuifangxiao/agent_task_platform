"""LLM registry, prompt loading, streaming, and JSON parsing for deck nodes."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from core.deck.runtime_metrics import record_model_call, record_model_response, record_retry, record_token_usage


PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


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
    if hasattr(llm, "stream"):
        record_model_call()
        for chunk in llm.stream(messages):
            record_token_usage(chunk)
            text = extract_text(chunk)
            if text:
                yield text
        return
    response = llm.invoke(messages)
    record_model_response(response)
    text = extract_text(response)
    if text:
        yield text


def invoke_json_with_retries(llm: Any, system: str, user: str, *, attempts: int) -> dict[str, Any]:
    last_error: Exception | None = None
    prompt = user
    for _attempt in range(max(1, attempts)):
        try:
            record_model_call()
            response = llm.invoke([("system", system), ("human", prompt)])
            record_token_usage(response)
            return parse_json_object(extract_text(response))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_error = exc
            if _attempt + 1 < max(1, attempts):
                record_retry()
            prompt = user + f"\n\nYour previous response was invalid JSON: {exc}. Return only one JSON object."
    raise ValueError(str(last_error or "model did not return JSON"))


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
