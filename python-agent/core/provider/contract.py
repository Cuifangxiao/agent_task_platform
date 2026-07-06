"""provider 领域的输入/输出契约。

刻意用 Input/Output 而非 Request/Response 命名: 这些 DTO 隔离 protobuf,
内核只认识它们, proto <-> DTO 的转换收敛在 handler 层。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TestCredentialInput:
    user_id: int
    username: str
    provider: str
    api_key: str
    base_url: str


@dataclass(frozen=True)
class TestCredentialOutput:
    request_id: str
    provider: str
    status: str
    message: str


@dataclass(frozen=True)
class StreamTextInput:
    user_id: int
    username: str
    provider: str
    model: str
    api_key: str
    base_url: str
    prompt: str
    system_prompt: str
    temperature: float
    max_tokens: int
    provider_options_json: str


@dataclass(frozen=True)
class StreamTextOutput:
    request_id: str
    delta: str = ""
    done: bool = False
    error: str = ""
    sequence: int = 0


@dataclass(frozen=True)
class ProviderModelParameterOutput:
    name: str
    display_name: str
    type: str
    description: str
    default_value: str
    options: list[str]
    required: bool
    min: float | None
    max: float | None


@dataclass(frozen=True)
class ProviderModelOutput:
    id: str
    model_name: str
    display_name: str
    type: str
    reasoning_supported: bool
    parameters: list[ProviderModelParameterOutput]


@dataclass(frozen=True)
class ProviderOutput:
    name: str
    display_name: str
    description: str
    manager: str
    sdk: str
    default_base_url: str
    supports_credential_test: bool
    supports_custom_base_url: bool
    models: list[ProviderModelOutput]
