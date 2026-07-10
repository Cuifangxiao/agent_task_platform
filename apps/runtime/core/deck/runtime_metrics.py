"""Thread-safe per-node runtime metrics collected from actual model calls."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass
class NodeMetricCollector:
    token_usage: int = 0
    retry_count: int = 0
    fallback_count: int = 0
    model_call_count: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add(self, *, tokens: int = 0, retries: int = 0, fallbacks: int = 0, calls: int = 0) -> None:
        with self._lock:
            self.token_usage += max(0, int(tokens))
            self.retry_count += max(0, int(retries))
            self.fallback_count += max(0, int(fallbacks))
            self.model_call_count += max(0, int(calls))

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "token_usage": self.token_usage,
                "retry_count": self.retry_count,
                "fallback_count": self.fallback_count,
                "model_call_count": self.model_call_count,
            }


_CURRENT: ContextVar[NodeMetricCollector | None] = ContextVar("deck_node_metrics", default=None)


def begin_node_metrics() -> tuple[NodeMetricCollector, Token[NodeMetricCollector | None]]:
    collector = NodeMetricCollector()
    return collector, _CURRENT.set(collector)


def end_node_metrics(token: Token[NodeMetricCollector | None]) -> None:
    _CURRENT.reset(token)


def record_model_response(response: Any) -> None:
    collector = _CURRENT.get()
    if collector is not None:
        collector.add(tokens=_response_tokens(response), calls=1)


def record_model_call() -> None:
    collector = _CURRENT.get()
    if collector is not None:
        collector.add(calls=1)


def record_token_usage(response: Any) -> None:
    collector = _CURRENT.get()
    if collector is not None:
        collector.add(tokens=_response_tokens(response))


def record_retry(count: int = 1) -> None:
    collector = _CURRENT.get()
    if collector is not None:
        collector.add(retries=count)


def record_fallback(count: int = 1) -> None:
    collector = _CURRENT.get()
    if collector is not None:
        collector.add(fallbacks=count)


def _response_tokens(response: Any) -> int:
    for candidate in (
        getattr(response, "usage_metadata", None),
        getattr(response, "usage", None),
        getattr(response, "response_metadata", None),
    ):
        total = _tokens_from_mapping(candidate)
        if total > 0:
            return total
    return 0


def _tokens_from_mapping(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    for nested_key in ("token_usage", "usage", "usage_metadata"):
        total = _tokens_from_mapping(value.get(nested_key))
        if total > 0:
            return total
    for key in ("total_tokens", "total_token_count"):
        try:
            total = int(value.get(key) or 0)
        except (TypeError, ValueError):
            total = 0
        if total > 0:
            return total
    token_keys = ("input_tokens", "output_tokens") if value.get("input_tokens") or value.get("output_tokens") else ("prompt_tokens", "completion_tokens")
    values: list[int] = []
    for key in token_keys:
        try:
            item = int(value.get(key) or 0)
        except (TypeError, ValueError):
            item = 0
        if item > 0:
            values.append(item)
    return sum(values)
