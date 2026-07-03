"""provider 相关 RPC 的编排层: proto <-> core contract 的双向转换。

不处理异常、不 set StatusCode——那是 server 层(错误边界)的职责。
"""

from api import provider_pb2
from core.provider.contract import TestCredentialInput
from core.provider.service import ProviderService


class ProviderHandler:
    def __init__(self, provider_service: ProviderService) -> None:
        self._provider_service = provider_service

    def test_credential(
        self, req: provider_pb2.TestCredentialRequest
    ) -> provider_pb2.TestCredentialResponse:
        out = self._provider_service.test_credential(
            TestCredentialInput(
                user_id=req.user.user_id,
                username=req.user.username,
                provider=req.provider,
                api_key=req.api_key,
                base_url=req.base_url,
            )
        )
        return provider_pb2.TestCredentialResponse(
            request_id=out.request_id,
            provider=out.provider,
            status=out.status,
            message=out.message,
        )
