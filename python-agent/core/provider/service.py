"""Provider 凭证相关的业务逻辑。"""

import logging
import uuid

from core.provider.contract import TestCredentialInput, TestCredentialOutput

logger = logging.getLogger(__name__)


class ProviderService:
    def test_credential(self, inp: TestCredentialInput) -> TestCredentialOutput:
        request_id = str(uuid.uuid4())

        # TODO: 按 provider 真正发起一次探活调用(如 list models)验证 api_key/base_url,
        # 后续接入 core/llm 下对应的客户端。
        logger.info(
            "test credential: request_id=%s user_id=%s provider=%s",
            request_id,
            inp.user_id,
            inp.provider,
        )

        return TestCredentialOutput(
            request_id=request_id,
            provider=inp.provider,
            status="received",
            message="python runtime received credential test request",
        )
