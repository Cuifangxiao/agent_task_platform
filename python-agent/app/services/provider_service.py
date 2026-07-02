"""Provider 凭证相关的业务逻辑。"""

import uuid
from dataclasses import dataclass

from app.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class TestCredentialResult:
    request_id: str
    provider: str
    status: str
    message: str


class ProviderService:
    def test_credential(
        self,
        *,
        user_id: int,
        username: str,
        provider: str,
        api_key: str,
        base_url: str,
    ) -> TestCredentialResult:
        request_id = str(uuid.uuid4())

        # TODO: 按 provider 真正发起一次探活调用(如 list models)验证 api_key/base_url,
        # 后续接入 app/llm 下对应的客户端。
        logger.info(
            "test credential: request_id=%s user_id=%s provider=%s",
            request_id,
            user_id,
            provider,
        )

        return TestCredentialResult(
            request_id=request_id,
            provider=provider,
            status="received",
            message="python runtime received credential test request",
        )
