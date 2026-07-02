"""ProviderRuntimeService 的 gRPC servicer 实现。

对应 proto: api/provider/v1/provider.proto
"""

import grpc

from api.provider.v1 import provider_pb2
from api.provider.v1 import provider_pb2_grpc

from app.logger import get_logger
from app.services.provider_service import ProviderService

logger = get_logger(__name__)


class ProviderRuntimeServicer(provider_pb2_grpc.ProviderRuntimeServiceServicer):
    def __init__(self, provider_service: ProviderService) -> None:
        self._provider_service = provider_service

    def TestCredential(self, request, context):
        logger.info(
            "recv TestCredential: user_id=%s username=%s provider=%s device=%s",
            request.user.user_id,
            request.user.username,
            request.provider,
            request.user.device,
        )

        try:
            result = self._provider_service.test_credential(
                user_id=request.user.user_id,
                username=request.user.username,
                provider=request.provider,
                api_key=request.api_key,
                base_url=request.base_url,
            )
        except Exception:
            logger.exception("TestCredential failed: provider=%s", request.provider)
            context.abort(grpc.StatusCode.INTERNAL, "test credential failed")

        return provider_pb2.TestCredentialResponse(
            request_id=result.request_id,
            provider=result.provider,
            status=result.status,
            message=result.message,
        )
