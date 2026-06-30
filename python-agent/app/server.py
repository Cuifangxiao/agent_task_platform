from concurrent import futures
import logging

import grpc

from app.services.provider_runtime_service import ProviderRuntimeService
from api.provider.v1 import provider_pb2_grpc


def serve() -> None:
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    provider_pb2_grpc.add_ProviderRuntimeServiceServicer_to_server(
        ProviderRuntimeService(),
        server,
    )

    listen_addr = "0.0.0.0:50051"
    server.add_insecure_port(listen_addr)
    server.start()

    logging.basicConfig(level=logging.INFO)
    logging.info("python agent gRPC server listening on %s", listen_addr)

    server.wait_for_termination()
