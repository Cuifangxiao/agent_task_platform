import json
from types import SimpleNamespace

import core.deck.pipeline as pipeline
from core.deck.pipeline.llm import invoke_json_with_retries
from core.deck.runtime_metrics import begin_node_metrics, end_node_metrics, record_fallback


class RetryingUsageLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(content="not-json", usage_metadata={"input_tokens": 10, "output_tokens": 2})
        return SimpleNamespace(content=json.dumps({"ok": True}), usage_metadata={"total_tokens": 20})


def test_actual_model_tokens_retries_and_fallbacks_are_collected():
    collector, token = begin_node_metrics()
    try:
        result = invoke_json_with_retries(RetryingUsageLLM(), system="system", user="user", attempts=2)
        record_fallback()
    finally:
        end_node_metrics(token)
    assert result == {"ok": True}
    assert collector.snapshot() == {
        "token_usage": 32,
        "retry_count": 1,
        "fallback_count": 1,
        "model_call_count": 2,
    }


def test_instrumented_node_emits_real_model_and_usage_metrics(monkeypatch):
    emitted = []
    monkeypatch.setattr(pipeline, "get_stream_writer", lambda: emitted.append)

    class UsageLLM:
        def invoke(self, _messages):
            return SimpleNamespace(content='{"value": 1}', usage_metadata={"total_tokens": 17})

    def node(_state):
        invoke_json_with_retries(UsageLLM(), system="system", user="user", attempts=1)
        return {}

    wrapped = pipeline._instrumented(
        "writer",
        node,
        {"__metadata__": {"writer": {"provider": "openai", "model": "gpt-test"}}},
    )
    wrapped({})
    metrics = emitted[-1]["payload"]["metrics"]
    assert metrics["provider"] == "openai"
    assert metrics["model"] == "gpt-test"
    assert metrics["token_usage"] == 17
    assert metrics["model_call_count"] == 1
    assert metrics["retry_count"] == 0
