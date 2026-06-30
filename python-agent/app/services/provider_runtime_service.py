import logging
import uuid

from api.provider.v1 import provider_pb2
from api.provider.v1 import provider_pb2_grpc


class ProviderRuntimeService(provider_pb2_grpc.ProviderRuntimeServiceServicer):
    def TestCredential(self, request, context):
        request_id = str(uuid.uuid4())

        logging.info(
            "received provider test request: request_id=%s user_id=%s username=%s provider=%s device=%s",
            request_id,
            request.user.user_id,
            request.user.username,
            request.provider,
            request.user.device,
        )

        return provider_pb2.TestCredentialResponse(
            request_id=request_id,
            provider=request.provider,
            status="received",
            message="python runtime received credential test request",
        )
