import json
import logging
from types import SimpleNamespace

import core.deck.instrumentation as instrumentation
from core.deck.llm import invoke_json_with_retries
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


def test_instrumented_node_logs_metrics_without_emitting_a_completion_event(monkeypatch, caplog):
    def fail_if_stream_writer_is_requested():
        raise AssertionError("instrument_node must not emit public events")

    monkeypatch.setattr(instrumentation, "get_stream_writer", fail_if_stream_writer_is_requested)
    caplog.set_level(logging.INFO, logger=instrumentation.__name__)

    class UsageLLM:
        def invoke(self, _messages):
            return SimpleNamespace(content='{"value": 1}', usage_metadata={"total_tokens": 17})

    def node(_state):
        invoke_json_with_retries(UsageLLM(), system="system", user="user", attempts=1)
        return {"value": 1}

    wrapped = instrumentation.instrument_node(
        "writer",
        node,
        {"__metadata__": {"writer": {"provider": "openai", "model": "gpt-test"}}},
    )

    assert wrapped({"task_id": "task-1"}) == {"value": 1}
    assert "event=ppt_step_end" in caplog.text
    assert "stage=writer" in caplog.text
    assert "model_call_count=1" in caplog.text
    assert "token_usage=17" in caplog.text
    assert "retry_count=0" in caplog.text
    assert "fallback_count=0" in caplog.text
