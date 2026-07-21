import pytest
from pydantic import BaseModel, ConfigDict

from core.deck.runtime.llm import StructuredOutputError, invoke_structured_with_retries


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int


class OutputParserException(Exception):
    """Mirrors the exception name emitted by LangChain structured parsers."""


class _StructuredLLM:
    def __init__(self, responses, *, metadata=None):
        self.responses = list(responses)
        self.calls = 0
        self.metadata = metadata
        self.structured_output_kwargs = None

    def with_structured_output(self, _model_type, **kwargs):
        self.structured_output_kwargs = kwargs
        return self

    def invoke(self, _messages):
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


def test_structured_parser_failure_is_retried_as_validation_error():
    llm = _StructuredLLM([OutputParserException("invalid schema"), {"value": 7}])

    result = invoke_structured_with_retries(
        llm,
        _Payload,
        system="Return a payload.",
        user="A value is required.",
        attempts=2,
    )

    assert result.value == 7
    assert llm.calls == 2


def test_exhausted_structured_parser_failures_remain_non_transport_errors():
    llm = _StructuredLLM([OutputParserException("bad one"), OutputParserException("bad two")])

    with pytest.raises(StructuredOutputError, match="bad two"):
        invoke_structured_with_retries(
            llm,
            _Payload,
            system="Return a payload.",
            user="A value is required.",
            attempts=2,
        )


def test_structured_output_uses_method_from_managed_model_metadata():
    llm = _StructuredLLM(
        [{"value": 7}],
        metadata={"structured_output_method": "json_mode"},
    )

    result = invoke_structured_with_retries(
        llm,
        _Payload,
        system="Return a payload.",
        user="A value is required.",
        attempts=1,
    )

    assert result.value == 7
    assert llm.structured_output_kwargs == {"method": "json_mode"}
