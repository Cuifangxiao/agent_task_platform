import logging
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ConfigDict

import core.deck.pipeline.instrumentation as instrumentation
from core.deck.runtime.llm import invoke_structured_with_retries
from core.deck.runtime.metrics import begin_node_metrics, end_node_metrics, record_fallback


class RetryingUsageLLM:
    def __init__(self):
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return SimpleNamespace(content="not-json", usage_metadata={"input_tokens": 10, "output_tokens": 2})
        return SimpleNamespace(content='{"ok": true}', usage_metadata={"total_tokens": 20})


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool


def test_actual_model_tokens_retries_and_fallbacks_are_collected():
    collector, token = begin_node_metrics()
    try:
        result = invoke_structured_with_retries(
            RetryingUsageLLM(),
            _Payload,
            system="system",
            user="user",
            attempts=2,
        )
        record_fallback()
    finally:
        end_node_metrics(token)

    assert result == _Payload(ok=True)
    assert collector.snapshot() == {
        "token_usage": 32,
        "retry_count": 1,
        "fallback_count": 1,
        "model_call_count": 2,
    }


@pytest.mark.parametrize(
    ("stage", "model_key", "model_name"),
    [
        ("generate_slide_design", "style", "style-model"),
        ("generate_slide_html", "designer", "designer-model"),
    ],
)
def test_instrumented_model_nodes_use_role_metadata_and_log_metrics(
    caplog,
    stage,
    model_key,
    model_name,
):
    caplog.set_level(logging.INFO, logger=instrumentation.__name__)

    class UsageLLM:
        def invoke(self, _messages):
            return SimpleNamespace(content='{"ok": true}', usage_metadata={"total_tokens": 17})

    def node(_state):
        invoke_structured_with_retries(
            UsageLLM(),
            _Payload,
            system="system",
            user="user",
            attempts=1,
        )
        return {"value": 1}

    wrapped = instrumentation.instrument_node(
        stage,
        node,
        {
            "__metadata__": {
                model_key: {"provider": "openai", "model": model_name}
            }
        },
        model_key=model_key,
    )

    assert wrapped({"task_id": "task-1"}) == {"value": 1}
    assert "event=ppt_step_start" in caplog.text
    assert "event=ppt_step_end" in caplog.text
    assert f"stage={stage}" in caplog.text
    assert "provider=openai" in caplog.text
    assert f"model={model_name}" in caplog.text
    assert "model_call_count=1" in caplog.text
    assert "token_usage=17" in caplog.text
    assert "retry_count=0" in caplog.text
    assert "fallback_count=0" in caplog.text
