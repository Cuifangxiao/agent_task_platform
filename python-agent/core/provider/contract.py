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
